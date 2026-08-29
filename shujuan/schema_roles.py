from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .schema import SCHEMA_SQL


ADMISSION_GATES = [
    "independent_factuality",
    "independent_lifecycle",
    "stable_write_path",
    "stable_read_path",
    "insufficient_existing_alternatives",
    "no_added_default_cognitive_burden",
    "trusted_migrations_no_drift",
    "tests_for_write_read_empty_table_migration_default_hidden",
]

REVIEW_LANE_ACTIVATION_CRITERIA = [
    "independent_lifecycle",
    "independent_query_or_workbench_need",
    "strong_check_and_evidence_constraints",
    "structured_conclusions_beyond_artifact_text",
]

CONTRACTION_CANDIDATE_TABLES = (
    "standard_events",
    "work_chains",
    "review_results",
    "endpoint_inherited_blockers",
    "source_promises",
    "hard_predicates",
    "forbidden_substitutes",
    "task_predicate_links",
    "evidence_predicate_coverage",
)
CONTRACTED_SCHEMA_TABLES = set(CONTRACTION_CANDIDATE_TABLES)

CONTRACTION_MIGRATION_FILENAME = "004_p2_physical_schema_contraction.sql"

PREDICATE_TABLE_REPLACEMENT_CARRIERS = {
    "source_promises": [
        "source-plan artifacts",
        "semantic_items.props",
        "edges",
    ],
    "hard_predicates": [
        "single-intent acceptance_checks",
        "acceptance_checks.expected_evidence_type",
    ],
    "forbidden_substitutes": [
        "acceptance_checks.check_body",
        "semantic warnings",
        "audit findings",
        "non-compression rules",
    ],
    "task_predicate_links": [
        "edges",
        "check/source relationships",
    ],
    "evidence_predicate_coverage": [
        "evidence_records",
        "acceptance_checks.closed_by_node_id",
        "optional evidence/check props",
    ],
}

DELEGATION_PRODUCTIZATION_BOUNDARY = (
    "Delegation stays artifact-primary until a future scoped productization proves daily "
    "write/read paths for delegation_lanes, delegation_packets, and worker_ownership_snapshots."
)


SCHEMA_FREEZE_POLICY = {
    "policy": "schema_freeze_until_drift_and_roles_resolved",
    "business_table_additions_allowed": False,
    "dormant_or_merge_candidate_default_activation_allowed": False,
    "schema_roles_db_table_allowed": False,
    "allowed_exceptions": [
        "migration ledger repair tooling",
        "forward-only repair migration when live schema does not match code expectation",
    ],
    "forbidden_repair_side_effects": ["drop", "archive", "shrink"],
}


@dataclass(frozen=True)
class TableRole:
    table: str
    role: str
    source_of_truth: bool
    default_visible: bool
    normal_write_path: bool
    activation_rule: str
    replacement_path: str


def _core(table: str, reason: str) -> TableRole:
    return TableRole(
        table=table,
        role="core_fact",
        source_of_truth=True,
        default_visible=True,
        normal_write_path=True,
        activation_rule="current lightweight repo-local governance core",
        replacement_path=reason,
    )


def _support(table: str, reason: str) -> TableRole:
    return TableRole(
        table=table,
        role="support_fact",
        source_of_truth=False,
        default_visible=False,
        normal_write_path=True,
        activation_rule="supporting material; visible through provider/source/evidence surfaces, not default closeout truth",
        replacement_path=reason,
    )


def _capture_support(table: str, reason: str) -> TableRole:
    return TableRole(
        table=table,
        role="capture_support_fact",
        source_of_truth=False,
        default_visible=False,
        normal_write_path=True,
        activation_rule="capture/discussion support fact; available through capture and discussion routes, not default closeout truth",
        replacement_path=reason,
    )


def _projection(table: str, reason: str) -> TableRole:
    return TableRole(
        table=table,
        role="projection_cache",
        source_of_truth=False,
        default_visible=False,
        normal_write_path=True,
        activation_rule="projection cache only; regenerate from source facts",
        replacement_path=reason,
    )


