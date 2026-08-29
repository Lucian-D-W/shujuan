from __future__ import annotations

import argparse
import ast
import html
import json
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..services import agcp_policy, endpoint_projection
from ..services.command_effects import endpoint_doctor_effects
from ..services.review_state import load_review_state, review_material_obligations
from ..services.role_policy import normalize_role, role_capsule


def _configure(deps: Mapping[str, Any]) -> None:
    globals().update(deps)

def cmd_center_update(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    body = read_arg_or_stdin(args.body)
    current = conn.execute(
        "SELECT * FROM center_bodies WHERE is_current = 1 ORDER BY version DESC LIMIT 1"
    ).fetchone()
    next_version = int(current["version"]) + 1 if current else 1
    node_id = create_node(conn, "center_body", f"Project center v{next_version}", body[:240])
    center_id = new_id("center")
    conn.execute("UPDATE center_bodies SET is_current = 0 WHERE is_current = 1")
    conn.execute(
        """
        INSERT INTO center_bodies
          (id, node_id, body, version, is_current, created_from_node_id, created_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (center_id, node_id, body, next_version, args.from_node, now_iso()),
    )
    supersedes_node_id = None
    if current:
        supersedes_node_id = current["node_id"]
        create_edge(conn, node_id, "SUPERSEDES", current["node_id"], reason="Center body version update.")
    if args.from_node:
        create_edge(conn, node_id, "DERIVED_FROM", args.from_node, reason="Center update source node.")
    conn.commit()
    print_json(
        {
            "ok": True,
            "center_body_id": center_id,
            "node_id": node_id,
            "version": next_version,
            "supersedes_node_id": supersedes_node_id,
        }
    )
    return 0

def cmd_center_show(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    if args.all:
        rows = conn.execute("SELECT * FROM center_bodies ORDER BY version DESC").fetchall()
        print_json({"centers": [row_to_dict(row) for row in rows]})
        return 0
    row = conn.execute(
        "SELECT * FROM center_bodies WHERE is_current = 1 ORDER BY version DESC LIMIT 1"
    ).fetchone()
    print_json({"center": row_to_dict(row)})
    return 0


def cmd_center_suggest(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    prompt = read_arg_or_stdin(getattr(args, "from_prompt", None), file_path=getattr(args, "from_prompt_file", None), label="from_prompt")
    prompt_lower = prompt.lower()
    conn = connect(repo)
    rows = conn.execute(
        """
        SELECT id, version, body
        FROM center_bodies
        WHERE is_current = 1
        ORDER BY version DESC, created_at DESC
        LIMIT ?
        """,
        (max(1, args.top),),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        body = str(row["body"] or "")
        evidence = ["current_center_exists"]
        if any(token in prompt_lower for token in ("center", "project", "overview", "first surface", "recover", "scope")):
            evidence.append("prompt_requests_project_orientation")
        candidates.append(
            {
                "center_id": str(row["id"]),
                "version": int(row["version"]),
                "confidence": "medium" if body else "low",
                "evidence": evidence,
                "first_surface": [
                    "python -m shujuan center show",
                    "python -m shujuan report project --overview --markdown",
                ],
                "write_allowed": False,
                "safe_next_action": "Read the current center and project overview before binding any writeful route.",
            }
        )
    print_json(
        {
            "ok": True,
            "read_only": True,
            "write_allowed": False,
            "auto_bind": False,
            "candidates": candidates[: max(1, args.top)],
            "safe_next_action": "Use the current center only as read-only orientation; endpoint binding still needs explicit confirmation.",
        }
    )
    return 0

def cmd_export_center(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    row = conn.execute(
        "SELECT * FROM center_bodies WHERE is_current = 1 ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise SystemExit("no current center body")
    out_path = repo / ".shujuan" / "exports" / "center.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(str(row["body"]).rstrip() + "\n", encoding="utf-8")
    print_json({"ok": True, "path": relpath(out_path, repo), "center_body_id": row["id"], "version": row["version"]})
    return 0

def cmd_export_glossary(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    rows = conn.execute(
        """
        SELECT canonical_term, definition, avoid_aliases, ambiguity_notes, node_id
        FROM terms
        WHERE valid_to IS NULL
        ORDER BY lower(canonical_term)
        """
    ).fetchall()
    lines = ["# Glossary", ""]
    for row in rows:
        lines.append(f"## {row['canonical_term']}")
        lines.append("")
        lines.append(str(row["definition"]).rstrip())
        aliases = json.loads(row["avoid_aliases"] or "[]")
        if aliases:
            lines.append("")
            lines.append("Avoid aliases: " + ", ".join(str(item) for item in aliases))
        if row["ambiguity_notes"]:
            lines.append("")
            lines.append("Ambiguity notes: " + str(row["ambiguity_notes"]))
        lines.append("")
        lines.append(f"Node: `{row['node_id']}`")
        lines.append("")
    out_path = repo / ".shujuan" / "exports" / "glossary.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print_json({"ok": True, "path": relpath(out_path, repo), "terms": len(rows)})
    return 0

def active_audit_findings_for_endpoint(conn: sqlite3.Connection, endpoint_name: str, *, limit: int = 200) -> list[dict[str, Any]]:
    status = endpoint_status_payload(conn, endpoint_name, include_chain=False)
    return (status.get("recent_audit_findings") or [])[:limit]

def endpoint_active_obligations(status: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    decision_notes = [
        item
        for item in status.get("recent_work_notes") or []
        if props_dict(item).get("kind") == "needs_user_decision" or props_dict(item).get("active_obligation") is True
    ]
    return {
        "current_tasks": status.get("current_tasks") or [],
        "open_checks": status.get("open_checks") or [],
        "review_material": status.get("review_material") or [],
        "audit_findings": status.get("recent_audit_findings") or [],
        "inherited_active_blockers": status.get("inherited_active_blockers") or [],
        "unresolved": status.get("unresolved") or [],
        "needs_user_decision": decision_notes,
        "child_chain_blockers": (status.get("chain_brief") or {}).get("active_children") or [],
    }

def endpoint_active_obligation_count(obligations: dict[str, list[dict[str, Any]]]) -> int:
    seen: set[tuple[str, str]] = set()
    count = 0
    for key, items in obligations.items():
        for item in items:
            item_id = item.get("id") or item.get("node_id") or item.get("endpoint") or item.get("endpoint_node_id")
            dedupe_key = (str(item.get("type") or item.get("relationship") or key), str(item_id or id(item)))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            count += 1
    return count

def _readiness_ref(kind: str, item: dict[str, Any], *, hidden: bool = False) -> dict[str, Any]:
    ref = item.get("id") or item.get("node_id") or item.get("endpoint") or item.get("endpoint_node_id")
    summary = item.get("label") or item.get("summary") or item.get("task_body") or item.get("check_body") or item.get("description")
    return {
        "kind": kind,
        "ref": ref,
        "hidden": hidden,
        "detail_ref": item.get("detail_ref") or ref,
        "summary": summary,
    }

def endpoint_readiness_diagnostic(
    status: dict[str, Any],
    obligations: dict[str, list[dict[str, Any]]] | None = None,
    *,
    role: str | None = None,
    read_only: bool = True,
    strict_closeout: bool = False,
) -> dict[str, Any]:
    obligations = obligations or endpoint_active_obligations(status)
    active_count = endpoint_active_obligation_count(obligations)
    blocking_refs: list[dict[str, Any]] = []
    hidden_blocking_refs: list[dict[str, Any]] = []
    for key, kind in [
        ("current_tasks", "task"),
        ("open_checks", "acceptance_check"),
        ("review_material", "review_material"),
        ("audit_findings", "audit_finding"),
        ("unresolved", "unresolved"),
        ("needs_user_decision", "needs_user_decision"),
    ]:
        blocking_refs.extend(_readiness_ref(kind, item) for item in obligations.get(key) or [])
    hidden_blocking_refs.extend(
        _readiness_ref("inherited_active_blocker", item, hidden=True)
        for item in obligations.get("inherited_active_blockers") or []
    )
    hidden_blocking_refs.extend(
        _readiness_ref("child_chain_blocker", item, hidden=True)
        for item in obligations.get("child_chain_blockers") or []
    )
    unlinked = status.get("unlinked_scope_candidates") or {}
    for key, kind in [
        ("tasks", "unlinked_remediation_task"),
        ("checks", "unlinked_remediation_check"),
    ]:
        hidden_blocking_refs.extend(_readiness_ref(kind, item, hidden=True) for item in unlinked.get(key) or [])

    warnings: list[dict[str, Any]] = []
    open_by_task: dict[str, list[str]] = {}
    for check in status.get("open_checks") or []:
        open_by_task.setdefault(str(check.get("task_id")), []).append(str(check.get("id")))
    closed_by_task: dict[str, list[str]] = {}
    for check in status.get("closed_checks") or []:
        closed_by_task.setdefault(str(check.get("task_id")), []).append(str(check.get("id")))
    for task in status.get("tasks") or []:
        task_id = str(task.get("id"))
        if task.get("closed_by_node_id"):
            continue
        if closed_by_task.get(task_id) and not open_by_task.get(task_id):
            warnings.append(
                {
                    "code": "checks_closed_task_open",
                    "severity": "warning",
                    "message": f"Task {task_id} has closed checks but the task is still open.",
                    "recommendation": "Controller can review the closed checks and close or rescope the task with evidence-backed authority.",
                    "task_id": task_id,
                    "closed_check_ids": closed_by_task[task_id],
                }
            )

    reason_code = "closeout_ready"
    blocking_reason = "No active closeout blockers are projected."
    if obligations.get("audit_findings"):
        reason_code = "active_audit_findings"
        blocking_reason = "Active audit findings block closeout; open tasks or checks may also remain."
    elif obligations.get("inherited_active_blockers"):
        reason_code = "inherited_active_blockers"
        blocking_reason = "Inherited active blockers are folded from parent endpoint findings."
    elif obligations.get("child_chain_blockers"):
        reason_code = "child_chain_blockers"
        blocking_reason = "Child chain endpoints still have active obligations."
    elif obligations.get("review_material"):
        reason_code = "review_material_pending"
        blocking_reason = "Review material is still packet-only or not controller-adopted."
    elif obligations.get("current_tasks") or obligations.get("open_checks"):
        reason_code = "open_obligations"
        blocking_reason = "Endpoint has open tasks or acceptance checks."
    elif obligations.get("unresolved") or obligations.get("needs_user_decision"):
        reason_code = "active_decision_blockers"
        blocking_reason = "Unresolved questions or user-decision notes still block closeout."
    elif unlinked.get("tasks") or unlinked.get("checks"):
        reason_code = "unlinked_remediation_blockers"
        blocking_reason = "Same-source remediation candidates are folded outside scoped obligations."
    elif (
        not status.get("closed_checks")
        and not status.get("evidence")
        and not (status.get("semantic_projection") or {}).get("inactive")
    ):
        reason_code = "no_evidence_backed_closure"
        blocking_reason = "Strict closeout has no evidence-backed closure or inactive resolution history."

    closeout_ready = reason_code == "closeout_ready"
    role_card = _activation_role_capsule(role) if role else None
    delegated = bool(role_card and not role_card.get("can_close_checks_or_tasks"))
    authority_boundary = (
        f"{role_card['role']}: {role_card['authority']}; "
        "current-project closeout writes are not authorized for this role."
        if delegated
        else f"{role_card['role']}: {role_card['authority']}."
        if role_card
        else "diagnostic_only; controller_agent owns governance writes, endpoint refresh, exec stop, evidence import, and check/task closure."
    )
    if closeout_ready:
        next_safe_action = (
            "Controller may run strict closeout verification without --read-only when closeout authority applies."
            if not delegated
            else "Return material to the controller; do not claim closeout from a delegated role."
        )
    elif delegated:
        next_safe_action = "Work only inside the delegated scope and return material; do not write governance DB facts or close checks/tasks."
    else:
        next_safe_action = "Resolve listed blockers or record source-backed defer/scope/unresolved decisions before closeout."
    return {
        "schema": "endpoint_readiness.v1",
        "diagnostic_only": True,
        "stored_as_completion_state": False,
        "closeout_ready": closeout_ready,
        "execution_ready": bool(active_count or hidden_blocking_refs or warnings),
        "blocking_reason_code": reason_code,
        "blocking_reason": blocking_reason,
        "active_obligation_count": active_count,
        "visible_blocking_refs": blocking_refs,
        "hidden_blocking_refs": hidden_blocking_refs,
        "warnings": warnings,
        "next_safe_action": next_safe_action,
        "authority_boundary": authority_boundary,
        "read_only": read_only,
        "strict_closeout": strict_closeout,
    }

def endpoint_chain_children(conn: sqlite3.Connection, endpoint_name: str) -> list[dict[str, Any]]:
    endpoint = query_endpoint(conn, endpoint_name)
    rows = conn.execute(
        """
        SELECT e.id AS edge_id, e.reason, e.confidence, e.props,
               child_ep.name AS child_endpoint_name,
               child_ep.node_id AS child_endpoint_node_id,
               child_ep.root_node_id AS child_root_node_id,
               child_ep.description AS child_description
        FROM edges e
        JOIN endpoints child_ep ON child_ep.node_id = e.to_node_id
        WHERE e.from_node_id = ?
          AND e.type = 'CHAIN_CHILD'
          AND child_ep.archived_at IS NULL
        ORDER BY child_ep.created_at ASC, child_ep.name ASC
        """,
        (endpoint["node_id"],),
    ).fetchall()
    children = []
    for row in rows:
        child_status = endpoint_status_payload(conn, str(row["child_endpoint_name"]), include_chain=False)
        obligations = endpoint_active_obligations(child_status)
        active_count = endpoint_active_obligation_count(obligations)
        children.append(
            {
                "edge_id": row["edge_id"],
                "relationship": "CHAIN_CHILD",
                "endpoint": row["child_endpoint_name"],
                "endpoint_node_id": row["child_endpoint_node_id"],
                "root_node_id": row["child_root_node_id"],
                "description": row["child_description"],
                "active_obligation_count": active_count,
                "open_task_count": len(child_status.get("current_tasks") or []),
                "open_check_count": len(child_status.get("open_checks") or []),
                "open_task_ids": [item.get("id") for item in child_status.get("current_tasks") or []],
                "open_check_ids": [item.get("id") for item in child_status.get("open_checks") or []],
                "unreviewed_discussion_count": (child_status.get("discussion_brief") or {}).get("unreviewed_count", 0),
                "status": "active" if active_count else "clear",
                "edge_style": "solid",
                "confidence": row["confidence"] if row["confidence"] is not None else 1.0,
                "detail_ref": f"endpoint brief {row['child_endpoint_name']}",
            }
        )
    return children

def cmd_endpoint_update(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    body = read_arg_or_stdin(args.body) if args.body is not None else None
    result = upsert_endpoint_body(
        conn,
        endpoint_name=args.endpoint,
        body=body,
        description=args.description,
        root_node=args.root_node,
        from_node=args.from_node,
    )
    conn.commit()
    print_json({"ok": True, **result})
    return 0

def cmd_endpoint_create(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    existing = conn.execute("SELECT id FROM endpoints WHERE name = ?", (args.endpoint,)).fetchone()
    if existing:
        raise SystemExit(f"endpoint already exists: {args.endpoint}")
    if not args.root_node and not args.rootless:
        raise SystemExit("endpoint create requires --root-node, or pass --rootless to make the missing root explicit")
    result = upsert_endpoint_body(
        conn,
        endpoint_name=args.endpoint,
        body=None,
        description=args.description,
        root_node=args.root_node,
    )
    rootless_note_id = None
    if args.rootless:
        rootless_note_id = create_node(
            conn,
            "assumption",
            "rootless endpoint",
            f"Endpoint {args.endpoint} was explicitly created without a root node.",
            {"body": args.reason or "Rootless endpoint explicitly requested.", "endpoint": args.endpoint},
        )
        endpoint = query_endpoint(conn, args.endpoint)
        register_semantic_item(
            conn,
            rootless_note_id,
            "assumption",
            state="active",
            source_node=rootless_note_id,
            scope_node=endpoint["node_id"],
            event_type="created",
            reason="Rootless endpoint assumption recorded.",
            props={"endpoint": args.endpoint},
        )
        create_edge(conn, rootless_note_id, "APPLIES_TO", endpoint["node_id"], reason="Rootless endpoint assumption applies to endpoint.", created_by="agent")
    conn.commit()
    print_json({"ok": True, **result, "rootless": bool(args.rootless), "rootless_note_id": rootless_note_id})
    return 0

def cmd_endpoint_bind_root(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    query_endpoint(conn, args.endpoint)
    result = upsert_endpoint_body(
        conn,
        endpoint_name=args.endpoint,
        body=None,
        description=args.description,
        root_node=args.root_node,
    )
    conn.commit()
    print_json({"ok": True, **result})
    return 0

def cmd_endpoint_link_child(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    parent_name = resolve_endpoint_identifier(conn, repo, args.parent)
    child_name = resolve_endpoint_identifier(conn, repo, args.child)
    parent = query_endpoint(conn, parent_name)
    child = query_endpoint(conn, child_name)
    existing = conn.execute(
        "SELECT id FROM edges WHERE from_node_id = ? AND to_node_id = ? AND type = 'CHAIN_CHILD' LIMIT 1",
        (parent["node_id"], child["node_id"]),
    ).fetchone()
    if existing:
        edge_id = str(existing["id"])
    else:
        edge_id = create_edge(
            conn,
            parent["node_id"],
            "CHAIN_CHILD",
            child["node_id"],
            reason=args.reason or "Umbrella v4 endpoint decomposes into child chain endpoint.",
            confidence=args.confidence,
            created_by="agent",
            props={"child_root_node_id": child["root_node_id"], "relationship": "task_chain"},
        )
    conn.commit()
    print_json({"ok": True, "parent": parent_name, "child": child_name, "edge_id": edge_id, "relationship": "CHAIN_CHILD"})
    return 0

V6_ACTIVATION_SCHEMA = "activation.v6"
ACTIVATION_BRIEF_TITLE = "Activation Brief"
V6_LINEAGE_ANCHORS = [
    {
        "endpoint": "shujuan-v1-v5-design-current-state-audit-2026-05-21",
        "relation": "directly_addressed",
        "detail_ref": "report endpoint shujuan-v1-v5-design-current-state-audit-2026-05-21 --active-only --markdown",
    },
    {
        "endpoint": "shujuan-v5-dccp-delegated-collaboration-2026-05-20",
        "relation": "directly_addressed",
        "detail_ref": "report endpoint shujuan-v5-dccp-delegated-collaboration-2026-05-20 --active-only --markdown",
    },
]


def _activation_lineage_anchors(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    anchors = []
    for item in V6_LINEAGE_ANCHORS:
        endpoint = conn.execute(
            """
            SELECT e.name, e.node_id, e.root_node_id, b.node_id AS body_node_id, bn.props AS body_props
            FROM endpoints e
            LEFT JOIN endpoint_bodies b ON b.id = e.current_body_id
            LEFT JOIN nodes bn ON bn.id = b.node_id
            WHERE e.name = ?
            """,
            (item["endpoint"],),
        ).fetchone()
        endpoint_props = props_dict(endpoint["body_props"]) if endpoint else {}
        anchors.append(
            {
                **item,
                "endpoint_exists": bool(endpoint),
                "endpoint_node_id": endpoint["node_id"] if endpoint else None,
                "root_node_id": endpoint["root_node_id"] if endpoint else None,
                "current_body_node_id": endpoint["body_node_id"] if endpoint else None,
                "projection_hash": endpoint_props.get("projection_hash"),
            }
        )
    return anchors


def _brief_excerpt(value: Any, *, limit: int = 320) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _safe_rows(name: str, *args: Any) -> list[dict[str, Any]]:
    fn = globals().get(name)
    if not callable(fn):
        return []
    return [row_to_dict(row) for row in fn(*args)]


def _normalize_activation_mode(mode: str | None) -> tuple[str, dict[str, Any]]:
    normalized = normalize_mode(mode) if callable(globals().get("normalize_mode")) else (mode or "standard")
    contract_fn = globals().get("mode_contract_payload")
    if callable(contract_fn):
        return normalized, contract_fn(normalized)
    return normalized, {
        "mode": normalized,
        "db_writes": normalized != "no_governance",
        "capture_claim": normalized in {"capture", "explore"},
        "creates_run": normalized in {"light", "standard", "full"},
        "creates_change_set": False,
    }


def _activation_role_capsule(role: str | None) -> dict[str, Any]:
    return role_capsule(role)


def _activation_item_summary(item: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    summary = {key: item.get(key) for key in fields if item.get(key) is not None}
    summary["detail_ref"] = item.get("detail_ref") or item.get("id") or item.get("node_id")
    return summary


def _activation_public_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    hidden_fields = {
        "current_body",
        "current_body_props",
        "current_body_id",
        "current_body_node_id",
        "body_created_at",
    }
    public = {key: value for key, value in endpoint.items() if key not in hidden_fields}
    public["current_body_ref"] = endpoint.get("current_body_id")
    public["current_body_node_ref"] = endpoint.get("current_body_node_id")
    public["hidden_source_count"] = sum(1 for key in hidden_fields if endpoint.get(key) is not None)
    public["detail_ref"] = f"endpoint status {endpoint.get('name')} --markdown"
    return public


def _activation_center_capsule(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    center = conn.execute(
        "SELECT * FROM center_bodies WHERE is_current = 1 ORDER BY version DESC LIMIT 1"
    ).fetchone()
    direction = payload.get("direction") or {}
    scope = direction.get("scope_contract") or {}
    endpoint = direction.get("endpoint") or {}
    root_node_id = endpoint.get("root_node_id") or scope.get("node_id")
    term_rows = []
    if root_node_id:
        term_rows = conn.execute(
            """
            SELECT canonical_term, definition, avoid_aliases, ambiguity_notes, node_id
            FROM terms
            WHERE valid_to IS NULL
              AND (scope_node_id = ? OR scope_node_id IS NULL)
            ORDER BY lower(canonical_term)
            LIMIT 12
            """,
            (root_node_id,),
        ).fetchall()
    if not term_rows:
        term_rows = conn.execute(
            """
            SELECT canonical_term, definition, avoid_aliases, ambiguity_notes, node_id
            FROM terms
            WHERE valid_to IS NULL
            ORDER BY lower(canonical_term)
            LIMIT 12
            """
        ).fetchall()
    schema = inspect_schema(conn) if callable(globals().get("inspect_schema")) else {}
    return {
        "project_identity": "shujuan",
        "center_body_id": center["id"] if center else None,
        "center_version": center["version"] if center else None,
        "center_excerpt": _brief_excerpt(center["body"] if center else None),
        "current_stage_boundary": _brief_excerpt(
            scope.get("non_downgrade_rules")
            or scope.get("contract_body")
            or scope.get("body")
            or (endpoint.get("description") if isinstance(endpoint, dict) else None)
        ),
        "runtime_governance_invariants": [
            f"backend={schema.get('backend') or 'unknown'}",
            f"schema_state={schema.get('state') or 'unknown'}",
            "PostgreSQL success requires a real project-owned runtime chain; no SQLite runtime/write fallback.",
            "Provider facts and projection payloads are not closure evidence.",
        ],
        "role_doctrine": "DCCP roles bound authority; controller closes, delegated workers return material.",
        "term_anchors": [
            {
                "term": row["canonical_term"],
                "definition": _brief_excerpt(row["definition"], limit=160),
                "node_id": row["node_id"],
            }
            for row in term_rows
        ],
        "non_goals": [
            "Do not treat endpoint bodies as terminal completion claims.",
            "Do not make product-grade backlog items blockers unless explicitly promoted.",
            "Do not dump full closed-check or evidence history in activation brief.",
        ],
        "detail_ref": "center show",
    }


def _activation_proof_capsule(
    conn: sqlite3.Connection,
    endpoint_row: dict[str, Any],
    payload: dict[str, Any],
    *,
    work_chain: str | None,
) -> dict[str, Any]:
    endpoint_id = str(endpoint_row.get("id") or "")
    predicates = _safe_rows("endpoint_agcp_predicate_rows", conn, endpoint_id)
    predicate_ids = [str(row.get("id")) for row in predicates if row.get("id")]
    links = _safe_rows("predicate_link_rows", conn, predicate_ids) if predicate_ids else []
    closure = payload.get("closure_state") or {}
    projection = ((payload.get("direction") or {}).get("projection") or {})
    chains = _safe_rows("endpoint_work_chain_rows", conn, endpoint_id, work_chain)
    forbidden = _safe_rows("endpoint_forbidden_substitute_rows", conn, endpoint_id)
    return {
        "hard_predicates": predicates,
        "hard_predicate_count": len(predicates),
        "forbidden_substitutes": forbidden,
        "forbidden_substitute_count": len(forbidden),
        "task_predicate_links": links,
        "task_predicate_link_count": len(links),
        "work_chains": chains,
        "work_chain_count": len(chains),
        "projection": {
            "projection_hash": projection.get("projection_hash"),
            "stored_projection_hash": projection.get("stored_projection_hash"),
            "stale": projection.get("stale"),
            "source_kind": projection.get("source_kind"),
            "last_fact_at": projection.get("last_fact_at"),
            "generated_at": projection.get("generated_at"),
        },
        "closed_history_summary": {
            "closed_check_count": closure.get("closed_check_count", 0),
            "evidence_count": closure.get("evidence_count", 0),
            "inactive_semantic_item_count": closure.get("inactive_semantic_item_count", 0),
            "detail_ref": f"report endpoint {payload['endpoint']} --full --markdown",
            "hidden_source_count": sum(
                int(closure.get(key, 0) or 0)
                for key in ["closed_check_count", "evidence_count", "inactive_semantic_item_count"]
            ),
        },
        "focus_consistency": {
            "detail_ref": (
                f"python -m shujuan work focus --endpoint {payload['endpoint']} --work-chain {work_chain}"
                if work_chain
                else f"python -m shujuan work focus --endpoint {payload['endpoint']}"
            ),
            "reuses_fields": ["work_chains", "hard_predicates", "forbidden_substitutes", "task_predicate_links"],
        },
    }


def activation_brief_payload(
    conn: sqlite3.Connection,
    endpoint_name: str,
    *,
    role: str | None = None,
    mode: str | None = None,
    tasks: list[str] | None = None,
    checks: list[str] | None = None,
    work_chain: str | None = None,
) -> dict[str, Any]:
    selected_role = role or "worker_agent"
    payload = endpoint_report_payload(conn, endpoint_name, active_only=True, role=selected_role)
    raw_direction = payload["direction"]
    status_for_board = endpoint_status_payload(conn, endpoint_name, include_chain=True)
    endpoint_row = _activation_public_endpoint(raw_direction.get("endpoint") or {})
    direction = {
        **raw_direction,
        "endpoint": endpoint_row,
        "active_scope_board": raw_direction.get("active_scope_board") or status_for_board.get("active_scope_board") or {},
    }
    obligations = payload["active_obligations"]
    normalized_mode, mode_contract = _normalize_activation_mode(mode)
    active_count = payload["next_valid_entry_point"]["active_obligation_count"]
    readiness = payload.get("readiness") or {}
    endpoint_capsule = {
        "line": f"{endpoint_name} root={endpoint_row.get('root_node_id') or 'None'} active_obligations={active_count}",
        "endpoint": endpoint_row,
        "root_node": direction.get("root_node"),
        "scope_contract": direction.get("scope_contract"),
        "projection": direction.get("projection") or {},
        "active_obligation_count": active_count,
        "readiness": readiness,
        "active_obligations": {
            "tasks": [
                _activation_item_summary(item, ["id", "node_id", "task_body", "label"])
                for item in obligations.get("current_tasks") or []
            ],
            "open_checks": [
                _activation_item_summary(item, ["id", "node_id", "task_id", "check_body", "expected_evidence_type", "label"])
                for item in obligations.get("open_checks") or []
            ],
            "audit_findings": [
                _activation_item_summary(item, ["id", "node_id", "label", "summary"])
                for item in obligations.get("audit_findings") or []
            ],
            "unresolved": [
                _activation_item_summary(item, ["id", "node_id", "label", "summary"])
                for item in obligations.get("unresolved") or []
            ],
            "child_chain_blockers": obligations.get("child_chain_blockers") or [],
        },
        "active_scope_board": direction.get("active_scope_board") or {},
        "detail_ref": f"report endpoint {endpoint_name} --active-only --markdown",
    }
    proof_capsule = _activation_proof_capsule(conn, endpoint_row, payload, work_chain=work_chain)
    normalized_role, role_error = normalize_role(role)
    if role_error:
        normalized_role = role or "worker_agent"
    next_commands = [
        f"python -m shujuan report endpoint {endpoint_name} --active-only --markdown",
        proof_capsule["focus_consistency"]["detail_ref"],
    ]
    next_commands.extend(payload["next_valid_entry_point"].get("commands") or [])
    activation = {
        "activation_schema": V6_ACTIVATION_SCHEMA,
        "inputs": {
            "role": normalized_role,
            "requested_role": role or "worker_agent",
            "mode": normalized_mode,
            "task": tasks or [],
            "check": checks or [],
            "work_chain": work_chain,
        },
        "center_capsule": _activation_center_capsule(conn, payload),
        "endpoint_capsule": endpoint_capsule,
        "role_capsule": _activation_role_capsule(role),
        "mode_capsule": {
            "mode": normalized_mode,
            "contract": mode_contract,
            "side_effect_boundary": "Mode describes allowed work side effects; endpoint brief itself is read-only.",
        },
        "proof_capsule": proof_capsule,
        "action_capsule": {
            "next_safe_action": readiness.get("next_safe_action")
            or (
                "Resolve active obligations inside the requested role/mode boundary."
                if active_count
                else "Run strict doctor for closeout verification only when controller authority applies."
            ),
            "commands": list(dict.fromkeys(next_commands)),
            "backing_ledger_ref": f"report endpoint {endpoint_name} --active-only --markdown",
            "full_history_ref": f"report endpoint {endpoint_name} --full --markdown",
            "read_only": True,
            "db_writes": 0,
        },
        "lineage_anchors": _activation_lineage_anchors(conn),
        "detail_refs": {
            "backing_ledger": f"report endpoint {endpoint_name} --active-only --markdown",
            "full_history": f"report endpoint {endpoint_name} --full --markdown",
            "work_focus": proof_capsule["focus_consistency"]["detail_ref"],
        },
    }
    return {
        "ok": True,
        "endpoint": endpoint_name,
        "activation_schema": V6_ACTIVATION_SCHEMA,
        "activation": activation,
        "direction": direction,
        "active_obligations": obligations,
        "active_scope_board": direction.get("active_scope_board") or {},
        "active_obligation_count": active_count,
        "readiness": readiness,
        "next_valid_entry_point": payload["next_valid_entry_point"],
        "chain_brief": direction.get("chain_brief") or {},
        "read_only": True,
        "db_writes": 0,
    }


def render_activation_brief_markdown(brief: dict[str, Any]) -> str:
    activation = brief["activation"]
    center = activation["center_capsule"]
    endpoint = activation["endpoint_capsule"]
    role = activation["role_capsule"]
    mode = activation["mode_capsule"]
    proof = activation["proof_capsule"]
    action = activation["action_capsule"]
    lines = [
        f"# {ACTIVATION_BRIEF_TITLE}",
        "",
        "- Activation schema: available in JSON output; markdown hides the internal compatibility id.",
        f"- Endpoint: {brief['endpoint']}",
        f"- Read-only: {'yes' if brief.get('read_only') else 'no'}",
        "",
        "## Center Capsule",
        "",
        f"- Project identity: {center.get('project_identity')}",
        f"- Current-stage boundary: {center.get('current_stage_boundary') or 'None'}",
        f"- Role doctrine: {center.get('role_doctrine')}",
        f"- Term anchors: {', '.join(item['term'] for item in center.get('term_anchors') or []) or 'None'}",
        f"- Non-goals: {'; '.join(center.get('non_goals') or [])}",
        "",
        "## Endpoint Capsule",
        "",
        f"- Line: {endpoint.get('line')}",
        f"- Projection hash: {(endpoint.get('projection') or {}).get('projection_hash') or 'None'}",
        f"- Stored projection hash: {(endpoint.get('projection') or {}).get('stored_projection_hash') or 'None'}",
        f"- Projection stale: {'yes' if (endpoint.get('projection') or {}).get('stale') else 'no'}",
        f"- Active obligations: {endpoint.get('active_obligation_count', 0)}",
        f"- Detail ref: {endpoint.get('detail_ref')}",
    ]
    board_counts = ((endpoint.get("active_scope_board") or {}).get("counts") or {})
    if board_counts:
        lines.append(
            "- Active scope board: "
            + ", ".join(
                [
                    f"continuations={board_counts.get('continuations', 0)}",
                    f"successors={board_counts.get('successors', 0)}",
                    f"replaced={board_counts.get('superseded_or_replaced', 0)}",
                    f"forks={board_counts.get('forks', 0)}",
                    f"independent={board_counts.get('independent_roots', 0)}",
                    f"unbound={board_counts.get('unbound_predecessors', 0)}",
                ]
            )
        )
    lines.extend(
        [
            "",
            "## Readiness",
            "",
            f"- Closeout ready: {'yes' if (endpoint.get('readiness') or {}).get('closeout_ready') else 'no'}",
            f"- Execution ready: {'yes' if (endpoint.get('readiness') or {}).get('execution_ready') else 'no'}",
            f"- Blocking reason: {(endpoint.get('readiness') or {}).get('blocking_reason') or 'None'}",
            f"- Hidden blocker refs: {len((endpoint.get('readiness') or {}).get('hidden_blocking_refs') or [])}",
            f"- Next safe action: {(endpoint.get('readiness') or {}).get('next_safe_action') or 'None'}",
            f"- Authority boundary: {(endpoint.get('readiness') or {}).get('authority_boundary') or 'None'}",
            "",
            "## Role Capsule",
            "",
            f"- Role: {role.get('role')}",
            f"- Authority: {role.get('authority')}",
            f"- Current-project governance write authorized: {'yes' if role.get('current_project_governance_write_authorized') else 'no'}",
            f"- Forbidden actions: {', '.join(role.get('forbidden_actions') or []) or 'None'}",
            "",
            "## Mode Capsule",
            "",
            f"- Mode: {mode.get('mode')}",
            f"- Contract: {(mode.get('contract') or {}).get('summary') or mode.get('side_effect_boundary')}",
            f"- Creates run: {'yes' if (mode.get('contract') or {}).get('creates_run') else 'no'}",
            f"- Creates change set: {'yes' if (mode.get('contract') or {}).get('creates_change_set') else 'no'}",
            "",
            "## Proof Capsule",
            "",
            f"- Hard predicates: {proof.get('hard_predicate_count', 0)}",
            f"- Forbidden substitutes: {proof.get('forbidden_substitute_count', 0)}",
            f"- Task predicate links: {proof.get('task_predicate_link_count', 0)}",
            f"- Work chains: {proof.get('work_chain_count', 0)}",
            f"- Closed history hidden sources: {(proof.get('closed_history_summary') or {}).get('hidden_source_count', 0)}",
            f"- Full history detail ref: {(proof.get('closed_history_summary') or {}).get('detail_ref')}",
            "",
            "## Action Capsule",
            "",
            f"- Next safe action: {action.get('next_safe_action')}",
            f"- Backing ledger: {action.get('backing_ledger_ref')}",
            "",
            "## Lineage Anchors",
            "",
        ]
    )
    for item in activation.get("lineage_anchors") or []:
        lines.append(f"- {item['endpoint']}: {item['relation']} ({item['detail_ref']})")
    return "\n".join(lines).rstrip() + "\n"


def cmd_endpoint_brief(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    _, role_error = normalize_role(args.role)
    if role_error:
        print_json({"ok": False, "read_only": True, "error": role_error})
        return 2
    conn = connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
    brief = activation_brief_payload(
        conn,
        endpoint_name,
        role=args.role,
        mode=args.mode,
        tasks=args.task,
        checks=args.check,
        work_chain=args.work_chain,
    )
    if args.markdown:
        print_text(render_activation_brief_markdown(brief), end="")
    else:
        print_json(brief)
    return 0

def query_endpoint(conn: sqlite3.Connection, endpoint_name: str) -> sqlite3.Row:
    endpoint = conn.execute(
        """
        SELECT e.*, b.body AS current_body, b.node_id AS current_body_node_id,
               b.id AS current_body_id, b.created_at AS body_created_at,
               bn.props AS current_body_props
        FROM endpoints e
        LEFT JOIN endpoint_bodies b ON b.id = e.current_body_id
        LEFT JOIN nodes bn ON bn.id = b.node_id
        WHERE e.name = ?
        """,
        (endpoint_name,),
    ).fetchone()
    if not endpoint:
        raise SystemExit(f"endpoint not found: {endpoint_name}")
    return endpoint

def endpoint_scope_facts(conn: sqlite3.Connection, endpoint: sqlite3.Row) -> dict[str, Any]:
    root_node_id = endpoint["root_node_id"]
    contract = None
    scope_kind = "rootless"
    tasks: list[sqlite3.Row] = []
    checks: list[sqlite3.Row] = []
    if root_node_id:
        contract = conn.execute("SELECT * FROM scope_contracts WHERE node_id = ?", (root_node_id,)).fetchone()
    if contract:
        scope_kind = "scope_contract"
        tasks = conn.execute(
            """
            SELECT t.*, n.label
            FROM tasks t
            JOIN nodes n ON n.id = t.node_id
            WHERE t.contract_id = ?
            ORDER BY t.closed_at IS NOT NULL, n.created_at ASC, t.id ASC
            """,
            (contract["id"],),
        ).fetchall()
    elif root_node_id:
        root_task = conn.execute(
            """
            SELECT t.*, n.label
            FROM tasks t
            JOIN nodes n ON n.id = t.node_id
            WHERE t.node_id = ?
            """,
            (root_node_id,),
        ).fetchone()
        if root_task:
            scope_kind = "task"
            tasks = [root_task]
            seen_task_ids = {str(root_task["id"])}
            frontier = [str(root_task["id"])]
            while frontier:
                placeholders = ",".join("?" for _ in frontier)
                descendants = conn.execute(
                    f"""
                    SELECT t.*, n.label
                    FROM tasks t
                    JOIN nodes n ON n.id = t.node_id
                    WHERE t.parent_task_id IN ({placeholders})
                    ORDER BY t.closed_at IS NOT NULL, n.created_at ASC, t.id ASC
                    """,
                    frontier,
                ).fetchall()
                frontier = []
                for task in descendants:
                    task_id = str(task["id"])
                    if task_id in seen_task_ids:
                        continue
                    seen_task_ids.add(task_id)
                    frontier.append(task_id)
                    tasks.append(task)
        else:
            scope_kind = "node"
    endpoint_run_tasks = conn.execute(
        """
        SELECT t.*, n.label
        FROM edges endpoint_edge
        JOIN agent_runs ar ON ar.node_id = endpoint_edge.from_node_id
        JOIN edges executes_edge ON executes_edge.from_node_id = ar.node_id
        JOIN tasks t ON t.node_id = executes_edge.to_node_id
        JOIN nodes n ON n.id = t.node_id
        WHERE endpoint_edge.type = 'APPLIES_TO'
          AND endpoint_edge.to_node_id = ?
          AND executes_edge.type = 'EXECUTES'
        ORDER BY t.closed_at IS NOT NULL, n.created_at ASC, t.id ASC
        """,
        (endpoint["node_id"],),
    ).fetchall()
    seen_task_ids = {str(row["id"]) for row in tasks}
    for task in endpoint_run_tasks:
        if str(task["id"]) not in seen_task_ids:
            tasks.append(task)
            seen_task_ids.add(str(task["id"]))
    task_ids = [str(row["id"]) for row in tasks]
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        checks = conn.execute(
            f"""
            SELECT ac.*, n.label, en.type AS closed_by_type, en.label AS closed_by_label
            FROM acceptance_checks ac
            JOIN nodes n ON n.id = ac.node_id
            LEFT JOIN nodes en ON en.id = ac.closed_by_node_id
            WHERE ac.task_id IN ({placeholders})
            ORDER BY ac.closed_at IS NOT NULL, n.created_at ASC, ac.id ASC
            """,
            task_ids,
        ).fetchall()
    target_node_ids = [str(endpoint["node_id"])]
    if root_node_id:
        target_node_ids.append(str(root_node_id))
    target_node_ids.extend(str(row["node_id"]) for row in tasks)
    target_node_ids.extend(str(row["node_id"]) for row in checks)
    return {
        "contract": contract,
        "scope_kind": scope_kind,
        "tasks": tasks,
        "checks": checks,
        "task_ids": task_ids,
        "target_node_ids": list(dict.fromkeys(target_node_ids)),
    }

def parent_endpoints_for_child(conn: sqlite3.Connection, child_endpoint_node_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT parent_ep.*
        FROM edges e
        JOIN endpoints parent_ep ON parent_ep.node_id = e.from_node_id
        WHERE e.type = 'CHAIN_CHILD'
          AND e.to_node_id = ?
          AND parent_ep.archived_at IS NULL
        ORDER BY parent_ep.created_at ASC, parent_ep.name ASC
        """,
        (child_endpoint_node_id,),
    ).fetchall()

def inherited_active_blockers_for_endpoint(
    conn: sqlite3.Connection,
    endpoint: sqlite3.Row,
    scope: dict[str, Any],
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    parent_endpoints = parent_endpoints_for_child(conn, str(endpoint["node_id"]))
    if not parent_endpoints:
        return []
    parent_node_ids = [str(row["node_id"]) for row in parent_endpoints]
    child_target_node_ids = [
        str(node_id)
        for node_id in scope["target_node_ids"]
        if str(node_id) != str(endpoint["node_id"])
    ]
    if not child_target_node_ids:
        return []
    parent_placeholders = ",".join("?" for _ in parent_node_ids)
    target_placeholders = ",".join("?" for _ in child_target_node_ids)
    inactive_states = sorted(INACTIVE_SEMANTIC_STATES)
    inactive_placeholders = ",".join("?" for _ in inactive_states)
    rows = conn.execute(
        f"""
        SELECT n.id, n.type, n.label, n.summary, n.created_at, n.updated_at, n.props,
               parent_ep.name AS inherited_from_endpoint,
               parent_ep.node_id AS inherited_from_endpoint_node_id,
               target_edge.to_node_id AS inherited_target_node_id
        FROM nodes n
        JOIN edges parent_edge ON parent_edge.from_node_id = n.id
        JOIN endpoints parent_ep ON parent_ep.node_id = parent_edge.to_node_id
        JOIN edges target_edge ON target_edge.from_node_id = n.id
        WHERE n.type = 'audit_finding'
          AND {active_node_clause("n")}
          AND parent_edge.type = 'APPLIES_TO'
          AND parent_edge.to_node_id IN ({parent_placeholders})
          AND target_edge.type = 'APPLIES_TO'
          AND target_edge.to_node_id IN ({target_placeholders})
          AND NOT EXISTS (
              SELECT 1
              FROM semantic_items si
              WHERE si.node_id = n.id
                AND si.current_state IN ({inactive_placeholders})
          )
        ORDER BY n.created_at DESC, n.id DESC
        LIMIT ?
        """,
        [*parent_node_ids, *child_target_node_ids, *inactive_states, limit],
    ).fetchall()
    blockers: dict[str, dict[str, Any]] = {}
    for row in rows:
        finding_id = str(row["id"])
        item = blockers.setdefault(
            finding_id,
            {
                "id": row["id"],
                "type": row["type"],
                "label": row["label"],
                "summary": row["summary"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "props": row["props"],
                "inherited_active_blocker": True,
                "inherited_from_endpoints": [],
                "inherited_from_endpoint_node_ids": [],
                "inherited_target_node_ids": [],
                "inheritance_reason": "active audit_finding applies to parent endpoint and to this endpoint scope",
            },
        )
        if row["inherited_from_endpoint"] not in item["inherited_from_endpoints"]:
            item["inherited_from_endpoints"].append(row["inherited_from_endpoint"])
        if row["inherited_from_endpoint_node_id"] not in item["inherited_from_endpoint_node_ids"]:
            item["inherited_from_endpoint_node_ids"].append(row["inherited_from_endpoint_node_id"])
        if row["inherited_target_node_id"] not in item["inherited_target_node_ids"]:
            item["inherited_target_node_ids"].append(row["inherited_target_node_id"])
    return list(blockers.values())

def query_nodes_applying_to(
    conn: sqlite3.Connection,
    *,
    node_types: set[str],
    target_node_ids: list[str],
    limit: int = 20,
    active_lifecycle_only: bool = False,
) -> list[sqlite3.Row]:
    if not target_node_ids:
        return []
    target_placeholders = ",".join("?" for _ in target_node_ids)
    type_placeholders = ",".join("?" for _ in node_types)
    lifecycle_clause = ""
    inactive_states = sorted(INACTIVE_SEMANTIC_STATES)
    params: list[Any] = [*sorted(node_types), *target_node_ids]
    if active_lifecycle_only:
        state_placeholders = ",".join("?" for _ in inactive_states)
        lifecycle_clause = (
            "AND NOT EXISTS ("
            "SELECT 1 FROM semantic_items si "
            "WHERE si.node_id = n.id "
            f"AND si.current_state IN ({state_placeholders})"
            ")"
        )
        params.extend(inactive_states)
    params.append(limit)
    return conn.execute(
        f"""
        SELECT DISTINCT n.id, n.type, n.label, n.summary, n.created_at, n.updated_at, n.props
        FROM nodes n
        JOIN edges e ON e.from_node_id = n.id
        WHERE n.type IN ({type_placeholders})
          AND {active_node_clause("n")}
          AND e.type = 'APPLIES_TO'
          AND e.to_node_id IN ({target_placeholders})
          {lifecycle_clause}
        ORDER BY n.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

def endpoint_projection_facts(status: dict[str, Any]) -> dict[str, Any]:
    return endpoint_projection.endpoint_projection_facts(status)

def endpoint_projection_hash(status: dict[str, Any]) -> str:
    return sha256_text(json_dumps(endpoint_projection_facts(status)))

def endpoint_latest_fact_at(status: dict[str, Any]) -> str | None:
    return endpoint_projection.endpoint_latest_fact_at(status)


def active_scope_board(conn: sqlite3.Connection, *, endpoint: sqlite3.Row, scope: dict[str, Any], tasks: list[sqlite3.Row]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "continuations": [],
        "successors": [],
        "superseded_or_replaced": [],
        "forks": [],
        "independent_roots": [],
        "unbound_predecessors": [],
    }
    task_node_ids = [str(row["node_id"]) for row in tasks]
    task_by_node = {str(row["node_id"]): row for row in tasks}
    for row in tasks:
        item = {
            "task_id": row["id"],
            "node_id": row["node_id"],
            "label": row["label"],
            "parent_task_id": row["parent_task_id"],
            "detail_ref": f"graph detail --node {row['node_id']}",
        }
        if row["parent_task_id"]:
            buckets["continuations"].append({**item, "relation": "parent_task"})
        else:
            buckets["independent_roots"].append({**item, "relation": "no_parent_task"})
    target_node_ids = list(dict.fromkeys([*scope.get("target_node_ids", []), *task_node_ids]))
    if target_node_ids:
        placeholders = ",".join("?" for _ in target_node_ids)
        for edge in conn.execute(
            f"""
            SELECT e.type, e.from_node_id, e.to_node_id, e.reason,
                   nf.type AS from_type, nf.label AS from_label,
                   nt.type AS to_type, nt.label AS to_label
            FROM edges e
            JOIN nodes nf ON nf.id = e.from_node_id
            JOIN nodes nt ON nt.id = e.to_node_id
            WHERE (e.from_node_id IN ({placeholders}) OR e.to_node_id IN ({placeholders}))
              AND e.type IN ('SUPERSEDES', 'RESOLVES', 'CHAIN_CHILD', 'APPLIES_TO', 'DERIVED_FROM')
            ORDER BY e.created_at DESC
            LIMIT 120
            """,
            [*target_node_ids, *target_node_ids],
        ).fetchall():
            relation = str(edge["type"])
            entry = {
                "relation": relation,
                "from_node_id": edge["from_node_id"],
                "from_label": edge["from_label"],
                "to_node_id": edge["to_node_id"],
                "to_label": edge["to_label"],
                "reason": edge["reason"],
                "detail_ref": f"graph detail --node {edge['from_node_id']}",
            }
            if relation in {"SUPERSEDES", "RESOLVES"}:
                buckets["superseded_or_replaced"].append(entry)
            elif relation == "CHAIN_CHILD":
                buckets["successors"].append(entry)
            elif relation == "APPLIES_TO" and (edge["from_type"] in {"scope_change", "work_note"} or edge["to_type"] in {"scope_change", "work_note"}):
                buckets["continuations"].append(entry)
            elif relation == "DERIVED_FROM" and (edge["from_type"] == "source_item" or edge["to_type"] == "source_item"):
                props_row = edge["from_node_id"] if edge["from_type"] == "source_item" else edge["to_node_id"]
                source = conn.execute("SELECT props FROM nodes WHERE id = ?", (props_row,)).fetchone()
                props = props_dict(source["props"]) if source else {}
                status = props.get("status")
                destination = props.get("graph_destination") if isinstance(props.get("graph_destination"), dict) else {}
                if status in {"absorbed", "superseded", "indirectly_dissolved"}:
                    buckets["superseded_or_replaced"].append({**entry, "source_item_status": status})
                elif destination.get("kind") in {"fork", "fork_variant"}:
                    buckets["forks"].append(entry)
    seen_with_relation = {
        str(item.get("from_node_id") or item.get("node_id"))
        for key in ("continuations", "successors", "superseded_or_replaced", "forks")
        for item in buckets[key]
    } | {
        str(item.get("to_node_id"))
        for key in ("continuations", "successors", "superseded_or_replaced", "forks")
        for item in buckets[key]
        if item.get("to_node_id")
    }
    for node_id, row in task_by_node.items():
        if node_id not in seen_with_relation and not row["parent_task_id"]:
            continue
        if row["parent_task_id"] and node_id not in seen_with_relation:
            buckets["unbound_predecessors"].append(
                {
                    "task_id": row["id"],
                    "node_id": node_id,
                    "label": row["label"],
                    "relation": "parent_task_id_without_graph_edge",
                    "detail_ref": f"graph detail --node {node_id}",
                }
            )
    return {
        "buckets": {key: value[:12] for key, value in buckets.items()},
        "counts": {key: len(value) for key, value in buckets.items()},
        "source": "derived from existing graph edges, task parentage, lifecycle/source props, and endpoint scope",
        "read_only": True,
    }

def endpoint_status_payload(conn: sqlite3.Connection, endpoint_name: str, *, include_chain: bool = True, repo: Path | None = None) -> dict[str, Any]:
    endpoint = query_endpoint(conn, endpoint_name)
    endpoint_node_id = str(endpoint["node_id"])
    warnings: list[str] = []
    root_node = None
    if endpoint["root_node_id"]:
        root_node = row_to_dict(require_node(conn, str(endpoint["root_node_id"]), "endpoint root node"))
    scope = endpoint_scope_facts(conn, endpoint)
    contract = scope["contract"]
    scope_kind = scope.get("scope_kind")
    tasks = scope["tasks"]
    check_rows = scope["checks"]
    task_ids = scope["task_ids"]
    target_node_ids = scope["target_node_ids"]
    if not contract and scope_kind != "task":
        warnings.append("Endpoint has no scope_contract root; task/check/evidence workbench sections are empty until root_node_id is set.")
    unlinked_task_rows: list[sqlite3.Row] = []
    unlinked_check_rows: list[sqlite3.Row] = []
    unlinked_evidence: list[sqlite3.Row] = []
    if contract:
        unlinked_task_rows = conn.execute(
            """
            SELECT t.*, n.label
            FROM tasks t
            JOIN nodes n ON n.id = t.node_id
            WHERE t.contract_id IS NULL
              AND t.created_from_node_id = ?
            ORDER BY t.closed_at IS NOT NULL, n.created_at ASC, t.id ASC
            """,
            (contract["source_node_id"],),
        ).fetchall()
        if unlinked_task_rows:
            warnings.append(
                "Endpoint has tasks derived from the same scope source but not linked to its scope_contract; "
                "they are shown under unlinked scope candidates and do not count as endpoint-scoped closure until linked with --contract."
            )
            unlinked_task_ids = [str(row["id"]) for row in unlinked_task_rows]
            unlinked_placeholders = ",".join("?" for _ in unlinked_task_ids)
            unlinked_check_rows = conn.execute(
                f"""
                SELECT ac.*, n.label, en.type AS closed_by_type, en.label AS closed_by_label
                FROM acceptance_checks ac
                JOIN nodes n ON n.id = ac.node_id
                LEFT JOIN nodes en ON en.id = ac.closed_by_node_id
                WHERE ac.task_id IN ({unlinked_placeholders})
                ORDER BY ac.closed_at IS NOT NULL, n.created_at ASC, ac.id ASC
                """,
                unlinked_task_ids,
            ).fetchall()
            unlinked_evidence_ids = []
            unlinked_evidence_ids.extend(str(row["closed_by_node_id"]) for row in unlinked_task_rows if row["closed_by_node_id"])
            unlinked_evidence_ids.extend(str(row["closed_by_node_id"]) for row in unlinked_check_rows if row["closed_by_node_id"])
            unlinked_evidence_ids = current_evidence_ids(conn, list(dict.fromkeys(unlinked_evidence_ids)))
            if unlinked_evidence_ids:
                unlinked_evidence_placeholders = ",".join("?" for _ in unlinked_evidence_ids)
                unlinked_evidence = conn.execute(
                    f"""
                    SELECT id, type, label, summary, created_at, props
                    FROM nodes
                    WHERE id IN ({unlinked_evidence_placeholders})
                    ORDER BY created_at DESC
                    """,
                    unlinked_evidence_ids,
                ).fetchall()
    evidence_ids: list[str] = []
    evidence_ids.extend(str(row["closed_by_node_id"]) for row in tasks if row["closed_by_node_id"])
    evidence_ids.extend(str(row["closed_by_node_id"]) for row in check_rows if row["closed_by_node_id"])
    evidence_ids.extend(explicit_active_direct_evidence_ids(conn, target_node_ids))
    evidence_ids = current_evidence_ids(conn, list(dict.fromkeys(evidence_ids)))
    if evidence_ids:
        evidence_placeholders = ",".join("?" for _ in evidence_ids)
        evidence = conn.execute(
            f"""
            SELECT id, type, label, summary, created_at, props
            FROM nodes
            WHERE id IN ({evidence_placeholders})
            ORDER BY created_at DESC
            """,
            evidence_ids,
        ).fetchall()
    else:
        evidence = []
    audit_findings = query_nodes_applying_to(
        conn,
        node_types={"audit_finding"},
        target_node_ids=target_node_ids,
        active_lifecycle_only=True,
    )
    direct_audit_findings = [row_to_dict(row) for row in audit_findings]
    inherited_active_blockers = inherited_active_blockers_for_endpoint(conn, endpoint, scope)
    inherited_by_id = {str(item["id"]): item for item in inherited_active_blockers}
    combined_audit_findings = []
    for finding in direct_audit_findings:
        inherited = inherited_by_id.get(str(finding["id"]))
        if inherited:
            finding = {**finding, **{key: value for key, value in inherited.items() if key.startswith("inherited_") or key == "inheritance_reason"}}
        combined_audit_findings.append(finding)
    direct_ids = {str(item["id"]) for item in combined_audit_findings}
    combined_audit_findings.extend(item for item in inherited_active_blockers if str(item["id"]) not in direct_ids)
    semantic_rows = query_nodes_applying_to(
        conn,
        node_types={"unresolved_question", "scope_change", "defer_decision", "assumption"},
        target_node_ids=target_node_ids,
        active_lifecycle_only=True,
    )
    work_notes = query_nodes_applying_to(
        conn,
        node_types={"work_note"},
        target_node_ids=target_node_ids,
        limit=10,
        active_lifecycle_only=True,
    )
    deferred_task_node_ids = {
        str(row["node_id"])
        for row in tasks
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
            (row["node_id"],),
        ).fetchone()
    }
    deferred_task_ids = {str(row["id"]) for row in tasks if str(row["node_id"]) in deferred_task_node_ids}
    active_tasks = [row_to_dict(row) for row in tasks if row["closed_by_node_id"] is None and row["id"] not in deferred_task_ids]
    deferred_tasks = [row_to_dict(row) for row in tasks if row["closed_by_node_id"] is None and row["id"] in deferred_task_ids]
    open_checks = [row_to_dict(row) for row in check_rows if row["closed_by_node_id"] is None and row["task_id"] not in deferred_task_ids]
    deferred_checks = [row_to_dict(row) for row in check_rows if row["closed_by_node_id"] is None and row["task_id"] in deferred_task_ids]
    closed_checks = [row_to_dict(row) for row in check_rows if row["closed_by_node_id"] is not None]
    semantic_projection = semantic_lifecycle_projection(conn, target_node_ids)
    recent_discussions = discussion_rows(conn, endpoint_name, include_reviewed=True, limit=10)
    discussion_status_rows = conn.execute(
        """
        SELECT ds.status, COUNT(*) AS count
        FROM discussion_segments ds
        WHERE ds.endpoint_id = ?
        GROUP BY ds.status
        """,
        (endpoint["id"],),
    ).fetchall()
    discussion_status_counts = {str(row["status"]): int(row["count"]) for row in discussion_status_rows}
    chain_children = endpoint_chain_children(conn, endpoint_name) if include_chain else []
    chain_active = [child for child in chain_children if child["active_obligation_count"]]
    review_state = load_review_state(repo, endpoint_name) if repo is not None else None
    review_material = review_material_obligations(review_state)
    board = active_scope_board(conn, endpoint=endpoint, scope=scope, tasks=tasks)
    payload = {
        "endpoint": row_to_dict(endpoint),
        "warnings": warnings,
        "root_node": root_node,
        "scope_contract": row_to_dict(contract),
        "scope_kind": scope_kind,
        "tasks": [row_to_dict(row) for row in tasks],
        "current_tasks": active_tasks,
        "deferred_tasks": deferred_tasks,
        "open_checks": open_checks,
        "deferred_checks": deferred_checks,
        "closed_checks": closed_checks,
        "evidence": [row_to_dict(row) for row in evidence],
        "recent_audit_findings": combined_audit_findings,
        "inherited_active_blockers": inherited_active_blockers,
        "unresolved": [row_to_dict(row) for row in semantic_rows if row["type"] == "unresolved_question"],
        "scope_changes": [row_to_dict(row) for row in semantic_rows if row["type"] == "scope_change"],
        "defer_decisions": [row_to_dict(row) for row in semantic_rows if row["type"] == "defer_decision"],
        "assumptions": [row_to_dict(row) for row in semantic_rows if row["type"] == "assumption"],
        "recent_work_notes": [row_to_dict(row) for row in work_notes],
        "unlinked_scope_candidates": {
            "reason": "same source_node_id as endpoint scope_contract but task.contract_id is null",
            "tasks": [row_to_dict(row) for row in unlinked_task_rows],
            "checks": [row_to_dict(row) for row in unlinked_check_rows],
            "evidence": [row_to_dict(row) for row in unlinked_evidence],
        },
        "semantic_projection": semantic_projection,
        "discussion_brief": {
            "unreviewed_count": discussion_status_counts.get("unreviewed", 0),
            "reviewed_count": discussion_status_counts.get("reviewed", 0),
            "consumed_count": discussion_status_counts.get("consumed", 0),
            "extracted_count": discussion_status_counts.get("extracted", 0),
            "superseded_count": discussion_status_counts.get("superseded", 0),
            "status_counts": discussion_status_counts,
        },
        "chain_children": chain_children,
        "chain_brief": {
            "child_count": len(chain_children),
            "active_child_count": len(chain_active),
            "active_children": chain_active,
            "can_close_with_children": not chain_active,
        },
        "recent_discussions": [row_to_dict(row) for row in recent_discussions],
        "review_state": review_state,
        "review_material": review_material,
        "active_scope_board": board,
        "completion_rule": "No status field is computed; completion is inferred from evidence-backed closed acceptance checks and tasks.",
    }
    body_props = props_dict(endpoint["current_body_props"])
    projection_hash = endpoint_projection_hash(payload)
    last_fact_at = endpoint_latest_fact_at(payload)
    stored_hash = body_props.get("projection_hash")
    source_kind = body_props.get("source_kind") or "manual"
    stale = bool(stored_hash and stored_hash != projection_hash)
    if source_kind != "projection":
        warnings.append("Current endpoint body is not an endpoint refresh projection; run endpoint refresh for a DB-backed projection.")
    if stale:
        warnings.append("Current endpoint body projection_hash is stale; run endpoint refresh.")
    payload["projection"] = {
        "source_kind": source_kind,
        "generated_by": body_props.get("generated_by"),
        "stored_projection_hash": stored_hash,
        "projection_hash": projection_hash,
        "last_fact_at": last_fact_at,
        "generated_at": body_props.get("generated_at"),
        "stale": stale,
    }
    return payload

def render_endpoint_status_markdown(status: dict[str, Any]) -> str:
    endpoint = status["endpoint"]
    lines = [
        f"Endpoint workbench: {endpoint['name']}",
        "",
        f"Description: {endpoint.get('description') or 'No description recorded.'}",
        f"Root node: {endpoint.get('root_node_id') or 'None'}",
        f"Projection source: {status['projection']['source_kind']}",
        f"Projection hash: {status['projection']['projection_hash']}",
        f"Latest relevant fact at: {status['projection'].get('last_fact_at') or 'None'}",
        f"Stale: {'yes' if status['projection'].get('stale') else 'no'}",
    ]
    contract = status.get("scope_contract")
    if contract:
        lines.append(f"Scope contract: {contract['id']}")
    if status.get("warnings"):
        lines.extend(["", "Warnings:"])
        for warning in status["warnings"]:
            lines.append(f"- {warning}")
    board_counts = ((status.get("active_scope_board") or {}).get("counts") or {})
    if board_counts:
        lines.extend(["", "Active Scope Board:"])
        lines.append(
            "- "
            + ", ".join(
                [
                    f"continuations={board_counts.get('continuations', 0)}",
                    f"successors={board_counts.get('successors', 0)}",
                    f"replaced={board_counts.get('superseded_or_replaced', 0)}",
                    f"forks={board_counts.get('forks', 0)}",
                    f"independent={board_counts.get('independent_roots', 0)}",
                    f"unbound={board_counts.get('unbound_predecessors', 0)}",
                ]
            )
        )
    lines.extend(["", "Current tasks:"])
    if status["current_tasks"]:
        for task in status["current_tasks"]:
            lines.append(f"- {task['id']}: {task['task_body']}")
    else:
        lines.append("- None")
    lines.extend(["", "Deferred tasks:"])
    if status["deferred_tasks"]:
        for task in status["deferred_tasks"]:
            lines.append(f"- {task['id']}: {task['task_body']}")
    else:
        lines.append("- None")
    lines.extend(["", "Open checks:"])
    if status["open_checks"]:
        for check in status["open_checks"]:
            lines.append(f"- {check['id']} ({check['expected_evidence_type']}): {check['check_body']}")
    else:
        lines.append("- None")
    lines.extend(["", "Review material:"])
    if status.get("review_material"):
        for item in status["review_material"]:
            lines.append(
                f"- {item['id']} [{item.get('state_kind') or 'review_material'}]: "
                f"{item.get('summary') or ''}"
            )
    else:
        lines.append("- None")
    lines.extend(["", "Deferred checks:"])
    if status["deferred_checks"]:
        for check in status["deferred_checks"]:
            lines.append(f"- {check['id']} ({check['expected_evidence_type']}): {check['check_body']}")
    else:
        lines.append("- None")
    lines.extend(["", "Closed checks:"])
    if status["closed_checks"]:
        for check in status["closed_checks"]:
            lines.append(f"- {check['id']} by {check['closed_by_node_id']}: {check['check_body']}")
    else:
        lines.append("- None")
    unlinked = status.get("unlinked_scope_candidates") or {}
    if unlinked.get("tasks") or unlinked.get("checks") or unlinked.get("evidence"):
        lines.extend(["", "Unlinked scope candidates:"])
        lines.append(f"- Reason: {unlinked.get('reason')}")
        for task in unlinked.get("tasks") or []:
            state = "closed" if task.get("closed_by_node_id") else "open"
            lines.append(f"- task {task['id']} ({state}): {task['task_body']}")
        for check in unlinked.get("checks") or []:
            state = f"closed by {check.get('closed_by_node_id')}" if check.get("closed_by_node_id") else "open"
            lines.append(f"- check {check['id']} ({state}, {check['expected_evidence_type']}): {check['check_body']}")
        for evidence_item in unlinked.get("evidence") or []:
            lines.append(f"- evidence {evidence_item['id']} [{evidence_item['type']}]: {evidence_item.get('label') or evidence_item.get('summary') or ''}")
    for title, key in [
        ("Evidence", "evidence"),
        ("Recent audit findings", "recent_audit_findings"),
        ("Unresolved", "unresolved"),
        ("Scope changes", "scope_changes"),
        ("Defer decisions", "defer_decisions"),
        ("Assumptions", "assumptions"),
        ("Recent work notes", "recent_work_notes"),
        ("Recent discussions", "recent_discussions"),
    ]:
        lines.extend(["", f"{title}:"])
        items = status[key]
        if items:
            for item in items:
                item_type = item.get("type") or item.get("event_type") or "discussion_segment"
                lines.append(f"- {item['id']} [{item_type}]: {item.get('label') or item.get('title') or item.get('summary') or ''}")
        else:
            lines.append("- None")
    lines.extend(["", status["completion_rule"]])
    return "\n".join(lines).rstrip() + "\n"

def cmd_endpoint_status(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
    status = endpoint_status_payload(conn, endpoint_name, repo=repo)
    if args.markdown:
        print_text(render_endpoint_status_markdown(status), end="")
    else:
        print_json({"ok": True, **status})
    return 0

def refresh_endpoint_projection(
    conn: sqlite3.Connection,
    endpoint_name: str,
    *,
    description: str | None = None,
    root_node: str | None = None,
    from_node: str | None = None,
    repo: Path | None = None,
) -> dict[str, Any]:
    status = endpoint_status_payload(conn, endpoint_name, repo=repo)
    generated_at = now_iso()
    status["projection"].update(
        {
            "source_kind": "projection",
            "generated_by": "endpoint_refresh",
            "stored_projection_hash": status["projection"]["projection_hash"],
            "generated_at": generated_at,
            "stale": False,
        }
    )
    status["warnings"] = [
        warning
        for warning in status["warnings"]
        if not warning.startswith("Current endpoint body is not an endpoint refresh projection")
        and not warning.startswith("Current endpoint body projection_hash is stale")
    ]
    body = render_endpoint_status_markdown(status)
    return {
        "body": body,
        **upsert_endpoint_body(
            conn,
            endpoint_name=endpoint_name,
            body=body,
            description=description,
            root_node=root_node,
            from_node=from_node if from_node is not None else status["endpoint"].get("root_node_id"),
            body_props={
                "source_kind": "projection",
                "generated_by": "endpoint_refresh",
                "generated_at": generated_at,
                "projection_hash": status["projection"]["projection_hash"],
                "last_fact_at": status["projection"]["last_fact_at"],
            },
        ),
    }

def cmd_endpoint_refresh(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
    result = refresh_endpoint_projection(
        conn,
        description=args.description,
        root_node=args.root_node,
        endpoint_name=endpoint_name,
        repo=repo,
    )
    conn.commit()
    print_json({"ok": True, "endpoint": endpoint_name, **result})
    return 0

def doctor_add(buckets: dict[str, list[dict[str, Any]]], severity: str, code: str, message: str, recommendation: str, **extra: Any) -> None:
    buckets.setdefault(severity, []).append({"code": code, "message": message, "recommendation": recommendation, **extra})

def endpoint_agcp_doctor_findings(conn: sqlite3.Connection, endpoint_id: str) -> dict[str, Any]:
    return agcp_policy.endpoint_agcp_doctor_findings(
        conn,
        endpoint_id,
        endpoint_agcp_predicate_rows=endpoint_agcp_predicate_rows,
        predicate_link_rows=predicate_link_rows,
        endpoint_source_nondowngrade_audit=endpoint_source_nondowngrade_audit,
        row_to_dict=row_to_dict,
    )

def endpoint_doctor_payload(
    conn: sqlite3.Connection,
    repo: Path,
    endpoint_name: str,
    *,
    strict_closeout: bool = False,
    read_only: bool = True,
) -> dict[str, Any]:
    status = endpoint_status_payload(conn, endpoint_name, repo=repo)
    buckets: dict[str, list[dict[str, Any]]] = {"P0": [], "P1": [], "P2": []}
    endpoint = status["endpoint"]
    if not endpoint.get("root_node_id"):
        doctor_add(buckets, "P0", "rootless_endpoint", "Endpoint has no root_node_id.", "Bind a scope_contract root with endpoint bind-root, or recreate as explicitly rootless.")
    if status["projection"]["source_kind"] != "projection":
        doctor_add(buckets, "P1", "non_projection_endpoint_body", "Current endpoint body is not an endpoint refresh projection.", "Run endpoint refresh before handoff.")
    if status["projection"].get("stale"):
        doctor_add(buckets, "P0", "stale_endpoint_projection", "Stored endpoint projection hash does not match current DB facts.", "Run endpoint refresh and review the changed facts.")
    if status["open_checks"] or status["current_tasks"]:
        doctor_add(
            buckets,
            "P0" if strict_closeout else "P2",
            "open_obligations",
            "Endpoint has open tasks or acceptance checks.",
            "Close with evidence, or record defer/scope_change/unresolved if not active.",
            open_task_count=len(status["current_tasks"]),
            open_check_count=len(status["open_checks"]),
        )
    decision_notes = [
        item
        for item in status["recent_work_notes"]
        if props_dict(item).get("kind") == "needs_user_decision" or props_dict(item).get("active_obligation") is True
    ]
    active_children = (status.get("chain_brief") or {}).get("active_children") or []
    if active_children:
        doctor_add(
            buckets,
            "P0" if strict_closeout else "P2",
            "active_child_chain_obligations",
            "Endpoint has linked child chain endpoints with active obligations.",
            "Close or explicitly defer child chain obligations before umbrella closeout.",
            active_child_count=len(active_children),
            active_children=active_children,
        )
    review_material = status.get("review_material") or []
    if review_material:
        state = status.get("review_state") or {}
        code = "review_material_not_executed" if not state.get("reviewer_executed") else "review_material_not_adopted"
        doctor_add(
            buckets,
            "P0" if strict_closeout else "P2",
            code,
            "Endpoint has review material that is not yet reviewer-executed and controller-adopted.",
            "Run review record-return for reviewer output, then review adopt before using review material in closeout.",
            review_state=state,
            review_material=review_material,
        )
    inherited_blockers = status.get("inherited_active_blockers") or []
    if inherited_blockers:
        doctor_add(
            buckets,
            "P0" if strict_closeout else "P2",
            "inherited_active_blockers",
            "Endpoint inherits active audit blockers from a parent endpoint finding that targets this endpoint scope.",
            "Resolve, defer, or rescope the parent finding before treating this child endpoint as clean.",
            node_ids=[item["id"] for item in inherited_blockers],
            inherited_active_blockers=inherited_blockers,
        )
    agcp_findings = endpoint_agcp_doctor_findings(conn, endpoint["id"])
    if agcp_findings["unmapped_predicates"]:
        doctor_add(
            buckets,
            "P0" if strict_closeout else "P2",
            "hard_predicate_without_task_link",
            "Endpoint has active hard predicates that are not mapped to task/check proof links.",
            "Run work split to map each active hard predicate to the task/check that proves or guards it, or record a source-backed defer/scope change.",
            predicate_ids=[item["id"] for item in agcp_findings["unmapped_predicates"]],
            unmapped_predicates=agcp_findings["unmapped_predicates"],
        )
    active_missing_coverage = []
    overridden_missing_coverage = []
    for item in agcp_findings["closed_checks_missing_predicate_coverage"]:
        override = effective_predicate_coverage_override(
            conn,
            check_id=str(item["check_id"]),
            evidence_node_id=str(item["closed_by_node_id"]),
        )
        if override and override.get("effective"):
            overridden_missing_coverage.append({**item, "override": override})
        else:
            active_missing_coverage.append({**item, "override": override})
    if active_missing_coverage:
        doctor_add(
            buckets,
            "P0",
            "closed_check_missing_predicate_coverage",
            "A closed check is linked to hard predicates that lack passing coverage rows from its closure evidence.",
            "Replace or supplement the closure evidence with a predicate_coverage_matrix, or reopen/supersede the closure.",
            missing_coverage=active_missing_coverage,
            overridden_missing_coverage=overridden_missing_coverage,
        )
    if agcp_findings["source_non_downgrade_findings"]:
        doctor_add(
            buckets,
            "P0" if strict_closeout else "P2",
            "source_non_downgrade_findings",
            "Source-to-DB audit found hard source promises, required terms, enumerated items, or forbidden substitutes that do not survive into acceptance checks.",
            "Run work audit-source, preserve the source terms in task/check extraction, or record a source-backed scope_change/defer that applies to the downgraded target.",
            findings=agcp_findings["source_non_downgrade_findings"],
            source_promise_matrix=agcp_findings["source_non_downgrade_matrix"],
        )
    if strict_closeout and agcp_findings["non_accepting_reviews"]:
        doctor_add(
            buckets,
            "P0",
            "review_not_accepting_closeout",
            "Endpoint has review results that are partial, rejected, or need a user decision.",
            "Resolve review findings or submit a source-bound accepting review before closeout.",
            review_results=agcp_findings["non_accepting_reviews"],
        )
    if (
        strict_closeout
        and not status["current_tasks"]
        and not status["open_checks"]
        and not status["recent_audit_findings"]
        and not status["unresolved"]
        and not decision_notes
        and not active_children
        and not inherited_blockers
        and not status["closed_checks"]
        and not status["evidence"]
    ):
        doctor_add(
            buckets,
            "P0",
            "closeout_reality_no_evidence",
            "Strict closeout has no active blockers, but also no evidence-backed closure or failure-condition resolution history.",
            "Record evidence-backed closure, or a source-backed defer/scope/resolution record, before treating the endpoint as clean.",
        )
    if strict_closeout and status["recent_audit_findings"]:
        doctor_add(
            buckets,
            "P0",
            "active_audit_findings",
            "Endpoint has active audit findings.",
            "Resolve findings with source-backed evidence before strict closeout.",
            node_ids=[item["id"] for item in status["recent_audit_findings"]],
        )
    if strict_closeout and status["unresolved"]:
        doctor_add(
            buckets,
            "P0",
            "active_unresolved_questions",
            "Endpoint has active unresolved questions.",
            "Resolve the question or record a defer/scope change that removes it from active closeout.",
            node_ids=[item["id"] for item in status["unresolved"]],
        )
    if strict_closeout and decision_notes:
        doctor_add(
            buckets,
            "P0",
            "needs_user_decision",
            "Endpoint has active notes that require a user decision.",
            "Get the decision or record an explicit defer before strict closeout.",
            node_ids=[item["id"] for item in decision_notes],
        )
    for task in status["tasks"]:
        if task.get("closed_by_node_id"):
            open_for_task = [check["id"] for check in status["open_checks"] if check["task_id"] == task["id"]]
            if open_for_task:
                doctor_add(
                    buckets,
                    "P0",
                    "task_closed_with_open_checks",
                    f"Task {task['id']} is closed while checks remain open.",
                    "Reopen/fix the task closure or close remaining checks with evidence.",
                    task_id=task["id"],
                    open_checks=open_for_task,
                )
    for check in status["closed_checks"]:
        evidence_node_id = check.get("closed_by_node_id")
        evidence_node = conn.execute("SELECT id, type FROM nodes WHERE id = ?", (evidence_node_id,)).fetchone()
        if not evidence_node or evidence_node["type"] not in EVIDENCE_NODE_TYPES:
            doctor_add(
                buckets,
                "P0",
                "check_closed_by_non_evidence",
                f"Check {check['id']} is closed by a missing or non-evidence node.",
                "Close checks only with change_set/test_result/artifact/user_confirmation evidence.",
                check_id=check["id"],
                closed_by_node_id=evidence_node_id,
            )
        elif evidence_node["type"] not in expected_evidence_allowed(check.get("expected_evidence_type")):
            override = effective_evidence_type_override(
                conn,
                check_id=str(check["id"]),
                evidence_node_id=str(evidence_node_id),
            )
            if override and override.get("effective"):
                continue
            doctor_add(
                buckets,
                "P0",
                "evidence_type_mismatch",
                f"Check {check['id']} expected {check.get('expected_evidence_type')} but is closed by {evidence_node['type']}.",
                "Use matching evidence or close with an explicit override warning.",
                check_id=check["id"],
                evidence_node_id=evidence_node_id,
                override=override,
            )
    evidence_checks: list[dict[str, Any]] = []
    for row in evidence_nodes_for_endpoint(conn, endpoint_name):
        evidence_checks.extend(verify_evidence_row(repo, conn, row))
    for check in evidence_checks:
        if check["status"] in {"tampered", "missing_file", "missing_ref"}:
            doctor_add(
                buckets,
                "P0",
                "evidence_verify_failed",
                f"Evidence {check['node_id']} {check['label']} is {check['status']}.",
                "Restore the captured artifact or record replacement evidence.",
                evidence_check=check,
            )
    readiness = endpoint_readiness_diagnostic(
        status,
        endpoint_active_obligations(status),
        role="controller_agent",
        read_only=read_only,
        strict_closeout=strict_closeout,
    )
    for warning in readiness.get("warnings") or []:
        doctor_add(
            buckets,
            "P2",
            warning["code"],
            warning["message"],
            warning["recommendation"],
            task_id=warning.get("task_id"),
            closed_check_ids=warning.get("closed_check_ids") or [],
        )
    recommendations = [item["recommendation"] for severity in ["P0", "P1", "P2"] for item in buckets.get(severity, [])]
    return {
        "ok": not buckets["P0"] and not buckets["P1"],
        "endpoint": endpoint_name,
        "status_kind": "endpoint_strict_closeout_blocked" if (strict_closeout and (buckets["P0"] or buckets["P1"])) else "endpoint_diagnostic_clear",
        "runtime_status_kind": "postgres_runtime_connected",
        "schema_status_kind": "schema_checked_by_runtime_connection",
        "migration_status_kind": "not_checked_by_endpoint_doctor",
        "writability_status_kind": "read_only_diagnostic" if read_only else "controller_writeful_closeout_path",
        "next_schema_check_command": "python -m shujuan migrate status",
        "strict_closeout": strict_closeout,
        "readiness": readiness,
        "severity_buckets": buckets,
        "recommendations": list(dict.fromkeys(recommendations)),
        "projection": status["projection"],
        "agcp": agcp_findings,
    }

def cmd_endpoint_doctor(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    refresh = None
    read_only = bool(getattr(args, "read_only", False))
    conn = connect_read_only(repo) if read_only else connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
    if args.strict_closeout and not read_only:
        refresh = refresh_endpoint_projection(conn, endpoint_name, repo=repo)
        conn.commit()
    payload = endpoint_doctor_payload(conn, repo, endpoint_name, strict_closeout=args.strict_closeout, read_only=read_only)
    payload["read_only"] = read_only
    payload["refresh_policy"] = (
        "suppressed_by_read_only"
        if args.strict_closeout and read_only
        else "strict_closeout_refresh"
        if args.strict_closeout
        else "diagnostic_only"
    )
    payload["command_effects"] = endpoint_doctor_effects(strict_closeout=bool(args.strict_closeout), read_only=read_only)
    if refresh:
        payload["endpoint_refresh"] = {key: value for key, value in refresh.items() if key != "body"}
    print_json(payload)
    return 0 if payload["ok"] or args.allow_fail else 1

def has_outgoing_edge(conn: sqlite3.Connection, node_id: str | None, edge_type: str) -> bool:
    if not node_id:
        return False
    return bool(conn.execute("SELECT 1 FROM edges WHERE from_node_id = ? AND type = ? LIMIT 1", (node_id, edge_type)).fetchone())

def test_result_has_trusted_argv(props: dict[str, Any]) -> bool:
    return (
        isinstance(props.get("argv"), list)
        and bool(props.get("argv"))
        and bool(props.get("command"))
        and bool(props.get("stdout_ref"))
        and bool(props.get("stderr_ref"))
        and props.get("predicate_ok") is True
    )

def readiness_requirement(name: str, ok: bool, message: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "message": message, **extra}

def new_project_readiness_payload(conn: sqlite3.Connection, repo: Path, endpoint_name: str) -> dict[str, Any]:
    schema = inspect_schema(conn)
    status = endpoint_status_payload(conn, endpoint_name, repo=repo)
    scope = status.get("scope_contract")
    tasks = status.get("tasks") or []
    all_checks = [*status.get("open_checks", []), *status.get("closed_checks", []), *status.get("deferred_checks", [])]
    source_backed_nodes = []
    if scope:
        source_backed_nodes.append(("scope_contract", scope.get("node_id")))
    source_backed_nodes.extend(("task", task.get("node_id")) for task in tasks)
    source_backed_nodes.extend(("acceptance_check", check.get("node_id")) for check in all_checks)
    missing_source = [
        {"kind": kind, "node_id": node_id}
        for kind, node_id in source_backed_nodes
        if not has_outgoing_edge(conn, node_id, "DERIVED_FROM")
    ]
    prompt_count = int(row_scalar(conn.execute("SELECT COUNT(*) AS count FROM messages WHERE actor = 'user'").fetchone(), "count") or 0)
    ended_runs = conn.execute(
        """
        SELECT ar.id
        FROM agent_runs ar
        WHERE ar.ended_at IS NOT NULL
          AND EXISTS (SELECT 1 FROM run_snapshots rs WHERE rs.run_id = ar.id AND rs.phase = 'before')
          AND EXISTS (SELECT 1 FROM run_snapshots rs WHERE rs.run_id = ar.id AND rs.phase = 'after')
        ORDER BY ar.started_at DESC
        LIMIT 20
        """
    ).fetchall()
    test_rows = conn.execute(
        "SELECT id, props FROM nodes WHERE type = 'test_result' ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    argv_evidence = []
    for row in test_rows:
        props = props_dict(row)
        if test_result_has_trusted_argv(props):
            argv_evidence.append({"node_id": row["id"], "argv": props.get("argv"), "command": props.get("command")})
    audit_payload = audit_consume_payload(conn, endpoint_name, require_zero=True)
    doctor_payload = endpoint_doctor_payload(conn, repo, endpoint_name, strict_closeout=True)
    report_payload = endpoint_report_payload(conn, endpoint_name, active_only=True)
    evidence_payload = evidence_verify_payload(repo, conn, endpoint_name)
    provider_contract = impact_provider_contract(repo)
    body = (status.get("endpoint") or {}).get("current_body") or ""
    fixed_sections = ["Current tasks:", "Open checks:", "Closed checks:", "Evidence:"]
    missing_sections = [section for section in fixed_sections if section not in body]
    requirements = [
        readiness_requirement(
            "postgres_backend_current",
            schema.get("backend") == "postgres" and schema.get("state") == "current",
            "Database backend must be current project-owned PostgreSQL.",
            schema=schema,
        ),
        readiness_requirement(
            "source_backed_scope_task_check",
            bool(scope) and bool(tasks) and bool(all_checks) and not missing_source,
            "Endpoint must have source-backed scope/task/check records.",
            missing_source=missing_source,
            task_count=len(tasks),
            check_count=len(all_checks),
        ),
        readiness_requirement(
            "prompt_session_context",
            prompt_count > 0,
            "At least one user prompt/session message must be captured.",
            prompt_count=prompt_count,
        ),
        readiness_requirement(
            "exec_start_stop",
            bool(ended_runs),
            "At least one exec run must have before and after snapshots.",
            run_ids=[row["id"] for row in ended_runs],
        ),
        readiness_requirement(
            "argv_evidence",
            bool(argv_evidence),
            "At least one passing test_result must preserve argv/cwd/stdout/stderr predicate trust.",
            evidence=argv_evidence[:5],
        ),
        readiness_requirement(
            "audit_consume_require_zero",
            audit_payload["ok"],
            "audit consume --require-zero must find no active audit findings.",
            audit=audit_payload,
        ),
        readiness_requirement(
            "endpoint_refresh_fixed_sections",
            status["projection"]["source_kind"] == "projection" and not status["projection"].get("stale") and not missing_sections,
            "Endpoint refresh projection must be current and include fixed workbench sections.",
            projection=status["projection"],
            missing_sections=missing_sections,
        ),
        readiness_requirement(
            "strict_closeout",
            doctor_payload["ok"],
            "endpoint doctor --strict-closeout must pass.",
            doctor=doctor_payload,
        ),
        readiness_requirement(
            "active_only_report",
            endpoint_active_obligation_count(report_payload["active_obligations"]) == 0,
            "report endpoint --active-only must expose no active obligations for a closeout-ready endpoint.",
            report=report_payload,
        ),
        readiness_requirement(
            "evidence_verify",
            evidence_payload["ok"],
            "evidence verify --endpoint must pass for current evidence.",
            evidence_verify=evidence_payload,
        ),
        readiness_requirement(
            "provider_default_off",
            provider_contract.get("required") is False,
            "GitNexus must remain an optional provider contract, not a closure authority or hard runtime dependency.",
            provider_contract=provider_contract,
        ),
    ]
    ok = all(item["ok"] for item in requirements)
    return {
        "ok": ok,
        "endpoint": endpoint_name,
        "requirements": requirements,
        "failed": [item for item in requirements if not item["ok"]],
        "next_valid_entry_point": report_payload["next_valid_entry_point"],
    }

def cmd_ready_new_project(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    payload = new_project_readiness_payload(conn, repo, args.endpoint)
    print_json(payload)
    return 0 if payload["ok"] or args.allow_fail else 1

def db_doctor_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {"P0": [], "P1": [], "P2": []}
    schema = inspect_schema(conn)
    if schema["state"] != "current":
        doctor_add(buckets, "P0", "schema_not_current", f"Schema state is {schema['state']}.", "Run migrate status/apply or repair bootstrap metadata.", schema=schema)
    orphan_types = {"unresolved_question", "scope_change", "defer_decision", "assumption", "work_note", "audit_finding"}
    placeholders = ",".join("?" for _ in orphan_types)
    orphan_rows = conn.execute(
        f"""
        SELECT n.id, n.type, n.label
        FROM nodes n
        WHERE n.type IN ({placeholders})
          AND {active_node_clause("n")}
          AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.from_node_id = n.id AND e.type = 'APPLIES_TO')
        ORDER BY n.created_at DESC
        LIMIT 50
        """,
        tuple(sorted(orphan_types)),
    ).fetchall()
    for row in orphan_rows:
        doctor_add(buckets, "P1", "orphan_semantic_node", f"{row['type']} {row['id']} has no APPLIES_TO edge.", "Attach it to an endpoint/root/task/check or supersede it.", node_id=row["id"], node_type=row["type"])
    missing_source_rows = conn.execute(
        f"""
        SELECT n.id, n.type, n.label
        FROM nodes n
        WHERE n.type IN ({placeholders})
          AND {active_node_clause("n")}
          AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.from_node_id = n.id AND e.type = 'DERIVED_FROM')
        ORDER BY n.created_at DESC
        LIMIT 50
        """,
        tuple(sorted(orphan_types)),
    ).fetchall()
    for row in missing_source_rows:
        doctor_add(buckets, "P1", "missing_source", f"{row['type']} {row['id']} has no DERIVED_FROM source.", "Create future semantic nodes from source evidence; add a sourced replacement if this node matters.", node_id=row["id"], node_type=row["type"])
    recommendations = [item["recommendation"] for severity in ["P0", "P1", "P2"] for item in buckets.get(severity, [])]
    return {"ok": not buckets["P0"] and not buckets["P1"], "schema": schema, "severity_buckets": buckets, "recommendations": list(dict.fromkeys(recommendations))}

def cmd_db_doctor(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    payload = db_doctor_payload(conn)
    print_json(payload)
    return 0 if payload["ok"] or args.allow_fail else 1


def cmd_endpoint_suggest(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    prompt = read_arg_or_stdin(getattr(args, "from_prompt", None), file_path=getattr(args, "from_prompt_file", None), label="from_prompt")
    prompt_lower = prompt.lower()
    tokens = {token for token in re.split(r"[^a-z0-9_-]+", prompt_lower) if len(token) >= 3}
    conn = connect(repo)
    rows = conn.execute(
        """
        SELECT name, description
        FROM endpoints
        WHERE archived_at IS NULL
        ORDER BY created_at DESC, id DESC
        """
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        name = str(row["name"])
        haystack = f"{name} {row['description'] or ''}".lower()
        score = 0
        evidence: list[str] = []
        if name.lower() in prompt_lower:
            score += 10
            evidence.append("endpoint_name_in_prompt")
        overlap = [token for token in tokens if token in haystack]
        if overlap:
            score += len(overlap)
            evidence.append("token_overlap:" + ",".join(overlap[:5]))
        if score <= 0:
            continue
        candidates.append(
            {
                "endpoint": name,
                "confidence": "high" if score >= 10 else "medium",
                "evidence": evidence,
                "first_surface": [
                    f"python -m shujuan report endpoint {name} --active-only --markdown",
                    f"python -m shujuan endpoint doctor {name} --strict-closeout --read-only --allow-fail",
                ],
                "write_allowed": False,
                "safe_next_action": f"Inspect endpoint {name} read-only before any writeful route.",
            }
        )
    print_json(
        {
            "ok": True,
            "read_only": True,
            "write_allowed": False,
            "auto_bind": False,
            "candidates": candidates[: max(1, args.top)],
            "safe_next_action": "Use report project --overview or ask for explicit confirmation when no high-confidence endpoint exists.",
        }
    )
    return 0

def build_endpoint_handlers(deps: Mapping[str, Any]) -> dict[str, Any]:
    _configure(deps)
    return {
        "center_update": cmd_center_update,
        "center_show": cmd_center_show,
        "center_suggest": cmd_center_suggest,
        "endpoint_create": cmd_endpoint_create,
        "endpoint_bind_root": cmd_endpoint_bind_root,
        "endpoint_link_child": cmd_endpoint_link_child,
        "endpoint_brief": cmd_endpoint_brief,
        "endpoint_update": cmd_endpoint_update,
        "endpoint_status": cmd_endpoint_status,
        "endpoint_refresh": cmd_endpoint_refresh,
        "endpoint_doctor": cmd_endpoint_doctor,
        "endpoint_suggest": cmd_endpoint_suggest,
        "export_center": cmd_export_center,
        "export_glossary": cmd_export_glossary,
        "ready_new_project": cmd_ready_new_project,
        "db_doctor": cmd_db_doctor,
    }


def register_db(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, Any]) -> None:
    db_parser = subparsers.add_parser("db")
    db_sub = db_parser.add_subparsers(dest="db_command", required=True)
    db_doctor = db_sub.add_parser("doctor")
    db_doctor.add_argument("--allow-fail", action="store_true")
    db_doctor.set_defaults(func=handlers["db_doctor"])


def register_center(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, Any]) -> None:
    center = subparsers.add_parser("center")
    center_sub = center.add_subparsers(dest="center_command", required=True)
    center_update = center_sub.add_parser("update")
    center_update.add_argument("--body")
    center_update.add_argument("--from-node")
    center_update.set_defaults(func=handlers["center_update"])
    center_show = center_sub.add_parser("show")
    center_show.add_argument("--all", action="store_true")
    center_show.set_defaults(func=handlers["center_show"])
    center_suggest = center_sub.add_parser("suggest")
    center_suggest.add_argument("--from-prompt")
    center_suggest.add_argument("--from-prompt-file", help="Read long prompt text from a UTF-8 file.")
    center_suggest.add_argument("--top", type=int, default=3)
    center_suggest.set_defaults(func=handlers["center_suggest"])


def register_endpoint(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, Any]) -> None:
    endpoint = subparsers.add_parser("endpoint")
    endpoint_sub = endpoint.add_subparsers(dest="endpoint_command", required=True)
    endpoint_create = endpoint_sub.add_parser("create")
    endpoint_create.add_argument("endpoint")
    endpoint_create.add_argument("--description")
    endpoint_create.add_argument("--root-node")
    endpoint_create.add_argument("--rootless", action="store_true")
    endpoint_create.add_argument("--reason")
    endpoint_create.set_defaults(func=handlers["endpoint_create"])
    endpoint_bind = endpoint_sub.add_parser("bind-root")
    endpoint_bind.add_argument("endpoint")
    endpoint_bind.add_argument("--root-node", required=True)
    endpoint_bind.add_argument("--description")
    endpoint_bind.set_defaults(func=handlers["endpoint_bind_root"])
    endpoint_link_child = endpoint_sub.add_parser("link-child")
    endpoint_link_child.add_argument("--parent", required=True)
    endpoint_link_child.add_argument("--child", required=True)
    endpoint_link_child.add_argument("--reason")
    endpoint_link_child.add_argument("--confidence", type=float)
    endpoint_link_child.set_defaults(func=handlers["endpoint_link_child"])
    endpoint_brief = endpoint_sub.add_parser("brief")
    endpoint_brief.add_argument("endpoint")
    endpoint_brief.add_argument("--role")
    endpoint_brief.add_argument("--mode")
    endpoint_brief.add_argument("--task", action="append", default=[])
    endpoint_brief.add_argument("--check", action="append", default=[])
    endpoint_brief.add_argument("--work-chain")
    endpoint_brief.add_argument("--markdown", action="store_true")
    endpoint_brief.set_defaults(func=handlers["endpoint_brief"])
    endpoint_update = endpoint_sub.add_parser("update")
    endpoint_update.add_argument("--endpoint", required=True)
    endpoint_update.add_argument("--body")
    endpoint_update.add_argument("--description")
    endpoint_update.add_argument("--root-node")
    endpoint_update.add_argument("--from-node")
    endpoint_update.set_defaults(func=handlers["endpoint_update"])
    endpoint_status = endpoint_sub.add_parser("status")
    endpoint_status.add_argument("endpoint")
    endpoint_status.add_argument("--markdown", action="store_true")
    endpoint_status.set_defaults(func=handlers["endpoint_status"])
    endpoint_refresh = endpoint_sub.add_parser("refresh")
    endpoint_refresh.add_argument("endpoint")
    endpoint_refresh.add_argument("--description")
    endpoint_refresh.add_argument("--root-node")
    endpoint_refresh.set_defaults(func=handlers["endpoint_refresh"])
    endpoint_doctor = endpoint_sub.add_parser("doctor")
    endpoint_doctor.add_argument("endpoint")
    endpoint_doctor.add_argument("--allow-fail", action="store_true")
    endpoint_doctor.add_argument("--strict-closeout", action="store_true", help="Controller closeout path: refresh projection and fail on active blockers, unresolved decisions, open mandatory work, or bad evidence.")
    endpoint_doctor.add_argument(
        "--read-only",
        "--no-refresh",
        dest="read_only",
        action="store_true",
        help="Inspect doctor findings without refreshing the endpoint projection; use with --strict-closeout for read-only entry diagnostics.",
    )
    endpoint_doctor.set_defaults(func=handlers["endpoint_doctor"])
    endpoint_suggest = endpoint_sub.add_parser("suggest")
    endpoint_suggest.add_argument("--from-prompt")
    endpoint_suggest.add_argument("--from-prompt-file", help="Read long prompt text from a UTF-8 file.")
    endpoint_suggest.add_argument("--top", type=int, default=3)
    endpoint_suggest.set_defaults(func=handlers["endpoint_suggest"])


def register_export(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, Any]) -> None:
    export = subparsers.add_parser("export")
    export_sub = export.add_subparsers(dest="export_command", required=True)
    export_center = export_sub.add_parser("center")
    export_center.set_defaults(func=handlers["export_center"])
    export_glossary = export_sub.add_parser("glossary")
    export_glossary.set_defaults(func=handlers["export_glossary"])


def register_ready(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, Any]) -> None:
    ready = subparsers.add_parser("ready")
    ready_sub = ready.add_subparsers(dest="ready_command", required=True)
    ready_new_project = ready_sub.add_parser("new-project")
    ready_new_project.add_argument("--endpoint", required=True)
    ready_new_project.add_argument("--allow-fail", action="store_true")
    ready_new_project.set_defaults(func=handlers["ready_new_project"])
