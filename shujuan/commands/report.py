from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..schema_roles import schema_visibility_policy


ReportHandler = Callable[[argparse.Namespace], int]
REPORT_HANDLER_KEYS = ("project", "endpoint", "lifecycle", "v6_phase0")
REPORT_DEPENDENCY_KEYS = (
    "connect",
    "connect_read_only",
    "open_db_raw",
    "assert_runtime_schema_ready",
    "inspect_schema",
    "endpoint_status_payload",
    "endpoint_active_obligations",
    "endpoint_active_obligation_count",
    "endpoint_readiness_diagnostic",
    "maybe_runtime_preflight",
    "resolve_endpoint_identifier",
    "diagnostics_payload",
    "require_node",
    "display_lifecycle_event",
    "display_semantic_row",
    "canonical_semantic_state",
    "print_text",
    "print_json",
    "active_node_clause",
    "PRODUCT_BACKLOG_STATE",
    "SEMANTIC_STATE_DISPLAY_ORDER",
)


def row_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def props_dict(row_or_text: sqlite3.Row | dict[str, Any] | str | None) -> dict[str, Any]:
    if row_or_text is None:
        return {}
    if isinstance(row_or_text, str):
        raw = row_or_text
    elif isinstance(row_or_text, dict):
        raw = row_or_text.get("props")
    else:
        raw = row_or_text["props"] if "props" in row_or_text.keys() else None
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def active_node_clause(alias: str) -> str:
    return "1 = 1"


connect: Callable[[Any], sqlite3.Connection] | None = None
connect_read_only: Callable[[Any], sqlite3.Connection] | None = None
inspect_schema: Callable[[sqlite3.Connection], dict[str, Any]] | None = None
endpoint_status_payload: Callable[..., dict[str, Any]] | None = None
endpoint_active_obligations: Callable[[dict[str, Any]], dict[str, list[dict[str, Any]]]] | None = None
endpoint_active_obligation_count: Callable[[dict[str, list[dict[str, Any]]]], int] | None = None
endpoint_readiness_diagnostic: Callable[..., dict[str, Any]] | None = None
maybe_runtime_preflight: Callable[[argparse.Namespace, str], dict[str, Any] | None] | None = None
resolve_endpoint_identifier: Callable[[sqlite3.Connection, Any, str], str] | None = None
diagnostics_payload: Callable[..., dict[str, Any]] | None = None
require_node: Callable[[sqlite3.Connection, str, str], sqlite3.Row] | None = None
display_lifecycle_event: Callable[[sqlite3.Row | dict[str, Any] | None], dict[str, Any] | None] | None = None
display_semantic_row: Callable[[sqlite3.Row | dict[str, Any] | None], dict[str, Any] | None] | None = None
canonical_semantic_state: Callable[[str | None], str] | None = None
print_text: Callable[..., None] | None = None
print_json: Callable[[Any], None] | None = None
PRODUCT_BACKLOG_STATE = "product_backlog"
SEMANTIC_STATE_DISPLAY_ORDER = ("active", "resolved", "deferred", PRODUCT_BACKLOG_STATE, "invalidated", "superseded")

V6_PHASE0_ENDPOINT = "shujuan-v6-activation-consolidation-2026-05-21"
V6_PHASE0_SOURCE_PATH = r"E:\AA-HomeforAI\AA-obisdian\Lucian_system\shujuan_V6_current_stage_activation_consolidation_plan.md"
V6_PHASE0_IDS = {
    "document": "doc_31db71b65cd4436a",
    "document_node": "node_25ba2151f0eb4f13",
    "scope_contract": "contract_ccd2b57099684d8d",
    "scope_node": "node_6147bee32e0f4123",
    "endpoint_node": "node_e3fe6ee879134d4b",
    "old_v1_v5_endpoint_node": "node_bd776e783f184651",
    "old_v5_dccp_endpoint_node": "node_84317412d1b448dc",
    "p1_defer_note_node": "node_2f9f4f905cdc4d9d",
    "p2_product_backlog_node": "node_aca7d604f76e4340",
}
V6_PHASE0_RELATIONSHIP_NOTE_IDS = [
    "node_5cba2dd2600943bb",
    "node_6a704d1863884b37",
    "node_512ab8d107df4257",
    "node_2f9f4f905cdc4d9d",
]
V6_PHASE0_REQUIRED_TERMS = [
    "Activation Surface",
    "Center Capsule",
    "Endpoint Capsule",
    "Role Capsule",
    "Mode Capsule",
    "Proof Capsule",
    "Return Capsule",
    "current_project_governance_write",
    "isolated_fixture_governance_write",
]


def _validate_handlers(handlers: Mapping[str, ReportHandler]) -> None:
    missing = [key for key in REPORT_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"report command boundary is missing: {', '.join(missing)}")


def _report_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in REPORT_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"report handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in REPORT_DEPENDENCY_KEYS}


def _require_dependency(name: str) -> Any:
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"report command dependency is not configured: {name}")
    return value


def build_report_handlers(deps: Mapping[str, Any]) -> dict[str, ReportHandler]:
    """Build report handlers from cli.py-owned shared helpers without importing cli.py."""
    boundary = _report_dependencies(deps)
    globals().update(boundary)
    if "row_to_dict" in deps:
        globals()["row_to_dict"] = deps["row_to_dict"]
    if "props_dict" in deps:
        globals()["props_dict"] = deps["props_dict"]
    return {
        "project": cmd_report_project,
        "endpoint": cmd_report_endpoint,
        "lifecycle": cmd_report_lifecycle,
        "v6_phase0": cmd_report_v6_phase0,
    }


def project_non_active_task_ids(conn: sqlite3.Connection, tasks: list[sqlite3.Row]) -> set[str]:
    non_active_task_ids: set[str] = set()
    for task in tasks:
        if conn.execute(
            f"""
            SELECT 1
            FROM edges e
            JOIN nodes n ON n.id = e.to_node_id
            WHERE e.from_node_id = ?
              AND e.type = 'DEFERRED_BY'
              AND {active_node_clause("n")}
            LIMIT 1
            """,
            (task["node_id"],),
        ).fetchone():
            non_active_task_ids.add(str(task["id"]))
            continue
        if conn.execute(
            """
            SELECT 1
            FROM semantic_items
            WHERE node_id = ?
              AND current_state IN ('deferred', 'backlog', 'product_backlog')
            LIMIT 1
            """,
            (task["node_id"],),
        ).fetchone():
            non_active_task_ids.add(str(task["id"]))
    return non_active_task_ids