def _dormant(table: str, reason: str) -> TableRole:
    return TableRole(
        table=table,
        role="dormant_extension",
        source_of_truth=False,
        default_visible=False,
        normal_write_path=False,
        activation_rule="activate only after the concept has daily write/read paths and passes all new-table admission gates",
        replacement_path=reason,
    )


def _merge(table: str, reason: str) -> TableRole:
    return TableRole(
        table=table,
        role="merge_candidate",
        source_of_truth=False,
        default_visible=False,
        normal_write_path=False,
        activation_rule="frozen/default-hidden until a future scoped migration or explicit user-approved promotion",
        replacement_path=reason,
    )


def _contracted(table: str, reason: str) -> TableRole:
    return TableRole(
        table=table,
        role="contracted_table",
        source_of_truth=False,
        default_visible=False,
        normal_write_path=False,
        activation_rule=(
            "physically contracted by forward-only migration "
            f"{CONTRACTION_MIGRATION_FILENAME}; use the replacement path"
        ),
        replacement_path=reason,
    )


SCHEMA_ROLES: tuple[TableRole, ...] = (
    _core("project_meta", "project identity and schema version entry point"),
    _core("applied_migrations", "tracked migration ledger"),
    _core("nodes", "canonical object identity layer"),
    _core("edges", "governance relationship graph"),
    _core("terms", "scoped terminology anchors"),
    _core("center_bodies", "center memory bodies"),
    _core("endpoints", "direction-level recoverable breakpoints"),
    _core("endpoint_bodies", "endpoint projection history"),
    _core("conversation_sessions", "session provenance"),
    _core("messages", "raw conversation message facts"),
    _core("source_documents", "source document facts"),
    _core("document_sections", "source section slices"),
    _core("activation_logs", "activation/recovery provenance"),
    _core("agent_runs", "execution run records"),
    _core("run_snapshots", "before/after execution snapshots"),
    _core("change_sets", "change_set evidence carrier"),
    _core("diff_files", "file-level diff evidence"),
    _core("diff_hunks", "hunk-level diff evidence"),
    _core("code_objects", "code object index"),
    _core("change_code_links", "change/code impact bridge"),
    _core("scope_contracts", "source-backed scope boundary"),
    _core("tasks", "execution obligations"),
    _core("acceptance_checks", "single-intent acceptance predicates"),
    _core("evidence_records", "evidence-backed closure records"),
    _core("semantic_items", "audit/unresolved/assumption/defer/work-note carrier"),
    _core("semantic_lifecycle_events", "semantic item lifecycle history"),
    _projection("projection_snapshots", "read-only workbench/report cache; not primary truth"),
    _support("provider_runs", "provider run boundary; material only"),
    _support("provider_artifacts", "provider output artifact material; controller import required"),
    _support("provider_entity_map", "external-to-local mapping material"),
    _support("provider_facts", "provider_fact/provider_hypothesis material; not closure evidence"),
    _capture_support("interaction_events", "messages plus interaction provenance route"),
    _capture_support("discussion_segments", "reviewable discussion material"),
    _capture_support("discussion_messages", "message units inside discussion segments"),
    _capture_support("discussion_lifecycle_events", "discussion capture lifecycle material"),
    _dormant("delegation_lanes", "current delegation is artifact-primary"),
    _dormant("delegation_packets", "delegate packet artifact is primary until controller productizes lane indexing"),
    _dormant("worker_ownership_snapshots", "ownership manifests are material-only return fields"),
    _contracted("standard_events", "messages and interaction_events carry captured interaction provenance"),
    _contracted("source_promises", "source-plan artifact, semantic_items.props, and edges"),
    _contracted("hard_predicates", "acceptance_checks with expected evidence"),
    _contracted("forbidden_substitutes", "check body, semantic warning, audit finding, and non-compression rule"),
    _contracted("task_predicate_links", "edges or check/source relationships"),
    _contracted("evidence_predicate_coverage", "evidence_records plus acceptance_checks.closed_by_node_id and props"),
    _contracted("work_chains", "derive from endpoints, tasks, checks, semantic_items, and edges"),
    _contracted("review_results", "reviewer material enters semantic/evidence/artifact only by controller adoption"),
    _contracted("endpoint_inherited_blockers", "derive dynamically from semantic_items and edges"),
)


