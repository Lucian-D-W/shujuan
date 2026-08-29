from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from typing import Any


DiscussHandler = Callable[[argparse.Namespace], int]
DISCUSS_HANDLER_KEYS = (
    "capture",
    "inbox",
    "status",
    "review",
    "extract",
    "consume",
    "replace",
)
DISCUSS_DEPENDENCY_KEYS = (
    "connect",
    "create_acceptance_row_for_node",
    "create_discussion_capture",
    "create_edge",
    "create_node",
    "create_scope_contract_row_for_node",
    "create_task_row_for_node",
    "discussion_messages",
    "discussion_rows",
    "endpoint_unreviewed_discussion_count",
    "normalize_mode",
    "now_iso",
    "print_json",
    "query_endpoint",
    "read_arg_or_stdin",
    "record_discussion_lifecycle_event",
    "register_semantic_item",
    "require_node",
    "resolve_discussion_identifier",
    "resolve_discussion_segment",
    "resolve_endpoint_identifier",
    "row_to_dict",
)


def _validate_handlers(handlers: Mapping[str, DiscussHandler]) -> None:
    missing = [key for key in DISCUSS_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"discuss command boundary is missing: {', '.join(missing)}")


def _discuss_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in DISCUSS_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"discuss handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in DISCUSS_DEPENDENCY_KEYS}


def _require_dependency(name: str) -> Any:
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"discuss command dependency is not configured: {name}")
    return value


def build_discuss_handlers(deps: Mapping[str, Any]) -> dict[str, DiscussHandler]:
    """Build discussion handlers from cli.py-owned graph/session/discussion helpers."""
    globals().update(_discuss_dependencies(deps))
    return {
        "capture": cmd_discuss_capture,
        "inbox": cmd_discuss_inbox,
        "status": cmd_discuss_status,
        "review": cmd_discuss_review,
        "extract": cmd_discuss_extract,
        "consume": cmd_discuss_consume,
        "replace": cmd_discuss_replace,
    }