def project_report_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    inspect_schema_fn = _require_dependency("inspect_schema")
    endpoint_status_payload_fn = _require_dependency("endpoint_status_payload")
    center = conn.execute("SELECT * FROM center_bodies WHERE is_current = 1 ORDER BY version DESC LIMIT 1").fetchone()
    endpoints = conn.execute(
        """
        SELECT e.*, b.created_at AS body_created_at, bn.props AS body_props
        FROM endpoints e
        LEFT JOIN endpoint_bodies b ON b.id = e.current_body_id
        LEFT JOIN nodes bn ON bn.id = b.node_id
        WHERE e.archived_at IS NULL
        ORDER BY e.created_at
        """
    ).fetchall()
    terms = conn.execute("SELECT canonical_term, definition, node_id FROM terms WHERE valid_to IS NULL ORDER BY lower(canonical_term)").fetchall()
    tasks = conn.execute(
        """
        SELECT t.*, n.label
        FROM tasks t
        JOIN nodes n ON n.id = t.node_id
        ORDER BY t.closed_at IS NOT NULL, n.created_at ASC, t.id ASC
        LIMIT 200
        """
    ).fetchall()
    checks = conn.execute(
        """
        SELECT ac.*, n.label, en.type AS closed_by_type
        FROM acceptance_checks ac
        JOIN nodes n ON n.id = ac.node_id
        LEFT JOIN nodes en ON en.id = ac.closed_by_node_id
        ORDER BY ac.closed_at IS NOT NULL, n.created_at ASC, ac.id ASC
        LIMIT 300
        """
    ).fetchall()
    non_active_task_ids = project_non_active_task_ids(conn, tasks)
    current_tasks = [row_to_dict(row) for row in tasks if row["closed_by_node_id"] is None and row["id"] not in non_active_task_ids]
    deferred_tasks = [row_to_dict(row) for row in tasks if row["closed_by_node_id"] is None and row["id"] in non_active_task_ids]
    open_checks = [row_to_dict(row) for row in checks if row["closed_by_node_id"] is None and row["task_id"] not in non_active_task_ids]
    deferred_checks = [row_to_dict(row) for row in checks if row["closed_by_node_id"] is None and row["task_id"] in non_active_task_ids]
    evidence = conn.execute(
        "SELECT id, type, label, summary, created_at FROM nodes WHERE type IN ('change_set','test_result','artifact','user_confirmation') ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    risks = conn.execute(
        f"""
        SELECT id, type, label, summary, created_at
        FROM nodes
        WHERE type IN ('audit_finding','unresolved_question','assumption','defer_decision','scope_change','work_note')
          AND {active_node_clause("nodes")}
        ORDER BY created_at DESC
        LIMIT 80
        """
    ).fetchall()
    endpoint_items = []
    for row in endpoints:
        item = row_to_dict(row)
        status = endpoint_status_payload_fn(conn, str(row["name"]), include_chain=True)
        projection = status.get("projection") or {}
        stored_hash = projection.get("stored_projection_hash")
        current_hash = projection.get("projection_hash")
        item["projection"] = projection
        item["projection_hash"] = current_hash
        item["stored_projection_hash"] = stored_hash
        item["projection_hash_missing"] = not bool(stored_hash)
        item["projection_hash_mismatch"] = bool(stored_hash and current_hash and stored_hash != current_hash)
        endpoint_items.append(item)
    return {
        "schema": inspect_schema_fn(conn),
        "schema_visibility": schema_visibility_policy(),
        "center": row_to_dict(center),
        "endpoints": endpoint_items,
        "terms": [row_to_dict(row) for row in terms],
        "tasks": [row_to_dict(row) for row in tasks],
        "acceptance_checks": [row_to_dict(row) for row in checks],
        "current_tasks": current_tasks,
        "deferred_tasks": deferred_tasks,
        "open_checks": open_checks,
        "deferred_checks": deferred_checks,
        "evidence": [row_to_dict(row) for row in evidence],
        "risks_and_notes": [row_to_dict(row) for row in risks],
    }


def render_project_report_markdown(payload: dict[str, Any]) -> str:
    lines = ["# shujuan Project Report", ""]
    schema = payload.get("schema") or {}
    lines.extend(["## Database", ""])
    lines.append(f"- Backend: {schema.get('backend') or 'unknown'}")
    lines.append(f"- Schema state: {schema.get('state') or 'unknown'}")
    lines.append(f"- Project meta versions: {', '.join(schema.get('project_meta_versions') or []) or 'None'}")
    visibility = payload.get("schema_visibility") or {}
    lines.append(f"- Default surface: {visibility.get('default_surface') or 'governance_objects'}")
    lines.append(f"- Default visible objects: {', '.join(visibility.get('default_visible_objects') or []) or 'None'}")
    lines.append(f"- Default hidden schema roles: {', '.join(visibility.get('default_hidden_roles') or []) or 'None'}")
    lines.append("")
    center = payload.get("center")
    lines.extend(["## Center", ""])
    lines.append((center or {}).get("body", "No center body recorded.").strip())
    lines.extend(["", "## Endpoints", ""])
    for endpoint in payload["endpoints"]:
        props = props_dict(endpoint.get("body_props"))
        lines.append(
            f"- {endpoint['name']}: root={endpoint.get('root_node_id') or 'None'}, "
            f"body_source={props.get('source_kind') or 'manual'}, "
            f"projection_hash={endpoint.get('projection_hash') or 'None'}, "
            f"stored_projection_hash={endpoint.get('stored_projection_hash') or 'None'}, "
            f"projection_hash_missing={'yes' if endpoint.get('projection_hash_missing') else 'no'}, "
            f"projection_hash_mismatch={'yes' if endpoint.get('projection_hash_mismatch') else 'no'}"
        )
    if not payload["endpoints"]:
        lines.append("- None")
    lines.extend(["", "## Open Obligations", ""])
    open_tasks = payload.get("current_tasks") or []
    open_checks = payload.get("open_checks") or []
    for task in open_tasks:
        lines.append(f"- Task {task['id']}: {task['task_body']}")
    for check in open_checks:
        lines.append(f"- Check {check['id']} ({check.get('expected_evidence_type')}): {check['check_body']}")
    if not open_tasks and not open_checks:
        lines.append("- None")
    lines.extend(["", "## Evidence-Backed Closures", ""])
    closed_checks = [check for check in payload["acceptance_checks"] if check.get("closed_by_node_id") is not None]
    for check in closed_checks:
        lines.append(f"- Check {check['id']} by {check.get('closed_by_type') or check.get('closed_by_node_id')}: {check['check_body']}")
    if not closed_checks:
        lines.append("- None")
    lines.extend(["", "## Recent Evidence", ""])
    for item in payload["evidence"]:
        lines.append(f"- {item['id']} [{item['type']}]: {item.get('label') or item.get('summary') or ''}")
    if not payload["evidence"]:
        lines.append("- None")
    lines.extend(["", "## Risks, Findings, And Notes", ""])
    for item in payload["risks_and_notes"]:
        lines.append(f"- {item['id']} [{item['type']}]: {item.get('label') or item.get('summary') or ''}")
    if not payload["risks_and_notes"]:
        lines.append("- None")
    lines.extend(["", "## Terms", ""])
    for term in payload["terms"]:
        lines.append(f"- {term['canonical_term']}: {term['definition']}")
    if not payload["terms"]:
        lines.append("- None")
    lines.extend(["", "Completion rule: tasks and acceptance checks are complete only when closed by evidence nodes. Endpoint bodies are recoverable breakpoints, not terminal completion claims."])
    return "\n".join(lines).rstrip() + "\n"