def schema_role_rows() -> list[dict[str, Any]]:
    return [asdict(role) for role in SCHEMA_ROLES]


def schema_role_by_table() -> dict[str, dict[str, Any]]:
    return {row["table"]: row for row in schema_role_rows()}


def expected_schema_snapshot() -> dict[str, Any]:
    tables: dict[str, dict[str, Any]] = {}
    pattern = re.compile(r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\);", re.S)
    for match in pattern.finditer(SCHEMA_SQL):
        table = match.group(1)
        body = match.group(2)
        columns: list[str] = []
        foreign_keys: list[dict[str, str]] = []
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            if not line:
                continue
            upper = line.upper()
            if upper.startswith("FOREIGN KEY"):
                fk = re.match(r"FOREIGN KEY\s+\(([^)]+)\)\s+REFERENCES\s+([a-zA-Z_][a-zA-Z0-9_]*)\(([^)]+)\)", line, re.I)
                if fk:
                    foreign_keys.append(
                        {
                            "columns": fk.group(1).strip(),
                            "references_table": fk.group(2).strip(),
                            "references_columns": fk.group(3).strip(),
                        }
                    )
                continue
            if upper.startswith(("PRIMARY KEY", "UNIQUE", "CHECK")):
                continue
            columns.append(line.split()[0])
        tables[table] = {"columns": sorted(columns), "foreign_keys": foreign_keys}
    return {"table_count": len(tables), "tables": tables}


def verify_schema_roles(*, live_tables: list[str] | None = None) -> dict[str, Any]:
    role_rows = schema_role_by_table()
    role_tables = set(role_rows)
    active_role_tables = {table for table, row in role_rows.items() if row["role"] != "contracted_table"}
    contracted_role_tables = {table for table, row in role_rows.items() if row["role"] == "contracted_table"}
    expected_tables = set(expected_schema_snapshot()["tables"])
    live_set = set(live_tables or [])
    target_tables = live_set if live_tables is not None else expected_tables
    missing_from_roles = sorted(target_tables - role_tables)
    extra_roles = sorted(active_role_tables - target_tables)
    contracted_tables_present = sorted(contracted_role_tables & target_tables)
    contracted_tables_absent = sorted(contracted_role_tables - target_tables)
    default_visible_advanced = sorted(
        row["table"]
        for row in schema_role_rows()
        if row["role"] in {"dormant_extension", "merge_candidate", "contracted_table"} and row["default_visible"]
    )
    schema_roles_table_present = "schema_roles" in target_tables or "schema_roles" in role_tables
    schema_py_mismatches = {
        "missing_in_roles_against_schema_py": sorted(expected_tables - active_role_tables),
        "extra_roles_against_schema_py": sorted(active_role_tables - expected_tables),
    }
    ok = not (
        missing_from_roles
        or extra_roles
        or (live_tables is not None and contracted_tables_present)
        or default_visible_advanced
        or schema_roles_table_present
        or schema_py_mismatches["missing_in_roles_against_schema_py"]
        or schema_py_mismatches["extra_roles_against_schema_py"]
    )
    return {
        "ok": ok,
        "physical_schema_table_count": len(expected_tables),
        "current_physical_schema_tables": len(expected_tables),
        "role_registry_count": len(role_tables),
        "contracted_legacy_role_count": len(contracted_role_tables),
        "contracted_legacy_tables_absent": len(contracted_tables_absent),
        "role_count": len(role_tables),
        "expected_schema_table_count": len(expected_tables),
        "live_table_count": len(live_set) if live_tables is not None else None,
        "missing_from_roles": missing_from_roles,
        "extra_roles": extra_roles,
        "contracted_tables_present": contracted_tables_present,
        "contracted_tables_absent": contracted_tables_absent,
        "contracted_tables_expected_absent": sorted(contracted_role_tables),
        "contraction_migration": CONTRACTION_MIGRATION_FILENAME,
        "default_visible_advanced": default_visible_advanced,
        "schema_roles_table_present": schema_roles_table_present,
        **schema_py_mismatches,
    }


