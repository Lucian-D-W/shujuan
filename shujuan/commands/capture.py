from __future__ import annotations

import argparse
from datetime import datetime
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..services.sovereignty_gate import explicit_no_governance_reasons, no_governance_payload


CaptureHandler = Callable[[argparse.Namespace], int]
CAPTURE_HANDLER_KEYS = (
    "doc_import",
    "hook_user_prompt",
    "hook_stop",
    "session_import",
    "context_load",
    "workflow_begin",
    "workflow_trace",
    "mode_suggest",
    "alias_set",
    "alias_list",
)
CAPTURE_DEPENDENCY_KEYS = (
    "connect",
    "create_discussion_segment",
    "create_edge",
    "create_node",
    "ensure_session",
    "first_heading",
    "insert_message",
    "json_dumps",
    "load_aliases",
    "load_context_payload",
    "mode_contract_payload",
    "mode_gate_warnings",
    "new_id",
    "normalize_mode",
    "now_iso",
    "print_json",
    "json_error_payload",
    "read_arg_or_stdin",
    "read_file_text",
    "relpath",
    "resolve_endpoint_identifier",
    "read_trace_events",
    "save_aliases",
    "sha256_text",
    "split_markdown_sections",
    "suggest_mode_from_args",
    "transcript_records",
    "append_trace_event",
)


def _validate_handlers(handlers: Mapping[str, CaptureHandler]) -> None:
    missing = [key for key in CAPTURE_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"capture command boundary is missing: {', '.join(missing)}")


def _capture_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in CAPTURE_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"capture handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in CAPTURE_DEPENDENCY_KEYS}


def _require_dependency(name: str) -> Any:
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"capture command dependency is not configured: {name}")
    return value


def build_capture_handlers(deps: Mapping[str, Any]) -> dict[str, CaptureHandler]:
    """Build capture handlers from cli.py-owned graph/session/context helpers."""
    globals().update(_capture_dependencies(deps))
    return {
        "doc_import": cmd_doc_import,
        "hook_user_prompt": cmd_hook_user_prompt,
        "hook_stop": cmd_hook_stop,
        "session_import": cmd_session_import,
        "context_load": cmd_context_load,
        "workflow_begin": cmd_workflow_begin,
        "workflow_trace": cmd_workflow_trace,
        "mode_suggest": cmd_mode_suggest,
        "alias_set": cmd_alias_set,
        "alias_list": cmd_alias_list,
    }


def _explicit_no_governance_payload(*, command: str, content: str, contract: dict[str, Any] | None = None) -> dict[str, Any]:
    return no_governance_payload(command=command, content=content, contract=contract)


def _content_requests_no_governance(content: str) -> tuple[bool, list[str]]:
    direct_reasons = explicit_no_governance_reasons(content)
    if direct_reasons:
        return True, direct_reasons
    mode, reasons = _require_dependency("suggest_mode_from_args")(
        argparse.Namespace(intent=content, mode=None, no_governance=False, capture_only=False)
    )
    return mode == "no_governance", reasons


