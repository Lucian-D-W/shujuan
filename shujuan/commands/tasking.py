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

from ..services.command_effects import scope_change_effects


def _configure(deps: Mapping[str, Any]) -> None:
    globals().update(deps)

def cmd_term_define(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    old = conn.execute(
        "SELECT * FROM terms WHERE canonical_term = ? AND valid_to IS NULL ORDER BY valid_from DESC LIMIT 1",
        (args.term,),
    ).fetchone()
    node_id = create_node(conn, "term", args.term, args.definition, {"avoid_aliases": args.avoid_aliases})
    semantic_item_id = register_semantic_item(
        conn,
        node_id,
        "term",
        state="active",
        source_node=args.from_node,
        scope_node=args.scope_node,
        event_type="created",
        reason="Term definition recorded.",
        props={"term": args.term},
    )
    term_id = new_id("term")
    conn.execute(
        """
        INSERT INTO terms
          (id, node_id, canonical_term, definition, avoid_aliases, ambiguity_notes,
           scope_node_id, created_from_node_id, valid_from)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            term_id,
            node_id,
            args.term,
            args.definition,
            json_dumps(args.avoid_aliases or []),
            args.ambiguity_notes,
            args.scope_node,
            args.from_node,
            now_iso(),
        ),
    )
    supersedes = None
    if old:
        conn.execute("UPDATE terms SET valid_to = ? WHERE id = ?", (now_iso(), old["id"]))
        conn.execute("UPDATE nodes SET valid_to = ?, superseded_by_node_id = ? WHERE id = ?", (now_iso(), node_id, old["node_id"]))
        supersedes = old["node_id"]
        create_edge(conn, node_id, "SUPERSEDES", old["node_id"], reason="New term definition supersedes previous active definition.")
        transition_semantic_item(conn, old["node_id"], state="superseded", event_type="superseded", source_node=node_id, reason="New term definition supersedes previous active definition.")
    conn.commit()
    print_json({"ok": True, "term_id": term_id, "node_id": node_id, "semantic_item_id": semantic_item_id, "supersedes_node_id": supersedes})
    return 0

def cmd_scope_create(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    require_node(conn, args.source_node, "scope source node")
    body = read_arg_or_stdin(args.body, file_path=getattr(args, "body_file", None), label="body")
    node_id = create_node(conn, "scope_contract", "Scope contract", body[:240])
    contract_id = new_id("contract")
    conn.execute(
        """
        INSERT INTO scope_contracts
          (id, node_id, source_node_id, body, non_downgrade_rules, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (contract_id, node_id, args.source_node, body, args.non_downgrade_rules, now_iso()),
    )
    create_edge(conn, node_id, "DERIVED_FROM", args.source_node, reason="Scope contract derived from source node.")
    conn.commit()
    print_json({"ok": True, "contract_id": contract_id, "node_id": node_id})
    return 0

def create_scope_change(
    conn: sqlite3.Connection,
    *,
    body: str,
    source_node: str,
    label: str | None = None,
    task_ids: list[str] | None = None,
    applies_to: list[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if not task_ids and not applies_to:
        raise SystemExit("scope change requires at least one --task or --applies-to target")
    require_node(conn, source_node, "scope change source node")
    node_id = create_node(
        conn,
        "scope_change",
        label or "scope change",
        body[:240],
        {"body": body},
    )
    semantic_item_id = register_semantic_item(
        conn,
        node_id,
        "scope_change",
        state="active",
        source_node=source_node,
        event_type="created",
        reason=reason or "Scope change recorded.",
        props={"body": body},
    )
    source_edges = link_source_nodes(conn, node_id, [source_node], reason="Scope change derived from source evidence node.")
    deferred_tasks = []
    for task_id in task_ids or []:
        task = conn.execute("SELECT id, node_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise SystemExit(f"task not found: {task_id}")
        create_edge(conn, task["node_id"], "DEFERRED_BY", node_id, reason=reason or "Task deferred by scope change.")
        create_edge(conn, node_id, "APPLIES_TO", task["node_id"], reason="Scope change applies to deferred task.", created_by="agent")
        deferred_tasks.append(row_to_dict(task))
    applies_edges = []
    for target_node_id in applies_to or []:
        require_node(conn, target_node_id, "scope change target node")
        applies_edges.append(
            create_edge(conn, node_id, "APPLIES_TO", target_node_id, reason="Scope change applies to target node.", created_by="agent")
        )
    return {"node_id": node_id, "semantic_item_id": semantic_item_id, "source_edges": source_edges, "applies_edges": applies_edges, "deferred_tasks": deferred_tasks}

def create_defer_decision(
    conn: sqlite3.Connection,
    *,
    body: str,
    source_node: str,
    label: str | None = None,
    task_ids: list[str] | None = None,
    applies_to: list[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if not task_ids and not applies_to:
        raise SystemExit("task defer requires at least one --task or --applies-to target")
    require_node(conn, source_node, "defer decision source node")
    node_id = create_node(
        conn,
        "defer_decision",
        label or "defer decision",
        body[:240],
        {"body": body},
    )
    semantic_item_id = register_semantic_item(
        conn,
        node_id,
        "defer_decision",
        state="deferred",
        source_node=source_node,
        event_type="deferred",
        reason=reason or "Defer decision recorded.",
        props={"body": body},
    )
    source_edges = link_source_nodes(conn, node_id, [source_node], reason="Defer decision derived from source evidence node.")
    deferred_tasks = []
    for task_id in task_ids or []:
        task = conn.execute("SELECT id, node_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise SystemExit(f"task not found: {task_id}")
        create_edge(conn, task["node_id"], "DEFERRED_BY", node_id, reason=reason or "Task deferred by defer decision.")
        create_edge(conn, node_id, "APPLIES_TO", task["node_id"], reason="Defer decision applies to deferred task.", created_by="agent")
        deferred_tasks.append(row_to_dict(task))
    applies_edges = []
    for target_node_id in applies_to or []:
        require_node(conn, target_node_id, "defer decision target node")
        applies_edges.append(
            create_edge(conn, node_id, "APPLIES_TO", target_node_id, reason="Defer decision applies to target node.", created_by="agent")
        )
    return {"node_id": node_id, "semantic_item_id": semantic_item_id, "source_edges": source_edges, "applies_edges": applies_edges, "deferred_tasks": deferred_tasks}

def cmd_scope_change(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    if args.task and (not getattr(args, "state_changing", False) or not getattr(args, "ack_defer_like", False)):
        print_json(
            json_error_payload(
                "scope_change_requires_explicit_state_ack",
                "scope change --task is defer-like and needs both --state-changing and --ack-defer-like",
                read_only=True,
                safe_next_action="Use scope change --applies-to for clarification notes, or task defer --task for a true deferral.",
                clarification_path="scope change --applies-to <target-node>",
                defer_path="task defer --task <task-id>",
            )
        )
        return 1
    conn = connect(repo)
    body = read_arg_or_stdin(args.body, file_path=getattr(args, "body_file", None), label="body")
    command_effects = scope_change_effects(
        task_count=len(args.task or []),
        applies_to_count=len(args.applies_to or []),
    )
    result = create_scope_change(
        conn,
        body=body,
        source_node=args.source_node,
        label=args.label,
        task_ids=args.task,
        applies_to=args.applies_to,
        reason=args.reason,
    )
    conn.commit()
    print_json(
        {
            "ok": True,
            **result,
            "state_effects": command_effects["close"]["state_effects"],
            "command_effects": command_effects,
        }
    )
    return 0

def cmd_task_add(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    require_node(conn, args.from_node, "task source node")
    body = read_arg_or_stdin(args.body, file_path=getattr(args, "body_file", None), label="body")
    contract = None
    if args.contract:
        contract = conn.execute("SELECT node_id FROM scope_contracts WHERE id = ?", (args.contract,)).fetchone()
        if not contract:
            raise SystemExit(f"scope contract not found: {args.contract}")
    if args.parent:
        parent = conn.execute("SELECT id FROM tasks WHERE id = ?", (args.parent,)).fetchone()
        if not parent:
            raise SystemExit(f"parent task not found: {args.parent}")
    node_id = create_node(conn, "task", body[:80], body[:240])
    task_id = new_id("task")
    conn.execute(
        """
        INSERT INTO tasks
          (id, node_id, contract_id, parent_task_id, task_body, is_mandatory, created_from_node_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, node_id, args.contract, args.parent, body, 0 if args.optional else 1, args.from_node),
    )
    if contract:
        create_edge(conn, contract["node_id"], "DECOMPOSES_TO", node_id, reason="Scope contract decomposes to task.")
    create_edge(conn, node_id, "DERIVED_FROM", args.from_node, reason="Task derived from source evidence node.")
    conn.commit()
    print_json({"ok": True, "task_id": task_id, "node_id": node_id})
    return 0

def cmd_task_defer(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    body = read_arg_or_stdin(args.body, file_path=getattr(args, "body_file", None), label="body")
    result = create_defer_decision(
        conn,
        body=body,
        source_node=args.source_node,
        label=args.label or "defer decision",
        task_ids=args.task,
        applies_to=args.applies_to,
        reason=args.reason or "Task deferred with explicit scope/defer evidence.",
    )
    conn.commit()
    print_json({"ok": True, **result})
    return 0

def cmd_acceptance_add(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    require_node(conn, args.from_node, "acceptance source node")
    body = read_arg_or_stdin(args.body, file_path=getattr(args, "body_file", None), label="body")
    task = conn.execute("SELECT node_id FROM tasks WHERE id = ?", (args.task,)).fetchone()
    if not task:
        raise SystemExit(f"task not found: {args.task}")
    node_id = create_node(conn, "acceptance_check", body[:80], body[:240])
    check_id = new_id("check")
    conn.execute(
        """
        INSERT INTO acceptance_checks
          (id, node_id, task_id, check_body, expected_evidence_type)
        VALUES (?, ?, ?, ?, ?)
        """,
        (check_id, node_id, args.task, body, args.expected_evidence_type),
    )
    create_edge(conn, task["node_id"], "DECOMPOSES_TO", node_id, reason="Task has acceptance check.")
    create_edge(conn, node_id, "DERIVED_FROM", args.from_node, reason="Acceptance check derived from source evidence node.")
    conn.commit()
    print_json({"ok": True, "acceptance_check_id": check_id, "node_id": node_id})
    return 0

def cmd_acceptance_close(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    check = conn.execute("SELECT * FROM acceptance_checks WHERE id = ?", (args.check,)).fetchone()
    if not check:
        raise SystemExit(f"acceptance check not found: {args.check}")
    close_result = close_check_with_evidence(
        conn,
        repo,
        check=check,
        evidence_node_id=args.evidence_node,
        override=args.override_evidence_type,
        override_reason=args.override_reason,
        override_predicate_coverage=args.override_predicate_coverage,
        elevated_predicate_coverage_override=args.elevated_predicate_coverage_override,
    )
    create_edge(conn, check["node_id"], "VALIDATED_BY", args.evidence_node, reason=args.reason or "Acceptance check closed by evidence node.")
    task_readiness = task_readiness_hint(conn, check["task_id"], evidence_node_id=args.evidence_node, last_check_id=args.check)
    if args.close_task:
        task_readiness = close_task_if_ready(conn, check["task_id"], args.evidence_node, last_check_id=args.check)
    conn.commit()
    print_json(
        {
            "ok": True,
            "acceptance_check_id": args.check,
            "closed_by_node_id": args.evidence_node,
            "warning_node_ids": close_result["warnings"],
            "idempotent": close_result["idempotent"],
            "task_readiness": aggregate_task_readiness([task_readiness]),
        }
    )
    return 0

def cmd_acceptance_replace_closure(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    check = conn.execute("SELECT * FROM acceptance_checks WHERE id = ?", (args.check,)).fetchone()
    if not check:
        raise SystemExit(f"acceptance check not found: {args.check}")
    old_evidence_node_id = check["closed_by_node_id"]
    if not old_evidence_node_id:
        raise SystemExit(f"acceptance check {args.check} is not closed; use normal evidence closure first")
    assert_evidence_can_close(conn, repo, args.evidence_node)
    warning_node_ids = []
    warning_node_id = validate_check_evidence_type(
        conn,
        check=check,
        evidence_node_id=args.evidence_node,
        override=args.override_evidence_type,
        override_reason=args.override_reason,
    )
    if warning_node_id:
        warning_node_ids.append(warning_node_id)
    if old_evidence_node_id == args.evidence_node:
        print_json(
            {
                "ok": True,
                "idempotent": True,
                "check_id": args.check,
                "old_evidence_node_id": old_evidence_node_id,
                "new_evidence_node_id": args.evidence_node,
                "task_updated": False,
                "warning_node_ids": warning_node_ids,
            }
        )
        return 0
    target_check_ids = existing_check_ids_closed_by_evidence(conn, args.evidence_node) + [str(check["id"])]
    predicate_warning_node_id = validate_test_result_predicate_coverage(
        conn,
        evidence_node_id=args.evidence_node,
        check_ids=target_check_ids,
        override=args.override_predicate_coverage,
        override_reason=args.override_reason,
        elevated_override=args.elevated_predicate_coverage_override,
    )
    if predicate_warning_node_id:
        warning_node_ids.append(predicate_warning_node_id)
    timestamp = now_iso()
    conn.execute(
        "UPDATE acceptance_checks SET closed_by_node_id = ?, closed_at = ? WHERE id = ?",
        (args.evidence_node, timestamp, args.check),
    )
    validated_edge_id = create_edge(
        conn,
        check["node_id"],
        "VALIDATED_BY",
        args.evidence_node,
        reason=args.reason,
        created_by="agent",
    )
    supersedes_edge_id = create_edge(
        conn,
        args.evidence_node,
        "SUPERSEDES",
        old_evidence_node_id,
        reason=args.reason,
        created_by="agent",
    )
    transition_semantic_item(
        conn,
        old_evidence_node_id,
        state="superseded",
        event_type="superseded",
        source_node=args.evidence_node,
        reason=args.reason,
    )
    register_evidence_lifecycle(
        conn,
        args.evidence_node,
        source_node=args.evidence_node,
        reason="Replacement closure evidence is current.",
    )
    task_updated = False
    task = conn.execute("SELECT id, closed_by_node_id FROM tasks WHERE id = ?", (check["task_id"],)).fetchone()
    if task and task["closed_by_node_id"] == old_evidence_node_id:
        open_checks = conn.execute(
            """
            SELECT id
            FROM acceptance_checks
            WHERE task_id = ? AND closed_by_node_id IS NULL
            LIMIT 1
            """,
            (check["task_id"],),
        ).fetchall()
        if not open_checks:
            conn.execute(
                "UPDATE tasks SET closed_by_node_id = ?, closed_at = ? WHERE id = ?",
                (args.evidence_node, timestamp, check["task_id"]),
            )
            task_updated = True
    conn.commit()
    print_json(
        {
            "ok": True,
            "idempotent": False,
            "check_id": args.check,
            "old_evidence_node_id": old_evidence_node_id,
            "new_evidence_node_id": args.evidence_node,
            "task_id": check["task_id"],
            "task_updated": task_updated,
            "validated_edge_id": validated_edge_id,
            "supersedes_edge_id": supersedes_edge_id,
            "warning_node_ids": warning_node_ids,
        }
    )
    return 0

def create_semantic_note(
    conn: sqlite3.Connection,
    *,
    node_type: str,
    body: str,
    source_node: str,
    label: str | None,
    applies_to: list[str],
    edge_reason: str,
) -> dict[str, Any]:
    node_id = create_node(conn, node_type, label or node_type.replace("_", " "), body[:240], {"body": body})
    semantic_item_id = register_semantic_item(
        conn,
        node_id,
        node_type,
        state="active",
        source_node=source_node,
        event_type="created",
        reason=edge_reason,
        props={"body": body},
    )
    source_edges = link_source_nodes(conn, node_id, [source_node], reason=edge_reason)
    applies_edges = [
        create_edge(conn, node_id, "APPLIES_TO", target_node_id, reason=f"{node_type} applies to target node.", created_by="agent")
        for target_node_id in applies_to
    ]
    return {"node_id": node_id, "semantic_item_id": semantic_item_id, "source_edges": source_edges, "applies_edges": applies_edges}

def create_work_note(
    conn: sqlite3.Connection,
    *,
    endpoint_name: str,
    kind: str,
    body: str,
    source_node: str,
    applies_to: list[str],
    run_id: str | None = None,
    active_obligation: bool = False,
    label: str | None = None,
) -> dict[str, Any]:
    endpoint = query_endpoint(conn, endpoint_name)
    require_node(conn, source_node, "work note source node")
    target_node_ids = [str(endpoint["node_id"])]
    for target_node_id in applies_to:
        require_node(conn, target_node_id, "work note target node")
        target_node_ids.append(target_node_id)
    node_id = create_node(
        conn,
        "work_note",
        label or f"{kind} note: {endpoint_name}",
        body[:240],
        {
            "kind": kind,
            "body": body,
            "endpoint": endpoint_name,
            "run_id": run_id,
            "active_obligation": bool(active_obligation),
        },
    )
    semantic_item_id = register_semantic_item(
        conn,
        node_id,
        "work_note",
        state="active" if active_obligation or kind in {"needs_user_decision", "finding", "risk"} else PRODUCT_BACKLOG_STATE,
        source_node=source_node,
        scope_node=endpoint["node_id"],
        event_type="created",
        reason="Work note recorded.",
        props={"kind": kind, "body": body, "endpoint": endpoint_name, "active_obligation": bool(active_obligation)},
    )
    source_edges = link_source_nodes(conn, node_id, [source_node], reason="Work note derived from source evidence node.")
    applies_edges = [
        create_edge(conn, node_id, "APPLIES_TO", target_node_id, reason="Work note applies to endpoint or target node.", created_by="agent")
        for target_node_id in list(dict.fromkeys(target_node_ids))
    ]
    return {"node_id": node_id, "semantic_item_id": semantic_item_id, "source_edges": source_edges, "applies_edges": applies_edges}

def maybe_refresh_endpoint(conn: sqlite3.Connection, endpoint_name: str, from_node: str | None = None) -> dict[str, Any] | None:
    return refresh_endpoint_projection(conn, endpoint_name, from_node=from_node)

def cmd_jot_add(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    result = create_work_note(
        conn,
        endpoint_name=args.endpoint,
        kind=args.kind,
        body=read_arg_or_stdin(args.body, file_path=getattr(args, "body_file", None), label="body"),
        source_node=args.source_node,
        applies_to=args.applies_to,
        run_id=args.run,
        active_obligation=args.active_obligation,
        label=args.label,
    )
    refresh = maybe_refresh_endpoint(conn, args.endpoint, result["node_id"]) if args.refresh_endpoint else None
    conn.commit()
    print_json({"ok": True, **result, "endpoint_refresh": refresh})
    return 0

def cmd_jot_handoff(args: argparse.Namespace) -> int:
    args.kind = "handoff"
    args.active_obligation = False
    return cmd_jot_add(args)

def cmd_assumption_add(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    result = create_semantic_note(
        conn,
        node_type="assumption",
        body=read_arg_or_stdin(args.body, file_path=getattr(args, "body_file", None), label="body"),
        source_node=args.source_node,
        label=args.label,
        applies_to=args.applies_to,
        edge_reason="Assumption derived from source evidence node.",
    )
    conn.commit()
    print_json({"ok": True, **result})
    return 0

def cmd_unresolved_add(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    result = create_semantic_note(
        conn,
        node_type="unresolved_question",
        body=read_arg_or_stdin(args.body, file_path=getattr(args, "body_file", None), label="body"),
        source_node=args.source_node,
        label=args.label,
        applies_to=args.applies_to,
        edge_reason="Unresolved question derived from source evidence node.",
    )
    conn.commit()
    print_json({"ok": True, **result})
    return 0

def cmd_semantic_set_state(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    node = require_node(conn, args.node, "semantic node")
    require_node(conn, args.source_node, "semantic lifecycle source node")
    state = "active" if args.state == "reopened" else args.state
    event_type = args.event_type or ("reopened" if args.state == "reopened" else args.state)
    semantic_item_id = transition_semantic_item(
        conn,
        args.node,
        state=state,
        event_type=event_type,
        source_node=args.source_node,
        reason=args.reason or f"Semantic item marked {args.state}.",
    )
    if semantic_item_id is None:
        semantic_item_id = register_semantic_item(
            conn,
            args.node,
            str(node["type"]),
            state=state,
            source_node=args.source_node,
            event_type=event_type,
            reason=args.reason or f"Semantic item marked {args.state}.",
        )
    refresh = refresh_endpoint_projection(conn, args.endpoint, from_node=args.source_node) if args.endpoint else None
    conn.commit()
    print_json(
        {
            "ok": True,
            "node_id": args.node,
            "semantic_item_id": semantic_item_id,
            "state": state,
            "event_type": event_type,
            "endpoint_refresh": refresh,
        }
    )
    return 0

def cmd_why(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    path = args.path.replace("\\", "/") if args.path else None
    code_object = None
    if path:
        code_object = conn.execute(
            "SELECT * FROM code_objects WHERE path = ? ORDER BY COALESCE(qualified_name, '') ASC, id ASC LIMIT 1",
            (path,),
        ).fetchone()
    elif args.symbol:
        code_object = conn.execute(
            "SELECT * FROM code_objects WHERE qualified_name = ? ORDER BY path ASC, id ASC LIMIT 1",
            (args.symbol,),
        ).fetchone()
    if not code_object:
        print_json({"found": False, "reason": "code_object_not_found"})
        return 0
    links = conn.execute(
        """
        SELECT ccl.*, cs.node_id AS change_set_node_id, cs.run_id, cs.patch_hash,
               ar.node_id AS run_node_id, ar.started_at, ar.final_report
        FROM change_code_links ccl
        JOIN change_sets cs ON cs.id = ccl.change_set_id
        JOIN agent_runs ar ON ar.id = cs.run_id
        WHERE ccl.code_object_id = ?
        ORDER BY cs.created_at DESC
        LIMIT 10
        """,
        (code_object["id"],),
    ).fetchall()
    hunks = []
    if path:
        line_clause = ""
        params: list[Any] = [path, path]
        if args.line is not None:
            line_clause = "AND dh.new_start IS NOT NULL AND ? BETWEEN dh.new_start AND (dh.new_start + COALESCE(dh.new_lines, 1) - 1)"
            params.append(args.line)
        hunks = conn.execute(
            f"""
            SELECT dh.*
            FROM diff_hunks dh
            JOIN diff_files df ON df.id = dh.diff_file_id
            WHERE (df.path_new = ? OR df.path_old = ?)
              {line_clause}
            ORDER BY dh.new_start DESC, dh.id DESC
            LIMIT 10
            """,
            params,
        ).fetchall()
    elif links:
        hunk_ids = [str(row["evidence_hunk_id"]) for row in links if row["evidence_hunk_id"]]
        if hunk_ids:
            placeholders = ",".join("?" for _ in hunk_ids[:10])
            hunks = conn.execute(
                f"""
                SELECT *
                FROM diff_hunks
                WHERE id IN ({placeholders})
                ORDER BY new_start DESC, id DESC
                LIMIT 10
                """,
                hunk_ids[:10],
            ).fetchall()
    change_node_ids = [str(row["change_set_node_id"]) for row in links]
    run_node_ids = [str(row["run_node_id"]) for row in links]
    related_edges = []
    for node_id in [*change_node_ids, *run_node_ids][:20]:
        related_edges.extend(
            row_to_dict(row)
            for row in conn.execute(
                """
                SELECT * FROM edges
                WHERE from_node_id = ? OR to_node_id = ?
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (node_id, node_id),
            ).fetchall()
        )
    print_json(
        {
            "found": True,
            "code_object": row_to_dict(code_object),
            "recent_change_links": [row_to_dict(row) for row in links],
            "recent_hunks": [row_to_dict(row) for row in hunks],
            "related_edges": related_edges,
            "note": "Facts come from captured shujuan diff/code object records; open source documents or messages for deeper evidence.",
        }
    )
    return 0

def build_tasking_handlers(deps: Mapping[str, Any]) -> dict[str, Any]:
    _configure(deps)
    return {
        "term_define": cmd_term_define,
        "scope_create": cmd_scope_create,
        "scope_change": cmd_scope_change,
        "task_add": cmd_task_add,
        "task_defer": cmd_task_defer,
        "acceptance_add": cmd_acceptance_add,
        "acceptance_close": cmd_acceptance_close,
        "acceptance_replace_closure": cmd_acceptance_replace_closure,
        "jot_add": cmd_jot_add,
        "jot_handoff": cmd_jot_handoff,
        "assumption_add": cmd_assumption_add,
        "unresolved_add": cmd_unresolved_add,
        "semantic_set_state": cmd_semantic_set_state,
        "why": cmd_why,
    }


def register_tasking(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, Any], semantic_state_type: Any) -> None:
    term = subparsers.add_parser("term")
    term_sub = term.add_subparsers(dest="term_command", required=True)
    term_define = term_sub.add_parser("define")
    term_define.add_argument("term")
    term_define.add_argument("--definition", required=True)
    term_define.add_argument("--avoid-aliases", action="append", default=[])
    term_define.add_argument("--ambiguity-notes")
    term_define.add_argument("--scope-node")
    term_define.add_argument("--from-node")
    term_define.set_defaults(func=handlers["term_define"])

    scope = subparsers.add_parser("scope")
    scope_sub = scope.add_subparsers(dest="scope_command", required=True)
    scope_create = scope_sub.add_parser("create")
    scope_create.add_argument("--body")
    scope_create.add_argument("--body-file", help="Read long body text from a UTF-8 file.")
    scope_create.add_argument("--source-node", required=True)
    scope_create.add_argument("--non-downgrade-rules")
    scope_create.set_defaults(func=handlers["scope_create"])
    scope_change = scope_sub.add_parser("change")
    scope_change.add_argument("--body")
    scope_change.add_argument("--body-file", help="Read long body text from a UTF-8 file.")
    scope_change.add_argument("--source-node", required=True)
    scope_change.add_argument("--label")
    scope_change.add_argument("--task", action="append", default=[])
    scope_change.add_argument("--applies-to", action="append", default=[])
    scope_change.add_argument("--reason")
    scope_change.add_argument("--state-changing", action="store_true")
    scope_change.add_argument("--ack-defer-like", action="store_true")
    scope_change.set_defaults(func=handlers["scope_change"])

    task = subparsers.add_parser("task")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    task_add = task_sub.add_parser("add")
    task_add.add_argument("--body")
    task_add.add_argument("--body-file", help="Read long body text from a UTF-8 file.")
    task_add.add_argument("--contract")
    task_add.add_argument("--parent")
    task_add.add_argument("--from-node", required=True)
    task_add.add_argument("--optional", action="store_true")
    task_add.set_defaults(func=handlers["task_add"])
    task_defer = task_sub.add_parser("defer")
    task_defer.add_argument("--task", action="append", required=True)
    task_defer.add_argument("--body")
    task_defer.add_argument("--body-file", help="Read long body text from a UTF-8 file.")
    task_defer.add_argument("--source-node", required=True)
    task_defer.add_argument("--label")
    task_defer.add_argument("--applies-to", action="append", default=[])
    task_defer.add_argument("--reason")
    task_defer.set_defaults(func=handlers["task_defer"])

    acceptance = subparsers.add_parser("acceptance")
    acceptance_sub = acceptance.add_subparsers(dest="acceptance_command", required=True)
    acceptance_add = acceptance_sub.add_parser("add")
    acceptance_add.add_argument("--task", required=True)
    acceptance_add.add_argument("--body")
    acceptance_add.add_argument("--body-file", help="Read long body text from a UTF-8 file.")
    acceptance_add.add_argument("--expected-evidence-type", default="diff")
    acceptance_add.add_argument("--from-node", required=True)
    acceptance_add.set_defaults(func=handlers["acceptance_add"])
    acceptance_close = acceptance_sub.add_parser("close")
    acceptance_close.add_argument("--check", required=True)
    acceptance_close.add_argument("--evidence-node", required=True)
    acceptance_close.add_argument("--reason")
    acceptance_close.add_argument("--close-task", action="store_true")
    acceptance_close.add_argument("--override-evidence-type", action="store_true")
    acceptance_close.add_argument("--override-predicate-coverage", action="store_true")
    acceptance_close.add_argument("--elevated-predicate-coverage-override", action="store_true")
    acceptance_close.add_argument("--override-reason")
    acceptance_close.set_defaults(func=handlers["acceptance_close"])
    acceptance_replace = acceptance_sub.add_parser("replace-closure")
    acceptance_replace.add_argument("--check", required=True)
    acceptance_replace.add_argument("--evidence-node", required=True)
    acceptance_replace.add_argument("--reason", required=True)
    acceptance_replace.add_argument("--override-evidence-type", action="store_true")
    acceptance_replace.add_argument("--override-predicate-coverage", action="store_true")
    acceptance_replace.add_argument("--elevated-predicate-coverage-override", action="store_true")
    acceptance_replace.add_argument("--override-reason")
    acceptance_replace.set_defaults(func=handlers["acceptance_replace_closure"])

    jot = subparsers.add_parser("jot")
    jot_sub = jot.add_subparsers(dest="jot_command", required=True)
    jot_add = jot_sub.add_parser("add")
    jot_add.add_argument("--endpoint", required=True)
    jot_add.add_argument("--kind", default="progress")
    jot_add.add_argument("--body")
    jot_add.add_argument("--body-file", help="Read long body text from a UTF-8 file.")
    jot_add.add_argument("--source-node", required=True)
    jot_add.add_argument("--applies-to", action="append", default=[])
    jot_add.add_argument("--run")
    jot_add.add_argument("--label")
    jot_add.add_argument("--active-obligation", action="store_true")
    jot_add.add_argument("--refresh-endpoint", action="store_true")
    jot_add.set_defaults(func=handlers["jot_add"])
    jot_handoff = jot_sub.add_parser("handoff")
    jot_handoff.add_argument("--endpoint", required=True)
    jot_handoff.add_argument("--body")
    jot_handoff.add_argument("--body-file", help="Read long body text from a UTF-8 file.")
    jot_handoff.add_argument("--source-node", required=True)
    jot_handoff.add_argument("--applies-to", action="append", default=[])
    jot_handoff.add_argument("--run")
    jot_handoff.add_argument("--label")
    jot_handoff.add_argument("--refresh-endpoint", action="store_true")
    jot_handoff.set_defaults(func=handlers["jot_handoff"])

    assumption = subparsers.add_parser("assumption")
    assumption_sub = assumption.add_subparsers(dest="assumption_command", required=True)
    assumption_add = assumption_sub.add_parser("add")
    assumption_add.add_argument("--body")
    assumption_add.add_argument("--body-file", help="Read long body text from a UTF-8 file.")
    assumption_add.add_argument("--source-node", required=True)
    assumption_add.add_argument("--label")
    assumption_add.add_argument("--applies-to", action="append", default=[])
    assumption_add.set_defaults(func=handlers["assumption_add"])

    unresolved = subparsers.add_parser("unresolved")
    unresolved_sub = unresolved.add_subparsers(dest="unresolved_command", required=True)
    unresolved_add = unresolved_sub.add_parser("add")
    unresolved_add.add_argument("--body")
    unresolved_add.add_argument("--body-file", help="Read long body text from a UTF-8 file.")
    unresolved_add.add_argument("--source-node", required=True)
    unresolved_add.add_argument("--label")
    unresolved_add.add_argument("--applies-to", action="append", default=[])
    unresolved_add.set_defaults(func=handlers["unresolved_add"])

    semantic = subparsers.add_parser("semantic")
    semantic_sub = semantic.add_subparsers(dest="semantic_command", required=True)
    semantic_set_state = semantic_sub.add_parser("set-state")
    semantic_set_state.add_argument("--node", required=True)
    semantic_set_state.add_argument("--state", required=True, type=semantic_state_type, metavar="STATE")
    semantic_set_state.add_argument("--source-node", required=True)
    semantic_set_state.add_argument("--reason")
    semantic_set_state.add_argument("--event-type")
    semantic_set_state.add_argument("--endpoint")
    semantic_set_state.set_defaults(func=handlers["semantic_set_state"])

    why = subparsers.add_parser("why")
    why.add_argument("--path")
    why.add_argument("--line", type=int)
    why.add_argument("--symbol")
    why.set_defaults(func=handlers["why"])