def cmd_discuss_review(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = _require_dependency("connect")(repo)
    endpoint_name = _require_dependency("resolve_endpoint_identifier")(conn, repo, args.endpoint) if args.endpoint else None
    segment_id = _require_dependency("resolve_discussion_identifier")(conn, repo, args.segment, endpoint_name=endpoint_name)
    segment = _require_dependency("resolve_discussion_segment")(conn, segment_id, endpoint_name=endpoint_name)
    event_id = _require_dependency("record_discussion_lifecycle_event")(
        conn,
        segment_id=segment["id"],
        event_type="reviewed",
        to_status="reviewed",
        source_node_id=args.source_node,
        actor=args.actor,
        reason=args.reason or "Discussion segment reviewed without semantic extraction.",
    )
    conn.commit()
    _require_dependency("print_json")(
        {"ok": True, "segment_id": segment["id"], "segment_node_id": segment["node_id"], "status": "reviewed", "lifecycle_event_id": event_id}
    )
    return 0


def cmd_discuss_extract(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = _require_dependency("connect")(repo)
    endpoint_name = _require_dependency("resolve_endpoint_identifier")(conn, repo, args.endpoint) if args.endpoint else None
    segment_id = _require_dependency("resolve_discussion_identifier")(conn, repo, args.segment, endpoint_name=endpoint_name)
    segment = _require_dependency("resolve_discussion_segment")(conn, segment_id, endpoint_name=endpoint_name)
    row_to_dict_fn = _require_dependency("row_to_dict")
    if not args.type:
        messages = [row_to_dict_fn(row) for row in _require_dependency("discussion_messages")(conn, segment["id"])]
        _require_dependency("print_json")(
            {
                "ok": True,
                "segment": row_to_dict_fn(segment),
                "messages": messages,
                "note": "No semantic nodes were created. Pass --type and --label to extract reviewable discussion into a semantic item.",
            }
        )
        return 0
    if not args.label:
        raise SystemExit("discuss extract --type requires --label")
    if args.type == "acceptance_check" and not args.task:
        raise SystemExit("discuss extract --type acceptance_check requires --task")
    body = args.summary or args.label
    node_id = _require_dependency("create_node")(
        conn,
        args.type,
        args.label,
        args.summary,
        {"discussion_segment_id": segment["id"], "manual": True, "extracted_from_discussion": True},
    )
    semantic_item_id = _require_dependency("register_semantic_item")(
        conn,
        node_id,
        args.type,
        state="active",
        source_node=segment["node_id"],
        scope_node=node_id,
        event_type="created",
        reason=args.reason or "Manual discussion extraction from captured segment.",
        props={"manual": True, "discussion_segment_id": segment["id"]},
    )
    edge_id = _require_dependency("create_edge")(
        conn,
        node_id,
        "DERIVED_FROM",
        segment["node_id"],
        reason=args.reason or "Semantic item extracted from discussion segment.",
        created_by="agent",
    )
    structured: dict[str, Any] = {"created": False}
    if args.type == "scope_contract":
        contract_id = _require_dependency("create_scope_contract_row_for_node")(
            conn,
            node_id=node_id,
            body=body,
            source_node_id=segment["node_id"],
            non_downgrade_rules=args.non_downgrade_rules,
        )
        structured = {"created": True, "scope_contract_id": contract_id, "contract_id": contract_id}
    elif args.type == "task":
        task_id = _require_dependency("create_task_row_for_node")(
            conn,
            node_id=node_id,
            body=body,
            contract_id=args.contract,
            parent_task_id=args.parent,
            optional=args.optional,
            created_from_node_id=segment["node_id"],
        )
        structured = {"created": True, "task_id": task_id}
    elif args.type == "acceptance_check":
        check_id = _require_dependency("create_acceptance_row_for_node")(
            conn,
            node_id=node_id,
            task_id=args.task,
            body=body,
            expected_evidence_type=args.expected_evidence_type,
        )
        structured = {"created": True, "acceptance_check_id": check_id}
    lifecycle_event_id = _require_dependency("record_discussion_lifecycle_event")(
        conn,
        segment_id=segment["id"],
        event_type="extracted",
        to_status="extracted",
        source_node_id=node_id,
        actor=args.actor,
        reason=args.reason or f"Discussion segment extracted into {args.type}.",
        metadata={"created_node_id": node_id, "type": args.type, "structured": structured},
    )
    conn.commit()
    _require_dependency("print_json")(
        {
            "ok": True,
            "segment_id": segment["id"],
            "segment_node_id": segment["node_id"],
            "node_id": node_id,
            "semantic_item_id": semantic_item_id,
            "edge_ids": [edge_id],
            "lifecycle_event_id": lifecycle_event_id,
            "structured": structured,
            **{key: value for key, value in structured.items() if key.endswith("_id")},
        }
    )
    return 0


def cmd_discuss_consume(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = _require_dependency("connect")(repo)
    endpoint_name = _require_dependency("resolve_endpoint_identifier")(conn, repo, args.endpoint) if args.endpoint else None
    segment_id = _require_dependency("resolve_discussion_identifier")(conn, repo, args.segment, endpoint_name=endpoint_name)
    segment = _require_dependency("resolve_discussion_segment")(conn, segment_id, endpoint_name=endpoint_name)
    targets: list[tuple[str, str]] = []
    for node_id in args.by_node:
        _require_dependency("require_node")(conn, node_id, "consumer node")
        targets.append((node_id, "node"))
    for run_id in args.run:
        run = conn.execute("SELECT node_id FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            raise SystemExit(f"run not found: {run_id}")
        targets.append((str(run["node_id"]), "run"))
    if not targets:
        raise SystemExit("discuss consume requires --by-node or --run")
    edge_ids = [
        _require_dependency("create_edge")(
            conn,
            target_node_id,
            "DERIVED_FROM",
            segment["node_id"],
            reason=args.reason or "Consumer node uses captured discussion segment.",
            created_by="agent",
        )
        for target_node_id, _kind in targets
    ]
    lifecycle_event_id = _require_dependency("record_discussion_lifecycle_event")(
        conn,
        segment_id=segment["id"],
        event_type="consumed",
        to_status="consumed",
        source_node_id=targets[0][0],
        actor=args.actor,
        reason=args.reason or "Discussion segment consumed by execution or semantic work.",
        metadata={"targets": [{"node_id": node_id, "kind": kind} for node_id, kind in targets]},
    )
    conn.commit()
    _require_dependency("print_json")({"ok": True, "segment_id": segment["id"], "status": "consumed", "edge_ids": edge_ids, "lifecycle_event_id": lifecycle_event_id})
    return 0


def cmd_discuss_replace(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = _require_dependency("connect")(repo)
    endpoint_name = _require_dependency("resolve_endpoint_identifier")(conn, repo, args.endpoint) if args.endpoint else None
    segment_id = _require_dependency("resolve_discussion_identifier")(conn, repo, args.segment, endpoint_name=endpoint_name)
    old_segment = _require_dependency("resolve_discussion_segment")(conn, segment_id, endpoint_name=endpoint_name)
    replacement = None
    replacement_node_id = args.by_segment
    if args.replacement_content is not None:
        if not endpoint_name:
            endpoint = conn.execute("SELECT name FROM endpoints WHERE id = ?", (old_segment["endpoint_id"],)).fetchone()
            endpoint_name = endpoint["name"] if endpoint else None
        if not endpoint_name:
            raise SystemExit("discussion replacement content requires --endpoint when the old segment endpoint cannot be resolved")
        replacement = _require_dependency("create_discussion_capture")(
            conn,
            endpoint_name=endpoint_name,
            content=_require_dependency("read_arg_or_stdin")(args.replacement_content),
            actor=args.actor,
            session_id=old_segment["session_id"],
            agent_name=None,
            model_name=None,
            source=args.source or "discussion_replace",
            title=args.title,
            mode=_require_dependency("normalize_mode")(args.mode or "capture"),
            reviewed=args.reviewed,
        )
        replacement_node_id = replacement["segment_node_id"]
    if not replacement_node_id:
        raise SystemExit("discuss replace requires --by-segment or --replacement-content")
    replacement_segment = _require_dependency("resolve_discussion_segment")(conn, replacement_node_id, endpoint_name=endpoint_name)
    edge_id = _require_dependency("create_edge")(
        conn,
        replacement_segment["node_id"],
        "SUPERSEDES",
        old_segment["node_id"],
        reason=args.reason or "Discussion segment replaced by newer captured segment.",
        created_by="agent",
    )
    lifecycle_event_id = _require_dependency("record_discussion_lifecycle_event")(
        conn,
        segment_id=old_segment["id"],
        event_type="superseded",
        to_status="superseded",
        source_node_id=replacement_segment["node_id"],
        actor=args.actor,
        reason=args.reason or "Discussion segment superseded by replacement.",
        metadata={"replacement_segment_id": replacement_segment["id"], "replacement_node_id": replacement_segment["node_id"]},
    )
    conn.commit()
    _require_dependency("print_json")(
        {
            "ok": True,
            "segment_id": old_segment["id"],
            "status": "superseded",
            "replacement_segment_id": replacement_segment["id"],
            "replacement": replacement,
            "edge_id": edge_id,
            "lifecycle_event_id": lifecycle_event_id,
        }
    )
    return 0


def cmd_discuss_capture(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    content = _require_dependency("read_arg_or_stdin")(args.content)
    conn = _require_dependency("connect")(repo)
    endpoint_name = _require_dependency("resolve_endpoint_identifier")(conn, repo, args.endpoint)
    result = _require_dependency("create_discussion_capture")(
        conn,
        endpoint_name=endpoint_name,
        content=content,
        actor=args.actor,
        session_id=args.session_id,
        agent_name=args.agent_name,
        model_name=args.model_name,
        source=args.source,
        title=args.title,
        mode=_require_dependency("normalize_mode")(args.mode or "capture"),
        reviewed=args.reviewed,
    )
    conn.commit()
    _require_dependency("print_json")(
        {"ok": True, **result, "unreviewed_count": _require_dependency("endpoint_unreviewed_discussion_count")(conn, endpoint_name)}
    )
    return 0


def cmd_discuss_status(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = _require_dependency("connect")(repo)
    endpoint_name = _require_dependency("resolve_endpoint_identifier")(conn, repo, args.endpoint)
    endpoint = _require_dependency("query_endpoint")(conn, endpoint_name)
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM discussion_segments
        WHERE endpoint_id = ?
        GROUP BY status
        """,
        (endpoint["id"],),
    ).fetchall()
    by_status = {str(row["status"]): int(row["count"]) for row in rows}
    _require_dependency("print_json")(
        {
            "ok": True,
            "endpoint": endpoint_name,
            "unreviewed_count": by_status.get("unreviewed", 0),
            "reviewed_count": by_status.get("reviewed", 0),
            "status_counts": by_status,
        }
    )
    return 0


def cmd_discuss_inbox(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = _require_dependency("connect")(repo)
    endpoint_name = _require_dependency("resolve_endpoint_identifier")(conn, repo, args.endpoint)
    rows = _require_dependency("discussion_rows")(conn, endpoint_name, include_reviewed=args.include_reviewed, limit=args.limit)
    reviewed_ids: list[str] = []
    lifecycle_event_ids: list[str] = []
    if args.mark_reviewed:
        timestamp = _require_dependency("now_iso")()
        for row in rows:
            lifecycle_event_ids.append(
                _require_dependency("record_discussion_lifecycle_event")(
                    conn,
                    segment_id=row["id"],
                    event_type="discussion_review",
                    to_status="reviewed",
                    actor="agent",
                    reason="Marked reviewed from discussion inbox.",
                    metadata={"command": "discuss inbox --mark-reviewed", "endpoint": endpoint_name},
                )
            )
            conn.execute(
                "UPDATE interaction_events SET reviewed_at = COALESCE(reviewed_at, ?) WHERE id = ?",
                (timestamp, row["event_id"]),
            )
            reviewed_ids.append(str(row["id"]))
        conn.commit()
    _require_dependency("print_json")(
        {
            "ok": True,
            "endpoint": endpoint_name,
            "segments": [_require_dependency("row_to_dict")(row) for row in rows],
            "marked_reviewed": reviewed_ids,
            "lifecycle_events": lifecycle_event_ids,
            "unreviewed_count": _require_dependency("endpoint_unreviewed_discussion_count")(conn, endpoint_name),
        }
    )
    return 0


def register_discuss(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, DiscussHandler],
) -> None:
    """Register the discuss command family while cli.py keeps global dispatch."""
    _validate_handlers(handlers)

    discuss = subparsers.add_parser("discuss")
    discuss_sub = discuss.add_subparsers(dest="discuss_command", required=True)
    discuss_capture = discuss_sub.add_parser("capture")
    discuss_capture.add_argument("--endpoint", required=True)
    discuss_capture.add_argument("--content")
    discuss_capture.add_argument("--actor", default="user")
    discuss_capture.add_argument("--session-id")
    discuss_capture.add_argument("--agent-name", default="codex")
    discuss_capture.add_argument("--model-name")
    discuss_capture.add_argument("--source")
    discuss_capture.add_argument("--title")
    discuss_capture.add_argument("--mode", default="capture")
    discuss_capture.add_argument("--reviewed", action="store_true")
    discuss_capture.set_defaults(func=handlers["capture"])
    discuss_inbox = discuss_sub.add_parser("inbox")
    discuss_inbox.add_argument("--endpoint", required=True)
    discuss_inbox.add_argument("--include-reviewed", action="store_true")
    discuss_inbox.add_argument("--mark-reviewed", action="store_true")
    discuss_inbox.add_argument("--limit", type=int, default=20)
    discuss_inbox.set_defaults(func=handlers["inbox"])
    discuss_status = discuss_sub.add_parser("status")
    discuss_status.add_argument("--endpoint", required=True)
    discuss_status.set_defaults(func=handlers["status"])
    discuss_review = discuss_sub.add_parser("review")
    discuss_review.add_argument("--segment", required=True)
    discuss_review.add_argument("--endpoint")
    discuss_review.add_argument("--source-node")
    discuss_review.add_argument("--actor", default="agent")
    discuss_review.add_argument("--reason")
    discuss_review.set_defaults(func=handlers["review"])
    discuss_extract = discuss_sub.add_parser("extract")
    discuss_extract.add_argument("--segment", required=True)
    discuss_extract.add_argument("--endpoint")
    discuss_extract.add_argument("--type")
    discuss_extract.add_argument("--label")
    discuss_extract.add_argument("--summary")
    discuss_extract.add_argument("--reason")
    discuss_extract.add_argument("--actor", default="agent")
    discuss_extract.add_argument("--contract")
    discuss_extract.add_argument("--parent")
    discuss_extract.add_argument("--optional", action="store_true")
    discuss_extract.add_argument("--task")
    discuss_extract.add_argument("--expected-evidence-type")
    discuss_extract.add_argument("--non-downgrade-rules")
    discuss_extract.set_defaults(func=handlers["extract"])
    discuss_consume = discuss_sub.add_parser("consume")
    discuss_consume.add_argument("--segment", required=True)
    discuss_consume.add_argument("--endpoint")
    discuss_consume.add_argument("--by-node", action="append", default=[])
    discuss_consume.add_argument("--run", action="append", default=[])
    discuss_consume.add_argument("--actor", default="agent")
    discuss_consume.add_argument("--reason")
    discuss_consume.set_defaults(func=handlers["consume"])
    discuss_replace = discuss_sub.add_parser("replace")
    discuss_replace.add_argument("--segment", required=True)
    discuss_replace.add_argument("--endpoint")
    discuss_replace.add_argument("--by-segment")
    discuss_replace.add_argument("--replacement-content")
    discuss_replace.add_argument("--actor", default="agent")
    discuss_replace.add_argument("--source")
    discuss_replace.add_argument("--title")
    discuss_replace.add_argument("--mode", default="capture")
    discuss_replace.add_argument("--reviewed", action="store_true")
    discuss_replace.add_argument("--reason")
    discuss_replace.set_defaults(func=handlers["replace"])
    discuss_supersede = discuss_sub.add_parser("supersede")
    discuss_supersede.add_argument("--segment", required=True)
    discuss_supersede.add_argument("--endpoint")
    discuss_supersede.add_argument("--by-segment")
    discuss_supersede.add_argument("--replacement-content")
    discuss_supersede.add_argument("--actor", default="agent")
    discuss_supersede.add_argument("--source")
    discuss_supersede.add_argument("--title")
    discuss_supersede.add_argument("--mode", default="capture")
    discuss_supersede.add_argument("--reviewed", action="store_true")
    discuss_supersede.add_argument("--reason")
    discuss_supersede.set_defaults(func=handlers["replace"])