def cmd_doc_import(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    path = Path(args.file)
    if not path.is_absolute():
        path = repo / path
    body = _require_dependency("read_file_text")(path)
    conn = _require_dependency("connect")(repo)
    relpath_fn = _require_dependency("relpath")
    json_dumps_fn = _require_dependency("json_dumps")
    sha256_text_fn = _require_dependency("sha256_text")
    create_node_fn = _require_dependency("create_node")
    create_edge_fn = _require_dependency("create_edge")
    new_id_fn = _require_dependency("new_id")
    now_iso_fn = _require_dependency("now_iso")
    title = args.title or _require_dependency("first_heading")(body) or path.stem
    doc_node_id = create_node_fn(
        conn,
        "source_document",
        title,
        f"Imported {args.source_type} document",
        {"origin": args.origin or relpath_fn(path, repo)},
    )
    doc_id = new_id_fn("doc")
    conn.execute(
        """
        INSERT INTO source_documents
          (id, node_id, title, source_type, origin, body, content_hash, imported_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            doc_node_id,
            title,
            args.source_type,
            args.origin or relpath_fn(path, repo),
            body,
            sha256_text_fn(body),
            now_iso_fn(),
            json_dumps_fn({"path": relpath_fn(path, repo)}),
        ),
    )
    section_ids: list[str] = []
    for index, section in enumerate(_require_dependency("split_markdown_sections")(body, args.max_chars)):
        node_id = create_node_fn(
            conn,
            "document_section",
            section["heading"] or f"{title} section {index + 1}",
            section["body"].strip()[:240],
            {"document_id": doc_id, "section_index": index},
        )
        section_id = new_id_fn("section")
        conn.execute(
            """
            INSERT INTO document_sections
              (id, document_id, node_id, section_index, heading, body,
               start_offset, end_offset, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                section_id,
                doc_id,
                node_id,
                index,
                section["heading"],
                section["body"],
                section["start_offset"],
                section["end_offset"],
                sha256_text_fn(section["body"]),
            ),
        )
        create_edge_fn(
            conn,
            node_id,
            "DERIVED_FROM",
            doc_node_id,
            reason="Document section was mechanically sliced from source document.",
        )
        section_ids.append(section_id)
    conn.commit()
    _require_dependency("print_json")(
        {
            "ok": True,
            "document_id": doc_id,
            "document_node_id": doc_node_id,
            "section_ids": section_ids,
            "sections": len(section_ids),
            "content_hash": sha256_text_fn(body),
        }
    )
    return 0


def cmd_hook_user_prompt(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    content = _require_dependency("read_arg_or_stdin")(args.content, file_path=getattr(args, "content_file", None), label="content")
    no_governance, reasons = _content_requests_no_governance(content)
    if no_governance:
        payload = _explicit_no_governance_payload(
            command="hook user-prompt",
            content=content,
            contract=_require_dependency("mode_contract_payload")("no_governance"),
        )
        payload["reasons"] = reasons
        _require_dependency("print_json")(payload)
        return 0
    conn = _require_dependency("connect")(repo)
    session_id = _require_dependency("ensure_session")(
        conn,
        session_id=args.session_id,
        agent_name=args.agent_name,
        model_name=args.model_name,
        source=args.source,
        metadata={"event_type": "user_prompt"},
    )
    message_id, node_id = _require_dependency("insert_message")(
        conn,
        session_id=session_id,
        actor="user",
        content=content,
        metadata={"event_type": "user_prompt", "cwd": str(repo)},
    )
    conn.commit()
    _require_dependency("print_json")({"ok": True, "session_id": session_id, "message_id": message_id, "node_id": node_id})
    return 0


def cmd_hook_stop(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = _require_dependency("connect")(repo)
    now_iso_fn = _require_dependency("now_iso")
    json_dumps_fn = _require_dependency("json_dumps")
    session_id = _require_dependency("ensure_session")(
        conn,
        session_id=args.session_id,
        agent_name=args.agent_name,
        model_name=args.model_name,
        source=args.source,
        metadata={"event_type": "run_stop"},
    )
    message = None
    if args.content is not None:
        content = _require_dependency("read_arg_or_stdin")(args.content)
        message_id, node_id = _require_dependency("insert_message")(
            conn,
            session_id=session_id,
            actor=args.actor,
            content=content,
            metadata={"event_type": "run_stop", "cwd": str(repo)},
        )
        message = {"message_id": message_id, "node_id": node_id}
    conn.execute(
        "UPDATE conversation_sessions SET ended_at = ?, metadata = ? WHERE id = ?",
        (now_iso_fn(), json_dumps_fn({"event_type": "run_stop", "cwd": str(repo)}), session_id),
    )
    if args.run:
        conn.execute(
            "UPDATE agent_runs SET ended_at = COALESCE(ended_at, ?), final_report = COALESCE(final_report, ?) WHERE id = ?",
            (now_iso_fn(), args.content, args.run),
        )
    conn.commit()
    _require_dependency("print_json")({"ok": True, "session_id": session_id, "message": message})
    return 0


def cmd_session_import(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    path = Path(args.transcript)
    if not path.is_absolute():
        path = repo / path
    relpath_fn = _require_dependency("relpath")
    sha256_text_fn = _require_dependency("sha256_text")
    records = _require_dependency("transcript_records")(path)
    conn = _require_dependency("connect")(repo)
    session_id = _require_dependency("ensure_session")(
        conn,
        session_id=args.session_id,
        agent_name=args.agent_name,
        model_name=args.model_name,
        source=args.source or relpath_fn(path, repo),
        metadata={"imported_from": relpath_fn(path, repo)},
    )
    inserted = []
    discussion_messages_for_capture = []
    for record in records:
        content = str(record.get("content") or record.get("message") or record.get("text") or "").strip()
        if not content:
            continue
        actor = str(record.get("actor") or record.get("role") or "user").lower()
        if actor == "assistant":
            actor = "agent"
        message_id, node_id = _require_dependency("insert_message")(
            conn,
            session_id=session_id,
            actor=actor,
            content=content,
            metadata={"imported_from": relpath_fn(path, repo), "raw": record},
            created_at=record.get("created_at") or record.get("timestamp"),
        )
        inserted.append({"message_id": message_id, "node_id": node_id, "actor": actor})
        discussion_messages_for_capture.append(
            {
                "actor": actor,
                "content": content,
                "turn_index": len(inserted) - 1,
                "source_node_id": node_id,
                "source_message_id": message_id,
                "metadata": {
                    "adapter": "session_import",
                    "source": relpath_fn(path, repo),
                    "content_hash": sha256_text_fn(content),
                    "raw": record,
                },
            }
        )
    discussion_capture = None
    if args.capture_discussion:
        if not args.endpoint:
            raise SystemExit("session import --capture-discussion requires --endpoint")
        endpoint_name = _require_dependency("resolve_endpoint_identifier")(conn, repo, args.endpoint)
        discussion_capture = _require_dependency("create_discussion_segment")(
            conn,
            endpoint_name=endpoint_name,
            messages=discussion_messages_for_capture,
            session_id=session_id,
            agent_name=args.agent_name,
            model_name=args.model_name,
            source=args.source or relpath_fn(path, repo),
            title=args.title,
            mode=_require_dependency("normalize_mode")(args.mode or "capture"),
            reviewed=args.reviewed,
            event_type="session_import",
            metadata={"adapter": "session_import", "imported_from": relpath_fn(path, repo)},
        )
    conn.commit()
    _require_dependency("print_json")({"ok": True, "session_id": session_id, "messages": inserted, "discussion_capture": discussion_capture})
    return 0


def cmd_context_load(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = _require_dependency("connect")(repo)
    payload = _require_dependency("load_context_payload")(conn, task=args.task, endpoint=args.endpoint, reason=args.reason)
    conn.commit()
    _require_dependency("print_json")(payload)
    return 0


def cmd_workflow_begin(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    normalize_mode_fn = _require_dependency("normalize_mode")
    mode_contract_payload_fn = _require_dependency("mode_contract_payload")
    content = _require_dependency("read_arg_or_stdin")(args.content, file_path=getattr(args, "content_file", None), label="content")
    no_governance, reasons = _content_requests_no_governance(content)
    if no_governance:
        payload = _explicit_no_governance_payload(
            command="workflow begin",
            content=content,
            contract=mode_contract_payload_fn("no_governance"),
        )
        payload["reasons"] = reasons
        _require_dependency("print_json")(payload)
        return 0
    mode = normalize_mode_fn(getattr(args, "mode", None) or "standard")
    contract = mode_contract_payload_fn(mode)
    if mode == "no_governance":
        _require_dependency("print_json")(
            {
                "ok": True,
                "mode": mode,
                "contract": contract,
                "db_writes": 0,
                "capture_claim": False,
                "context": None,
                "current_handle": None,
                "note": "No Governance returned without connecting to or mutating the shujuan DB.",
            }
        )
        return 0
    if not args.endpoint:
        _require_dependency("print_json")(
            _require_dependency("json_error_payload")(
                "missing_endpoint",
                (
                    f"workflow begin --mode {mode} requires --endpoint so prompt capture and context loading have an explicit scope. "
                    "Pass --endpoint <name>, --endpoint @current.endpoint, or use --mode no-governance for no DB writes."
                ),
                read_only=True,
                mode=mode,
                safe_next_action="Pass --endpoint <name>, --endpoint @current.endpoint, or use --mode no-governance for no DB writes.",
            )
        )
        return 1
    conn = _require_dependency("connect")(repo)
    endpoint_name = _require_dependency("resolve_endpoint_identifier")(conn, repo, args.endpoint)
    task_text = args.task or content
    session_id = _require_dependency("ensure_session")(
        conn,
        session_id=args.session_id,
        agent_name=args.agent_name,
        model_name=args.model_name,
        source=args.source,
        metadata={"event_type": "workflow_begin", "guard": "record_prompt_then_context"},
    )
    message_id, node_id = _require_dependency("insert_message")(
        conn,
        session_id=session_id,
        actor="user",
        content=content,
        metadata={"event_type": "user_prompt", "workflow_begin": True, "cwd": str(repo)},
    )
    context = _require_dependency("load_context_payload")(
        conn,
        task=task_text,
        endpoint=endpoint_name,
        reason=args.reason or "Workflow begin recorded user prompt and loaded context in one step.",
    )
    conn.commit()
    _require_dependency("append_trace_event")(
        repo,
        event_type="workflow_begin",
        endpoint=endpoint_name,
        route="Execute",
        mode=mode,
        read_only=False,
        status="captured",
        details={"session_id": session_id, "message_id": message_id},
    )
    _require_dependency("print_json")(
        {
            "ok": True,
            "mode": mode,
            "contract": contract,
            "session_id": session_id,
            "message_id": message_id,
            "node_id": node_id,
            "endpoint": endpoint_name,
            "context": context,
            "next_steps": [
                "graph candidates/extract from the recorded message when semantic nodes are needed",
                "create or link task/acceptance_check before exec start",
                "exec start before editing files",
            ],
        }
    )
    return 0


def cmd_mode_suggest(args: argparse.Namespace) -> int:
    mode, reasons = _require_dependency("suggest_mode_from_args")(args)
    warnings = _require_dependency("mode_gate_warnings")(mode, getattr(args, "intent", None))
    if getattr(args, "mode", None) and getattr(args, "no_governance", False):
        conflict = {
            "code": "mode_flag_conflict_explicit_mode_overrode_no_governance",
            "message": "Explicit --mode selected a governance mode while --no-governance was also supplied; choose one mode instead of silently escalating.",
            "requested_mode": mode,
            "selected_mode": None,
            "no_silent_execution_escalation": True,
        }
        warnings.append(conflict)
        _require_dependency("print_json")(
            {
                "ok": False,
                "usable": False,
                "suggested_mode": None,
                "requested_mode": mode,
                "reasons": reasons,
                "warnings": warnings,
                "errors": [conflict],
                "contract": None,
                "next_action": "Remove either --mode or --no-governance and rerun mode suggest.",
            }
        )
        return 1
    if getattr(args, "mode", None) and getattr(args, "capture_only", False):
        warnings.append(
            {
                "code": "mode_flag_conflict_explicit_mode_overrode_capture_only",
                "message": "Explicit --mode selected a mode while --capture-only was also supplied.",
                "selected_mode": mode,
                "no_silent_execution_escalation": True,
            }
        )
    if any(reason.startswith("ambiguous_intent:") for reason in reasons):
        warnings.append(
            {
                "code": "mode_intent_ambiguous",
                "message": "Intent matched multiple governance modes; suggested Explore so source can be captured without starting an execution run.",
                "matched_reasons": [reason for reason in reasons if reason.startswith("intent:")],
                "no_silent_execution_escalation": True,
            }
        )
    _require_dependency("print_json")(
        {
            "ok": True,
            "suggested_mode": mode,
            "reasons": reasons,
            "warnings": warnings,
            "contract": _require_dependency("mode_contract_payload")(mode),
        }
    )
    return 0


def cmd_workflow_trace(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    endpoint_name = args.endpoint
    events = _require_dependency("read_trace_events")(repo, endpoint=endpoint_name)
    if getattr(args, "since", None):
        try:
            since = datetime.fromisoformat(str(args.since).replace("Z", "+00:00"))
        except ValueError:
            _require_dependency("print_json")(
                {
                    "ok": False,
                    "read_only": True,
                    "error": {"code": "invalid_since_timestamp", "message": "--since must be an ISO-8601 timestamp"},
                }
            )
            return 1
        filtered: list[dict[str, Any]] = []
        for event in events:
            raw_timestamp = event.get("timestamp")
            if not raw_timestamp:
                continue
            try:
                event_time = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
            except ValueError:
                continue
            if event_time >= since:
                filtered.append(event)
        events = filtered
    payload = {
        "ok": True,
        "read_only": True,
        "endpoint": endpoint_name,
        "since": getattr(args, "since", None),
        "json": bool(getattr(args, "json", False)),
        "events": events,
        "route_transitions": [event for event in events if event.get("event_type") in {"route_guard", "workflow_begin"}],
        "writeful_commands": [event for event in events if event.get("read_only") is False],
        "dry_runs_vs_applies": [event for event in events if event.get("event_type") in {"plan_to_db_import_dry_run", "plan_to_db_import_apply"}],
        "review_status_changes": [event for event in events if str(event.get("event_type", "")).startswith("review_")],
        "artifact_index_changes": [event for event in events if event.get("event_type") == "artifact_index_refresh"],
        "evidence_or_closeout_actions": [
            event
            for event in events
            if str(event.get("event_type", "")).startswith("evidence_") or event.get("event_type") == "closeout"
        ],
        "no_governance_exit_events": [event for event in events if event.get("status") == "no_governance_exit"],
    }
    _require_dependency("print_json")(payload)
    return 0


def cmd_alias_set(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    aliases = _require_dependency("load_aliases")(repo)
    scoped = aliases.setdefault(args.kind, {})
    scoped[args.name] = args.target
    _require_dependency("save_aliases")(repo, aliases)
    _require_dependency("print_json")({"ok": True, "kind": args.kind, "name": args.name, "target": args.target, "ref": f"@alias.{args.name}"})
    return 0


def cmd_alias_list(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    aliases = _require_dependency("load_aliases")(repo)
    _require_dependency("print_json")({"ok": True, "aliases": aliases})
    return 0


def register_capture(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, CaptureHandler],
) -> None:
    """Register capture/governance command families while cli.py keeps global dispatch."""
    _validate_handlers(handlers)

    doc = subparsers.add_parser("doc")
    doc_sub = doc.add_subparsers(dest="doc_command", required=True)
    doc_import = doc_sub.add_parser("import")
    doc_import.add_argument("file")
    doc_import.add_argument("--title")
    doc_import.add_argument("--source-type", default="markdown")
    doc_import.add_argument("--origin")
    doc_import.add_argument("--max-chars", type=int, default=4000)
    doc_import.set_defaults(func=handlers["doc_import"])

    hook = subparsers.add_parser("hook")
    hook_sub = hook.add_subparsers(dest="hook_command", required=True)
    hook_user_prompt = hook_sub.add_parser("user-prompt")
    hook_user_prompt.add_argument("--session-id")
    hook_user_prompt.add_argument("--agent-name", default="codex")
    hook_user_prompt.add_argument("--model-name")
    hook_user_prompt.add_argument("--source", default="hook")
    hook_user_prompt.add_argument("--content")
    hook_user_prompt.add_argument("--content-file", help="Read long prompt/content text from a UTF-8 file.")
    hook_user_prompt.set_defaults(func=handlers["hook_user_prompt"])
    hook_stop = hook_sub.add_parser("stop")
    hook_stop.add_argument("--session-id")
    hook_stop.add_argument("--agent-name", default="codex")
    hook_stop.add_argument("--model-name")
    hook_stop.add_argument("--source", default="hook")
    hook_stop.add_argument("--content")
    hook_stop.add_argument("--actor", default="agent", choices=["agent", "tool", "system"])
    hook_stop.add_argument("--run")
    hook_stop.set_defaults(func=handlers["hook_stop"])

    session = subparsers.add_parser("session")
    session_sub = session.add_subparsers(dest="session_command", required=True)
    session_import = session_sub.add_parser("import")
    session_import.add_argument("--transcript", required=True)
    session_import.add_argument("--session-id")
    session_import.add_argument("--agent-name", default="codex")
    session_import.add_argument("--model-name")
    session_import.add_argument("--source")
    session_import.add_argument("--endpoint")
    session_import.add_argument("--capture-discussion", action="store_true")
    session_import.add_argument("--title")
    session_import.add_argument("--mode", default="capture")
    session_import.add_argument("--reviewed", action="store_true")
    session_import.set_defaults(func=handlers["session_import"])

    context = subparsers.add_parser("context")
    context_sub = context.add_subparsers(dest="context_command", required=True)
    context_load = context_sub.add_parser("load")
    context_load.add_argument("--task", required=True)
    context_load.add_argument("--endpoint")
    context_load.add_argument("--reason")
    context_load.set_defaults(func=handlers["context_load"])

    workflow = subparsers.add_parser("workflow")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_begin = workflow_sub.add_parser("begin")
    workflow_begin.add_argument("--content")
    workflow_begin.add_argument("--content-file", help="Read long prompt/content text from a UTF-8 file.")
    workflow_begin.add_argument("--task")
    workflow_begin.add_argument("--endpoint")
    workflow_begin.add_argument("--session-id")
    workflow_begin.add_argument("--agent-name")
    workflow_begin.add_argument("--model-name")
    workflow_begin.add_argument("--source")
    workflow_begin.add_argument("--reason")
    workflow_begin.add_argument("--mode", default="standard")
    workflow_begin.set_defaults(func=handlers["workflow_begin"])
    workflow_trace = workflow_sub.add_parser("trace")
    workflow_trace.add_argument("--endpoint")
    workflow_trace.add_argument("--since")
    workflow_trace.add_argument("--json", action="store_true")
    workflow_trace.set_defaults(func=handlers["workflow_trace"])

    mode = subparsers.add_parser("mode")
    mode_sub = mode.add_subparsers(dest="mode_command", required=True)
    mode_suggest = mode_sub.add_parser("suggest")
    mode_suggest.add_argument("--intent")
    mode_suggest.add_argument("--mode")
    mode_suggest.add_argument("--no-governance", action="store_true")
    mode_suggest.add_argument("--capture-only", action="store_true")
    mode_suggest.set_defaults(func=handlers["mode_suggest"])

    alias = subparsers.add_parser("alias")
    alias_sub = alias.add_subparsers(dest="alias_command", required=True)
    alias_set = alias_sub.add_parser("set")
    alias_set.add_argument("--kind", required=True, choices=["endpoint", "task", "check", "discussion", "node"])
    alias_set.add_argument("--name", required=True)
    alias_set.add_argument("--target", required=True)
    alias_set.set_defaults(func=handlers["alias_set"])
    alias_list = alias_sub.add_parser("list")
    alias_list.set_defaults(func=handlers["alias_list"])
