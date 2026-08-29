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


def _configure(deps: Mapping[str, Any]) -> None:
    globals().update(deps)

def audit_consume_payload(conn: sqlite3.Connection, endpoint_name: str, *, require_zero: bool = False) -> dict[str, Any]:
    findings = active_audit_findings_for_endpoint(conn, endpoint_name)
    active_count = len(findings)
    return {
        "ok": not require_zero or active_count == 0,
        "endpoint": endpoint_name,
        "require_zero": require_zero,
        "active_count": active_count,
        "active_audit_findings": findings,
        "recommendation": (
            "Resolve active audit findings with source-backed lifecycle evidence before strict closeout."
            if active_count
            else "No active audit findings block closeout."
        ),
    }

def cmd_audit_consume(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    payload = audit_consume_payload(conn, args.endpoint, require_zero=args.require_zero)
    print_json(payload)
    return 0 if payload["ok"] or args.allow_fail else 1

def read_audit_body(repo: Path, path_arg: str | None, body_arg: str | None) -> tuple[str, Path | None]:
    if path_arg:
        path = Path(path_arg)
        if not path.is_absolute():
            path = repo / path
        if not path.exists() or not path.is_file():
            raise SystemExit(f"audit file not found: {path}")
        return path.read_text(encoding="utf-8"), path
    return read_arg_or_stdin(body_arg), None

def cmd_audit_record(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    body, path = read_audit_body(repo, args.path, args.body)
    conn = connect(repo)
    require_node(conn, args.source_node, "audit source node")
    endpoint = query_endpoint(conn, args.endpoint)
    artifact_props: dict[str, Any]
    if path:
        artifact_props = capture_artifact_file(repo, path, prefix="audit")
    else:
        capture_ref = write_artifact_text(repo, f"audit_{new_id('capture')}.md", body)
        artifact_props = {
            "original_path": None,
            "capture_ref": capture_ref,
            "size": len(body.encode("utf-8")),
            "is_text": True,
            **text_artifact_hash_props(body),
        }
    artifact_props.update(
        {
            "artifact_type": "audit_summary",
            "endpoint": args.endpoint,
            "body_hash": artifact_props.get("normalized_text_hash") or sha256_text(body),
        }
    )
    artifact_node_id = create_node(
        conn,
        "artifact",
        args.label or f"audit summary: {args.endpoint}",
        body[:240],
        artifact_props,
    )
    source_edges = link_source_nodes(conn, artifact_node_id, [args.source_node], reason="Audit artifact derived from source evidence node.")
    applies_edges = [
        create_edge(conn, artifact_node_id, "APPLIES_TO", endpoint["node_id"], reason="Audit artifact applies to endpoint.", created_by="agent")
    ]
    linked_tasks = []
    for task_id in args.task:
        task = conn.execute("SELECT id, node_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise SystemExit(f"task not found: {task_id}")
        applies_edges.append(create_edge(conn, artifact_node_id, "APPLIES_TO", task["node_id"], reason="Audit artifact applies to task.", created_by="agent"))
        linked_tasks.append(row_to_dict(task))
    linked_checks = []
    for check_id in args.check:
        check = conn.execute("SELECT id, node_id, task_id FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone()
        if not check:
            raise SystemExit(f"acceptance check not found: {check_id}")
        applies_edges.append(create_edge(conn, artifact_node_id, "APPLIES_TO", check["node_id"], reason="Audit artifact applies to acceptance check.", created_by="agent"))
        linked_checks.append(row_to_dict(check))
    finding_node_ids = []
    findings = args.finding or [body.splitlines()[0].strip() if body.splitlines() else "Audit summary recorded."]
    for index, finding in enumerate(findings, start=1):
        finding_node_id = create_node(
            conn,
            "audit_finding",
            f"{args.label or args.endpoint} finding {index}",
            finding[:240],
            {
                "body": finding,
                "endpoint": args.endpoint,
                "artifact_node_id": artifact_node_id,
                "source_node_id": args.source_node,
            },
        )
        register_semantic_item(
            conn,
            finding_node_id,
            "audit_finding",
            state="active",
            source_node=artifact_node_id,
            scope_node=endpoint["node_id"],
            event_type="created",
            reason="Structured audit finding recorded.",
            props={"body": finding, "endpoint": args.endpoint},
        )
        create_edge(conn, finding_node_id, "DERIVED_FROM", artifact_node_id, reason="Structured audit finding derived from audit artifact.", created_by="agent")
        create_edge(conn, finding_node_id, "APPLIES_TO", endpoint["node_id"], reason="Structured audit finding applies to endpoint.", created_by="agent")
        for task in linked_tasks:
            create_edge(conn, finding_node_id, "APPLIES_TO", task["node_id"], reason="Structured audit finding applies to task.", created_by="agent")
        for check in linked_checks:
            create_edge(conn, finding_node_id, "APPLIES_TO", check["node_id"], reason="Structured audit finding applies to acceptance check.", created_by="agent")
        finding_node_ids.append(finding_node_id)
    refresh_result = None
    if args.refresh_endpoint:
        refresh_result = refresh_endpoint_projection(conn, args.endpoint, from_node=artifact_node_id)
    conn.commit()
    print_json(
        {
            "ok": True,
            "endpoint": args.endpoint,
            "artifact_node_id": artifact_node_id,
            "artifact": artifact_props,
            "audit_finding_node_ids": finding_node_ids,
            "source_edges": source_edges,
            "applies_edges": applies_edges,
            "linked_tasks": linked_tasks,
            "linked_checks": linked_checks,
            "endpoint_refresh": refresh_result,
        }
    )
    return 0

def cmd_audit_import_agent_output(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    body, path = read_audit_body(repo, args.path, args.body)
    conn = connect(repo)
    require_node(conn, args.source_node, "agent output source node")
    endpoint = query_endpoint(conn, args.endpoint)
    if path:
        artifact_props = capture_artifact_file(repo, path, prefix="agent_output")
    else:
        capture_ref = write_artifact_text(repo, f"agent_output_{new_id('capture')}.md", body)
        artifact_props = {
            "original_path": None,
            "capture_ref": capture_ref,
            "size": len(body.encode("utf-8")),
            "is_text": True,
            **text_artifact_hash_props(body),
        }
    artifact_props.update({"artifact_type": "agent_output", "endpoint": args.endpoint, "body_hash": artifact_props.get("normalized_text_hash") or sha256_text(body)})
    artifact_node_id = create_node(conn, "artifact", args.label or f"agent output: {args.endpoint}", body[:240], artifact_props)
    artifact_semantic_item_id = register_evidence_lifecycle(
        conn,
        artifact_node_id,
        source_node=args.source_node,
        reason="Imported agent output artifact recorded.",
    )
    source_edges = link_source_nodes(conn, artifact_node_id, [args.source_node], reason="Agent output artifact derived from source node.")
    applies_edges = [
        create_edge(conn, artifact_node_id, "APPLIES_TO", endpoint["node_id"], reason="Agent output applies to endpoint.", created_by="agent")
    ]
    warnings = []
    classification = args.classification
    if classification is None:
        classification = "actionable" if args.finding else "summary"
        warnings.append(f"unclassified_agent_output_defaulted_to_{classification}")
    finding_node_ids = []
    work_note_result = None
    if classification == "actionable":
        for index, finding in enumerate(args.finding or [body.splitlines()[0].strip() if body.splitlines() else "Agent output imported."], start=1):
            finding_node_id = create_node(
                conn,
                "audit_finding",
                f"{args.label or args.endpoint} agent output finding {index}",
                finding[:240],
                {
                    "body": finding,
                    "endpoint": args.endpoint,
                    "artifact_node_id": artifact_node_id,
                    "source_node_id": args.source_node,
                    "classification": classification,
                },
            )
            register_semantic_item(
                conn,
                finding_node_id,
                "audit_finding",
                state="active",
                source_node=artifact_node_id,
                scope_node=endpoint["node_id"],
                event_type="created",
                reason="Imported actionable agent output finding recorded.",
                props={"body": finding, "endpoint": args.endpoint, "classification": classification},
            )
            create_edge(conn, finding_node_id, "DERIVED_FROM", artifact_node_id, reason="Finding derived from imported agent output.", created_by="agent")
            create_edge(conn, finding_node_id, "APPLIES_TO", endpoint["node_id"], reason="Finding applies to endpoint.", created_by="agent")
            finding_node_ids.append(finding_node_id)
    else:
        note_kind = {
            "summary": "handoff",
            "needs_user_decision": "needs_user_decision",
            "product_backlog": "product_backlog",
            "provider_hypothesis": "provider_hypothesis",
        }[classification]
        work_note_result = create_work_note(
            conn,
            endpoint_name=args.endpoint,
            kind=note_kind,
            body=body,
            source_node=artifact_node_id,
            applies_to=[],
            active_obligation=classification == "needs_user_decision",
            label=args.label or f"{classification} agent output: {args.endpoint}",
        )
    refresh_result = maybe_refresh_endpoint(conn, args.endpoint, artifact_node_id) if args.refresh_endpoint else None
    conn.commit()
    print_json(
        {
            "ok": True,
            "endpoint": args.endpoint,
            "classification": classification,
            "warnings": warnings,
            "artifact_node_id": artifact_node_id,
            "artifact_semantic_item_id": artifact_semantic_item_id,
            "artifact": artifact_props,
            "audit_finding_node_ids": finding_node_ids,
            "work_note": work_note_result,
            "source_edges": source_edges,
            "applies_edges": applies_edges,
            "endpoint_refresh": refresh_result,
        }
    )
    return 0

def build_audit_handlers(deps: Mapping[str, Any]) -> dict[str, Any]:
    _configure(deps)
    return {
        "record": cmd_audit_record,
        "import_agent_output": cmd_audit_import_agent_output,
        "consume": cmd_audit_consume,
    }


def register_audit(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, Any]) -> None:
    audit = subparsers.add_parser("audit")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_record = audit_sub.add_parser("record")
    audit_record.add_argument("--endpoint", required=True)
    audit_record.add_argument("--source-node", required=True)
    audit_record.add_argument("--path")
    audit_record.add_argument("--body")
    audit_record.add_argument("--label")
    audit_record.add_argument("--task", action="append", default=[])
    audit_record.add_argument("--check", action="append", default=[])
    audit_record.add_argument("--finding", action="append", default=[])
    audit_record.add_argument("--refresh-endpoint", action="store_true")
    audit_record.set_defaults(func=handlers["record"])
    audit_import = audit_sub.add_parser("import-agent-output")
    audit_import.add_argument("--endpoint", required=True)
    audit_import.add_argument("--source-node", required=True)
    audit_import.add_argument("--path")
    audit_import.add_argument("--body")
    audit_import.add_argument("--label")
    audit_import.add_argument("--classification", choices=["summary", "actionable", "needs_user_decision", "product_backlog", "provider_hypothesis"])
    audit_import.add_argument("--finding", action="append", default=[])
    audit_import.add_argument("--refresh-endpoint", action="store_true")
    audit_import.set_defaults(func=handlers["import_agent_output"])
    audit_consume = audit_sub.add_parser("consume")
    audit_consume.add_argument("--endpoint", required=True)
    audit_consume.add_argument("--require-zero", action="store_true")
    audit_consume.add_argument("--allow-fail", action="store_true")
    audit_consume.set_defaults(func=handlers["consume"])
