from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


AdapterHandler = Callable[[argparse.Namespace], int]
ADAPTER_HANDLER_KEYS = ("manual_events", "manual_import")
ADAPTER_DEPENDENCY_KEYS = (
    "connect",
    "create_discussion_segment",
    "ensure_session",
    "insert_message",
    "insert_standard_event",
    "message_actor_for_event",
    "normalize_mode",
    "print_json",
    "relpath",
    "resolve_endpoint_identifier",
    "standard_events_from_transcript",
)

connect: Callable[..., Any] | None = None
create_discussion_segment: Callable[..., Any] | None = None
ensure_session: Callable[..., Any] | None = None
insert_message: Callable[..., Any] | None = None
insert_standard_event: Callable[..., Any] | None = None
message_actor_for_event: Callable[..., Any] | None = None
normalize_mode: Callable[..., Any] | None = None
print_json: Callable[[Any], None] | None = None
relpath: Callable[[Path, Path], str] | None = None
resolve_endpoint_identifier: Callable[..., Any] | None = None
standard_events_from_transcript: Callable[..., Any] | None = None


def _validate_handlers(handlers: Mapping[str, AdapterHandler]) -> None:
    missing = [key for key in ADAPTER_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"adapter command boundary is missing: {', '.join(missing)}")


def _adapter_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in ADAPTER_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"adapter handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in ADAPTER_DEPENDENCY_KEYS}


def _require_dependency(name: str) -> Any:
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"adapter command dependency is not configured: {name}")
    return value


def build_adapter_handlers(deps: Mapping[str, Any]) -> dict[str, AdapterHandler]:
    """Build adapter handlers from cli.py-owned shared import/discussion helpers."""
    globals().update(_adapter_dependencies(deps))
    return {
        "manual_events": cmd_adapter_manual_events,
        "manual_import": cmd_adapter_manual_import,
    }


def cmd_adapter_manual_events(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    transcript = Path(args.transcript)
    if not transcript.is_absolute():
        transcript = repo / transcript
    events = _require_dependency("standard_events_from_transcript")(
        repo,
        transcript,
        session_id=args.session_id,
        agent_name=args.agent_name,
        model_name=args.model_name,
        source=args.source,
    )
    _require_dependency("print_json")({"ok": True, "adapter": "manual", "events": events})
    return 0


def cmd_adapter_manual_import(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    transcript = Path(args.transcript)
    if not transcript.is_absolute():
        transcript = repo / transcript
    standard_events_from_transcript_fn = _require_dependency("standard_events_from_transcript")
    connect_fn = _require_dependency("connect")
    ensure_session_fn = _require_dependency("ensure_session")
    relpath_fn = _require_dependency("relpath")
    insert_standard_event_fn = _require_dependency("insert_standard_event")
    message_actor_for_event_fn = _require_dependency("message_actor_for_event")
    insert_message_fn = _require_dependency("insert_message")
    resolve_endpoint_identifier_fn = _require_dependency("resolve_endpoint_identifier")
    create_discussion_segment_fn = _require_dependency("create_discussion_segment")
    normalize_mode_fn = _require_dependency("normalize_mode")
    print_json_fn = _require_dependency("print_json")

    events = standard_events_from_transcript_fn(
        repo,
        transcript,
        session_id=args.session_id,
        agent_name=args.agent_name,
        model_name=args.model_name,
        source=args.source,
    )
    conn = connect_fn(repo)
    session_id = ensure_session_fn(
        conn,
        session_id=args.session_id,
        agent_name=args.agent_name,
        model_name=args.model_name,
        source=args.source or relpath_fn(transcript, repo),
        metadata={"adapter": "manual", "standard_event_model": "shujuan.standard_event.v1"},
    )
    imported = []
    discussion_messages_for_capture = []
    for event in events:
        event_id = insert_standard_event_fn(conn, event, session_id=session_id)
        message = None
        actor = message_actor_for_event_fn(event)
        content = str(event.get("content") or "").strip()
        if actor and content:
            message_id, node_id = insert_message_fn(
                conn,
                session_id=session_id,
                actor=actor,
                content=content,
                metadata={
                    "adapter": "manual",
                    "standard_event_id": event_id,
                    "standard_event_type": event["event_type"],
                    "source": event.get("source"),
                },
                created_at=event.get("occurred_at"),
            )
            message = {"message_id": message_id, "node_id": node_id, "actor": actor}
            discussion_messages_for_capture.append(
                {
                    "actor": actor,
                    "content": content,
                    "turn_index": event.get("turn_index"),
                    "source_node_id": node_id,
                    "source_message_id": message_id,
                    "metadata": {
                        "adapter": "manual",
                        "standard_event_id": event_id,
                        "standard_event_type": event["event_type"],
                        "source": event.get("source"),
                        "content_hash": event.get("content_hash"),
                    },
                }
            )
        imported.append({"event_id": event_id, "event_type": event["event_type"], "message": message})
    discussion_capture = None
    if args.capture_discussion:
        if not args.endpoint:
            raise SystemExit("adapter manual import --capture-discussion requires --endpoint")
        endpoint_name = resolve_endpoint_identifier_fn(conn, repo, args.endpoint)
        discussion_capture = create_discussion_segment_fn(
            conn,
            endpoint_name=endpoint_name,
            messages=discussion_messages_for_capture,
            session_id=session_id,
            agent_name=args.agent_name,
            model_name=args.model_name,
            source=args.source or relpath_fn(transcript, repo),
            title=args.title,
            mode=normalize_mode_fn(args.mode or "capture"),
            reviewed=args.reviewed,
            event_type="transcript_import",
            metadata={"adapter": "manual", "standard_event_model": "shujuan.standard_event.v1"},
        )
    conn.commit()
    print_json_fn(
        {
            "ok": True,
            "adapter": "manual",
            "standard_event_model": "shujuan.standard_event.v1",
            "session_id": session_id,
            "events": imported,
            "discussion_capture": discussion_capture,
        }
    )
    return 0


def register_adapter(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, AdapterHandler],
) -> None:
    """Register the adapter command family while cli.py keeps global flags and dispatch."""
    _validate_handlers(handlers)

    adapter = subparsers.add_parser("adapter")
    adapter_sub = adapter.add_subparsers(dest="adapter_command", required=True)
    manual = adapter_sub.add_parser("manual")
    manual_sub = manual.add_subparsers(dest="manual_command", required=True)
    manual_events = manual_sub.add_parser("events")
    manual_events.add_argument("--transcript", required=True)
    manual_events.add_argument("--session-id")
    manual_events.add_argument("--agent-name")
    manual_events.add_argument("--model-name")
    manual_events.add_argument("--source")
    manual_events.set_defaults(func=handlers["manual_events"])
    manual_import = manual_sub.add_parser("import")
    manual_import.add_argument("--transcript", required=True)
    manual_import.add_argument("--session-id")
    manual_import.add_argument("--agent-name")
    manual_import.add_argument("--model-name")
    manual_import.add_argument("--source")
    manual_import.add_argument("--endpoint")
    manual_import.add_argument("--capture-discussion", action="store_true")
    manual_import.add_argument("--title")
    manual_import.add_argument("--mode", default="capture")
    manual_import.add_argument("--reviewed", action="store_true")
    manual_import.set_defaults(func=handlers["manual_import"])


__all__ = [
    "ADAPTER_HANDLER_KEYS",
    "build_adapter_handlers",
    "cmd_adapter_manual_events",
    "cmd_adapter_manual_import",
    "register_adapter",
]
