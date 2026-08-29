from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..schema_roles import (
    CONTRACTION_CANDIDATE_TABLES,
    CONTRACTION_MIGRATION_FILENAME,
    CONTRACTED_SCHEMA_TABLES,
    DELEGATION_PRODUCTIZATION_BOUNDARY,
    PREDICATE_TABLE_REPLACEMENT_CARRIERS,
    REVIEW_LANE_ACTIVATION_CRITERIA,
    SCHEMA_FREEZE_POLICY,
    advanced_schema_visibility,
    contraction_candidate_policy,
    expected_schema_snapshot,
    predicate_table_replacement_carriers,
    review_lane_activation_criteria,
    schema_role_rows,
    schema_visibility_policy,
    table_role_summary,
    verify_schema_roles,
)


SchemaHandler = Callable[[argparse.Namespace], int]
SCHEMA_HANDLER_KEYS = ("roles", "verify", "guard", "drift_package", "p1_p2_package")
SCHEMA_DEPENDENCY_KEYS = (
    "connect_read_only",
    "open_db_raw",
    "migration_status",
    "migration_files",
    "applied_migrations",
    "sha256_text",
    "print_json",
    "relpath",
)

connect_read_only: Callable[[Path], sqlite3.Connection] | None = None
open_db_raw: Callable[..., sqlite3.Connection | None] | None = None
migration_status: Callable[[Path, sqlite3.Connection], dict[str, Any]] | None = None
migration_files: Callable[[Path], list[Path]] | None = None
applied_migrations: Callable[[sqlite3.Connection], dict[str, sqlite3.Row]] | None = None
sha256_text: Callable[[str], str] | None = None
print_json: Callable[[Any], None] | None = None
relpath: Callable[[Path, Path], str] | None = None


def _schema_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in SCHEMA_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"schema stewardship handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in SCHEMA_DEPENDENCY_KEYS}


def _require_dependency(name: str) -> Any:
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"schema stewardship command dependency is not configured: {name}")
    return value


def build_schema_stewardship_handlers(deps: Mapping[str, Any]) -> dict[str, SchemaHandler]:
    globals().update(_schema_dependencies(deps))
    return {
        "roles": cmd_schema_roles,
        "verify": cmd_schema_verify,
        "guard": cmd_schema_guard,
        "drift_package": cmd_schema_drift_package,
        "p1_p2_package": cmd_schema_p1_p2_package,
    }


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _live_tables(conn: sqlite3.Connection) -> list[str]:
    return sorted(
        str(row["table_name"])
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
        ).fetchall()
    )


def _table_counts(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {_quote_ident(table)}").fetchone()
        counts[table] = int(row["count"]) if row else 0
    return counts


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?",
        (table,),
    ).fetchone()
    return bool(row)


def _row_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def _json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _count_like_docs(repo: Path, patterns: list[str]) -> int:
    docs = repo / "docs"
    if not docs.exists():
        return 0
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(docs.glob(pattern))
    return len(paths)