def advanced_schema_visibility(table_counts: dict[str, int] | None = None) -> list[dict[str, Any]]:
    counts = table_counts or {}
    items = []
    for row in schema_role_rows():
        if row["role"] not in {"dormant_extension", "merge_candidate", "contracted_table"}:
            continue
        items.append(
            {
                "table": row["table"],
                "role": row["role"],
                "physical_status": "contracted_absent_expected" if row["role"] == "contracted_table" else "present_or_dormant",
                "has_rows": bool(counts.get(row["table"], 0)),
                "has_default_write_path": bool(row["normal_write_path"]),
                "has_current_product_surface": bool(row["default_visible"]),
                "replacement_path": row["replacement_path"],
            }
        )
    return items


def schema_visibility_policy(table_counts: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "default_surface": "governance_objects",
        "default_visible_objects": [
            "endpoints",
            "tasks",
            "acceptance_checks",
            "semantic_items",
            "evidence_records",
            "source_documents",
            "change_sets",
        ],
        "default_hidden_roles": ["dormant_extension", "merge_candidate", "contracted_table"],
        "support_hidden_roles": ["support_fact", "capture_support_fact", "projection_cache"],
        "advanced_schema_visibility": advanced_schema_visibility(table_counts),
        "non_goal_visibility_rule": "non_goal and out_of_scope decisions must remain visibly distinct from deferred and product_backlog records.",
    }


def review_lane_activation_criteria() -> list[str]:
    return list(REVIEW_LANE_ACTIVATION_CRITERIA)


def predicate_table_replacement_carriers() -> dict[str, list[str]]:
    return {table: list(carriers) for table, carriers in PREDICATE_TABLE_REPLACEMENT_CARRIERS.items()}


def contraction_candidate_policy(table_counts: dict[str, int] | None = None) -> list[dict[str, Any]]:
    counts = table_counts or {}
    roles = schema_role_by_table()
    rows = []
    for table in CONTRACTION_CANDIDATE_TABLES:
        role = roles[table]
        row_count = int(counts.get(table, 0))
        blockers: list[str] = []
        if row_count:
            blockers.append("table_has_rows")
        if not role["replacement_path"]:
            blockers.append("missing_replacement_path")
        rows.append(
            {
                "table": table,
                "role": role["role"],
                "row_count": row_count,
                "default_write_dependency_status": (
                    "default_write_path_present"
                    if role["normal_write_path"]
                    else "contracted_no_default_write_path"
                ),
                "default_read_dependency_status": (
                    "default_visible"
                    if role["default_visible"]
                    else "contracted_not_default_read_source"
                ),
                "physical_status": "contracted_absent_expected",
                "replacement_path": role["replacement_path"],
                "backup_forward_migration_requirement": f"satisfied_by_{CONTRACTION_MIGRATION_FILENAME}",
                "preflight_status": "blocked" if blockers else "passed",
                "blockers": blockers,
                "user_confirmation_required": False,
                "physical_contraction_allowed": not blockers,
                "contraction_migration": CONTRACTION_MIGRATION_FILENAME,
            }
        )
    return rows


def table_role_summary(table_counts: dict[str, int] | None = None) -> dict[str, Any]:
    rows = schema_role_rows()
    counts_by_role: dict[str, int] = {}
    for row in rows:
        counts_by_role[row["role"]] = counts_by_role.get(row["role"], 0) + 1
    return {
        "schema_freeze_policy": SCHEMA_FREEZE_POLICY,
        "new_table_admission_gates": ADMISSION_GATES,
        "review_lane_activation_criteria": review_lane_activation_criteria(),
        "roles": rows,
        "counts_by_role": counts_by_role,
        "visibility_policy": schema_visibility_policy(table_counts),
        "contraction_candidates": contraction_candidate_policy(table_counts),
        "verification": verify_schema_roles(),
    }