def render_project_overview_markdown(payload: dict[str, Any]) -> str:
    lines = ["# shujuan Project Overview", ""]
    schema = payload.get("schema") or {}
    lines.append(f"- Backend: {schema.get('backend') or 'unknown'}")
    lines.append(f"- Schema state: {schema.get('state') or 'unknown'}")
    visibility = payload.get("schema_visibility") or {}
    lines.append(f"- Default surface: {visibility.get('default_surface') or 'governance_objects'}")
    lines.append(f"- Default visible objects: {', '.join(visibility.get('default_visible_objects') or []) or 'None'}")
    active_tasks = payload.get("current_tasks") or []
    active_checks = payload.get("open_checks") or []
    lines.append(f"- Active tasks: {len(active_tasks)}")
    lines.append(f"- Open checks: {len(active_checks)}")
    lines.extend(["", "## Endpoint Entry Points", ""])
    for endpoint in payload["endpoints"]:
        lines.append(f"- {endpoint['name']}: use `report endpoint {endpoint['name']} --active-only` for agent entry")
    if not payload["endpoints"]:
        lines.append("- None")
    lines.extend(["", "Project overview is not a substitute for endpoint active-only context."])
    return "\n".join(lines).rstrip() + "\n"


def cmd_report_project(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    connect_fn = _require_dependency("connect_read_only")
    print_text_fn = _require_dependency("print_text")
    print_json_fn = _require_dependency("print_json")
    conn = connect_fn(repo)
    payload = project_report_payload(conn)
    if args.markdown:
        print_text_fn(render_project_overview_markdown(payload) if args.overview else render_project_report_markdown(payload), end="")
    else:
        mode = "overview" if args.overview else "full"
        if args.overview:
            print_json_fn(
                {
                    "ok": True,
                    "mode": mode,
                    "schema": payload["schema"],
                    "endpoints": payload["endpoints"],
                    "active_task_count": len(payload.get("current_tasks") or []),
                    "open_check_count": len(payload.get("open_checks") or []),
                    "entry_policy": "Use report endpoint --active-only as the default agent entry; project overview is a map, not an endpoint workbench.",
                }
            )
        else:
            print_json_fn({"ok": True, "mode": mode, **payload})
    return 0


def endpoint_report_payload(conn: sqlite3.Connection, endpoint_name: str, *, active_only: bool = False, role: str | None = None, repo: Path | None = None) -> dict[str, Any]:
    endpoint_status_payload_fn = _require_dependency("endpoint_status_payload")
    endpoint_active_obligations_fn = _require_dependency("endpoint_active_obligations")
    endpoint_active_obligation_count_fn = _require_dependency("endpoint_active_obligation_count")
    endpoint_readiness_diagnostic_fn = _require_dependency("endpoint_readiness_diagnostic")
    status = endpoint_status_payload_fn(conn, endpoint_name, repo=repo)
    obligations = endpoint_active_obligations_fn(status)
    active_count = endpoint_active_obligation_count_fn(obligations)
    readiness = endpoint_readiness_diagnostic_fn(status, obligations, role=role, read_only=True)
    closed_checks = status.get("closed_checks") or []
    evidence = status.get("evidence") or []
    inactive_items = (status.get("semantic_projection") or {}).get("inactive") or []
    direction = {
        "endpoint": status["endpoint"],
        "root_node": status.get("root_node"),
        "scope_contract": status.get("scope_contract"),
        "projection": status.get("projection"),
        "warnings": status.get("warnings") or [],
        "discussion_brief": status.get("discussion_brief") or {},
        "chain_brief": status.get("chain_brief") or {},
        "chain_children": status.get("chain_children") or [],
        "inherited_active_blockers": status.get("inherited_active_blockers") or [],
        "review_state": status.get("review_state"),
    }
    closure_state = {
        "closed_check_count": len(closed_checks),
        "evidence_count": len(evidence),
        "inactive_semantic_item_count": len(inactive_items),
        "completion_rule": status.get("completion_rule"),
        "historical_details": "omitted in active_only mode; use report endpoint --full for closed checks, evidence, inactive lifecycle items, and discussion history.",
    }
    next_entry = {
        "active_obligation_count": active_count,
        "recommendation": (
            "Resolve active obligations; use endpoint doctor --strict-closeout --read-only for diagnostics, then controller closeout may run strict doctor without --read-only."
            if active_count
            else "Use endpoint doctor --strict-closeout --read-only for read-only recovery, or controller closeout may run strict doctor without --read-only."
        ),
        "commands": (
            [
                f"python -m shujuan endpoint status {endpoint_name} --markdown",
                f"python -m shujuan endpoint doctor {endpoint_name} --strict-closeout --read-only",
            ]
            if active_count
            else [
                f"python -m shujuan endpoint doctor {endpoint_name} --strict-closeout --read-only",
                f"python -m shujuan workflow begin --endpoint {endpoint_name} --session-id <session> --content <prompt>",
            ]
        ),
    }
    payload = {
        "mode": "active_only" if active_only else "full",
        "endpoint": endpoint_name,
        "direction": direction,
        "closure_state": closure_state,
        "readiness": readiness,
        "active_obligations": obligations,
        "next_valid_entry_point": next_entry,
    }
    if not active_only:
        closure_state.update(
            {
                "closed_checks": closed_checks,
                "evidence": evidence,
                "inactive_semantic_items": inactive_items,
                "historical_details": "included",
            }
        )
        payload["historical_details"] = {
            "tasks": status.get("tasks") or [],
            "deferred_tasks": status.get("deferred_tasks") or [],
            "deferred_checks": status.get("deferred_checks") or [],
            "closed_checks": closed_checks,
            "evidence": evidence,
            "inactive_semantic_items": inactive_items,
            "recent_discussions": status.get("recent_discussions") or [],
            "scope_changes": status.get("scope_changes") or [],
            "assumptions": status.get("assumptions") or [],
            "defer_decisions": status.get("defer_decisions") or [],
        }
        payload["status"] = status
    return payload


def render_endpoint_report_markdown(payload: dict[str, Any]) -> str:
    direction = payload["direction"]
    endpoint = direction["endpoint"]
    scope = direction.get("scope_contract") or {}
    obligations = payload["active_obligations"]
    closure = payload["closure_state"]
    next_entry = payload["next_valid_entry_point"]
    active_only = payload.get("mode") == "active_only"
    lines = [
        "# Endpoint Active Report" if active_only else "# Endpoint Full Report",
        "",
        "## Direction",
        "",
        f"- Endpoint: {endpoint.get('name')}",
        f"- Description: {endpoint.get('description') or 'None'}",
        f"- Root node: {endpoint.get('root_node_id') or 'None'}",
        f"- Scope contract: {scope.get('id') or 'None'}",
        f"- Projection stale: {'yes' if (direction.get('projection') or {}).get('stale') else 'no'}",
        f"- Unreviewed discussion segments: {(direction.get('discussion_brief') or {}).get('unreviewed_count', 0)}",
        f"- Child chain endpoints: {(direction.get('chain_brief') or {}).get('child_count', 0)}",
        f"- Active child chain blockers: {(direction.get('chain_brief') or {}).get('active_child_count', 0)}",
        "",
        "## Readiness",
        "",
        f"- Closeout ready: {'yes' if (payload.get('readiness') or {}).get('closeout_ready') else 'no'}",
        f"- Execution ready: {'yes' if (payload.get('readiness') or {}).get('execution_ready') else 'no'}",
        f"- Blocking reason: {(payload.get('readiness') or {}).get('blocking_reason') or 'None'}",
        f"- Hidden blocker refs: {len((payload.get('readiness') or {}).get('hidden_blocking_refs') or [])}",
        f"- Next safe action: {(payload.get('readiness') or {}).get('next_safe_action') or 'None'}",
        f"- Authority boundary: {(payload.get('readiness') or {}).get('authority_boundary') or 'None'}",
        "",
        "## Closure Summary" if active_only else "## Evidence-Backed Closure",
        "",
    ]
    if active_only:
        lines.append(f"- Closed checks: {closure.get('closed_check_count', 0)}")
        lines.append(f"- Evidence records: {closure.get('evidence_count', 0)}")
        lines.append(f"- Inactive semantic items: {closure.get('inactive_semantic_item_count', 0)}")
        lines.append("- Historical details omitted; use `report endpoint --full` for closed checks, evidence, inactive lifecycle items, and discussion history.")
    else:
        closed_checks = closure.get("closed_checks") or []
        if closed_checks:
            for check in closed_checks:
                lines.append(f"- Check {check['id']} by {check.get('closed_by_node_id')}: {check.get('check_body') or ''}")
        else:
            lines.append("- None")
    if not active_only:
        lines.extend(["", "## Historical Details", ""])
        details = payload.get("historical_details") or {}
        lines.append(f"- Evidence records: {len(details.get('evidence') or [])}")
        lines.append(f"- Inactive semantic items: {len(details.get('inactive_semantic_items') or [])}")
        lines.append(f"- Deferred tasks: {len(details.get('deferred_tasks') or [])}")
        lines.append(f"- Deferred checks: {len(details.get('deferred_checks') or [])}")
        lines.append(f"- Recent discussions: {len(details.get('recent_discussions') or [])}")
        inactive_items = details.get("inactive_semantic_items") or []
        if inactive_items:
            lines.append("")
            lines.append("Inactive semantic items:")
            for item in inactive_items[:20]:
                lines.append(f"- {item.get('node_id')} [{item.get('current_state') or 'inactive'}]: {item.get('label') or item.get('summary') or ''}")
    lines.extend(["", "## Active Obligations", ""])
    any_items = False
    for title, key in [
        ("Tasks", "current_tasks"),
        ("Open checks", "open_checks"),
        ("Review material", "review_material"),
        ("Audit findings", "audit_findings"),
        ("Inherited active blockers", "inherited_active_blockers"),
        ("Unresolved", "unresolved"),
        ("Needs user decision", "needs_user_decision"),
        ("Child chain blockers", "child_chain_blockers"),
    ]:
        items = obligations.get(key) or []
        lines.append(f"{title}:")
        if items:
            any_items = True
            for item in items:
                item_id = item.get("id") or item.get("endpoint") or item.get("node_id") or item.get("endpoint_node_id")
                lines.append(f"- {item_id} [{item.get('type') or item.get('relationship') or key}]: {item.get('label') or item.get('summary') or item.get('task_body') or item.get('check_body') or item.get('description') or ''}")
        else:
            lines.append("- None")
    if not any_items:
        lines.append("")
        lines.append("No active obligations are projected for this endpoint.")
    lines.extend(["", "## Next Valid Entry Point", ""])
    lines.append(f"- {next_entry['recommendation']}")
    for command in next_entry.get("commands") or []:
        lines.append(f"- `{command}`")
    return "\n".join(lines).rstrip() + "\n"


def render_endpoint_report_compact_markdown(payload: dict[str, Any]) -> str:
    direction = payload["direction"]
    endpoint = direction["endpoint"]
    readiness = payload.get("readiness") or {}
    obligations = payload.get("active_obligations") or {}
    next_entry = payload.get("next_valid_entry_point") or {}
    visible_refs = readiness.get("visible_blocking_refs") or []
    hidden_refs = readiness.get("hidden_blocking_refs") or []
    lines = [
        "# Endpoint Active Surface",
        "",
        f"- Endpoint: {endpoint.get('name')}",
        f"- Closeout ready: {'yes' if readiness.get('closeout_ready') else 'no'}",
        f"- Blocking reason: {readiness.get('blocking_reason_code') or 'none'}",
        f"- Active obligations: {sum(len(items or []) for items in obligations.values())}",
        f"- Hidden blockers: {len(hidden_refs)}",
        f"- Unreviewed discussion segments: {(direction.get('discussion_brief') or {}).get('unreviewed_count', 0)}",
        "",
        "## First Screen",
        "",
    ]
    if visible_refs:
        for ref in visible_refs[:8]:
            lines.append(f"- {ref.get('kind')}: {ref.get('ref')} {ref.get('summary') or ''}".rstrip())
        if len(visible_refs) > 8:
            lines.append(f"- ... {len(visible_refs) - 8} more visible refs; use `--verbose --markdown`.")
    else:
        lines.append("- No visible active blockers projected.")
    lines.extend(["", "## Exact Next Commands", ""])
    commands = next_entry.get("commands") or []
    if commands:
        for command in commands[:3]:
            lines.append(f"- `{command}`")
    else:
        lines.append("- `python -m shujuan report endpoint <endpoint> --active-only --verbose --markdown`")
    lines.extend(["", "Compact view omits historical JSON/detail payloads; use `--json` for machine-readable output or `--verbose --markdown` for full markdown."])
    return "\n".join(lines).rstrip() + "\n"


def cmd_report_endpoint(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    maybe_runtime_preflight_fn = _require_dependency("maybe_runtime_preflight")
    print_json_fn = _require_dependency("print_json")
    preflight = maybe_runtime_preflight_fn(args, "report endpoint")
    if preflight:
        print_json_fn(preflight)
        return 1
    connect_fn = _require_dependency("connect_read_only")
    resolve_endpoint_identifier_fn = _require_dependency("resolve_endpoint_identifier")
    print_text_fn = _require_dependency("print_text")
    diagnostics_payload_fn = _require_dependency("diagnostics_payload")
    conn = connect_fn(repo)
    endpoint_name = resolve_endpoint_identifier_fn(conn, repo, args.endpoint)
    payload = endpoint_report_payload(conn, endpoint_name, active_only=args.active_only, repo=repo)
    if args.compact and args.verbose:
        raise SystemExit("pass only one of --compact or --verbose")
    if args.json_output and (args.markdown or args.compact or args.verbose):
        raise SystemExit("pass --json alone; markdown/compact/verbose are human-readable modes")
    if args.markdown or args.compact or args.verbose:
        if args.compact:
            print_text_fn(render_endpoint_report_compact_markdown(payload), end="")
        else:
            print_text_fn(render_endpoint_report_markdown(payload), end="")
    else:
        raw_count = sum(len(items) for items in (payload.get("active_obligations") or {}).values())
        hidden_count = len(((payload.get("historical_details") or {}).get("closed_checks") or [])) if args.active_only else 0
        print_json_fn(
            {
                "ok": True,
                "usable": True,
                "output_mode": "json",
                **payload,
                "diagnostics": diagnostics_payload_fn(
                    usable=True,
                    raw_count=raw_count,
                    visible_count=raw_count,
                    filtered_count=hidden_count,
                    next_action=(payload.get("next_valid_entry_point") or {}).get("recommendation"),
                ),
            }
        )
    return 0


def lifecycle_item_payload(conn: sqlite3.Connection, node_id: str) -> dict[str, Any]:
    require_node_fn = _require_dependency("require_node")
    display_lifecycle_event_fn = _require_dependency("display_lifecycle_event")
    display_semantic_row_fn = _require_dependency("display_semantic_row")
    canonical_semantic_state_fn = _require_dependency("canonical_semantic_state")
    node = require_node_fn(conn, node_id, "lifecycle item node")
    item = conn.execute("SELECT * FROM semantic_items WHERE node_id = ?", (node_id,)).fetchone()
    events = []
    if item:
        events = conn.execute(
            """
            SELECT *
            FROM semantic_lifecycle_events
            WHERE semantic_item_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (item["id"],),
        ).fetchall()
    partitions = {
        "active": [],
        "resolved": [],
        "deferred": [],
        PRODUCT_BACKLOG_STATE: [],
        "invalidated": [],
        "superseded": [],
        "history": [display_lifecycle_event_fn(row) for row in events],
    }
    if item:
        state = str(canonical_semantic_state_fn(item["current_state"]))
        partitions.setdefault(state, []).append(display_semantic_row_fn(item))
    return {
        "node": row_to_dict(node),
        "semantic_item": display_semantic_row_fn(item),
        "current_state": canonical_semantic_state_fn(item["current_state"]) if item else None,
        "partitions": partitions,
    }


def render_lifecycle_item_markdown(payload: dict[str, Any]) -> str:
    canonical_semantic_state_fn = _require_dependency("canonical_semantic_state")
    node = payload["node"]
    lines = ["# Lifecycle Item", "", f"- Node: {node['id']} [{node['type']}]", f"- Current state: {payload.get('current_state') or 'untracked'}"]
    for key in SEMANTIC_STATE_DISPLAY_ORDER:
        lines.extend(["", f"## {key}", ""])
        items = payload["partitions"].get(key) or []
        if items:
            for item in items:
                lines.append(f"- {item['id']} state={canonical_semantic_state_fn(item['current_state'])} updated={item.get('updated_at')}")
        else:
            lines.append("- None")
    lines.extend(["", "## History", ""])
    for event in payload["partitions"].get("history") or []:
        lines.append(f"- {event['created_at']}: {event['event_type']} {event.get('from_state') or 'None'} -> {event['to_state']}")
    if not payload["partitions"].get("history"):
        lines.append("- None")
    return "\n".join(lines).rstrip() + "\n"


def cmd_report_lifecycle(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    connect_fn = _require_dependency("connect")
    print_text_fn = _require_dependency("print_text")
    print_json_fn = _require_dependency("print_json")
    conn = connect_fn(repo)
    payload = lifecycle_item_payload(conn, args.item)
    if args.markdown:
        print_text_fn(render_lifecycle_item_markdown(payload), end="")
    else:
        print_json_fn({"ok": True, **payload})
    return 0


def _v6_edge_exists(conn: sqlite3.Connection, from_node_id: str, edge_type: str, to_node_id: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM edges WHERE from_node_id = ? AND type = ? AND to_node_id = ? LIMIT 1",
            (from_node_id, edge_type, to_node_id),
        ).fetchone()
    )


def _v6_section_for_node(conn: sqlite3.Connection, node_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, node_id, document_id, heading, left(body, 360) AS body_excerpt
        FROM document_sections
        WHERE node_id = ?
        LIMIT 1
        """,
        (node_id,),
    ).fetchone()
    return row_to_dict(row)


def _v6_semantic_for_node(conn: sqlite3.Connection, node_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM semantic_items WHERE node_id = ?", (node_id,)).fetchone()
    return row_to_dict(row)


def _v6_node(conn: sqlite3.Connection, node_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    return row_to_dict(row)


def _v6_assert(name: str, passed: bool, detail: str, refs: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail, "refs": refs or {}}


def v6_phase0_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    ids = V6_PHASE0_IDS
    assertions: list[dict[str, Any]] = []

    source_doc = conn.execute(
        "SELECT * FROM source_documents WHERE id = ? AND node_id = ?",
        (ids["document"], ids["document_node"]),
    ).fetchone()
    source_doc_dict = row_to_dict(source_doc)
    source_meta = props_dict(source_doc_dict.get("metadata") if source_doc_dict else None)
    source_path = str(source_meta.get("path") or "")
    assertions.append(
        _v6_assert(
            "v6_source_document_imported",
            bool(
                source_doc_dict
                and source_doc_dict.get("source_type") == "plan"
                and source_path.lower() == V6_PHASE0_SOURCE_PATH.lower()
            ),
            "V6 plan source document exists with the expected document/node IDs and origin path.",
            {
                "document_id": ids["document"],
                "document_node_id": ids["document_node"],
                "path": source_path,
            },
        )
    )

    scope = conn.execute(
        "SELECT * FROM scope_contracts WHERE id = ? AND node_id = ?",
        (ids["scope_contract"], ids["scope_node"]),
    ).fetchone()
    scope_dict = row_to_dict(scope)
    scope_source = _v6_section_for_node(conn, str(scope_dict.get("source_node_id"))) if scope_dict else None
    assertions.append(
        _v6_assert(
            "source_backed_scope_contract",
            bool(scope_dict and scope_dict.get("source_node_id") and scope_source and scope_source.get("document_id") == ids["document"]),
            "Scope contract exists, is bound to the V6 root node, and points back to a V6 source-plan section.",
            {
                "contract_id": ids["scope_contract"],
                "scope_node_id": ids["scope_node"],
                "source_node_id": (scope_dict or {}).get("source_node_id"),
                "source_section": (scope_source or {}).get("id"),
            },
        )
    )

    endpoint = conn.execute(
        "SELECT * FROM endpoints WHERE node_id = ? AND name = ?",
        (ids["endpoint_node"], V6_PHASE0_ENDPOINT),
    ).fetchone()
    endpoint_dict = row_to_dict(endpoint)
    root_edge = _v6_edge_exists(conn, ids["endpoint_node"], "ROOTS_AT", ids["scope_node"])
    assertions.append(
        _v6_assert(
            "endpoint_root_binding",
            bool(endpoint_dict and endpoint_dict.get("root_node_id") == ids["scope_node"] and root_edge),
            "V6 endpoint is rooted in the source-backed scope contract and has a ROOTS_AT edge to the root node.",
            {
                "endpoint": V6_PHASE0_ENDPOINT,
                "endpoint_node_id": ids["endpoint_node"],
                "root_node_id": (endpoint_dict or {}).get("root_node_id"),
                "roots_at_edge": root_edge,
            },
        )
    )

    terms = conn.execute(
        """
        SELECT t.*, si.current_state, si.source_node_id AS semantic_source_node_id
        FROM terms t
        LEFT JOIN semantic_items si ON si.node_id = t.node_id
        WHERE t.scope_node_id = ?
          AND t.valid_to IS NULL
        ORDER BY lower(t.canonical_term)
        """,
        (ids["scope_node"],),
    ).fetchall()
    terms_by_name = {str(row["canonical_term"]): row_to_dict(row) for row in terms}
    term_payload = []
    missing_terms = []
    unsourced_terms = []
    for term in V6_PHASE0_REQUIRED_TERMS:
        row = terms_by_name.get(term)
        source_section = _v6_section_for_node(conn, str(row.get("created_from_node_id"))) if row else None
        term_payload.append(
            {
                "term": term,
                "node_id": (row or {}).get("node_id"),
                "current_state": (row or {}).get("current_state"),
                "created_from_node_id": (row or {}).get("created_from_node_id"),
                "source_section": (source_section or {}).get("id"),
            }
        )
        if not row:
            missing_terms.append(term)
        elif not source_section or source_section.get("document_id") != ids["document"]:
            unsourced_terms.append(term)
    assertions.append(
        _v6_assert(
            "v6_term_nodes_source_backed",
            not missing_terms and not unsourced_terms,
            "Required V6 hard terms are present as scoped term nodes and trace to source-plan sections.",
            {
                "required_terms": V6_PHASE0_REQUIRED_TERMS,
                "missing_terms": missing_terms,
                "unsourced_terms": unsourced_terms,
            },
        )
    )

    old_endpoints = {}
    for key in ("old_v1_v5_endpoint_node", "old_v5_dccp_endpoint_node"):
        node_id = ids[key]
        old_endpoints[key] = _v6_node(conn, node_id)
    relationship_notes = []
    relationship_note_failures = []
    for node_id in V6_PHASE0_RELATIONSHIP_NOTE_IDS:
        node = _v6_node(conn, node_id)
        semantic = _v6_semantic_for_node(conn, node_id)
        source_node_id = str((semantic or {}).get("source_node_id") or "")
        source_section = _v6_section_for_node(conn, source_node_id) if source_node_id else None
        applies_v6 = _v6_edge_exists(conn, node_id, "APPLIES_TO", ids["endpoint_node"])
        applies_old = [
            old_id
            for old_id in (ids["old_v1_v5_endpoint_node"], ids["old_v5_dccp_endpoint_node"])
            if _v6_edge_exists(conn, node_id, "APPLIES_TO", old_id)
        ]
        relationship_notes.append(
            {
                "node_id": node_id,
                "label": (node or {}).get("label"),
                "state": (semantic or {}).get("current_state"),
                "source_node_id": source_node_id or None,
                "source_section": (source_section or {}).get("id"),
                "applies_to_v6_endpoint": applies_v6,
                "applies_to_old_endpoint_nodes": applies_old,
            }
        )
        if not node or not semantic or not source_section or not applies_v6:
            relationship_note_failures.append(node_id)
    old_link_count = sum(len(note["applies_to_old_endpoint_nodes"]) for note in relationship_notes)
    assertions.append(
        _v6_assert(
            "old_endpoint_relationship_notes_and_links",
            bool(old_endpoints["old_v1_v5_endpoint_node"])
            and bool(old_endpoints["old_v5_dccp_endpoint_node"])
            and not relationship_note_failures
            and old_link_count >= 2,
            "Old v1-v5 and v5 DCCP endpoint nodes exist; V6 relationship notes are source-backed, apply to V6, and at least two notes link back to old endpoint nodes.",
            {
                "old_endpoint_nodes": {
                    key: (value or {}).get("label") for key, value in old_endpoints.items()
                },
                "relationship_note_failures": relationship_note_failures,
                "old_endpoint_link_count": old_link_count,
            },
        )
    )

    p1_node = _v6_node(conn, ids["p1_defer_note_node"])
    p1_semantic = _v6_semantic_for_node(conn, ids["p1_defer_note_node"])
    p1_props = props_dict((p1_node or {}).get("props"))
    p1_body = str(p1_props.get("body") or (p1_node or {}).get("summary") or "")
    p1_source = _v6_section_for_node(conn, str((p1_semantic or {}).get("source_node_id") or ""))
    p1_phase_task = conn.execute(
        """
        SELECT t.id, t.node_id, n.label, t.task_body
        FROM tasks t
        JOIN nodes n ON n.id = t.node_id
        WHERE t.contract_id = ?
          AND (n.label ILIKE ? OR t.task_body ILIKE ?)
        LIMIT 1
        """,
        (ids["scope_contract"], "%Phase 6%", "%P1 hardening%"),
    ).fetchone()
    assertions.append(
        _v6_assert(
            "p1_defer_record_queryable",
            bool(p1_node and p1_semantic and p1_source and "defer P1" in p1_body and p1_phase_task),
            "P1 hardening is represented as a source-backed defer signal and a queryable Phase 6/P1 task that states it should not block basic activation closeout.",
            {
                "defer_note_node_id": ids["p1_defer_note_node"],
                "state": (p1_semantic or {}).get("current_state"),
                "source_section": (p1_source or {}).get("id"),
                "phase_task_id": (row_to_dict(p1_phase_task) or {}).get("id") if p1_phase_task else None,
            },
        )
    )

    p2_node = _v6_node(conn, ids["p2_product_backlog_node"])
    p2_semantic = _v6_semantic_for_node(conn, ids["p2_product_backlog_node"])
    p2_source = _v6_section_for_node(conn, str((p2_semantic or {}).get("source_node_id") or ""))
    p2_applies = _v6_edge_exists(conn, ids["p2_product_backlog_node"], "APPLIES_TO", ids["endpoint_node"])
    p2_body = str(props_dict((p2_node or {}).get("props")).get("body") or "")
    assertions.append(
        _v6_assert(
            "p2_product_backlog_record_queryable",
            bool(
                p2_node
                and p2_semantic
                and p2_semantic.get("current_state") == PRODUCT_BACKLOG_STATE
                and p2_source
                and p2_source.get("document_id") == ids["document"]
                and p2_applies
            ),
            "P2 non-goals are represented by a source-backed product_backlog semantic item linked to the V6 endpoint.",
            {
                "product_backlog_node_id": ids["p2_product_backlog_node"],
                "state": (p2_semantic or {}).get("current_state"),
                "source_section": (p2_source or {}).get("id"),
                "applies_to_endpoint": p2_applies,
            },
        )
    )

    scope_rules = str((scope_dict or {}).get("non_downgrade_rules") or "")
    assertions.append(
        _v6_assert(
            "center_stage_and_non_goals_source_backed",
            bool(scope_source and p2_source and "P0" in scope_rules and "P2" in p2_body),
            "Current-stage boundary and non-goals are queryable from source-backed scope/P2 records rather than chat-only text.",
            {
                "scope_source_section": (scope_source or {}).get("id"),
                "p2_source_section": (p2_source or {}).get("id"),
                "scope_rules_include_p0": "P0" in scope_rules,
                "p2_body_mentions_p2": "P2" in p2_body,
            },
        )
    )

    ok = all(item["passed"] for item in assertions)
    return {
        "ok": ok,
        "read_only": True,
        "db_writes": 0,
        "endpoint": V6_PHASE0_ENDPOINT,
        "ids": ids,
        "assertions": assertions,
        "source_document": {
            "id": ids["document"],
            "node_id": ids["document_node"],
            "title": (source_doc_dict or {}).get("title"),
            "source_type": (source_doc_dict or {}).get("source_type"),
            "path": source_path,
            "content_hash": (source_doc_dict or {}).get("content_hash"),
        },
        "scope_contract": {
            "id": ids["scope_contract"],
            "node_id": ids["scope_node"],
            "source_node_id": (scope_dict or {}).get("source_node_id"),
            "source_section": scope_source,
            "non_downgrade_rules": (scope_dict or {}).get("non_downgrade_rules"),
        },
        "endpoint_root": {
            "node_id": ids["endpoint_node"],
            "root_node_id": (endpoint_dict or {}).get("root_node_id"),
            "roots_at_edge": root_edge,
        },
        "terms": term_payload,
        "relationship_notes": relationship_notes,
        "p1_defer": {
            "node_id": ids["p1_defer_note_node"],
            "state": (p1_semantic or {}).get("current_state"),
            "source_section": p1_source,
            "phase_task": row_to_dict(p1_phase_task),
        },
        "p2_product_backlog": {
            "node_id": ids["p2_product_backlog_node"],
            "state": (p2_semantic or {}).get("current_state"),
            "source_section": p2_source,
            "applies_to_endpoint": p2_applies,
        },
    }


def render_v6_phase0_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# V6 Phase 0 Verification",
        "",
        f"- Endpoint: {payload['endpoint']}",
        f"- Read-only: {'yes' if payload.get('read_only') else 'no'}",
        f"- DB writes: {payload.get('db_writes', 'unknown')}",
        f"- Result: {'pass' if payload.get('ok') else 'fail'}",
        "",
        "## Assertions",
        "",
    ]
    for item in payload.get("assertions") or []:
        lines.append(f"- {'PASS' if item.get('passed') else 'FAIL'} {item.get('name')}: {item.get('detail')}")
    lines.extend(["", "## Key Records", ""])
    source = payload.get("source_document") or {}
    scope = payload.get("scope_contract") or {}
    root = payload.get("endpoint_root") or {}
    lines.append(f"- Source document: {source.get('id')} / {source.get('node_id')} / {source.get('path')}")
    lines.append(f"- Scope contract: {scope.get('id')} / {scope.get('node_id')} from {(scope.get('source_section') or {}).get('id')}")
    lines.append(f"- Endpoint root: {root.get('node_id')} -> {root.get('root_node_id')}")
    lines.extend(["", "## Terms", ""])
    for term in payload.get("terms") or []:
        lines.append(f"- {term.get('term')}: {term.get('node_id')} source={term.get('source_section')}")
    lines.extend(["", "## Relationship Notes", ""])
    for note in payload.get("relationship_notes") or []:
        old_links = ", ".join(note.get("applies_to_old_endpoint_nodes") or []) or "None"
        lines.append(f"- {note.get('node_id')}: source={note.get('source_section')} old_links={old_links}")
    p1 = payload.get("p1_defer") or {}
    p2 = payload.get("p2_product_backlog") or {}
    lines.extend(["", "## Non-Active Scope Signals", ""])
    lines.append(f"- P1 defer signal: {p1.get('node_id')} source={(p1.get('source_section') or {}).get('id')}")
    lines.append(f"- P2 product_backlog: {p2.get('node_id')} state={p2.get('state')} source={(p2.get('source_section') or {}).get('id')}")
    return "\n".join(lines).rstrip() + "\n"


def cmd_report_v6_phase0(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    open_db_raw_fn = _require_dependency("open_db_raw")
    assert_runtime_schema_ready_fn = _require_dependency("assert_runtime_schema_ready")
    print_text_fn = _require_dependency("print_text")
    print_json_fn = _require_dependency("print_json")
    conn = open_db_raw_fn(repo, allow_filesystem_writes=False)
    if conn is None:
        raise SystemExit("No shujuan database is available for V6 Phase 0 verification.")
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        assert_runtime_schema_ready_fn(conn, purpose="v6 phase0 read-only verification")
        payload = v6_phase0_payload(conn)
    finally:
        conn.close()
    if args.markdown:
        print_text_fn(render_v6_phase0_markdown(payload), end="")
    else:
        print_json_fn(payload)
    return 0 if payload["ok"] else 1


def register_report(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, ReportHandler],
) -> None:
    """Register the report command family while cli.py keeps global flags and dispatch."""
    _validate_handlers(handlers)

    report = subparsers.add_parser("report")
    report_sub = report.add_subparsers(dest="report_command", required=True)
    report_project = report_sub.add_parser("project")
    project_mode = report_project.add_mutually_exclusive_group()
    project_mode.add_argument("--overview", action="store_true")
    project_mode.add_argument("--full", action="store_true")
    report_project.add_argument("--markdown", action="store_true")
    report_project.set_defaults(func=handlers["project"])

    report_endpoint = report_sub.add_parser("endpoint")
    report_endpoint.add_argument("endpoint")
    endpoint_mode = report_endpoint.add_mutually_exclusive_group()
    endpoint_mode.add_argument("--active-only", action="store_true")
    endpoint_mode.add_argument("--full", action="store_true")
    report_endpoint.add_argument("--markdown", action="store_true")
    report_endpoint.add_argument("--compact", action="store_true", help="Print a short first-screen markdown surface with exact next commands.")
    report_endpoint.add_argument("--verbose", action="store_true", help="Print the full markdown surface when combined with human output.")
    report_endpoint.add_argument("--json", dest="json_output", action="store_true", help="Print parseable JSON output.")
    report_endpoint.add_argument("--runtime-preflight", action="store_true")
    report_endpoint.set_defaults(func=handlers["endpoint"])

    report_lifecycle = report_sub.add_parser("lifecycle")
    report_lifecycle.add_argument("--item", required=True)
    report_lifecycle.add_argument("--markdown", action="store_true")
    report_lifecycle.set_defaults(func=handlers["lifecycle"])

    report_v6_phase0 = report_sub.add_parser("v6-phase0")
    report_v6_phase0.add_argument("--markdown", action="store_true")
    report_v6_phase0.set_defaults(func=handlers["v6_phase0"])


__all__ = [
    "REPORT_HANDLER_KEYS",
    "build_report_handlers",
    "cmd_report_endpoint",
    "cmd_report_lifecycle",
    "cmd_report_project",
    "cmd_report_v6_phase0",
    "endpoint_report_payload",
    "lifecycle_item_payload",
    "project_report_payload",
    "register_report",
    "render_endpoint_report_markdown",
    "render_lifecycle_item_markdown",
    "render_project_overview_markdown",
    "render_project_report_markdown",
    "render_v6_phase0_markdown",
    "v6_phase0_payload",
]