def _table_count_or_zero(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {_quote_ident(table)}").fetchone()
    return int(row["count"]) if row else 0


def _endpoint_row(conn: sqlite3.Connection, endpoint_name: str) -> sqlite3.Row:
    endpoint = conn.execute(
        "SELECT * FROM endpoints WHERE name = ? AND archived_at IS NULL",
        (endpoint_name,),
    ).fetchone()
    if not endpoint:
        raise SystemExit(f"endpoint not found: {endpoint_name}")
    return endpoint


def _endpoint_contract(conn: sqlite3.Connection, endpoint: sqlite3.Row) -> sqlite3.Row | None:
    root_node_id = endpoint["root_node_id"]
    if not root_node_id:
        return None
    return conn.execute("SELECT * FROM scope_contracts WHERE node_id = ?", (root_node_id,)).fetchone()


def _endpoint_task_rows(conn: sqlite3.Connection, endpoint: sqlite3.Row, contract: sqlite3.Row | None) -> list[sqlite3.Row]:
    target_node_ids = [str(endpoint["node_id"])]
    if endpoint["root_node_id"]:
        target_node_ids.append(str(endpoint["root_node_id"]))
    params: list[Any] = []
    clauses: list[str] = []
    if contract is not None:
        clauses.append("t.contract_id = ?")
        params.append(contract["id"])
    placeholders = ",".join("?" for _ in target_node_ids)
    clauses.append(
        "EXISTS (SELECT 1 FROM edges e WHERE e.from_node_id = t.node_id AND e.type = 'APPLIES_TO' AND e.to_node_id IN ("
        + placeholders
        + "))"
    )
    params.extend(target_node_ids)
    return conn.execute(
        f"""
        SELECT t.*, n.label, n.summary, n.created_at AS node_created_at
        FROM tasks t
        JOIN nodes n ON n.id = t.node_id
        WHERE {" OR ".join(clauses)}
        ORDER BY t.closed_at IS NOT NULL, n.created_at ASC, t.id ASC
        """,
        params,
    ).fetchall()


def _checks_for_tasks(conn: sqlite3.Connection, task_ids: list[str]) -> list[sqlite3.Row]:
    if not task_ids:
        return []
    placeholders = ",".join("?" for _ in task_ids)
    return conn.execute(
        f"""
        SELECT ac.*, n.label, n.summary, n.created_at AS node_created_at
        FROM acceptance_checks ac
        JOIN nodes n ON n.id = ac.node_id
        WHERE ac.task_id IN ({placeholders})
        ORDER BY ac.closed_at IS NOT NULL, n.created_at ASC, ac.id ASC
        """,
        task_ids,
    ).fetchall()


def _semantic_items_for_targets(conn: sqlite3.Connection, target_node_ids: list[str]) -> list[sqlite3.Row]:
    if not target_node_ids:
        return []
    placeholders = ",".join("?" for _ in target_node_ids)
    return conn.execute(
        f"""
        SELECT DISTINCT n.id, n.type, n.label, n.summary, n.created_at, n.updated_at, n.props,
               si.current_state, si.item_type
        FROM nodes n
        LEFT JOIN semantic_items si ON si.node_id = n.id
        JOIN edges e ON e.from_node_id = n.id
        WHERE e.type = 'APPLIES_TO'
          AND e.to_node_id IN ({placeholders})
          AND n.type IN ('audit_finding', 'unresolved_question', 'assumption', 'defer_decision', 'scope_change', 'work_note')
        ORDER BY n.created_at DESC, n.id DESC
        """,
        target_node_ids,
    ).fetchall()


def _evidence_for_checks(conn: sqlite3.Connection, checks: list[sqlite3.Row]) -> list[sqlite3.Row]:
    evidence_ids = list(dict.fromkeys(str(row["closed_by_node_id"]) for row in checks if row["closed_by_node_id"]))
    if not evidence_ids:
        return []
    placeholders = ",".join("?" for _ in evidence_ids)
    return conn.execute(
        f"""
        SELECT n.id, n.type, n.label, n.summary, n.created_at, n.props,
               er.id AS evidence_record_id, er.record_type, er.ref, er.sha256
        FROM nodes n
        LEFT JOIN evidence_records er ON er.evidence_node_id = n.id
        WHERE n.id IN ({placeholders})
        ORDER BY n.created_at DESC, n.id ASC
        """,
        evidence_ids,
    ).fetchall()


def work_chain_view(conn: sqlite3.Connection, endpoint_name: str) -> dict[str, Any]:
    endpoint = _endpoint_row(conn, endpoint_name)
    contract = _endpoint_contract(conn, endpoint)
    tasks = _endpoint_task_rows(conn, endpoint, contract)
    task_ids = [str(row["id"]) for row in tasks]
    checks = _checks_for_tasks(conn, task_ids)
    target_node_ids = [str(endpoint["node_id"])]
    if endpoint["root_node_id"]:
        target_node_ids.append(str(endpoint["root_node_id"]))
    target_node_ids.extend(str(row["node_id"]) for row in tasks)
    target_node_ids.extend(str(row["node_id"]) for row in checks)
    target_node_ids = list(dict.fromkeys(target_node_ids))
    semantic_items = _semantic_items_for_targets(conn, target_node_ids)
    evidence = _evidence_for_checks(conn, checks)
    open_checks = [row for row in checks if row["closed_by_node_id"] is None]
    closed_checks = [row for row in checks if row["closed_by_node_id"] is not None]
    return {
        "read_only": True,
        "db_writes": 0,
        "derived": True,
        "view": "work_chain_view",
        "endpoint": _row_to_dict(endpoint),
        "contract": _row_to_dict(contract),
        "source_tables": [
            "endpoints",
            "scope_contracts",
            "tasks",
            "acceptance_checks",
            "edges",
            "semantic_items",
            "evidence_records",
            "nodes",
        ],
        "frozen_tables_not_read_as_primary": ["work_chains"],
        "chain": {
            "id": f"derived:{endpoint_name}",
            "name": f"Derived work chain for {endpoint_name}",
            "task_count": len(tasks),
            "check_count": len(checks),
            "open_check_count": len(open_checks),
            "closed_check_count": len(closed_checks),
            "semantic_item_count": len(semantic_items),
            "evidence_count": len(evidence),
            "tasks": [_row_to_dict(row) for row in tasks],
            "checks": [_row_to_dict(row) for row in checks],
            "semantic_items": [_row_to_dict(row) for row in semantic_items],
            "evidence": [_row_to_dict(row) for row in evidence],
        },
    }


def dynamic_inherited_blockers_view(conn: sqlite3.Connection, endpoint_name: str, *, work_view: dict[str, Any] | None = None) -> dict[str, Any]:
    endpoint = _endpoint_row(conn, endpoint_name)
    work_view = work_view or work_chain_view(conn, endpoint_name)
    target_node_ids = [
        str(item["node_id"])
        for item in (work_view.get("chain") or {}).get("tasks", [])
        if item.get("node_id")
    ]
    target_node_ids.extend(
        str(item["node_id"])
        for item in (work_view.get("chain") or {}).get("checks", [])
        if item.get("node_id")
    )
    if endpoint["root_node_id"]:
        target_node_ids.append(str(endpoint["root_node_id"]))
    target_node_ids = list(dict.fromkeys(target_node_ids))
    parent_rows = conn.execute(
        """
        SELECT parent_ep.id, parent_ep.name, parent_ep.node_id
        FROM edges e
        JOIN endpoints parent_ep ON parent_ep.node_id = e.from_node_id
        WHERE e.type = 'CHAIN_CHILD'
          AND e.to_node_id = ?
          AND parent_ep.archived_at IS NULL
        ORDER BY parent_ep.created_at ASC, parent_ep.name ASC
        """,
        (endpoint["node_id"],),
    ).fetchall()
    blockers: list[dict[str, Any]] = []
    if parent_rows and target_node_ids:
        parent_node_ids = [str(row["node_id"]) for row in parent_rows]
        parent_placeholders = ",".join("?" for _ in parent_node_ids)
        target_placeholders = ",".join("?" for _ in target_node_ids)
        rows = conn.execute(
            f"""
            SELECT DISTINCT n.id, n.type, n.label, n.summary, n.created_at, n.updated_at, n.props,
                   parent_ep.name AS inherited_from_endpoint,
                   target_edge.to_node_id AS inherited_target_node_id
            FROM nodes n
            JOIN edges parent_edge ON parent_edge.from_node_id = n.id
            JOIN endpoints parent_ep ON parent_ep.node_id = parent_edge.to_node_id
            JOIN edges target_edge ON target_edge.from_node_id = n.id
            LEFT JOIN semantic_items si ON si.node_id = n.id
            WHERE n.type = 'audit_finding'
              AND n.valid_to IS NULL
              AND parent_edge.type = 'APPLIES_TO'
              AND parent_edge.to_node_id IN ({parent_placeholders})
              AND target_edge.type = 'APPLIES_TO'
              AND target_edge.to_node_id IN ({target_placeholders})
              AND COALESCE(si.current_state, 'active') NOT IN ('resolved', 'deferred', 'product_backlog', 'invalidated', 'superseded')
            ORDER BY n.created_at DESC, n.id DESC
            """,
            [*parent_node_ids, *target_node_ids],
        ).fetchall()
        blockers = [_row_to_dict(row) for row in rows]
    child_rows = conn.execute(
        """
        SELECT child_ep.id, child_ep.name, child_ep.node_id
        FROM edges e
        JOIN endpoints child_ep ON child_ep.node_id = e.to_node_id
        WHERE e.type = 'CHAIN_CHILD'
          AND e.from_node_id = ?
          AND child_ep.archived_at IS NULL
        ORDER BY child_ep.created_at ASC, child_ep.name ASC
        """,
        (endpoint["node_id"],),
    ).fetchall()
    return {
        "read_only": True,
        "db_writes": 0,
        "derived": True,
        "source_tables": ["endpoints", "edges", "semantic_items", "nodes", "tasks", "acceptance_checks"],
        "frozen_tables_not_read_as_primary": ["endpoint_inherited_blockers"],
        "endpoint": endpoint_name,
        "parent_endpoint_count": len(parent_rows),
        "child_chain_context": {
            "child_endpoint_count": len(child_rows),
            "child_endpoints": [_row_to_dict(row) for row in child_rows],
        },
        "blocker_count": len(blockers),
        "blockers": blockers,
    }


def review_adoption_policy(conn: sqlite3.Connection, endpoint_name: str) -> dict[str, Any]:
    endpoint = _endpoint_row(conn, endpoint_name)
    review_count = _table_count_or_zero(conn, "review_results")
    endpoint_review_count = 0
    if _table_exists(conn, "review_results"):
        endpoint_review_count = int(
            conn.execute("SELECT COUNT(*) AS count FROM review_results WHERE endpoint_id = ?", (endpoint["id"],)).fetchone()["count"]
        )
    return {
        "read_only": True,
        "material_only_until_controller_adoption": True,
        "review_results_default_closure_source": False,
        "physical_status": "contracted_absent_expected" if not _table_exists(conn, "review_results") else "legacy_present_pending_contraction",
        "activation_criteria": review_lane_activation_criteria(),
        "formal_review_lane_activation_criteria": list(REVIEW_LANE_ACTIVATION_CRITERIA),
        "review_results_row_count": review_count,
        "endpoint_review_results_row_count": endpoint_review_count,
        "adoption_paths": [
            "controller adoption as artifact evidence",
            "controller rejection or unresolved decision as semantic item",
            "explicit review lane only after all four activation criteria hold",
        ],
    }


def predicate_replacement_view() -> dict[str, Any]:
    return {
        "read_only": True,
        "frozen_default_write_tables": list(PREDICATE_TABLE_REPLACEMENT_CARRIERS),
        "replacement_carriers": predicate_table_replacement_carriers(),
        "non_compression_rule": "Named source-plan deliverables stay visible through task/check/lifecycle rows and cannot collapse into broad parents or artifact-only ordered plans.",
        "default_write_path": False,
    }


def delegation_reconciliation_view(conn: sqlite3.Connection, repo: Path) -> dict[str, Any]:
    delegation_tables = ["delegation_lanes", "delegation_packets", "worker_ownership_snapshots"]
    row_counts = {table: _table_count_or_zero(conn, table) for table in delegation_tables}
    controller_import_count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM nodes
            WHERE type = 'artifact'
              AND (
                lower(COALESCE(label, '')) LIKE '%worker return%'
                OR lower(COALESCE(summary, '')) LIKE '%worker return%'
                OR lower(COALESCE(props, '')) LIKE '%agent_output%'
                OR lower(COALESCE(props, '')) LIKE '%delegate%'
              )
            """
        ).fetchone()["count"]
    )
    artifact_count = _count_like_docs(repo, ["worker_packet*.md", "worker_return*.md"])
    return {
        "read_only": True,
        "artifact_primary_mode": True,
        "artifact_count": artifact_count,
        "db_row_count": sum(row_counts.values()),
        "delegation_table_row_counts": row_counts,
        "controller_import_count": controller_import_count,
        "future_productization_boundary": DELEGATION_PRODUCTIZATION_BOUNDARY,
        "frozen_tables_not_forced": delegation_tables,
    }


def contraction_gate_package(conn: sqlite3.Connection, endpoint_name: str) -> dict[str, Any]:
    live_tables = _live_tables(conn)
    table_counts = _table_counts(conn, [table for table in CONTRACTION_CANDIDATE_TABLES if table in live_tables])
    candidates = contraction_candidate_policy(table_counts)
    proof_status = {
        "standard_events": "messages/interaction_events replacement route documented; no contraction without backup and forward migration",
        "work_chains": "work_chain_view derives endpoint work from endpoints, tasks, checks, edges, semantic_items, and evidence",
        "review_results": "review_adoption_policy keeps reviewer output material-only until controller adoption/rejection",
        "endpoint_inherited_blockers": "dynamic_inherited_blockers_view derives blockers from endpoint lineage, edges, semantic_items, and child context",
        "source_promises": "replacement carriers defined in source artifacts, semantic props, and edges",
        "hard_predicates": "replacement carriers defined in single-intent acceptance checks with expected evidence",
        "forbidden_substitutes": "replacement carriers defined in check bodies, warnings, audit findings, and non-compression rules",
        "task_predicate_links": "replacement carriers defined in edges or check/source relationships",
        "evidence_predicate_coverage": "replacement carriers defined in evidence records, closed_by_node_id, and optional props",
    }
    for candidate in candidates:
        candidate["replacement_path_proof_status"] = proof_status[candidate["table"]]
        candidate["physical_action"] = "contracted_absent_expected" if candidate["table"] not in live_tables else "legacy_present_pending_migration_apply"
        candidate["live_table_present"] = candidate["table"] in live_tables
    physical_contraction_allowed = all(candidate["physical_contraction_allowed"] for candidate in candidates)
    return {
        "read_only": True,
        "endpoint": endpoint_name,
        "candidate_count": len(candidates),
        "required_candidate_tables": list(CONTRACTION_CANDIDATE_TABLES),
        "contracted_tables_expected_absent": sorted(CONTRACTED_SCHEMA_TABLES),
        "contracted_tables_present": sorted(set(live_tables) & CONTRACTED_SCHEMA_TABLES),
        "contraction_migration": CONTRACTION_MIGRATION_FILENAME,
        "candidates": candidates,
        "user_confirmation_required": False,
        "physical_contraction_allowed": physical_contraction_allowed,
        "destructive_migration_created": True,
        "migration_apply_performed": False,
        "drop_archive_shrink_performed": False,
        "strict_user_confirmation_policy": "Physical contraction was user-approved for the nine contracted tables; controller applies the forward-only migration after review.",
    }


def _information_schema_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = _live_tables(conn)
    columns = [
        dict(row)
        for row in conn.execute(
            """
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()
    ]
    foreign_keys = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              tc.table_name,
              kcu.column_name,
              ccu.table_name AS references_table,
              ccu.column_name AS references_column,
              tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = current_schema()
            ORDER BY tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
            """
        ).fetchall()
    ]
    return {"table_count": len(tables), "tables": tables, "columns": columns, "foreign_keys": foreign_keys}


def _roles_payload(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    table_counts = _table_counts(conn, _live_tables(conn)) if conn is not None else None
    payload = table_role_summary(table_counts)
    if conn is not None:
        live_tables = _live_tables(conn)
        payload["verification"] = verify_schema_roles(live_tables=live_tables)
        payload["live_table_count"] = len(live_tables)
    return payload


def cmd_schema_roles(args: argparse.Namespace) -> int:
    print_json_fn = _require_dependency("print_json")
    conn = None
    if args.live:
        conn = _require_dependency("connect_read_only")(args.repo.resolve())
    payload = _roles_payload(conn)
    if not args.advanced:
        verification = payload["verification"]
        visibility_policy = dict(payload["visibility_policy"])
        visibility_policy.pop("advanced_schema_visibility", None)
        payload = {
            "schema_freeze_policy": payload["schema_freeze_policy"],
            "new_table_admission_gates": payload["new_table_admission_gates"],
            "review_lane_activation_criteria": payload["review_lane_activation_criteria"],
            "counts_by_role": payload["counts_by_role"],
            "visibility_policy": visibility_policy,
            "verification": verification,
            "roles": [row for row in schema_role_rows() if row["default_visible"]],
            "advanced_material_omitted": True,
            "advanced_flag": "--advanced",
            "default_surface_note": "Default schema roles output shows current governance objects; dormant and contracted legacy material requires --advanced.",
        }
    print_json_fn({"ok": True, "schema_roles_db_table": False, **payload})
    return 0


def cmd_schema_verify(args: argparse.Namespace) -> int:
    print_json_fn = _require_dependency("print_json")
    conn = _require_dependency("connect_read_only")(args.repo.resolve()) if args.live else None
    live_tables = _live_tables(conn) if conn is not None else None
    verification = verify_schema_roles(live_tables=live_tables)
    visibility_policy = schema_visibility_policy(_table_counts(conn, live_tables) if conn is not None and live_tables else None)
    visibility_policy.pop("advanced_schema_visibility", None)
    payload = {
        "ok": verification["ok"],
        "read_only": True,
        "schema_roles_db_table": False,
        "physical_schema_table_count": verification["physical_schema_table_count"],
        "current_physical_schema_tables": verification["current_physical_schema_tables"],
        "role_registry_count": verification["role_registry_count"],
        "contracted_legacy_role_count": verification["contracted_legacy_role_count"],
        "contracted_legacy_tables_absent": verification["contracted_legacy_tables_absent"],
        "verification": verification,
        "visibility_policy": visibility_policy,
        "advanced_material_omitted": True,
        "advanced_roles_command": "python -m shujuan schema roles --advanced",
    }
    print_json_fn(payload)
    return 0 if verification["ok"] else 1


def cmd_schema_guard(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    print_json_fn = _require_dependency("print_json")
    conn = _require_dependency("connect_read_only")(repo) if args.live else None
    live_tables = _live_tables(conn) if conn is not None else None
    verification = verify_schema_roles(live_tables=live_tables)
    migrate_status = None
    migration_drift_present = False
    if conn is not None:
        migrate_status = _require_dependency("migration_status")(repo, conn)
        migration_drift_present = bool((migrate_status.get("migration_drift") or {}).get("present"))
    schema_integrity_ok = bool(verification["ok"] and not migration_drift_present)
    payload = {
        "ok": schema_integrity_ok,
        "read_only": True,
        "schema_guard_passed": schema_integrity_ok,
        "schema_integrity_ok": schema_integrity_ok,
        "business_table_addition_allowed": False,
        "ordinary_schema_change_allowed": False,
        "allowed_next_actions": [
            "ledger repair if drift",
            "forward-only repair migration if live schema differs",
        ],
        "schema_freeze_policy": SCHEMA_FREEZE_POLICY,
        "verification": verification,
        "migration_drift_present": migration_drift_present,
        "migration_status_kind": (migrate_status or {}).get("status_kind"),
        "blockers": [],
    }
    if migration_drift_present:
        payload["blockers"].append("migration_drift_present")
    if not verification["ok"]:
        payload["blockers"].append("schema_roles_verification_failed")
    print_json_fn(payload)
    return 0 if schema_integrity_ok else 1


def _source_truth_branch(status: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    drift = status.get("migration_drift") or {}
    runtime_ok = bool((status.get("runtime_schema") or {}).get("ok"))
    if drift.get("present") and runtime_ok and verification.get("ok"):
        branch = "ledger_repair_only"
        action = "Run migrate repair-ledger with an explicit reason from controller authority."
    elif status.get("schema_state") == "current" and not verification.get("ok"):
        branch = "unresolved_needs_user_decision"
        action = "Stop schema operation; live tables and schema_roles/code expectation disagree."
    elif status.get("schema_state") != "current":
        branch = "forward_only_repair_migration"
        action = "Create a forward-only repair migration to align live schema with code expectation."
    else:
        branch = "no_repair_needed"
        action = "No checksum drift or live/schema role disagreement was observed."
    return {
        "branch": branch,
        "action": action,
        "physical_drop_archive_shrink_allowed": False,
    }


def cmd_schema_drift_package(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    print_json_fn = _require_dependency("print_json")
    relpath_fn = _require_dependency("relpath")
    conn = _require_dependency("open_db_raw")(repo, allow_filesystem_writes=False)
    if conn is None:
        raise SystemExit("No shujuan PostgreSQL database is available for drift fact package generation.")
    conn.execute("SET TRANSACTION READ ONLY")
    status = _require_dependency("migration_status")(repo, conn)
    live_snapshot = _information_schema_snapshot(conn)
    live_tables = live_snapshot["tables"]
    verification = verify_schema_roles(live_tables=live_tables)
    applied = _require_dependency("applied_migrations")(conn)
    files = _require_dependency("migration_files")(repo)
    sha256_text_fn = _require_dependency("sha256_text")
    file_checksums = {
        path.name: sha256_text_fn(path.read_text(encoding="utf-8"))
        for path in files
    }
    package = {
        "schema": "shujuan.schema_stewardship.drift_fact_package.v1",
        "read_only": True,
        "governance_db_rows_written": False,
        "endpoint": "shujuan-v7-schema-stewardship-2026-05-25",
        "migrate_status": status,
        "applied_migrations_ledger": {
            filename: {
                "id": row["id"],
                "filename": row["filename"],
                "checksum": row["checksum"],
                "applied_at": row["applied_at"],
            }
            for filename, row in applied.items()
        },
        "current_003_checksum": file_checksums.get("003_v5_runtime_schema_ownership.sql"),
        "live_information_schema_snapshot": live_snapshot,
        "schema_py_expected_snapshot": expected_schema_snapshot(),
        "schema_roles_verification": verification,
        "repair_policy": {
            "allowed_change_set_scope": [
                "migration ledger repair tooling",
                "forward-only repair migration when code expectation and live schema differ",
            ],
            "forbidden_physical_actions": ["drop", "archive", "shrink"],
        },
        "source_of_truth": _source_truth_branch(status, verification),
    }
    out_path = Path(args.path)
    if not out_path.is_absolute():
        out_path = repo / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print_json_fn(
        {
            "ok": True,
            "path": relpath_fn(out_path, repo),
            "read_only": True,
            "governance_db_rows_written": False,
            "source_of_truth": package["source_of_truth"],
            "migration_drift_present": bool((status.get("migration_drift") or {}).get("present")),
        }
    )
    return 0


def cmd_schema_p1_p2_package(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    print_json_fn = _require_dependency("print_json")
    relpath_fn = _require_dependency("relpath")
    conn = _require_dependency("open_db_raw")(repo, allow_filesystem_writes=False)
    if conn is None:
        raise SystemExit("No shujuan PostgreSQL database is available for P1/P2 schema stewardship package generation.")
    conn.execute("SET TRANSACTION READ ONLY")
    endpoint_name = args.endpoint
    live_tables = _live_tables(conn)
    work_view = work_chain_view(conn, endpoint_name)
    inherited_view = dynamic_inherited_blockers_view(conn, endpoint_name, work_view=work_view)
    package = {
        "schema": "shujuan.schema_stewardship.p1_p2_gate_package.v1",
        "read_only": True,
        "governance_db_rows_written": False,
        "current_project_governance_write": False,
        "endpoint": endpoint_name,
        "schema_freeze_policy": SCHEMA_FREEZE_POLICY,
        "schema_roles_verification": verify_schema_roles(live_tables=live_tables),
        "p1": {
            "work_chain_view": work_view,
            "dynamic_inherited_blockers": inherited_view,
            "review_adoption_policy": review_adoption_policy(conn, endpoint_name),
            "predicate_table_replacement": predicate_replacement_view(),
            "delegation_reconciliation": delegation_reconciliation_view(conn, repo),
        },
        "p2_non_destructive_gate": contraction_gate_package(conn, endpoint_name),
        "attestations": {
            "no_governance_db_rows_written": True,
            "no_migration_apply_performed_by_package_command": True,
            "no_drop_archive_shrink": True,
            "physical_contraction_scope_limited_to_nine_tables": True,
            "user_confirmation_already_recorded_in_worker_packet": True,
        },
    }
    out_path = Path(args.path)
    if not out_path.is_absolute():
        out_path = repo / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print_json_fn(
        {
            "ok": True,
            "path": relpath_fn(out_path, repo),
            "read_only": True,
            "governance_db_rows_written": False,
            "candidate_count": package["p2_non_destructive_gate"]["candidate_count"],
            "physical_contraction_allowed": package["p2_non_destructive_gate"]["physical_contraction_allowed"],
            "user_confirmation_required": False,
            "contraction_migration": CONTRACTION_MIGRATION_FILENAME,
        }
    )
    return 0


def register_schema_stewardship(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, SchemaHandler],
) -> None:
    missing = [key for key in SCHEMA_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"schema stewardship command boundary is missing: {', '.join(missing)}")
    parser = subparsers.add_parser("schema", help="Schema stewardship diagnostics and table role policy.")
    sub = parser.add_subparsers(dest="schema_command", required=True)

    roles = sub.add_parser("roles")
    roles.add_argument("--live", action="store_true", help="Compare roles with the live PostgreSQL table list.")
    roles.add_argument("--advanced", action="store_true", help="Include dormant and merge-candidate table rows.")
    roles.set_defaults(func=handlers["roles"])

    verify = sub.add_parser("verify")
    verify.add_argument("--live", action="store_true", help="Verify against the live PostgreSQL table list.")
    verify.set_defaults(func=handlers["verify"])

    guard = sub.add_parser("guard")
    guard.add_argument("--live", action="store_true", help="Require live roles verification and no migration drift.")
    guard.set_defaults(func=handlers["guard"])

    drift = sub.add_parser("drift-package")
    drift.add_argument("--path", required=True)
    drift.set_defaults(func=handlers["drift_package"])

    p1_p2 = sub.add_parser("p1-p2-package")
    p1_p2.add_argument("--endpoint", required=True)
    p1_p2.add_argument("--path", required=True)
    p1_p2.set_defaults(func=handlers["p1_p2_package"])


__all__ = [
    "build_schema_stewardship_handlers",
    "register_schema_stewardship",
    "work_chain_view",
    "dynamic_inherited_blockers_view",
]
