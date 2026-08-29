from __future__ import annotations

import argparse
import ast
import hashlib
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from difflib import unified_diff
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .services import activation_policy, evidence_policy, relation_policy
from .services.errors import StructuredPayloadError, StructuredRuntimeError, json_error_payload
from .services.skill_registry import ROLE_PROFILE_NAMES, registry_payload, role_target_dir, skill_specs, skill_target_dir
from .commands.delegate import register_delegate
from .commands.delegate_handlers import build_delegate_boundary, maybe_runtime_preflight
from .commands.adapter import build_adapter_handlers, register_adapter
from .commands.execution import build_execution_handlers, register_diff, register_execution
from .commands.init import build_init_handlers, register_init
from .commands.capture import build_capture_handlers, register_capture
from .commands.artifact_index import build_artifact_index_handlers, register_artifact_index
from .commands.evidence import build_evidence_handlers, register_evidence
from .commands.discuss import build_discuss_handlers, register_discuss
from .commands.audit import (
    audit_consume_payload,
    build_audit_handlers,
    cmd_audit_consume,
    cmd_audit_import_agent_output,
    cmd_audit_record,
    read_audit_body,
    register_audit,
)
from .commands.endpoint import (
    active_audit_findings_for_endpoint,
    build_endpoint_handlers,
    cmd_center_show,
    cmd_center_update,
    cmd_db_doctor,
    cmd_endpoint_bind_root,
    cmd_endpoint_brief,
    cmd_endpoint_create,
    cmd_endpoint_doctor,
    cmd_endpoint_link_child,
    cmd_endpoint_refresh,
    cmd_endpoint_status,
    cmd_endpoint_update,
    cmd_export_center,
    cmd_export_glossary,
    cmd_ready_new_project,
    db_doctor_payload,
    doctor_add,
    endpoint_active_obligation_count,
    endpoint_active_obligations,
    endpoint_agcp_doctor_findings,
    endpoint_chain_children,
    endpoint_doctor_payload,
    endpoint_latest_fact_at,
    endpoint_projection_facts,
    endpoint_projection_hash,
    endpoint_readiness_diagnostic,
    endpoint_scope_facts,
    endpoint_status_payload,
    has_outgoing_edge,
    inherited_active_blockers_for_endpoint,
    new_project_readiness_payload,
    parent_endpoints_for_child,
    query_endpoint,
    query_nodes_applying_to,
    readiness_requirement,
    refresh_endpoint_projection,
    register_center,
    register_db,
    register_endpoint,
    register_export,
    register_ready,
    render_endpoint_status_markdown,
    test_result_has_trusted_argv,
)
from .commands.graph import (
    build_graph_handlers,
    create_acceptance_row_for_node,
    create_scope_contract_row_for_node,
    create_task_row_for_node,
    graph_detail_payload,
    graph_projection_payload,
    register_graph,
)
from .commands.tasking import (
    build_tasking_handlers,
    create_defer_decision,
    create_semantic_note,
    create_scope_change,
    create_work_note,
    maybe_refresh_endpoint,
    register_tasking,
)
from .commands.migrate import (
    applied_migrations,
    build_migrate_handlers,
    cmd_migrate_apply,
    cmd_migrate_status,
    legacy_runtime_migration_dir,
    migration_dir,
    migration_files,
    migration_status,
    register_migrate,
)
from .commands.postgres_dev import (
    build_postgres_dev_handlers,
    postgres_dev_lifecycle_payload,
    choose_postgres_dev_port,
    default_postgres_dev_port,
    initialize_postgres_dev,
    register_postgres_dev,
)
from .commands.provider import (
    PROVIDER_CONTRACT_VERSION,
    build_provider_handlers,
    gitnexus_command,
    impact_provider_contract,
    impact_metadata,
    register_provider,
)
from .commands.plan_to_db import build_plan_to_db_handlers, register_plan_to_db
from .commands.install_layout import build_install_layout_handlers, register_install_layout
from .commands.report import (
    build_report_handlers,
    endpoint_report_payload,
    project_report_payload,
    register_report,
    render_endpoint_report_markdown,
    render_project_report_markdown,
)
from .commands.recall import build_recall_handlers, register_recall
from .commands.route import build_route_handlers, register_route
from .commands.review import build_review_handlers, register_review
from .commands.schema_stewardship import build_schema_stewardship_handlers, register_schema_stewardship
from .commands.workbench import (
    attach_workbench_details,
    build_workbench_handlers,
    cmd_workbench_export,
    ensure_workbench_g6_asset,
    register_workbench,
    render_workbench_html,
)
from .commands.workflow import build_workflow_handlers, register_workflows
from .schema import SCHEMA_VERSION
from .store import (
    acquire_postgres_ddl_lock,
    assert_runtime_schema_ready,
    connect,
    connect_read_only,
    create_edge,
    create_node,
    ensure_project_meta,
    ensure_layout,
    init_schema,
    inspect_schema,
    inspect_runtime_schema,
    json_dumps,
    new_id,
    now_iso,
    open_db_raw,
    release_postgres_ddl_lock,
    resolve_database_config,
    sha256_bytes,
    sha256_text,
    write_schema_version_file,
)


def run_git(repo: Path, args: list[str], *, allow_fail: bool = False) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace") if completed.stdout is not None else ""
    stderr = completed.stderr.decode("utf-8", errors="replace") if completed.stderr is not None else ""
    if completed.returncode and not allow_fail:
        raise SystemExit(stderr.strip() or f"git {' '.join(args)} failed")
    return stdout


def current_head(repo: Path) -> str | None:
    value = run_git(repo, ["rev-parse", "HEAD"], allow_fail=True).strip()
    return value or None


def current_branch(repo: Path) -> str | None:
    value = run_git(repo, ["branch", "--show-current"], allow_fail=True).strip()
    return value or None


def print_text(value: str, *, end: str = "\n") -> None:
    text = value + end
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(text.encode(sys.stdout.encoding or "utf-8", errors="backslashreplace").decode(sys.stdout.encoding or "utf-8"))
        sys.stdout.flush()
        return
    buffer.write(text.encode(sys.stdout.encoding or "utf-8", errors="backslashreplace"))
    buffer.flush()


def print_error(value: str, *, end: str = "\n") -> None:
    text = value + end
    buffer = getattr(sys.stderr, "buffer", None)
    if buffer is None:
        sys.stderr.write(text.encode(sys.stderr.encoding or "utf-8", errors="backslashreplace").decode(sys.stderr.encoding or "utf-8"))
        sys.stderr.flush()
        return
    buffer.write(text.encode(sys.stderr.encoding or "utf-8", errors="backslashreplace"))
    buffer.flush()


def print_json(value: Any) -> None:
    print_text(json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


class ShujuanUsageError(StructuredPayloadError):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(code, message, **extra)


class StructuredCliError(ShujuanUsageError):
    pass


class ShujuanArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print_error(message)
        raise StructuredCliError(
            "invalid_cli_arguments",
            message,
            read_only=True,
            safe_next_action="Fix the command arguments and rerun; no governance DB writes were attempted.",
        )


def trace_log_path(repo: Path, *, endpoint: str | None = None) -> Path:
    root = ensure_layout(repo) / "trace"
    if endpoint:
        root = root / endpoint
    root.mkdir(parents=True, exist_ok=True)
    return root / "workflow_trace.jsonl"


def append_trace_event(
    repo: Path,
    *,
    event_type: str,
    endpoint: str | None = None,
    route: str | None = None,
    mode: str | None = None,
    read_only: bool | None = None,
    apply: bool | None = None,
    status: str | None = None,
    details: dict[str, Any] | None = None,
) -> Path:
    path = trace_log_path(repo, endpoint=endpoint)
    event = {
        "timestamp": now_iso(),
        "event_type": event_type,
        "endpoint": endpoint,
        "route": route,
        "mode": mode,
        "read_only": read_only,
        "apply": apply,
        "status": status,
        "details": details or {},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
        handle.write("\n")
    return path


def read_trace_events(repo: Path, *, endpoint: str | None = None) -> list[dict[str, Any]]:
    path = trace_log_path(repo, endpoint=endpoint)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def diagnostics_payload(
    *,
    usable: bool,
    raw_count: int = 0,
    visible_count: int = 0,
    filtered_count: int = 0,
    render_errors: list[str] | None = None,
    report_errors: list[str] | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    return {
        "usable": usable,
        "raw_count": raw_count,
        "visible_count": visible_count,
        "filtered_count": filtered_count,
        "render_errors": render_errors or [],
        "report_errors": report_errors or [],
        "next_action": next_action,
    }


def is_database_constraint_error(exc: BaseException) -> bool:
    exc_type = type(exc)
    module = exc_type.__module__
    names = {cls.__name__ for cls in exc_type.mro()}
    return module.startswith("psycopg") and bool(names & {"IntegrityError", "ForeignKeyViolation"})


def row_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def row_scalar(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[index]


def alias_path(repo: Path) -> Path:
    return repo / ".shujuan" / "aliases.json"


def load_aliases(repo: Path) -> dict[str, Any]:
    path = alias_path(repo)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_aliases(repo: Path, aliases: dict[str, Any]) -> None:
    path = alias_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(aliases), encoding="utf-8")


def props_dict(row_or_text: sqlite3.Row | dict[str, Any] | str | None) -> dict[str, Any]:
    if row_or_text is None:
        return {}
    if isinstance(row_or_text, dict) and "props" in row_or_text:
        raw = row_or_text["props"]
    elif hasattr(row_or_text, "keys") and "props" in row_or_text.keys():
        raw = row_or_text["props"]
    else:
        raw = row_or_text
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw if isinstance(raw, str) else str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


RESOLUTION_EDGE_TYPES = {"RESOLVES", "SUPERSEDES"}
GRAPH_LINK_EDGE_TYPES = {"APPLIES_TO", "DERIVED_FROM", "RESOLVES", "SUPERSEDES", "DECOMPOSES_TO", "PRODUCES"}
SEMANTIC_ITEM_TYPES = {
    "audit_finding",
    "unresolved_question",
    "scope_change",
    "defer_decision",
    "assumption",
    "work_note",
    "requirement",
    "constraint",
    "decision",
    "term",
    "change_set",
    "test_result",
    "artifact",
    "user_confirmation",
    "interaction_event",
    "discussion_segment",
    "discussion_message",
}
PRODUCT_BACKLOG_STATE = "product_backlog"
LEGACY_BACKLOG_STATE = "backlog"
SEMANTIC_STATE_ALIASES = {LEGACY_BACKLOG_STATE: PRODUCT_BACKLOG_STATE}
INACTIVE_SEMANTIC_STATES = {"resolved", "deferred", PRODUCT_BACKLOG_STATE, LEGACY_BACKLOG_STATE, "invalidated", "superseded"}
SEMANTIC_STATE_DISPLAY_ORDER = ["active", "resolved", "deferred", PRODUCT_BACKLOG_STATE, "invalidated", "superseded"]


def canonical_semantic_state(state: Any) -> Any:
    if state is None:
        return None
    value = str(state)
    return SEMANTIC_STATE_ALIASES.get(value, value)


def semantic_state_arg(value: str) -> str:
    state = canonical_semantic_state(value)
    allowed = set(SEMANTIC_STATE_DISPLAY_ORDER) | {"reopened"}
    if state not in allowed:
        raise argparse.ArgumentTypeError(f"expected one of: {', '.join(SEMANTIC_STATE_DISPLAY_ORDER)}, reopened")
    return str(state)


def evidence_state_arg(value: str) -> str:
    state = canonical_semantic_state(value)
    allowed = set(SEMANTIC_STATE_DISPLAY_ORDER)
    if state not in allowed:
        raise argparse.ArgumentTypeError(f"expected one of: {', '.join(SEMANTIC_STATE_DISPLAY_ORDER)}")
    return str(state)


def display_semantic_row(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    item = row_to_dict(row)
    if item and item.get("current_state") is not None:
        item["current_state"] = canonical_semantic_state(item["current_state"])
    return item


def display_lifecycle_event(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    event = row_to_dict(row)
    if event:
        for key in ("from_state", "to_state"):
            if event.get(key) is not None:
                event[key] = canonical_semantic_state(event[key])
    return event


def active_node_clause(alias: str = "n") -> str:
    return (
        f"{alias}.valid_to IS NULL "
        f"AND NOT EXISTS ("
        f"SELECT 1 FROM edges active_edge "
        f"JOIN nodes active_from ON active_from.id = active_edge.from_node_id "
        f"WHERE active_edge.to_node_id = {alias}.id "
        f"AND active_edge.type IN ('RESOLVES', 'SUPERSEDES')"
        f"AND active_from.valid_to IS NULL "
        f")"
    )


def append_lifecycle_event(
    conn: sqlite3.Connection,
    *,
    semantic_item_id: str,
    node_id: str,
    event_type: str,
    from_state: str | None,
    to_state: str,
    source_node: str | None,
    reason: str | None,
    props: dict[str, Any] | None = None,
) -> str:
    event_id = new_id("semantic_event")
    conn.execute(
        """
        INSERT INTO semantic_lifecycle_events
          (id, semantic_item_id, node_id, event_type, from_state, to_state, source_node_id, reason, created_at, props)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (event_id, semantic_item_id, node_id, event_type, from_state, to_state, source_node, reason, now_iso(), json_dumps(props or {})),
    )
    return event_id


def create_semantic_item(
    conn: sqlite3.Connection,
    node_id: str,
    item_type: str,
    *,
    state: str = "active",
    source_node: str | None = None,
    scope_node: str | None = None,
    event_type: str = "created",
    reason: str | None = None,
    props: dict[str, Any] | None = None,
) -> str | None:
    if item_type not in SEMANTIC_ITEM_TYPES:
        return None
    state = str(canonical_semantic_state(state))
    assert_runtime_schema_ready(conn, purpose="semantic item write")
    timestamp = now_iso()
    existing = conn.execute("SELECT id, current_state FROM semantic_items WHERE node_id = ?", (node_id,)).fetchone()
    if existing:
        item_id = str(existing["id"])
        from_state = str(existing["current_state"])
        conn.execute(
            "UPDATE semantic_items SET current_state = ?, source_node_id = COALESCE(?, source_node_id), "
            "scope_node_id = COALESCE(?, scope_node_id), updated_at = ?, props = ? WHERE id = ?",
            (state, source_node, scope_node, timestamp, json_dumps(props or {}), item_id),
        )
    else:
        item_id = new_id("semantic")
        from_state = None
        conn.execute(
            """
            INSERT INTO semantic_items
              (id, node_id, item_type, current_state, scope_node_id, source_node_id, created_at, updated_at, props)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item_id, node_id, item_type, state, scope_node, source_node, timestamp, timestamp, json_dumps(props or {})),
        )
    append_lifecycle_event(
        conn,
        semantic_item_id=item_id,
        node_id=node_id,
        event_type=event_type,
        from_state=from_state,
        to_state=state,
        source_node=source_node,
        reason=reason,
        props=props,
    )
    return item_id


def register_semantic_item(*args: Any, **kwargs: Any) -> str | None:
    return create_semantic_item(*args, **kwargs)


def transition_semantic_item(
    conn: sqlite3.Connection,
    node_id: str,
    *,
    state: str,
    event_type: str,
    source_node: str | None = None,
    reason: str | None = None,
    props: dict[str, Any] | None = None,
) -> str | None:
    state = str(canonical_semantic_state(state))
    row = conn.execute("SELECT type FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        return None
    return register_semantic_item(
        conn,
        node_id,
        str(row["type"]),
        state=state,
        source_node=source_node,
        event_type=event_type,
        reason=reason,
        props=props,
    )


def semantic_lifecycle_projection(conn: sqlite3.Connection, target_node_ids: list[str]) -> dict[str, Any]:
    assert_runtime_schema_ready(conn, purpose="semantic lifecycle projection")
    target_node_ids = list(dict.fromkeys(target_node_ids))
    if not target_node_ids:
        return {"active": [], "inactive": [], "counts": {"active": 0, "inactive": 0}}
    placeholders = ",".join("?" for _ in target_node_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT si.*, n.label, n.summary, n.created_at AS node_created_at
        FROM semantic_items si
        JOIN nodes n ON n.id = si.node_id
        JOIN edges e ON e.from_node_id = si.node_id
        WHERE e.type = 'APPLIES_TO'
          AND e.to_node_id IN ({placeholders})
        ORDER BY si.updated_at DESC
        LIMIT 100
        """,
        target_node_ids,
    ).fetchall()
    active = []
    inactive = []
    for row in rows:
        item = display_semantic_row(row)
        if str(row["current_state"]) in INACTIVE_SEMANTIC_STATES:
            inactive.append(item)
        else:
            active.append(item)
    return {
        "active": active,
        "inactive": inactive,
        "counts": {"active": len(active), "inactive": len(inactive)},
    }


def read_file_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def relpath(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path)


SHUJUAN_AGENTS_MARKER = "<!-- shujuan-agent-instructions:v1 -->"


SHUJUAN_AGENTS_TEMPLATE = f"""# shujuan Repository Instructions

{SHUJUAN_AGENTS_MARKER}

When working in this repository, use this `AGENTS.md` block as the single canonical shujuan policy. Start with the positive working route, then add only the boundary checks that the selected route needs.

## Shared Route Grammar

Use one grammar for every Shujuan situation:

- Trigger: name the user situation that selects the route.
- First surface: load the smallest DB-backed report, source, or packet that orients the route.
- Action chain: run the route's commands in order.
- Evidence/adoption: state what can become closure evidence and who adopts it.
- Handoff: report active obligations, closed checks, unresolved decisions, and next valid entry.

## Default Operating Core

- Identify center/endpoint, DCCP role, and governance mode before acting.
- Choose exactly one entry route first: `Recover`, `Recall`, `Execute`, `Close`, or `Delegate`; any transition to another route must be explicit.
- `Recover` and `Recall` read current surfaces for orientation or history; closure claims stay with `Close`.
- `Execute` records the user request with `workflow begin`, starts scoped work with `exec start`, verifies, then stops through controller `exec stop`.
- `Close` is the controller evidence route: match evidence, refresh endpoint, run `evidence verify`, then strict doctor.
- `Delegate` returns bounded material; workers, reviewers, researchers, writers, and provider tools enter this material/adoption lane before controller closure.
- Active obligations are open tasks/checks/findings/unresolved/decisions; deferred/backlog items are inactive until promoted.
- Contracted/dormant schema is not the default working surface; legacy writes are disabled diagnostics.
- Derived read/material surfaces guide decisions and become closure material only after controller evidence adoption.
- PostgreSQL is the runtime/write path; SQLite and contracted legacy tables are not write fallbacks.
- Use advanced primitives through routed references after the selected route reveals the need.

## Route Map

- `Recover`: if the endpoint is named, run `report endpoint <endpoint> --active-only --markdown` and `endpoint doctor <endpoint> --strict-closeout --read-only --allow-fail`. Use `report project --overview --markdown` only to choose an endpoint.
- `Recall`: answer history/rationale from endpoint active/full reports, endpoint brief, source documents, graph/detail views, `why`, text search, and full project report only when history crosses endpoints.
- `Execute`: pass the DB readiness gate, then run `workflow begin`, `exec start`, scoped edits, verification, and controller `exec stop`.
- `Close`: gather `endpoint`, `task_id`, `check_id`, `expected_evidence_type`, and `current_matching_evidence_ref`; bare close requests return `missing_closeout_inputs`.
- `Delegate`: worker packets flow `Delegate` -> scoped `Execute`; provider output enters as bounded material; if provider output is gathered inside an active `Execute`, the same adoption rule still applies; controller adoption flows returned material -> import -> independent verification -> `Close`.

## Runtime Readiness

- DB readiness gate: if the project database service is unavailable, run `python -m shujuan postgres-dev start`; continue only after `python -m shujuan postgres-dev status` reports ready.
- DB-backed reports, `workflow begin`, execution, endpoint refresh/doctor, evidence commands, and delegate/audit imports run after readiness.
- If the user has not asked you to start services, report the runtime block and keep scope intact instead of reducing the requested goal.

## Current Terms

- `endpoint`: direction-level recoverable cognitive breakpoint carrying current scope, obligations, evidence, blockers, and next valid entry; it is not a terminal completion claim.
- `active`: still needs attention in the current scope.
- `closed`: scoped task/check closure backed by current matching evidence.
- `resolved`: semantic item is historical and not active.
- `deferred` / `product_backlog`: return to active scope only when explicitly promoted.
- `audit_finding`: actionable only while active.
- `evidence`: current `change_set`, `test_result`, `artifact`, or `user_confirmation` node that matches the check contract.
- `provider_fact` / `provider_hypothesis`: provider/impact provenance and confidence input for the material/adoption lane, not closure evidence by itself.
- `PostgreSQL success`: a real project-owned PostgreSQL runtime chain with prompt/session/run/evidence/endpoint/report operations, migrations, constraints, and persistence.
- `interaction_event`, `discussion_segment`, `discussion_message`, `mode_router`, `projection payload`, `read-only workbench`, `detail_ref`, and `hidden_source_count` are current traceability terms.

## Repository Boundaries

- `AGENTS.md` is the canonical policy surface. The installed skill is intentionally short and acts as an activation card; if the skill differs from this file, follow `AGENTS.md` and report the mismatch. Detailed guidance lives under `.agents/skills/shujuan-core/references/`; handoff forms live under `.agents/skills/shujuan-core/templates/`.
- Prefer `python -m shujuan init --postgres-dev` for new project setup when native PostgreSQL is available. It creates a project-owned PostgreSQL dev database under `.shujuan/postgres-dev/`.
- When conversation changes design intent, execution priority, scope, or acceptance criteria, record it with `workflow begin`, `hook user-prompt`, `session import`, or `audit record` before relying on it for implementation.
- Use `scope change --applies-to` for non-state-changing scope notes; use `task defer --task` for ordinary deferral decisions.
- Plan-to-DB conversion keeps named source-plan deliverables individually visible with classification, graph destination, rationale, and promotion/reopen rule.
- For impact/dependency work, use GitNexus directly through the global `gitnexus-*` skills or CLI. Provider output remains material and never becomes closure evidence by itself.
- Treat `.gitnexus/` as a reusable ignored local index; keep generated provider assets outside the public tree.
- Important audit/research summaries should become DB artifacts or audit findings and should refresh the relevant endpoint.

## DCCP Role Cards

The canonical role surface is this `AGENTS.md` block; `.agents/skills/shujuan-core/SKILL.md` only summarizes startup activation. `python -m shujuan init` installs synced policy and activation templates for new repos.

- `controller_agent`: runs governance DB writes, shujuan DB writes, scope changes, endpoint refresh, exec stop, evidence import, check/task closure, and final closeout claims. The controller may delegate implementation or review, then import and verify returned material before using it as closure evidence.
- `worker_agent`: implements scoped code, docs, templates, or tests and returns changed files, tests, impact notes, and unresolved risks. Governance authority stays with the controller unless the packet grants it.
- `reviewer_agent`: performs independent read-only review against source, diffs, tests, and packets, then returns findings, evidence sufficiency, and risk notes.
- `researcher_agent`: gathers source-backed facts and impact context, separating observations from inferences for controller adoption.
- `writer_agent`: drafts summaries, reports, packets, or external prose. Default writer work is `writing_no_governance`: prose output only, with any governance adoption handled by the controller.

Delegated packets state the role, scope, authority boundary, expected return fields, tests run, changed files, impact expectations, and unresolved risks. Packets that permit code modification include explicit `gitnexus-impact-analysis` or impact expectations. Controller packets are the authority surface for governance write or closeout permission.
"""


SHUJUAN_SKILL_TEMPLATE = """---
name: shujuan-core
description: Repo-local shujuan activation card for recovery, recall, execution, delegation, and evidence closeout.
---

# Shujuan Core Activation

Use this skill in a shujuan-enabled repository when the task involves recovery, recall, execution, delegation, review, or closeout.

## Authority

`AGENTS.md` is the canonical repo policy. This skill is an activation card. If this skill and `AGENTS.md` differ, follow `AGENTS.md` and report the mismatch.

Details removed from this card are not obsolete by default; use routed references and templates when the selected route needs command-level or handoff detail.

## Activation

1. Read the repo `AGENTS.md`.
2. Identify the center/endpoint from the prompt, handoff, current alias, or project overview.
3. Name your DCCP role and governance mode.
4. Choose one entry route: `Recover`, `Recall`, `Execute`, `Close`, or `Delegate`.
5. Use the Shared Route Grammar from `AGENTS.md`: trigger, first surface, action chain, evidence/adoption, and handoff.
6. Open routed references only when the selected route needs detail.

## Five Routes

- `Recover`: load the endpoint active surface and read-only strict doctor for orientation.
- `Recall`: answer history, rationale, lineage, and version/change questions without execution, refresh, governance writes, or closure.
- `Execute`: pass the DB readiness gate, then use `workflow begin`, `exec start`, scoped work, verification, and controller `exec stop`.
- `Close`: use the controller evidence route; require endpoint/task/check/evidence inputs, then refresh, verify, and run strict doctor.
- `Delegate`: use the role-bounded material lane; returned material becomes closure input only after controller import and independent verification.

## Minimal Hard Boundaries

- PostgreSQL is the write/runtime path; SQLite and contracted legacy tables are not write fallbacks.
- Provider, reviewer, researcher, writer, and worker output is material until controller adoption.
- Evidence closure requires current matching `change_set`, `test_result`, `artifact`, or `user_confirmation`.
- Deferred/backlog items are inactive until promoted.
- The controller owns governance writes, endpoint refresh, exec stop, evidence import, check/task closure, and final closeout unless a packet grants authority.

## References

Route detail: `references/activation-first.md`, `references/evidence-closeout.md`, `references/delegation.md`, `references/modes-and-terms.md`, `references/postgres-runtime.md`, `references/plan-to-db-task-chain-hygiene.md`.

Handoffs: `templates/delegate-return.md`, `templates/reviewer-return.md`, `templates/closeout-handoff.md`.
"""


SHUJUAN_SKILL_REFERENCE_TEMPLATES = {
    "references/activation-first.md": """# Activation-First Entry

Start from center/endpoint, role, and mode, then choose one default route: `Recover`, `Recall`, `Execute`, `Close`, or `Delegate`.

## Shared Route Grammar

Every route uses the same five slots: trigger, first surface, action chain, evidence/adoption rule, and handoff. This keeps route choice, command depth, and closeout authority at the same granularity across agents.

`Recover` is read-only orientation for a new window, resumed thread, or handoff. If the endpoint is named, stay endpoint-specific; use project overview only to choose an endpoint:

```bash
python -m shujuan report endpoint <endpoint> --active-only --markdown
python -m shujuan endpoint doctor <endpoint> --strict-closeout --read-only --allow-fail
```

Use project overview only when no endpoint is named; reserve the full project report for Recall questions that cross endpoints:

```bash
python -m shujuan report project --overview --markdown
```

`Recall` is read-only lineage review for history, rationale, version comparison, deferred/backlog/non-goal, and "why did this change" questions. Start from the endpoint; use full project history only when the question crosses endpoints:

```bash
python -m shujuan report endpoint <endpoint> --active-only --markdown
python -m shujuan report endpoint <endpoint> --full --markdown
python -m shujuan endpoint brief <endpoint> --role <role> --mode <mode> --markdown
python -m shujuan graph detail --node <node_id>
python -m shujuan why --path <path>
python -m shujuan report project --markdown
```

Separate facts from inference. Recall material becomes closure evidence only when a controller records and verifies it through the normal evidence path.

DB readiness gate: if the project database service is unavailable, run `python -m shujuan postgres-dev start`; continue only after `python -m shujuan postgres-dev status` reports ready.

Execute starts scoped work only after readiness and prompt capture:

```bash
python -m shujuan workflow begin --session-id <session_id> --endpoint "<endpoint>" --content "<current user request>"
python -m shujuan workflow begin --session-id <session_id> --endpoint "<endpoint>" --content-file prompt.txt
python -m shujuan exec start --endpoint <endpoint> --task-node <task_node_id> --summary "<summary>"
```

`Execute` uses `workflow begin`, `exec start`, and controller-owned `exec stop` after the DB readiness gate. `Close` starts from `endpoint`, `task_id`, `check_id`, `expected_evidence_type`, and `current_matching_evidence_ref`; bare close requests return `missing_closeout_inputs`. `Delegate` worker flow is `Delegate` -> scoped `Execute`; provider output enters as bounded material; `provider_fact` and `provider_hypothesis` are not closure evidence by themselves; if provider output is gathered inside an active `Execute`, the same adoption rule still applies; controller adoption is import -> independent verification -> `Close`.

Advanced fallback primitives are not the normal first path:

```bash
python -m shujuan task add --body "<task body>" --from-node <source_node_id>
python -m shujuan acceptance add --task <task_id> --body "<check body>" --expected-evidence-type <change_set|test_result|artifact|user_confirmation> --from-node <source_node_id>
python -m shujuan scope change --body "<why scope changed>" --source-node <source_node_id> --applies-to <target_node_id>
python -m shujuan task defer --task <task_id> --body "<why deferred>" --source-node <source_node_id>
python -m shujuan unresolved add --body "<question>" --source-node <source_node_id> --applies-to <target_node_id>
python -m shujuan assumption add --body "<assumption>" --source-node <source_node_id> --applies-to <target_node_id>
```

Closed checks prove prior scoped closure only. Continue from a new request, open work, unresolved blockers, active audit findings, promoted defers, or a new scope contract.
""",
    "references/evidence-closeout.md": """# Evidence Closeout

Checks/tasks close from `endpoint`, `task_id`, `check_id`, `expected_evidence_type`, and `current_matching_evidence_ref`: `change_set`, `test_result`, `artifact`, or `user_confirmation`. Bare close requests return `missing_closeout_inputs` before closure.

```bash
python -m shujuan evidence test-result --check <check_id> --close-check -- <test command>
python -m shujuan evidence artifact --path <file> --check <check_id> --close-check
python -m shujuan evidence user-confirmation --body "<confirmation>" --check <check_id> --close-check
python -m shujuan endpoint refresh <endpoint>
python -m shujuan evidence verify --endpoint <endpoint>
python -m shujuan endpoint doctor <endpoint> --strict-closeout --allow-fail
```

`Recover` uses `endpoint doctor --strict-closeout --read-only --allow-fail` as a read-only diagnostic. `Close` uses `endpoint doctor --strict-closeout` without `--read-only` as the writeful controller closeout path and may refresh the endpoint projection before diagnosing.

Reviewer output gives evidence sufficiency and risk notes. Delegate output, codegraph/GitNexus/provider output, provider facts, and provider hypotheses enter closure through controller import and verification.
""",
    "references/delegation.md": """# Delegation And Role Boundaries

The controller runs governance DB writes, scope changes, endpoint refresh, exec stop, evidence import, check/task closure, and final closeout claims.

`Delegate` is the default route for role-bounded handoff, review, and provider/impact output. A worker packet flows `Delegate` -> scoped `Execute`; provider output enters as bounded material; controller adoption flows returned material -> import -> independent verification -> `Close`. `provider_fact` and `provider_hypothesis` are not closure evidence by themselves. If provider output is gathered inside an active `Execute` task, keep it as execution input until the controller adopts it; the same adoption rule still applies.

```bash
python -m shujuan delegate packet --endpoint <endpoint> --task <task_id> --check <check_id> --role worker --body "<delegation body>"
python -m shujuan delegate review --endpoint <endpoint> --task <task_id> --check <check_id> --result accept --summary "<review summary>"
python -m shujuan delegate import --endpoint <endpoint> --task <task_id> --check <check_id> --import-kind summary --artifact <handoff.md>
python -m shujuan audit import-agent-output --endpoint <endpoint> --source-node <source_node_id> --path <handoff.md>
python -m shujuan review start --endpoint <endpoint>
```

Delegated workers, reviewers, researchers, writers, and provider tools return bounded material for controller adoption unless a controller packet grants governance authority.
""",
    "references/modes-and-terms.md": """# Modes And Terms

`No Governance` writes no DB facts and makes no capture claim. `Capture` and `Explore` may capture discussion but create no run or change_set. `Light`, `Standard`, and `Full` are execution modes with increasing evidence expectations.

Default routes are `Recover`, `Recall`, `Execute`, `Close`, and `Delegate`. `Recover` is read-only diagnostic orientation; `Recall` is read-only history and rationale review; `Close` is the writeful controller closeout path. Advanced primitives belong behind those routes as fallback/reference material.

Core terms: `endpoint`, `closed`, `resolved`, `active`, `deferred`, `product_backlog`, `audit_finding`, `evidence`, `provider_fact`, `provider_hypothesis`, `PostgreSQL success`, `interaction_event`, `discussion_segment`, `mode_router`, `projection payload`, `read-only workbench`, `detail_ref`, and `hidden_source_count`. Define each by its use first, then add exclusions only for high-risk confusion.

`PostgreSQL success` requires a real project-owned PostgreSQL runtime chain and no SQLite runtime/write fallback.
""",
    "references/postgres-runtime.md": """# PostgreSQL Runtime

PostgreSQL is the current runtime/write backend. DB readiness gate: if the project database service is unavailable, run `python -m shujuan postgres-dev start`; continue only after `python -m shujuan postgres-dev status` reports ready. PostgreSQL is the setup, recovery, execution, and closeout write path.

```bash
python -m shujuan init --postgres-dev --name "<project>"
python -m shujuan postgres-dev start
python -m shujuan postgres-dev status
```

Use project-owned PostgreSQL or an explicit `postgresql://` URL. Current state comes from PostgreSQL.
""",
    "references/plan-to-db-task-chain-hygiene.md": """# Plan-to-DB Task Chain Hygiene

Use this as advanced fallback after the `Recover`, `Execute`, `Close`, or `Delegate` route shows decomposition work is needed.

Classify work as `P0`, `P1`, `P2`, or `non-goal`; make phase/order explicit; keep the P0 golden path small; keep acceptance checks single-intent; align expected evidence type with the body; route reviewer, provider, and delegate outputs through controller adoption; avoid false closeout.

Non-compression rule: source-plan deliverables must not collapse into broad parents, deferred umbrellas, or artifact-only ordered plans. Each item needs classification, graph destination, rationale, promotion_rule, and reopen_rule. Absorbed, superseded, and indirectly dissolved items stay visible with their consuming destination/rationale. Promotion back into active scope requires explicit task/check/lifecycle rows.

Use `python -m shujuan plan-to-db verify-artifact --artifact <json>` for artifact shape checks and `python -m shujuan plan-to-db lifecycle-reconcile --endpoint <endpoint> --allow-fail` for read-only RESOLVES/SUPERSEDES residual diagnostics.
""",
}


SHUJUAN_SKILL_HANDOFF_TEMPLATES = {
    "templates/delegate-return.md": """# Delegate Return Packet

- Role:
- Default route: `Delegate`
- Scope handled:
- Changed files:
- Ownership lanes: required for code-modifying worker packets, dirty-worktree separation, deletion, or provider asset ownership questions; otherwise mark `not applicable`.
  - `worker_owned`:
  - `pre_existing_dirty`:
  - `provider_runtime`:
  - `observed_only`:
  - `not_owned`:
  - `deleted_obsolete`: `None` unless deletion was explicitly approved; `.codegraph/`, `.gitnexus/`, `.ai/codegraph/`, `.claude/skills/gitnexus/`, and GitNexus/codegraph provider assets belong in reusable analysis assets, not cleanup lanes.
  - `fallback`: path-level fallback only; does not claim pre-existing dirty hunks.
  - `out_of_scope`:
- Ownership manifest fields: required when ownership lanes apply, otherwise mark `not applicable`: `lane`, `path`, `hunk_id`, `hunk_header`, `range`, `hash`, `claimed_owner`, `pre_existing_dirty`, `source`, `reason`, `promotion_or_reopen_rule`.
- Ownership manifest adoption: controller import/verification is required before ownership material can support closure evidence.
- Default ownership surface: use `python -m shujuan delegate ownership --endpoint <endpoint> --pre-existing-dirty-path <path> --claimed-path <path>` when the controller asks for lane separation; otherwise mark ownership lanes `not applicable`.
- Commands/tests run and outcomes:
- codegraph/GitNexus/provider tools used: `none` or seed/question/boundary/output classification; controller import/verification decides governance adoption.
- Acceptance checks materially satisfied:
- Unresolved risks, assumptions, known reds:
- Controller adoption status: returned for import/verification before any Close-route evidence use.
- Governance action attestation: no DB writes, endpoint refresh, exec stop, or check/task closure performed unless explicitly granted by controller packet.
""",
    "templates/reviewer-return.md": """# Reviewer Return Packet

- Role: `reviewer_agent`
- Review surface:
- Sources/diffs/tests inspected:
- Findings by severity:
- Material recommendation: `accept|reject|unclear`
- Covered predicates/checks:
- Missing predicates/checks:
- Provider/delegate material treated as advisory: `yes`
- Controller adoption status: return these findings for import, independent verification, and Close-route evidence decisions.
- Governance action attestation: no DB writes, endpoint refresh, exec stop, or check/task closure performed unless explicitly granted by controller packet.
""",
    "templates/closeout-handoff.md": """# Closeout Handoff Surface

Use this for the controller `Close` route. `Recover` uses the read-only strict doctor for diagnostics; `Close` uses writeful strict doctor after matching evidence exists.

- Endpoint:
- Scope/task/checks:
- Expected evidence type:
- Current matching evidence ref:
- Evidence nodes or artifacts available:
- Commands/tests run:
- Endpoint refresh status:
- Evidence verify status:
- Strict endpoint doctor status:
- Open obligations:
- Controller closeout action: use matching evidence, endpoint refresh, evidence verify, and strict doctor without `--read-only`; adopt this handoff through evidence before closing checks/tasks.
""",
}


def _resource_text(relative_path: str, fallback: str) -> str:
    try:
        resource = importlib_resources.files("shujuan").joinpath("assets", *Path(relative_path).parts)
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        pass
    return fallback


def _asset_resource_files(relative_root: str) -> dict[str, str]:
    try:
        root = importlib_resources.files("shujuan").joinpath("assets", *Path(relative_root).parts)
        if not root.is_dir():
            return {}
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return {}

    files: dict[str, str] = {}

    def walk(node: Any, prefix: str = "") -> None:
        for child in node.iterdir():
            if child.name == "__pycache__" or child.name.endswith((".pyc", ".pyo")):
                continue
            rel = child.name if not prefix else f"{prefix}/{child.name}"
            if child.is_dir():
                walk(child, rel)
            elif child.is_file():
                files[rel] = child.read_text(encoding="utf-8").rstrip() + "\n"

    walk(root)
    return files


def _skill_resource_files(skill_name: str) -> dict[str, str]:
    return _asset_resource_files(f"skills/{skill_name}")


def _role_profile_resource_files() -> dict[str, str]:
    return _asset_resource_files("agents")


def _hook_resource_files() -> dict[str, str]:
    files = _asset_resource_files("hooks")
    config = _resource_text("hooks.json", "")
    if config:
        files["../hooks.json"] = config.rstrip() + "\n"
    return files


def ensure_agents_md(repo: Path, *, skill_expected: bool = True) -> dict[str, Any]:
    agents_path = repo / "AGENTS.md"
    template = _resource_text("AGENTS.md", SHUJUAN_AGENTS_TEMPLATE).rstrip() + "\n"
    if not skill_expected:
        no_skill_note = (
            "- This repo was initialized without installing `.agents/skills/shujuan-core/SKILL.md`; follow this AGENTS block as the canonical "
            "local shujuan policy until the activation card is installed. Detailed guidance normally lives under "
            "`.agents/skills/shujuan-core/references/`; install the skill to make those routed references available."
        )
        template = re.sub(
            r"- `AGENTS\.md` is the canonical policy surface\.[^\n]*",
            no_skill_note,
            template,
        )
    if not agents_path.exists():
        agents_path.write_text(template, encoding="utf-8")
        return {"path": relpath(agents_path, repo), "action": "created"}
    existing = agents_path.read_text(encoding="utf-8")
    if SHUJUAN_AGENTS_MARKER in existing:
        if "DCCP Role Cards" not in existing and existing.lstrip().startswith("# shujuan Repository Instructions"):
            agents_path.write_text(template, encoding="utf-8")
            return {"path": relpath(agents_path, repo), "action": "updated"}
        return {"path": relpath(agents_path, repo), "action": "present"}
    if "shujuan Repository Instructions" in existing or "shujuan-core" in existing:
        return {"path": relpath(agents_path, repo), "action": "present_unmanaged"}
    agents_path.write_text(existing.rstrip() + "\n\n" + template, encoding="utf-8")
    return {"path": relpath(agents_path, repo), "action": "injected"}


def ensure_shujuan_skill(repo: Path) -> dict[str, Any]:
    installed = []
    created = False
    updated = False
    for spec in skill_specs():
        skill_dir = skill_target_dir(repo, spec)
        skill_dir.mkdir(parents=True, exist_ok=True)
        desired_files = _skill_resource_files(spec.name)
        if not desired_files and spec.name == "shujuan-core":
            desired_files = {"SKILL.md": SHUJUAN_SKILL_TEMPLATE.rstrip() + "\n"}
            desired_files.update({path: text.rstrip() + "\n" for path, text in SHUJUAN_SKILL_REFERENCE_TEMPLATES.items()})
            desired_files.update({path: text.rstrip() + "\n" for path, text in SHUJUAN_SKILL_HANDOFF_TEMPLATES.items()})
        if not desired_files:
            desired_files = {"SKILL.md": f"---\nname: {spec.name}\ndescription: {spec.description}\n---\n\n# {spec.name}\n\n{spec.description}\n"}
        spec_created = False
        spec_updated = False
        for rel, content in desired_files.items():
            path = skill_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(content, encoding="utf-8")
                spec_created = True
            elif path.read_text(encoding="utf-8") != content:
                path.write_text(content, encoding="utf-8")
                spec_updated = True
        created = created or spec_created
        updated = updated or spec_updated
        installed.append(
            {
                "name": spec.name,
                "path": relpath(skill_dir / "SKILL.md", repo),
                "action": "created" if spec_created and not spec_updated else "updated" if spec_updated else "present",
                "version": spec.version,
                "compatibility": spec.compatibility,
                "references": sorted(path for path in desired_files if path.startswith("references/")),
                "templates": sorted(path for path in desired_files if path.startswith("templates/")),
            }
        )
    role_profiles = ensure_shujuan_role_profiles(repo)
    action = "created" if created and not updated else "updated" if updated else "present"
    return {
        "path": ".agents/skills/shujuan-core/SKILL.md",
        "action": action,
        "registry": registry_payload(),
        "skills": installed,
        "role_profiles": role_profiles,
    }


def ensure_shujuan_role_profiles(repo: Path) -> list[dict[str, str]]:
    target_dir = role_target_dir(repo)
    target_dir.mkdir(parents=True, exist_ok=True)
    desired_files = _role_profile_resource_files()
    if not desired_files:
        desired_files = {
            name: (
                f"name = \"{Path(name).stem}\"\n"
                f"description = \"{Path(name).stem} shujuan role profile.\"\n"
                "version = \"11.0\"\n"
                "compatibility = \"required\"\n"
                "sandbox_mode = \"read-only\"\n"
                "developer_instructions = \"Follow AGENTS.md; return role-bounded shujuan material without unauthorized closure.\"\n"
            )
            for name in ROLE_PROFILE_NAMES
        }
    installed = []
    for rel, content in desired_files.items():
        path = target_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        action = "present"
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            action = "created"
        elif path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            action = "updated"
        installed.append({"path": relpath(path, repo), "action": action})
    return installed


def ensure_codex_hooks(repo: Path) -> dict[str, Any]:
    hooks_dir = repo / ".codex" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    desired_files = _hook_resource_files()
    installed = []
    for rel, content in desired_files.items():
        path = hooks_dir / rel
        path = path.resolve()
        expected_root = (repo / ".codex").resolve()
        if expected_root not in (path, *path.parents):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        action = "present"
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            action = "created"
        elif path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            action = "updated"
        installed.append({"path": relpath(path, repo), "action": action})
    return {
        "path": ".codex/hooks.json",
        "action": "present" if all(item["action"] == "present" for item in installed) else "updated",
        "advisory": True,
        "installed": installed,
    }


def bundled_migration_source_dirs() -> list[Path]:
    return [
        Path(__file__).resolve().parent / "migrations" / "shujuan",
        Path(__file__).resolve().parents[1] / "migrations" / "shujuan",
    ]


def shujuan_migration_source_files() -> list[Path]:
    for source_dir in bundled_migration_source_dirs():
        if source_dir.exists():
            files = sorted(
                path
                for path in source_dir.iterdir()
                if path.is_file() and (path.suffix == ".sql" or path.name == "README.md")
            )
            if files:
                return files
    return []


def ensure_shujuan_migrations(repo: Path) -> dict[str, Any]:
    target_dir = repo / "migrations" / "shujuan"
    source_files = shujuan_migration_source_files()
    if not source_files:
        return {
            "path": relpath(target_dir, repo),
            "action": "unavailable",
            "files": [],
            "conflicts": [],
        }

    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    present: list[str] = []
    conflicts: list[str] = []
    for source_path in source_files:
        target_path = target_dir / source_path.name
        rel = relpath(target_path, repo)
        source_text = source_path.read_text(encoding="utf-8")
        if target_path.exists():
            if target_path.read_text(encoding="utf-8") == source_text:
                present.append(rel)
            else:
                conflicts.append(rel)
            continue
        shutil.copy2(source_path, target_path)
        created.append(rel)

    action = "conflict" if conflicts else "created" if created and not present else "updated" if created else "present"
    return {
        "path": relpath(target_dir, repo),
        "action": action,
        "files": sorted(created + present + conflicts),
        "created": sorted(created),
        "conflicts": sorted(conflicts),
    }



def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?",
            (name,),
        ).fetchone()
    )


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def split_markdown_sections(body: str, max_chars: int) -> list[dict[str, Any]]:
    headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", body))
    ranges: list[tuple[int, int, str | None]] = []
    if headings:
        if headings[0].start() > 0 and body[: headings[0].start()].strip():
            ranges.append((0, headings[0].start(), None))
        for index, match in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
            ranges.append((match.start(), end, match.group(2).strip()))
    else:
        ranges.append((0, len(body), None))

    sections: list[dict[str, Any]] = []
    for start, end, heading in ranges:
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + max_chars)
            if chunk_end < end:
                split_at = body.rfind("\n\n", cursor, chunk_end)
                if split_at > cursor + max_chars // 3:
                    chunk_end = split_at + 2
            chunk = body[cursor:chunk_end]
            if chunk.strip():
                sections.append(
                    {
                        "heading": heading,
                        "body": chunk,
                        "start_offset": cursor,
                        "end_offset": chunk_end,
                    }
                )
            cursor = chunk_end
    return sections


def first_heading(body: str) -> str | None:
    match = re.search(r"(?m)^#\s+(.+?)\s*$", body)
    return match.group(1).strip() if match else None


def read_arg_or_stdin(value: str | None, *, file_path: str | Path | None = None, label: str = "content") -> str:
    if value is not None and file_path is not None:
        raise StructuredCliError(
            "mutually_exclusive_input",
            f"pass either --{label} or --{label}-file, not both",
            read_only=True,
            input_label=label,
            safe_next_action=f"Provide --{label}, --{label}-file, or stdin; do not combine them.",
        )
    if file_path is not None:
        path = Path(file_path)
        try:
            return path.read_text(encoding="utf-8").rstrip("\n")
        except OSError as exc:
            raise StructuredCliError(
                "input_file_unreadable",
                f"{label} file could not be read: {path}: {exc}",
                read_only=True,
                input_label=label,
                path=str(path),
                safe_next_action=f"Provide a readable UTF-8 file via --{label}-file or pass --{label} directly.",
            ) from exc
    if value is not None:
        return value
    content = sys.stdin.read()
    if not content.strip():
        raise StructuredCliError(
            "missing_input",
            f"{label} is required via --{label}, --{label}-file, or stdin",
            read_only=True,
            input_label=label,
            safe_next_action=f"Provide --{label}, --{label}-file, or non-empty stdin.",
        )
    return content.rstrip("\n")


def ensure_session(
    conn: sqlite3.Connection,
    *,
    session_id: str | None,
    agent_name: str | None,
    model_name: str | None,
    source: str | None,
    metadata: dict[str, Any] | None = None,
) -> str:
    if session_id:
        existing = conn.execute("SELECT id FROM conversation_sessions WHERE id = ?", (session_id,)).fetchone()
        if existing:
            return session_id
    else:
        session_id = new_id("session")
    conn.execute(
        """
        INSERT INTO conversation_sessions
          (id, agent_name, model_name, source, started_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, agent_name, model_name, source, now_iso(), json_dumps(metadata or {})),
    )
    return session_id


def insert_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    actor: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> tuple[str, str]:
    content_hash = sha256_text(content)
    existing = conn.execute(
        """
        SELECT id, node_id
        FROM messages
        WHERE session_id = ? AND actor = ? AND content_hash = ?
        ORDER BY turn_index
        LIMIT 1
        """,
        (session_id, actor, content_hash),
    ).fetchone()
    if existing:
        return str(existing["id"]), str(existing["node_id"])
    turn_index_row = conn.execute(
        "SELECT COALESCE(MAX(turn_index), -1) + 1 AS next_turn_index FROM messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    turn_index = int(row_scalar(turn_index_row, "next_turn_index"))
    node_id = create_node(
        conn,
        "conversation_turn",
        f"{actor} turn {turn_index}",
        content.strip()[:240],
        {"session_id": session_id, "actor": actor, "turn_index": turn_index},
    )
    message_id = new_id("message")
    conn.execute(
        """
        INSERT INTO messages
          (id, session_id, node_id, actor, content, content_hash, turn_index, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            session_id,
            node_id,
            actor,
            content,
            content_hash,
            turn_index,
            created_at or now_iso(),
            json_dumps(metadata or {}),
        ),
    )
    return message_id, node_id


def transcript_records(path: Path) -> list[dict[str, Any]]:
    raw = read_file_text(path)
    stripped = raw.strip()
    if not stripped:
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in stripped.splitlines() if line.strip()]
    if path.suffix.lower() == ".json":
        data = json.loads(stripped)
        if isinstance(data, dict):
            data = data.get("messages", data.get("turns", []))
        if not isinstance(data, list):
            raise SystemExit("JSON transcript must be a list or an object with messages/turns")
        return [dict(item) for item in data]
    records: list[dict[str, Any]] = []
    current_actor = "user"
    current_lines: list[str] = []
    actor_pattern = re.compile(r"^(user|assistant|agent|tool|system)\s*:\s*(.*)$", re.IGNORECASE)
    for line in raw.splitlines():
        match = actor_pattern.match(line)
        if match:
            if current_lines:
                records.append({"actor": current_actor, "content": "\n".join(current_lines).strip()})
            current_actor = "agent" if match.group(1).lower() == "assistant" else match.group(1).lower()
            current_lines = [match.group(2)]
        else:
            current_lines.append(line)
    if current_lines:
        records.append({"actor": current_actor, "content": "\n".join(current_lines).strip()})
    return [record for record in records if record.get("content")]


STANDARD_EVENT_TYPES = {
    "user_prompt",
    "assistant_message",
    "tool_event",
    "system_message",
    "run_start",
    "run_stop",
}


def actor_to_standard_event(actor: str) -> str:
    normalized = actor.lower()
    if normalized == "assistant":
        normalized = "agent"
    return {
        "user": "user_prompt",
        "agent": "assistant_message",
        "tool": "tool_event",
        "system": "system_message",
    }.get(normalized, "assistant_message")


def standard_events_from_transcript(
    repo: Path,
    transcript: Path,
    *,
    session_id: str | None,
    agent_name: str | None,
    model_name: str | None,
    source: str | None,
) -> list[dict[str, Any]]:
    records = transcript_records(transcript)
    event_source = source or relpath(transcript, repo)
    events = []
    for index, record in enumerate(records):
        actor = str(record.get("actor") or record.get("role") or "user").lower()
        if actor == "assistant":
            actor = "agent"
        event_type = str(record.get("event_type") or actor_to_standard_event(actor))
        if event_type not in STANDARD_EVENT_TYPES:
            raise SystemExit(f"unsupported standard event_type: {event_type}")
        content = str(record.get("content") or record.get("message") or record.get("text") or "").strip()
        events.append(
            {
                "event_type": event_type,
                "session_id": session_id,
                "run_id": record.get("run_id"),
                "actor": actor,
                "content": content,
                "content_hash": sha256_text(content),
                "turn_index": index,
                "source": event_source,
                "occurred_at": record.get("created_at") or record.get("timestamp"),
                "agent_name": agent_name,
                "model_name": model_name,
                "metadata": {
                    "adapter": "manual",
                    "turn_index": index,
                    "content_hash": sha256_text(content),
                    "raw": record,
                    "cwd": str(repo),
                },
            }
        )
    return events


def insert_standard_event(conn: sqlite3.Connection, event: dict[str, Any], *, session_id: str) -> str:
    event_id = new_id("event")
    timestamp = now_iso()
    metadata = event.get("metadata") or {}
    content = str(event.get("content") or "")
    content_hash = str(metadata.get("content_hash") or sha256_text(content))
    if table_exists(conn, "standard_events"):
        conn.execute(
            """
            INSERT INTO standard_events
              (id, event_type, session_id, run_id, actor, content, source, occurred_at, imported_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event["event_type"],
                session_id,
                event.get("run_id"),
                event.get("actor"),
                content,
                event.get("source"),
                event.get("occurred_at"),
                timestamp,
                json_dumps(metadata),
            ),
        )
        return event_id
    if table_exists(conn, "interaction_events"):
        node_id = create_node(
            conn,
            "interaction_event",
            str(event.get("event_type") or "manual event"),
            content[:240],
            {
                "adapter": "manual",
                "standard_event_model": "shujuan.standard_event.v1",
                "session_id": session_id,
                "event_type": event.get("event_type"),
                "content_hash": content_hash,
            },
        )
        conn.execute(
            """
            INSERT INTO interaction_events
              (id, node_id, endpoint_id, event_type, mode, session_id, actor, summary,
               source, content_hash, occurred_at, imported_at, reviewed_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                node_id,
                None,
                event["event_type"],
                "capture",
                session_id,
                event.get("actor"),
                content[:240],
                event.get("source"),
                content_hash,
                event.get("occurred_at"),
                timestamp,
                None,
                json_dumps({"adapter": "manual", "standard_event_model": "shujuan.standard_event.v1", **metadata}),
            ),
        )
    return event_id


def record_evidence_record(
    conn: sqlite3.Connection,
    *,
    evidence_node_id: str,
    record_type: str,
    ref: str | None = None,
    sha256: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    assert_runtime_schema_ready(conn, purpose="evidence record write")
    record_id = new_id("evidence_record")
    conn.execute(
        """
        INSERT INTO evidence_records
          (id, evidence_node_id, record_type, ref, sha256, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (record_id, evidence_node_id, record_type, ref, sha256, now_iso(), json_dumps(metadata or {})),
    )
    return record_id


def message_actor_for_event(event: dict[str, Any]) -> str | None:
    return {
        "user_prompt": "user",
        "assistant_message": "agent",
        "tool_event": "tool",
        "system_message": "system",
    }.get(str(event.get("event_type")))


MODE_CONTRACTS: dict[str, dict[str, Any]] = {
    "no_governance": {
        "db_writes": False,
        "capture_claim": False,
        "creates_run": False,
        "creates_change_set": False,
        "summary": "No Governance writes no DB facts and makes no capture claim.",
    },
    "capture": {
        "db_writes": True,
        "capture_claim": True,
        "creates_run": False,
        "creates_change_set": False,
        "summary": "Capture records interaction/discussion source material only.",
    },
    "explore": {
        "db_writes": True,
        "capture_claim": True,
        "creates_run": False,
        "creates_change_set": False,
        "summary": "Explore records source material and questions, but starts no execution run.",
    },
    "light": {
        "db_writes": True,
        "capture_claim": False,
        "creates_run": True,
        "creates_change_set": False,
        "summary": "Light may start an execution run for scoped low-risk work.",
    },
    "standard": {
        "db_writes": True,
        "capture_claim": False,
        "creates_run": True,
        "creates_change_set": False,
        "summary": "Standard expects scoped task/check linkage and targeted verification.",
    },
    "full": {
        "db_writes": True,
        "capture_claim": False,
        "creates_run": True,
        "creates_change_set": False,
        "summary": "Full expects broad impact review, evidence, and strict closeout readiness.",
    },
}


def normalize_mode(value: str | None) -> str:
    try:
        return activation_policy.normalize_mode(value, MODE_CONTRACTS)
    except SystemExit as exc:
        allowed = ", ".join(sorted(MODE_CONTRACTS))
        raise ShujuanUsageError(
            "invalid_mode",
            str(exc) or f"mode must be one of: {allowed}",
            read_only=True,
            safe_next_action="Choose one of the listed modes, or omit --mode to let route guard infer it.",
        ) from None


EXPLICIT_NO_GOVERNANCE_INTENT_MARKERS = activation_policy.EXPLICIT_NO_GOVERNANCE_INTENT_MARKERS


def explicit_no_governance_reasons(intent: str) -> list[str]:
    return [f"explicit_no_governance:{marker}" for marker in relation_policy.explicit_no_governance_hits(intent)]


def recover_like_reasons(intent: str) -> list[str]:
    return activation_policy.recover_like_reasons(intent)


def suggest_mode_from_args(args: argparse.Namespace) -> tuple[str, list[str]]:
    if getattr(args, "mode", None):
        return normalize_mode(args.mode), ["explicit_mode"]
    if getattr(args, "no_governance", False):
        return "no_governance", ["flag:no_governance"]
    if getattr(args, "capture_only", False):
        return "capture", ["flag:capture_only"]
    intent = (getattr(args, "intent", None) or "").lower()
    reasons: list[str] = []
    no_governance_reasons = explicit_no_governance_reasons(intent)
    if no_governance_reasons:
        return "no_governance", no_governance_reasons
    recover_reasons = recover_like_reasons(intent)
    if recover_reasons:
        return "explore", recover_reasons
    relation = relation_policy.classify_relation(intent)
    if relation["mode_hint"] in MODE_CONTRACTS and relation["relation_type"] != "independent_root":
        reasons.append(f"relation_mode_hint:{relation['relation_type']}")
        return str(relation["mode_hint"]), reasons
    intent_rules = [
        ("explore", "intent:read_or_question", ["status", "summarize", "read only", "read-only", "question", "报告", "状态", "只读", "问题"]),
        ("capture", "intent:capture", ["capture", "save this", "record this", "discussion"]),
        ("explore", "intent:explore", ["explore", "research", "investigate", "design"]),
        ("full", "intent:full", ["schema", "migration", "security", "release", "cross-module", "broad"]),
        ("standard", "intent:standard", ["fix", "implement", "change", "test", "code"]),
    ]
    matches: list[tuple[str, str]] = []
    for mode, reason, tokens in intent_rules:
        if any(token in intent for token in tokens):
            matches.append((mode, reason))
    if len({mode for mode, _reason in matches}) > 1:
        reasons.extend(reason for _mode, reason in matches)
        reasons.append("ambiguous_intent:multiple_mode_matches")
        return "explore", reasons
    if matches:
        mode, reason = matches[0]
        reasons.append(reason)
        return mode, reasons
    reasons.append("default:light")
    return "light", reasons


def mode_contract_payload(mode: str) -> dict[str, Any]:
    return activation_policy.mode_contract_payload(mode, MODE_CONTRACTS)


def acceptance_template_for_mode(mode: str) -> dict[str, Any]:
    return activation_policy.acceptance_template_for_mode(mode)


def allowed_side_effects_for_mode(mode: str) -> list[str]:
    return activation_policy.allowed_side_effects_for_mode(mode)


def forbidden_side_effects_for_mode(mode: str) -> list[str]:
    return activation_policy.forbidden_side_effects_for_mode(mode)


HIGH_RISK_MODE_TERMS = activation_policy.HIGH_RISK_MODE_TERMS


def mode_gate_warnings(mode: str, intent: str | None) -> list[dict[str, Any]]:
    return activation_policy.mode_gate_warnings(mode, intent)


def endpoint_has_attention_packet(conn: sqlite3.Connection, endpoint_node_id: str | None, task_node_id: str | None = None) -> bool:
    target_node_ids = [node_id for node_id in [endpoint_node_id, task_node_id] if node_id]
    if not target_node_ids:
        return False
    placeholders = ",".join("?" for _ in target_node_ids)
    rows = conn.execute(
        f"""
        SELECT n.id, n.type, n.label, n.summary, n.props
        FROM nodes n
        JOIN edges e ON e.from_node_id = n.id
        WHERE e.type = 'APPLIES_TO'
          AND e.to_node_id IN ({placeholders})
          AND n.type IN ('artifact', 'work_note')
          AND {active_node_clause("n")}
        ORDER BY n.created_at DESC
        LIMIT 50
        """,
        target_node_ids,
    ).fetchall()
    for row in rows:
        props = props_dict(row)
        text = " ".join(str(value or "").lower() for value in [row["label"], row["summary"], props.get("kind"), props.get("artifact_type"), props.get("body")])
        if "attention_packet" in text or "attention packet" in text:
            return True
    return False


def endpoint_unreviewed_discussion_count(conn: sqlite3.Connection, endpoint_name: str) -> int:
    endpoint = query_endpoint(conn, endpoint_name)
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM discussion_segments
        WHERE endpoint_id = ?
          AND reviewed_at IS NULL
          AND status = 'unreviewed'
        """,
        (endpoint["id"],),
    ).fetchone()
    return int(row_scalar(row, "count") or 0)


def record_discussion_lifecycle_event(
    conn: sqlite3.Connection,
    *,
    segment_id: str,
    event_type: str,
    to_status: str,
    source_node_id: str | None = None,
    actor: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    update_segment: bool = True,
) -> str:
    assert_runtime_schema_ready(conn, purpose="discussion lifecycle write")
    row = conn.execute("SELECT status FROM discussion_segments WHERE id = ?", (segment_id,)).fetchone()
    if not row:
        raise SystemExit(f"discussion segment not found: {segment_id}")
    from_status = str(row["status"])
    event_id = new_id("discussion_lifecycle")
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO discussion_lifecycle_events
          (id, segment_id, event_type, from_status, to_status, source_node_id, actor, reason, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            segment_id,
            event_type,
            from_status,
            to_status,
            source_node_id,
            actor,
            reason,
            timestamp,
            json_dumps(metadata or {}),
        ),
    )
    if update_segment:
        reviewed_at_sql = ", reviewed_at = COALESCE(reviewed_at, ?)" if to_status in {"reviewed", "extracted", "consumed"} else ""
        params: tuple[Any, ...]
        if reviewed_at_sql:
            params = (to_status, timestamp, segment_id)
        else:
            params = (to_status, segment_id)
        conn.execute(f"UPDATE discussion_segments SET status = ?{reviewed_at_sql} WHERE id = ?", params)
    return event_id


def create_discussion_segment(
    conn: sqlite3.Connection,
    *,
    endpoint_name: str,
    messages: list[dict[str, Any]],
    session_id: str | None,
    agent_name: str | None,
    model_name: str | None,
    source: str | None,
    title: str | None,
    mode: str,
    reviewed: bool = False,
    event_type: str = "discussion_capture",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not messages:
        raise SystemExit("discussion capture requires at least one message")
    endpoint = query_endpoint(conn, endpoint_name)
    session = ensure_session(
        conn,
        session_id=session_id,
        agent_name=agent_name,
        model_name=model_name,
        source=source or "discussion",
        metadata={"event_type": event_type, "mode": mode, **(metadata or {})},
    )
    timestamp = now_iso()
    normalized_messages = []
    for index, message in enumerate(messages):
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        normalized_messages.append(
            {
                "actor": str(message.get("actor") or "user").lower(),
                "content": content,
                "turn_index": int(message.get("turn_index") if message.get("turn_index") is not None else index),
                "source_node_id": message.get("source_node_id"),
                "source_message_id": message.get("source_message_id"),
                "metadata": message.get("metadata") or {},
            }
        )
    if not normalized_messages:
        raise SystemExit("discussion capture requires at least one non-empty message")
    content_hash = sha256_text(json_dumps([{key: item[key] for key in ("actor", "content", "turn_index")} for item in normalized_messages]))
    summary = "\n".join(f"{item['actor']}: {item['content']}" for item in normalized_messages)[:240]
    event_node_id = create_node(
        conn,
        "interaction_event",
        f"{mode} interaction",
        summary,
        {
            "endpoint": endpoint_name,
            "mode": mode,
            "event_type": event_type,
            "governance_effect": "capture_only",
            "creates_task": False,
            "creates_check": False,
            "creates_run": False,
            "creates_change_set": False,
            "content_hash": content_hash,
            "message_count": len(normalized_messages),
        },
    )
    interaction_id = new_id("interaction")
    reviewed_at = timestamp if reviewed else None
    conn.execute(
        """
        INSERT INTO interaction_events
          (id, node_id, endpoint_id, event_type, mode, session_id, actor, summary,
           source, content_hash, occurred_at, imported_at, reviewed_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            interaction_id,
            event_node_id,
            endpoint["id"],
            event_type,
            mode,
            session,
            normalized_messages[0]["actor"],
            summary,
            source or "discussion",
            content_hash,
            timestamp,
            timestamp,
            reviewed_at,
            json_dumps({"endpoint": endpoint_name, "mode": mode, "message_count": len(normalized_messages), **(metadata or {})}),
        ),
    )
    segment_node_id = create_node(
        conn,
        "discussion_segment",
        title or f"{mode} discussion",
        summary,
        {"endpoint": endpoint_name, "mode": mode, "event_id": interaction_id, "reviewed": reviewed, "content_hash": content_hash},
    )
    segment_id = new_id("segment")
    segment_title = title or (normalized_messages[0]["content"].splitlines()[0][:80] if normalized_messages else f"{mode} discussion")
    status = "reviewed" if reviewed else "unreviewed"
    conn.execute(
        """
        INSERT INTO discussion_segments
          (id, node_id, endpoint_id, event_id, session_id, title, status, created_at, reviewed_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            segment_id,
            segment_node_id,
            endpoint["id"],
            interaction_id,
            session,
            segment_title,
            status,
            timestamp,
            reviewed_at,
            json_dumps({"mode": mode, "source": source or "discussion", "message_count": len(normalized_messages), **(metadata or {})}),
        ),
    )
    message_results = []
    for message in normalized_messages:
        message_node_id = create_node(
            conn,
            "discussion_message",
            f"{message['actor']} discussion message",
            message["content"][:240],
            {
                "endpoint": endpoint_name,
                "segment_id": segment_id,
                "event_id": interaction_id,
                "session_id": session,
                "agent_name": agent_name,
                "model_name": model_name,
                "turn_index": message["turn_index"],
                "source_node_id": message.get("source_node_id"),
                "source_message_id": message.get("source_message_id"),
            },
        )
        discussion_message_id = new_id("discussion_message")
        message_hash = sha256_text(message["content"])
        conn.execute(
            """
            INSERT INTO discussion_messages
              (id, segment_id, event_id, node_id, session_id, agent_name, model_name,
               source_message_id, source_node_id, actor, content, content_hash, turn_index, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                discussion_message_id,
                segment_id,
                interaction_id,
                message_node_id,
                session,
                agent_name,
                model_name,
                message.get("source_message_id"),
                message.get("source_node_id"),
                message["actor"],
                message["content"],
                message_hash,
                message["turn_index"],
                timestamp,
                json_dumps({"mode": mode, "content_hash": message_hash, **(message.get("metadata") or {})}),
            ),
        )
        create_edge(conn, message_node_id, "DERIVED_FROM", segment_node_id, reason="Discussion message is part of captured segment.", created_by="agent")
        if message.get("source_node_id"):
            create_edge(conn, message_node_id, "DERIVED_FROM", str(message["source_node_id"]), reason="Discussion message preserves imported source message provenance.", created_by="agent")
        message_results.append(
            {
                "message_id": discussion_message_id,
                "node_id": message_node_id,
                "actor": message["actor"],
                "turn_index": message["turn_index"],
                "content_hash": message_hash,
                "session_id": session,
                "agent_name": agent_name,
                "model_name": model_name,
                "source_message_id": message.get("source_message_id"),
                "source_node_id": message.get("source_node_id"),
            }
        )
    create_edge(conn, event_node_id, "APPLIES_TO", endpoint["node_id"], reason="Interaction event applies to endpoint.", created_by="agent")
    create_edge(conn, segment_node_id, "DERIVED_FROM", event_node_id, reason="Discussion segment captured from interaction event.", created_by="agent")
    create_edge(conn, segment_node_id, "APPLIES_TO", endpoint["node_id"], reason="Discussion segment applies to endpoint.", created_by="agent")
    lifecycle_event_id = record_discussion_lifecycle_event(
        conn,
        segment_id=segment_id,
        event_type="captured",
        to_status=status,
        source_node_id=event_node_id,
        actor=normalized_messages[0]["actor"],
        reason="Discussion segment captured as reviewable source material.",
        metadata={"message_count": len(normalized_messages), "content_hash": content_hash},
        update_segment=False,
    )
    return {
        "event_id": interaction_id,
        "event_node_id": event_node_id,
        "segment_id": segment_id,
        "segment_node_id": segment_node_id,
        "message_id": message_results[0]["message_id"] if len(message_results) == 1 else None,
        "message_node_id": message_results[0]["node_id"] if len(message_results) == 1 else None,
        "messages": message_results,
        "session_id": session,
        "lifecycle_event_id": lifecycle_event_id,
        "receipt": {
            "kind": "discussion_capture",
            "endpoint": endpoint_name,
            "mode": mode,
            "status": status,
            "reviewed": reviewed,
            "message_count": len(message_results),
            "turn_range": [min(item["turn_index"] for item in message_results), max(item["turn_index"] for item in message_results)],
            "content_hash": content_hash,
            "creates_task": False,
            "creates_check": False,
            "creates_run": False,
            "creates_change_set": False,
        },
    }


def create_discussion_capture(
    conn: sqlite3.Connection,
    *,
    endpoint_name: str,
    content: str,
    actor: str,
    session_id: str | None,
    agent_name: str | None,
    model_name: str | None,
    source: str | None,
    title: str | None,
    mode: str,
    reviewed: bool = False,
) -> dict[str, Any]:
    return create_discussion_segment(
        conn,
        endpoint_name=endpoint_name,
        messages=[{"actor": actor, "content": content, "turn_index": 0}],
        session_id=session_id,
        agent_name=agent_name,
        model_name=model_name,
        source=source,
        title=title,
        mode=mode,
        reviewed=reviewed,
    )


def discussion_rows(conn: sqlite3.Connection, endpoint_name: str, *, include_reviewed: bool, limit: int) -> list[sqlite3.Row]:
    endpoint = query_endpoint(conn, endpoint_name)
    reviewed_clause = "" if include_reviewed else "AND ds.reviewed_at IS NULL AND ds.status = 'unreviewed'"
    return conn.execute(
        f"""
        SELECT ds.*, n.label, n.summary, ie.mode, ie.event_type,
               cs.agent_name, cs.model_name,
               (SELECT COUNT(*) FROM discussion_messages dm WHERE dm.segment_id = ds.id) AS message_count
        FROM discussion_segments ds
        JOIN nodes n ON n.id = ds.node_id
        JOIN interaction_events ie ON ie.id = ds.event_id
        LEFT JOIN conversation_sessions cs ON cs.id = ds.session_id
        WHERE ds.endpoint_id = ?
          {reviewed_clause}
        ORDER BY ds.created_at DESC, ds.id DESC
        LIMIT ?
        """,
        (endpoint["id"], limit),
    ).fetchall()


def resolve_discussion_segment(conn: sqlite3.Connection, identifier: str, *, endpoint_name: str | None = None) -> sqlite3.Row:
    if identifier == "@last.discussion":
        if endpoint_name:
            endpoint = query_endpoint(conn, endpoint_name)
            row = conn.execute(
                "SELECT * FROM discussion_segments WHERE endpoint_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (endpoint["id"],),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM discussion_segments ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    else:
        row = conn.execute("SELECT * FROM discussion_segments WHERE id = ? OR node_id = ?", (identifier, identifier)).fetchone()
    if not row:
        raise SystemExit(f"discussion segment not found: {identifier}")
    return row


def discussion_messages(conn: sqlite3.Connection, segment_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM discussion_messages
        WHERE segment_id = ?
        ORDER BY turn_index ASC, created_at ASC, id ASC
        """,
        (segment_id,),
    ).fetchall()



def split_cli_id_text(value: str, *, generated_prefix: str) -> tuple[str, str]:
    for delimiter in ("::", "="):
        if delimiter in value:
            left, right = value.split(delimiter, 1)
            left = left.strip()
            right = right.strip()
            if not left or not right:
                raise SystemExit(f"value must use non-empty id and text around {delimiter!r}: {value}")
            return left, right
    text = value.strip()
    if not text:
        raise SystemExit("value must not be empty")
    return new_id(generated_prefix), text


def parse_forbidden_substitute_arg(value: str, default_predicate_id: str | None = None) -> dict[str, str]:
    parts = [part.strip() for part in value.split("::")]
    if len(parts) == 1:
        if not default_predicate_id:
            raise SystemExit("forbidden substitutes without predicate id require at least one predicate in this intake")
        return {"predicate_id": default_predicate_id, "substitute_text": parts[0], "reason": ""}
    if len(parts) == 2:
        return {"predicate_id": parts[0], "substitute_text": parts[1], "reason": ""}
    if len(parts) == 3:
        return {"predicate_id": parts[0], "substitute_text": parts[1], "reason": parts[2]}
    raise SystemExit("forbidden substitute must be TEXT, PREDICATE::TEXT, or PREDICATE::TEXT::REASON")


def parse_predicate_scoped_value(value: str, *, default_predicate_id: str | None, label: str) -> dict[str, str]:
    parts = [part.strip() for part in value.split("::", 1)]
    if len(parts) == 2:
        if not parts[0] or not parts[1]:
            raise SystemExit(f"{label} must be TEXT or PREDICATE::TEXT")
        return {"predicate_id": parts[0], "text": parts[1]}
    if not parts[0]:
        raise SystemExit(f"{label} must not be empty")
    if not default_predicate_id:
        raise SystemExit(f"{label} without predicate id requires at least one predicate in this intake")
    return {"predicate_id": default_predicate_id, "text": parts[0]}


def append_unique_metadata_value(metadata: dict[str, dict[str, list[str]]], predicate_id: str, key: str, value: str) -> None:
    values = metadata.setdefault(predicate_id, {}).setdefault(key, [])
    if value not in values:
        values.append(value)



def parse_work_split_link(value: str) -> dict[str, str]:
    parts = [part.strip() for part in value.split("::")]
    if len(parts) not in {3, 4} or any(not part for part in parts[:3]):
        raise SystemExit("work split --link must be TASK_ID::CHECK_ID::PREDICATE_ID[::RELATIONSHIP]")
    relationship = parts[3] if len(parts) == 4 else "proves"
    if relationship not in {"implements", "proves", "guards", "negative_test"}:
        raise SystemExit("task predicate relationship must be implements, proves, guards, or negative_test")
    return {"task_id": parts[0], "check_id": parts[1], "predicate_id": parts[2], "relationship": relationship}


def insert_task_predicate_link(conn: sqlite3.Connection, *, task_id: str, check_id: str, predicate_id: str, relationship: str) -> dict[str, str]:
    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        raise SystemExit(f"task not found: {task_id}")
    check = conn.execute("SELECT id, task_id FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone()
    if not check:
        raise SystemExit(f"acceptance check not found: {check_id}")
    if check["task_id"] != task_id:
        raise SystemExit(f"acceptance check {check_id} belongs to task {check['task_id']}, not {task_id}")
    contracted_tables = [
        table
        for table in ("hard_predicates", "task_predicate_links")
        if not table_exists(conn, table)
    ]
    if contracted_tables:
        return {
            "task_id": task_id,
            "check_id": check_id,
            "predicate_id": predicate_id,
            "relationship": relationship,
            "status": "contracted_table_unavailable",
            "contracted_tables": contracted_tables,
        }
    if not conn.execute("SELECT 1 FROM hard_predicates WHERE id = ?", (predicate_id,)).fetchone():
        raise SystemExit(f"hard predicate not found: {predicate_id}")
    existing = conn.execute(
        """
        SELECT 1 FROM task_predicate_links
        WHERE task_id = ? AND check_id = ? AND predicate_id = ? AND relationship = ?
        """,
        (task_id, check_id, predicate_id, relationship),
    ).fetchone()
    if not existing:
        conn.execute(
            """
            INSERT INTO task_predicate_links
              (task_id, check_id, predicate_id, relationship)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, check_id, predicate_id, relationship),
        )
    return {"task_id": task_id, "check_id": check_id, "predicate_id": predicate_id, "relationship": relationship}



def endpoint_agcp_predicate_rows(conn: sqlite3.Connection, endpoint_id: str) -> list[sqlite3.Row]:
    if not table_exists(conn, "source_promises") or not table_exists(conn, "hard_predicates"):
        return []
    return conn.execute(
        """
        SELECT hp.*, sp.id AS source_promise_id, sp.text AS source_promise_text, sp.source_locator
        FROM hard_predicates hp
        JOIN source_promises sp ON sp.id = hp.source_promise_id
        WHERE sp.endpoint_id = ?
          AND hp.lifecycle = 'active'
        ORDER BY sp.created_at ASC, hp.created_at ASC, hp.id ASC
        """,
        (endpoint_id,),
    ).fetchall()


def endpoint_forbidden_substitute_rows(conn: sqlite3.Connection, endpoint_id: str) -> list[sqlite3.Row]:
    if (
        not table_exists(conn, "source_promises")
        or not table_exists(conn, "hard_predicates")
        or not table_exists(conn, "forbidden_substitutes")
    ):
        return []
    return conn.execute(
        """
        SELECT fs.*, hp.source_promise_id
        FROM forbidden_substitutes fs
        JOIN hard_predicates hp ON hp.id = fs.predicate_id
        JOIN source_promises sp ON sp.id = hp.source_promise_id
        WHERE sp.endpoint_id = ?
          AND hp.lifecycle = 'active'
        ORDER BY fs.created_at ASC, fs.id ASC
        """,
        (endpoint_id,),
    ).fetchall()


def endpoint_work_chain_rows(conn: sqlite3.Connection, endpoint_id: str, chain_id: str | None = None) -> list[sqlite3.Row]:
    if not table_exists(conn, "work_chains"):
        return []
    if chain_id:
        return conn.execute(
            """
            SELECT * FROM work_chains
            WHERE endpoint_id = ? AND id = ? AND lifecycle = 'active'
            ORDER BY created_at ASC, id ASC
            """,
            (endpoint_id, chain_id),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM work_chains
        WHERE endpoint_id = ? AND lifecycle = 'active'
        ORDER BY created_at ASC, id ASC
        """,
        (endpoint_id,),
    ).fetchall()


def predicate_link_rows(conn: sqlite3.Connection, predicate_ids: list[str] | None = None) -> list[sqlite3.Row]:
    if not table_exists(conn, "task_predicate_links"):
        return []
    if predicate_ids:
        placeholders = ",".join("?" for _ in predicate_ids)
        return conn.execute(
            f"""
            SELECT * FROM task_predicate_links
            WHERE predicate_id IN ({placeholders})
            ORDER BY task_id ASC, check_id ASC, predicate_id ASC
            """,
            predicate_ids,
        ).fetchall()
    return conn.execute("SELECT * FROM task_predicate_links ORDER BY task_id ASC, check_id ASC, predicate_id ASC").fetchall()


def endpoint_source_promise_rows(conn: sqlite3.Connection, endpoint_id: str) -> list[sqlite3.Row]:
    if not table_exists(conn, "source_promises"):
        return []
    return conn.execute(
        """
        SELECT *
        FROM source_promises
        WHERE endpoint_id = ?
          AND hardness = 'hard'
        ORDER BY created_at ASC, id ASC
        """,
        (endpoint_id,),
    ).fetchall()


def text_contains_term(text: str, term: str) -> bool:
    return term.casefold() in text.casefold()


def predicate_terms(metadata: dict[str, Any]) -> list[dict[str, str]]:
    terms: list[dict[str, str]] = []
    for key, category in (
        ("required_terms", "required"),
        ("named_terms", "named"),
        ("must_terms", "must"),
    ):
        raw_values = metadata.get(key) or []
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        for value in raw_values if isinstance(raw_values, list) else []:
            term = str(value).strip()
            if term:
                terms.append({"term": term, "category": category})
    return terms


def predicate_enumerated_items(metadata: dict[str, Any]) -> list[str]:
    raw_values = metadata.get("enumerated_items") or []
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    if not isinstance(raw_values, list):
        return []
    return [str(value).strip() for value in raw_values if str(value).strip()]


def source_backed_downgrade_authorizations(
    conn: sqlite3.Connection,
    *,
    source_node_id: str,
    target_node_ids: list[str],
) -> list[dict[str, Any]]:
    if not target_node_ids:
        return []
    placeholders = ",".join("?" for _ in target_node_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT n.id, n.type, n.label, n.summary, n.updated_at, si.current_state
        FROM nodes n
        JOIN edges source_edge
          ON source_edge.from_node_id = n.id
         AND source_edge.type = 'DERIVED_FROM'
         AND source_edge.to_node_id = ?
        JOIN edges applies_edge
          ON applies_edge.from_node_id = n.id
         AND applies_edge.type = 'APPLIES_TO'
         AND applies_edge.to_node_id IN ({placeholders})
        LEFT JOIN semantic_items si ON si.node_id = n.id
        WHERE n.type IN ('scope_change', 'defer_decision')
          AND n.valid_to IS NULL
          AND (si.current_state IS NULL OR si.current_state NOT IN ('invalidated', 'superseded'))
        ORDER BY n.updated_at DESC, n.id ASC
        """,
        [source_node_id, *target_node_ids],
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def append_source_nondowngrade_finding(
    findings: list[dict[str, Any]],
    *,
    code: str,
    message: str,
    source_promise_id: str,
    source_locator: str | None,
    predicate_id: str | None = None,
    task_id: str | None = None,
    check_id: str | None = None,
    term: str | None = None,
    substitute_text: str | None = None,
    authorization: list[dict[str, Any]] | None = None,
) -> None:
    if authorization:
        return
    findings.append(
        {
            "code": code,
            "severity": "P0",
            "message": message,
            "source_promise_id": source_promise_id,
            "source_locator": source_locator,
            "predicate_id": predicate_id,
            "task_id": task_id,
            "check_id": check_id,
            "term": term,
            "substitute_text": substitute_text,
        }
    )


def endpoint_source_nondowngrade_audit(conn: sqlite3.Connection, endpoint_id: str) -> dict[str, Any]:
    contracted_tables = [
        table
        for table in ("source_promises", "hard_predicates", "forbidden_substitutes", "task_predicate_links")
        if not table_exists(conn, table)
    ]
    if contracted_tables:
        return {
            "ok": True,
            "source_promise_count": 0,
            "finding_count": 0,
            "findings": [],
            "source_promise_matrix": [],
            "status": "contracted_table_unavailable",
            "contracted_tables": contracted_tables,
            "replacement_path": "source commitments are carried by source artifacts, acceptance_checks, semantic_items.props, edges, and evidence_records",
        }
    promise_rows = endpoint_source_promise_rows(conn, endpoint_id)
    findings: list[dict[str, Any]] = []
    promise_matrix: list[dict[str, Any]] = []
    for promise in promise_rows:
        promise_id = str(promise["id"])
        source_locator = promise["source_locator"]
        if not source_locator:
            append_source_nondowngrade_finding(
                findings,
                code="source_locator_missing",
                message="Source promise has no source_locator, so the audit cannot map it back to a source document section.",
                source_promise_id=promise_id,
                source_locator=source_locator,
            )
        predicates = conn.execute(
            """
            SELECT *
            FROM hard_predicates
            WHERE source_promise_id = ?
              AND lifecycle = 'active'
            ORDER BY created_at ASC, id ASC
            """,
            (promise_id,),
        ).fetchall()
        promise_entry = {
            "source_promise_id": promise_id,
            "source_node_id": promise["source_node_id"],
            "source_locator": source_locator,
            "text": promise["text"],
            "hard_predicates": [],
        }
        if not predicates:
            append_source_nondowngrade_finding(
                findings,
                code="source_promise_without_hard_predicate",
                message="Hard source promise has no active hard predicate extracted from it.",
                source_promise_id=promise_id,
                source_locator=source_locator,
            )
        for predicate in predicates:
            predicate_id = str(predicate["id"])
            metadata = props_dict(predicate["metadata"])
            required_terms = predicate_terms(metadata)
            enumerated_items = predicate_enumerated_items(metadata)
            substitutes = conn.execute(
                """
                SELECT *
                FROM forbidden_substitutes
                WHERE predicate_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (predicate_id,),
            ).fetchall()
            links = conn.execute(
                """
                SELECT
                  tpl.task_id,
                  tpl.check_id,
                  tpl.relationship,
                  t.node_id AS task_node_id,
                  t.task_body,
                  ac.node_id AS check_node_id,
                  ac.check_body
                FROM task_predicate_links tpl
                LEFT JOIN tasks t ON t.id = tpl.task_id
                LEFT JOIN acceptance_checks ac ON ac.id = tpl.check_id
                WHERE tpl.predicate_id = ?
                ORDER BY tpl.task_id ASC, tpl.check_id ASC
                """,
                (predicate_id,),
            ).fetchall()
            predicate_entry = {
                "predicate_id": predicate_id,
                "claim": predicate["claim"],
                "source_locator": source_locator,
                "required_terms": required_terms,
                "enumerated_items": enumerated_items,
                "forbidden_substitutes": [row_to_dict(row) for row in substitutes],
                "acceptance_check_links": [],
            }
            if not links:
                append_source_nondowngrade_finding(
                    findings,
                    code="hard_predicate_missing_acceptance_check",
                    message="Hard predicate is not mapped to any acceptance check.",
                    source_promise_id=promise_id,
                    source_locator=source_locator,
                    predicate_id=predicate_id,
                )
            for link in links:
                target_node_ids = [str(value) for value in (link["task_node_id"], link["check_node_id"]) if value]
                authorization = source_backed_downgrade_authorizations(
                    conn,
                    source_node_id=str(promise["source_node_id"]),
                    target_node_ids=target_node_ids,
                )
                check_text = str(link["check_body"] or "")
                task_text = str(link["task_body"] or "")
                combined_text = f"{task_text}\n{check_text}"
                link_findings_before = len(findings)
                for term_info in required_terms:
                    term = term_info["term"]
                    if not text_contains_term(check_text, term):
                        append_source_nondowngrade_finding(
                            findings,
                            code=f"missing_{term_info['category']}_term_in_acceptance_check",
                            message=f"Acceptance check omits required source term: {term}",
                            source_promise_id=promise_id,
                            source_locator=source_locator,
                            predicate_id=predicate_id,
                            task_id=link["task_id"],
                            check_id=link["check_id"],
                            term=term,
                            authorization=authorization,
                        )
                for item in enumerated_items:
                    if not text_contains_term(check_text, item):
                        append_source_nondowngrade_finding(
                            findings,
                            code="missing_enumerated_item_in_acceptance_check",
                            message=f"Acceptance check omits enumerated source item: {item}",
                            source_promise_id=promise_id,
                            source_locator=source_locator,
                            predicate_id=predicate_id,
                            task_id=link["task_id"],
                            check_id=link["check_id"],
                            term=item,
                            authorization=authorization,
                        )
                for substitute in substitutes:
                    substitute_text = str(substitute["substitute_text"])
                    if text_contains_term(combined_text, substitute_text):
                        append_source_nondowngrade_finding(
                            findings,
                            code="forbidden_substitute_in_task_or_check",
                            message=f"Task/check text uses forbidden substitute: {substitute_text}",
                            source_promise_id=promise_id,
                            source_locator=source_locator,
                            predicate_id=predicate_id,
                            task_id=link["task_id"],
                            check_id=link["check_id"],
                            substitute_text=substitute_text,
                            authorization=authorization,
                        )
                predicate_entry["acceptance_check_links"].append(
                    {
                        "task_id": link["task_id"],
                        "check_id": link["check_id"],
                        "relationship": link["relationship"],
                        "task_node_id": link["task_node_id"],
                        "check_node_id": link["check_node_id"],
                        "authorization": authorization,
                        "finding_count": len(findings) - link_findings_before,
                    }
                )
            promise_entry["hard_predicates"].append(predicate_entry)
        promise_matrix.append(promise_entry)
    return {
        "ok": not findings,
        "source_promise_count": len(promise_rows),
        "finding_count": len(findings),
        "findings": findings,
        "source_promise_matrix": promise_matrix,
    }



def doctor_codes_from_payload(payload: dict[str, Any]) -> list[str]:
    return [
        item["code"]
        for severity in ("P0", "P1", "P2")
        for item in payload.get("severity_buckets", {}).get(severity, [])
    ]


def evidence_verify_failure_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {
        status: len(items)
        for status, items in (payload.get("buckets") or {}).items()
        if status != "ok" and items
    }


def record_full_closeout_override(
    conn: sqlite3.Connection,
    *,
    endpoint: sqlite3.Row,
    endpoint_name: str,
    doctor_payload: dict[str, Any],
    evidence_payload: dict[str, Any],
    reason: str | None,
) -> str:
    if not reason:
        raise SystemExit("Full mode closeout override requires --override-reason")
    doctor_codes = doctor_codes_from_payload(doctor_payload)
    evidence_failures = evidence_verify_failure_counts(evidence_payload)
    body = (
        f"Full mode closeout override used for endpoint {endpoint_name}. "
        f"Reason: {reason}. Doctor blockers: {', '.join(doctor_codes) or 'none'}. "
        f"Evidence verify failures: {json_dumps(evidence_failures) if evidence_failures else 'none'}."
    )
    warning_node_id = create_node(
        conn,
        "audit_finding",
        "full closeout override",
        body[:240],
        {
            "severity": "warning",
            "kind": "full_closeout_override",
            "endpoint": endpoint_name,
            "override_reason": reason,
            "doctor_ok": doctor_payload.get("ok"),
            "doctor_codes": doctor_codes,
            "evidence_verify_ok": evidence_payload.get("ok"),
            "evidence_failure_counts": evidence_failures,
            "body": body,
        },
    )
    register_semantic_item(
        conn,
        warning_node_id,
        "audit_finding",
        state="active",
        source_node=endpoint["node_id"],
        scope_node=endpoint["node_id"],
        event_type="created",
        reason="Full closeout override warning recorded.",
        props={"kind": "full_closeout_override", "endpoint": endpoint_name, "override_reason": reason},
    )
    create_edge(conn, warning_node_id, "DERIVED_FROM", endpoint["node_id"], reason="Full closeout override derived from endpoint closeout context.", created_by="agent")
    create_edge(conn, warning_node_id, "APPLIES_TO", endpoint["node_id"], reason="Full closeout override applies to endpoint.", created_by="agent")
    return warning_node_id


def full_closeout_gate(
    conn: sqlite3.Connection,
    repo: Path,
    *,
    endpoint_name: str,
    override: bool,
    override_reason: str | None,
) -> dict[str, Any]:
    endpoint = query_endpoint(conn, endpoint_name)
    doctor_payload = endpoint_doctor_payload(conn, repo, endpoint_name, strict_closeout=True)
    evidence_payload = evidence_verify_payload(repo, conn, endpoint_name)
    ok = bool(doctor_payload.get("ok")) and bool(evidence_payload.get("ok"))
    warning_node_id = None
    if not ok:
        if not override:
            doctor_codes = doctor_codes_from_payload(doctor_payload)
            evidence_failures = evidence_verify_failure_counts(evidence_payload)
            details = []
            if doctor_codes:
                details.append("strict endpoint doctor failed: " + ", ".join(doctor_codes))
            if evidence_failures:
                details.append("evidence verify failed: " + json_dumps(evidence_failures))
            raise SystemExit(
                "Full mode close/apply refused; "
                + "; ".join(details)
                + ". Run `endpoint doctor --strict-closeout` and `evidence verify --endpoint`, or pass --override-closeout with --override-reason to record an audit warning."
            )
        warning_node_id = record_full_closeout_override(
            conn,
            endpoint=endpoint,
            endpoint_name=endpoint_name,
            doctor_payload=doctor_payload,
            evidence_payload=evidence_payload,
            reason=override_reason,
        )
        conn.commit()
    return {
        "ok": ok,
        "doctor_ok": doctor_payload.get("ok"),
        "doctor_codes": doctor_codes_from_payload(doctor_payload),
        "evidence_verify_ok": evidence_payload.get("ok"),
        "evidence_failure_counts": evidence_verify_failure_counts(evidence_payload),
        "override": bool(override and not ok),
        "override_warning_node_id": warning_node_id,
    }



def load_context_payload(
    conn: sqlite3.Connection,
    *,
    task: str,
    endpoint: str | None,
    reason: str | None,
) -> dict[str, Any]:
    center = conn.execute(
        "SELECT * FROM center_bodies WHERE is_current = 1 ORDER BY version DESC LIMIT 1"
    ).fetchone()
    endpoint_rows = []
    endpoint_scope: dict[str, Any] | None = None
    active_endpoint_report: dict[str, Any] | None = None
    if endpoint:
        endpoint_rows = conn.execute(
            """
            SELECT e.name, e.description, b.*
            FROM endpoints e
            JOIN endpoint_bodies b ON b.id = e.current_body_id
            WHERE e.name = ? AND e.archived_at IS NULL
            """,
            (endpoint,),
        ).fetchall()
        endpoint_row = conn.execute("SELECT * FROM endpoints WHERE name = ? AND archived_at IS NULL", (endpoint,)).fetchone()
        if endpoint_row:
            endpoint_scope = endpoint_scope_facts(conn, endpoint_row)
            active_endpoint_report = endpoint_report_payload(conn, endpoint, active_only=True)
    else:
        endpoint_rows = conn.execute(
            """
            SELECT e.name, e.description, b.*
            FROM endpoints e
            JOIN endpoint_bodies b ON b.id = e.current_body_id
            WHERE e.archived_at IS NULL
            ORDER BY b.created_at DESC
            LIMIT 5
            """
        ).fetchall()
    keywords = keyword_tokens(" ".join(part for part in [task, endpoint or ""] if part))
    term_rows: list[sqlite3.Row] = []
    for keyword in keywords[:12]:
        term_rows.extend(
            conn.execute(
                """
                SELECT * FROM terms
                WHERE valid_to IS NULL
                  AND (lower(canonical_term) LIKE ? OR lower(definition) LIKE ?)
                LIMIT 5
                """,
                (f"%{keyword}%", f"%{keyword}%"),
            ).fetchall()
        )
    seen_terms: dict[str, sqlite3.Row] = {}
    for row in term_rows:
        seen_terms[str(row["node_id"])] = row
    if endpoint_scope and endpoint_scope["task_ids"]:
        task_placeholders = ",".join("?" for _ in endpoint_scope["task_ids"])
        open_tasks = conn.execute(
            f"""
            SELECT t.*, n.label
            FROM tasks t
            JOIN nodes n ON n.id = t.node_id
            WHERE t.closed_by_node_id IS NULL
              AND t.id IN ({task_placeholders})
              AND NOT EXISTS (
                SELECT 1 FROM edges de
                JOIN nodes dn ON dn.id = de.to_node_id
                WHERE de.from_node_id = t.node_id
                  AND de.type = 'DEFERRED_BY'
                  AND {active_node_clause("dn")}
              )
            ORDER BY t.is_mandatory DESC, n.created_at ASC, t.id ASC
            LIMIT 20
            """,
            endpoint_scope["task_ids"],
        ).fetchall()
        open_checks = conn.execute(
            f"""
            SELECT ac.*, n.label
            FROM acceptance_checks ac
            JOIN nodes n ON n.id = ac.node_id
            JOIN tasks t ON t.id = ac.task_id
            WHERE ac.closed_by_node_id IS NULL
              AND ac.task_id IN ({task_placeholders})
              AND NOT EXISTS (
                SELECT 1 FROM edges de
                JOIN nodes dn ON dn.id = de.to_node_id
                WHERE de.from_node_id = t.node_id
                  AND de.type = 'DEFERRED_BY'
                  AND {active_node_clause("dn")}
              )
            ORDER BY n.created_at ASC, ac.id ASC
            LIMIT 20
            """,
            endpoint_scope["task_ids"],
        ).fetchall()
    else:
        open_tasks = conn.execute(
            f"""
            SELECT t.*, n.label
            FROM tasks t
            JOIN nodes n ON n.id = t.node_id
            WHERE t.closed_by_node_id IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM edges de
                JOIN nodes dn ON dn.id = de.to_node_id
                WHERE de.from_node_id = t.node_id
                  AND de.type = 'DEFERRED_BY'
                  AND {active_node_clause("dn")}
              )
            ORDER BY t.is_mandatory DESC, n.created_at ASC, t.id ASC
            LIMIT 20
            """
        ).fetchall()
        open_checks = conn.execute(
            f"""
            SELECT ac.*, n.label
            FROM acceptance_checks ac
            JOIN nodes n ON n.id = ac.node_id
            JOIN tasks t ON t.id = ac.task_id
            WHERE ac.closed_by_node_id IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM edges de
                JOIN nodes dn ON dn.id = de.to_node_id
                WHERE de.from_node_id = t.node_id
                  AND de.type = 'DEFERRED_BY'
                  AND {active_node_clause("dn")}
              )
            ORDER BY n.created_at ASC, ac.id ASC
            LIMIT 20
            """
        ).fetchall()
    if endpoint_scope:
        semantic_context = query_nodes_applying_to(
            conn,
            node_types={"assumption", "unresolved_question", "scope_change", "defer_decision", "constraint", "decision", "audit_finding", "work_note"},
            target_node_ids=endpoint_scope["target_node_ids"],
            limit=30,
            active_lifecycle_only=True,
        )
    else:
        semantic_context = conn.execute(
            f"""
            SELECT id, type, label, summary, created_at, props
            FROM nodes
            WHERE type IN ('assumption', 'unresolved_question', 'scope_change', 'defer_decision', 'constraint', 'decision', 'audit_finding', 'work_note')
              AND {active_node_clause("nodes")}
                  AND NOT EXISTS (
                SELECT 1 FROM semantic_items si
                WHERE si.node_id = nodes.id
                  AND si.current_state IN ('resolved', 'deferred', 'product_backlog', 'backlog', 'invalidated', 'superseded')
              )
            ORDER BY created_at DESC, id DESC
            LIMIT 20
            """
        ).fetchall()
    code_objects = scoped_code_objects_for_context(conn, endpoint_scope)
    code_object_scope = "endpoint_change_set"
    if not code_objects:
        code_objects = global_code_objects_for_context(conn)
        code_object_scope = "global_fallback"
    ranked_context = rank_context(
        keywords=keywords,
        endpoint_name=endpoint,
        endpoint_rows=endpoint_rows,
        terms=list(seen_terms.values()),
        open_tasks=open_tasks,
        open_checks=open_checks,
        semantic_context=semantic_context,
        code_objects=code_objects,
        code_object_scope=code_object_scope,
    )
    if active_endpoint_report:
        ranked_context.insert(
            0,
            {
                "node_id": active_endpoint_report["direction"]["endpoint"]["node_id"],
                "kind": "endpoint_active_report",
                "label": endpoint,
                "summary": active_endpoint_report["next_valid_entry_point"]["recommendation"],
                "score": 999.0,
                "reasons": ["default_endpoint_entry", "active_only"],
                "mode": "active_only",
                "active_obligations": active_endpoint_report["active_obligations"],
                "next_valid_entry_point": active_endpoint_report["next_valid_entry_point"],
            },
        )
    loaded_node_ids = list(
        dict.fromkeys(
            [str(item["node_id"]) for item in ranked_context]
            + [row["node_id"] for row in [*open_tasks, *open_checks]]
            + [row["id"] for row in semantic_context]
        )
    )
    log_id = new_id("activation")
    conn.execute(
        """
        INSERT INTO activation_logs
          (id, task_text, loaded_center_body_id, loaded_endpoint_body_ids,
           loaded_term_node_ids, loaded_node_ids, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            log_id,
            task,
            center["id"] if center else None,
            json_dumps([row["id"] for row in endpoint_rows]),
            json_dumps(list(seen_terms.keys())),
            json_dumps(loaded_node_ids),
            reason or "Ranked center/endpoint/term/task/check/semantic/code activation.",
            now_iso(),
        ),
    )
    return {
        "activation_log_id": log_id,
        "center": row_to_dict(center),
        "endpoints": [row_to_dict(row) for row in endpoint_rows],
        "terms": [row_to_dict(row) for row in seen_terms.values()],
        "open_tasks": [row_to_dict(row) for row in open_tasks],
        "open_acceptance_checks": [row_to_dict(row) for row in open_checks],
        "semantic_context": [row_to_dict(row) for row in semantic_context],
        "active_endpoint_report": active_endpoint_report,
        "ranked_context": ranked_context,
    }


def scoped_code_objects_for_context(conn: sqlite3.Connection, endpoint_scope: dict[str, Any] | None) -> list[sqlite3.Row]:
    if not endpoint_scope or not endpoint_scope.get("target_node_ids"):
        return []
    target_node_ids = [str(node_id) for node_id in endpoint_scope["target_node_ids"]]
    placeholders = ",".join("?" for _ in target_node_ids)
    return conn.execute(
        f"""
        SELECT DISTINCT co.*, 'endpoint_change_set' AS context_scope, COALESCE(co.qualified_name, '') AS sort_qualified_name
        FROM code_objects co
        JOIN change_code_links ccl ON ccl.code_object_id = co.id
        JOIN change_sets cs ON cs.id = ccl.change_set_id
        JOIN edges e ON (
          e.from_node_id = cs.node_id
          AND e.type = 'IMPLEMENTS'
          AND e.to_node_id IN ({placeholders})
        ) OR (
          e.to_node_id = cs.node_id
          AND e.type = 'VALIDATED_BY'
          AND e.from_node_id IN ({placeholders})
        )
        WHERE co.archived_at IS NULL
        ORDER BY co.path ASC, sort_qualified_name ASC, co.id ASC
        LIMIT 200
        """,
        (*target_node_ids, *target_node_ids),
    ).fetchall()


def global_code_objects_for_context(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *, 'global_fallback' AS context_scope
        FROM code_objects
        WHERE archived_at IS NULL
        ORDER BY path ASC, COALESCE(qualified_name, '') ASC, id ASC
        LIMIT 200
        """
    ).fetchall()


def keyword_tokens(text: str) -> list[str]:
    raw = [part.lower() for part in re.findall(r"[\w.-]+", text) if len(part) > 1]
    expanded: list[str] = []
    for token in raw:
        expanded.append(token)
        expanded.extend(part for part in re.split(r"[._/-]+", token) if len(part) > 1)
    return list(dict.fromkeys(expanded))


def score_text(keywords: list[str], text: str) -> tuple[float, list[str]]:
    haystack = text.lower()
    score = 0.0
    reasons: list[str] = []
    for keyword in keywords:
        if not keyword:
            continue
        if re.search(rf"(?<![\w]){re.escape(keyword)}(?![\w])", haystack):
            score += 3.0
            reasons.append(f"keyword:{keyword}")
        elif keyword in haystack:
            score += 1.0
            reasons.append(f"substring:{keyword}")
    return score, list(dict.fromkeys(reasons))


def rank_context(
    *,
    keywords: list[str],
    endpoint_name: str | None,
    endpoint_rows: list[sqlite3.Row],
    terms: list[sqlite3.Row],
    open_tasks: list[sqlite3.Row],
    open_checks: list[sqlite3.Row],
    semantic_context: list[sqlite3.Row],
    code_objects: list[sqlite3.Row],
    code_object_scope: str,
) -> list[dict[str, Any]]:
    ranked: dict[str, dict[str, Any]] = {}

    def add(
        node_id: str,
        kind: str,
        label: str | None,
        summary: str | None,
        base: float,
        payload: dict[str, Any],
        *,
        require_match: bool = False,
    ) -> None:
        text = " ".join(str(part or "") for part in [label, summary, *payload.values()])
        score, reasons = score_text(keywords, text)
        if endpoint_name and endpoint_name.lower() in text.lower():
            score += 2.0
            reasons.append(f"endpoint:{endpoint_name}")
        if require_match and score <= 0:
            return
        total = base + score
        if total <= 0:
            return
        existing = ranked.get(node_id)
        item = {
            "node_id": node_id,
            "kind": kind,
            "label": label,
            "summary": summary,
            "score": round(total, 3),
            "reasons": list(dict.fromkeys(reasons or [kind])),
            **payload,
        }
        if not existing or item["score"] > existing["score"]:
            ranked[node_id] = item

    for row in endpoint_rows:
        add(
            str(row["node_id"]),
            "endpoint",
            str(row["name"]),
            str(row["body"]),
            2.0,
            {"endpoint_body_id": row["id"]},
        )
    for row in terms:
        add(
            str(row["node_id"]),
            "term",
            str(row["canonical_term"]),
            str(row["definition"]),
            4.0,
            {"term_id": row["id"]},
        )
    for row in open_tasks:
        add(
            str(row["node_id"]),
            "task",
            str(row["label"]),
            str(row["task_body"]),
            2.0 + (1.0 if row["is_mandatory"] else 0.0),
            {"task_id": row["id"], "is_mandatory": bool(row["is_mandatory"])},
        )
    for row in open_checks:
        add(
            str(row["node_id"]),
            "acceptance_check",
            str(row["label"]),
            str(row["check_body"]),
            2.0,
            {"acceptance_check_id": row["id"], "task_id": row["task_id"]},
        )
    for row in semantic_context:
        add(
            str(row["id"]),
            str(row["type"]),
            row["label"],
            row["summary"],
            1.0,
            {"props": row["props"]},
        )
    for row in code_objects:
        label = row["qualified_name"] or row["path"]
        code_base = 2.0 if row["type"] == "file" else 4.0
        add(
            str(row["node_id"]),
            f"code_{row['type']}",
            label,
            f"{row['path']} {row['symbol_name'] or ''}",
            code_base,
            {
                "code_object_id": row["id"],
                "path": row["path"],
                "qualified_name": row["qualified_name"],
                "symbol_name": row["symbol_name"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "context_scope": row["context_scope"] if "context_scope" in row.keys() else code_object_scope,
            },
            require_match=True,
        )
    return sorted(ranked.values(), key=lambda item: (-float(item["score"]), str(item["kind"]), str(item["label"])))[:30]


def capture_snapshot(conn: sqlite3.Connection, repo: Path, run_id: str, phase: str) -> str:
    worktree_patch = build_worktree_patch(repo)
    staged_patch = run_git(repo, ["diff", "--cached", "--binary"], allow_fail=True)
    state = build_snapshot_state(repo)
    patch_dir = repo / ".shujuan" / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    snapshot_id = new_id("snapshot")
    worktree_ref = patch_dir / f"{run_id}_{phase}_{snapshot_id}_worktree.patch"
    staged_ref = patch_dir / f"{run_id}_{phase}_{snapshot_id}_staged.patch"
    state_ref = snapshot_state_path(repo, run_id, phase, snapshot_id)
    worktree_ref.write_text(worktree_patch, encoding="utf-8")
    staged_ref.write_text(staged_patch, encoding="utf-8")
    state_ref.write_text(json_dumps(state), encoding="utf-8")
    conn.execute(
        """
        INSERT INTO run_snapshots
          (id, run_id, phase, head_commit, worktree_patch_hash, staged_patch_hash,
           patch_ref, captured_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            run_id,
            phase,
            current_head(repo),
            sha256_text(worktree_patch),
            sha256_text(staged_patch),
            relpath(worktree_ref, repo),
            now_iso(),
        ),
    )
    return snapshot_id


def active_run_path(repo: Path) -> Path:
    return repo / ".shujuan" / "active_run.json"


def resolve_alias(repo: Path, kind: str, identifier: str | None) -> str | None:
    if identifier is None:
        return None
    value = str(identifier)
    aliases = load_aliases(repo)
    if value.startswith("@alias."):
        alias_name = value.split(".", 1)[1]
        scoped = aliases.get(kind) or {}
        if alias_name not in scoped:
            raise SystemExit(f"{kind} alias not found: {alias_name}")
        return str(scoped[alias_name])
    if value.startswith("@user."):
        alias_name = value.split(".", 1)[1]
        scoped = aliases.get(kind) or {}
        if alias_name not in scoped:
            raise SystemExit(f"{kind} user alias not found: {alias_name}")
        return str(scoped[alias_name])
    return value


def resolve_current_endpoint(repo: Path, conn: sqlite3.Connection) -> str | None:
    work = repo / ".shujuan" / "current_work.json"
    if work.exists():
        try:
            current = json.loads(work.read_text(encoding="utf-8"))
            if current.get("endpoint"):
                return str(current["endpoint"])
        except json.JSONDecodeError:
            pass
    active = active_run_path(repo)
    if active.exists():
        try:
            run_id = json.loads(active.read_text(encoding="utf-8")).get("run_id")
        except json.JSONDecodeError:
            run_id = None
        if run_id:
            row = conn.execute("SELECT metadata FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            metadata = props_dict(row["metadata"] if row else None)
            if metadata.get("endpoint"):
                return str(metadata["endpoint"])
    return None


def resolve_endpoint_identifier(conn: sqlite3.Connection, repo: Path, identifier: str) -> str:
    if identifier is None:
        raise SystemExit("--endpoint is required")
    value = resolve_alias(repo, "endpoint", identifier) or identifier
    if value == "@current.endpoint":
        current = resolve_current_endpoint(repo, conn)
        if not current:
            raise SystemExit("@current.endpoint is not set; start work with --endpoint or create an endpoint alias")
        return current
    if value == "@last.endpoint":
        row = conn.execute("SELECT name FROM endpoints WHERE archived_at IS NULL ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        if not row:
            raise SystemExit("@last.endpoint is not available; no endpoints exist")
        return str(row["name"])
    return value


def current_work_handle(repo: Path) -> dict[str, Any]:
    work = repo / ".shujuan" / "current_work.json"
    if not work.exists():
        return {}
    try:
        value = json.loads(work.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def clear_current_work_for_run(repo: Path, run_id: str) -> bool:
    work = repo / ".shujuan" / "current_work.json"
    handle = current_work_handle(repo)
    if handle.get("run_id") != run_id:
        return False
    work.unlink(missing_ok=True)
    return True


def resolve_current_task_id(repo: Path, conn: sqlite3.Connection) -> str | None:
    handle = current_work_handle(repo)
    if handle.get("task"):
        return str(handle["task"])
    if handle.get("task_node"):
        task_ref = resolve_task_node_identifier(conn, str(handle["task_node"]))
        if task_ref:
            return str(task_ref["task_id"])
    active = active_run_path(repo)
    if active.exists():
        try:
            run_id = json.loads(active.read_text(encoding="utf-8")).get("run_id")
        except json.JSONDecodeError:
            run_id = None
        if run_id:
            row = conn.execute("SELECT metadata FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            metadata = props_dict(row["metadata"] if row else None)
            preflight = metadata.get("preflight") if isinstance(metadata.get("preflight"), dict) else {}
            if preflight.get("task_id"):
                return str(preflight["task_id"])
            if preflight.get("task_node_id"):
                task_ref = resolve_task_node_identifier(conn, str(preflight["task_node_id"]))
                if task_ref:
                    return str(task_ref["task_id"])
    return None


def resolve_task_id_identifier(conn: sqlite3.Connection, repo: Path, identifier: str) -> str:
    value = resolve_alias(repo, "task", identifier) or identifier
    if value == "@current.task":
        current = resolve_current_task_id(repo, conn)
        if not current:
            raise SystemExit("@current.task is not set; start work with --task or pass an explicit task id")
        return current
    if value == "@last.task":
        row = conn.execute(
            """
            SELECT t.id
            FROM tasks t
            JOIN nodes n ON n.id = t.node_id
            ORDER BY n.created_at DESC, t.id DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            raise SystemExit("@last.task is not available; no tasks exist")
        return str(row["id"])
    task_ref = resolve_task_node_identifier(conn, value)
    if not task_ref:
        raise SystemExit(f"task id or task node not found: {value}")
    return str(task_ref["task_id"])


def resolve_task_node_input_identifier(conn: sqlite3.Connection, repo: Path, identifier: str) -> str:
    value = resolve_alias(repo, "task", identifier) or identifier
    if value == "@current.task":
        return resolve_task_id_identifier(conn, repo, value)
    if value == "@last.task":
        return resolve_task_id_identifier(conn, repo, value)
    if not resolve_task_node_identifier(conn, value):
        raise SystemExit(f"task id or task node not found: {value}")
    return value


def resolve_current_check_id(repo: Path, conn: sqlite3.Connection) -> str | None:
    handle = current_work_handle(repo)
    if handle.get("check"):
        return str(handle["check"])
    task_id = resolve_current_task_id(repo, conn)
    if task_id:
        rows = conn.execute(
            """
            SELECT ac.id
            FROM acceptance_checks ac
            JOIN nodes n ON n.id = ac.node_id
            WHERE ac.task_id = ? AND ac.closed_by_node_id IS NULL
            ORDER BY n.created_at ASC, ac.id ASC
            """,
            (task_id,),
        ).fetchall()
        if len(rows) == 1:
            return str(rows[0]["id"])
        if len(rows) > 1:
            ids = ", ".join(str(row["id"]) for row in rows)
            raise SystemExit(f"@current.check is ambiguous for current task {task_id}; open checks: {ids}")
    endpoint_name = resolve_current_endpoint(repo, conn)
    if endpoint_name:
        status = endpoint_status_payload(conn, endpoint_name, include_chain=False)
        open_checks = status.get("open_checks") or []
        if len(open_checks) == 1:
            return str(open_checks[0]["id"])
        if len(open_checks) > 1:
            ids = ", ".join(str(item["id"]) for item in open_checks)
            raise SystemExit(f"@current.check is ambiguous for current endpoint {endpoint_name}; open checks: {ids}")
    return None


def resolve_check_identifier(conn: sqlite3.Connection, repo: Path, identifier: str) -> str:
    value = resolve_alias(repo, "check", identifier) or identifier
    if value == "@current.check":
        current = resolve_current_check_id(repo, conn)
        if not current:
            raise SystemExit("@current.check is not set; pass an explicit check id or create a check alias")
        return current
    if value == "@last.check":
        row = conn.execute(
            """
            SELECT ac.id
            FROM acceptance_checks ac
            JOIN nodes n ON n.id = ac.node_id
            ORDER BY n.created_at DESC, ac.id DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            raise SystemExit("@last.check is not available; no acceptance checks exist")
        return str(row["id"])
    check = conn.execute("SELECT id FROM acceptance_checks WHERE id = ?", (value,)).fetchone()
    if check:
        return str(check["id"])
    check_node = conn.execute("SELECT id FROM acceptance_checks WHERE node_id = ?", (value,)).fetchone()
    if check_node:
        return str(check_node["id"])
    raise SystemExit(f"acceptance check not found: {value}")


def resolve_run_identifier(conn: sqlite3.Connection, repo: Path, identifier: str | None) -> str | None:
    if not identifier:
        return None
    value = resolve_alias(repo, "node", identifier) or identifier
    if value == "@current.run":
        active = active_run_path(repo)
        if not active.exists():
            raise SystemExit("@current.run is not set; pass --run or start an execution run")
        try:
            return str(json.loads(active.read_text(encoding="utf-8"))["run_id"])
        except (KeyError, json.JSONDecodeError) as exc:
            raise SystemExit("@current.run is unreadable; pass an explicit run id") from exc
    if value == "@last.run":
        row = conn.execute("SELECT id FROM agent_runs ORDER BY started_at DESC, id DESC LIMIT 1").fetchone()
        if not row:
            raise SystemExit("@last.run is not available; no execution runs exist")
        return str(row["id"])
    if not conn.execute("SELECT 1 FROM agent_runs WHERE id = ?", (value,)).fetchone():
        raise SystemExit(f"run not found: {value}")
    return value


def resolve_discussion_identifier(conn: sqlite3.Connection, repo: Path, identifier: str, *, endpoint_name: str | None = None) -> str:
    value = resolve_alias(repo, "discussion", identifier) or identifier
    if value == "@last.discussion":
        row = resolve_discussion_segment(conn, value, endpoint_name=endpoint_name)
        return str(row["id"])
    return value


def resolve_task_node_identifier(conn: sqlite3.Connection, identifier: str) -> dict[str, Any] | None:
    task = conn.execute("SELECT id, node_id FROM tasks WHERE id = ?", (identifier,)).fetchone()
    if task:
        return {"task_id": task["id"], "node_id": task["node_id"], "input": identifier, "input_kind": "task_id"}
    task_node = conn.execute(
        """
        SELECT t.id, t.node_id
        FROM tasks t
        JOIN nodes n ON n.id = t.node_id
        WHERE n.id = ?
        """,
        (identifier,),
    ).fetchone()
    if task_node:
        return {"task_id": task_node["id"], "node_id": task_node["node_id"], "input": identifier, "input_kind": "task_node_id"}
    return None


def exec_start_preflight(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    endpoint_row = None
    task_ref = None
    mode = normalize_mode(getattr(args, "mode", None) or "standard")
    if args.endpoint:
        endpoint_row = conn.execute("SELECT id, node_id, root_node_id FROM endpoints WHERE name = ?", (args.endpoint,)).fetchone()
        if not endpoint_row:
            issues.append({"code": "endpoint_not_found", "message": f"endpoint not found: {args.endpoint}"})
        elif not endpoint_row["root_node_id"]:
            issues.append({"code": "endpoint_rootless", "message": f"endpoint has no root_node_id: {args.endpoint}"})
    else:
        issues.append({"code": "endpoint_missing", "message": "exec start should name the active endpoint"})
    if args.task_node:
        task_ref = resolve_task_node_identifier(conn, args.task_node)
        if not task_ref:
            issues.append({"code": "task_not_found", "message": f"task id or task node not found: {args.task_node}"})
    else:
        issues.append({"code": "task_node_missing", "message": "exec start should point at a task id or task node"})
    recent_prompt = conn.execute(
        """
        SELECT m.id, m.node_id, m.created_at
        FROM messages m
        WHERE m.actor = 'user'
        ORDER BY m.created_at DESC, m.turn_index DESC
        LIMIT 1
        """
    ).fetchone()
    if not recent_prompt:
        issues.append({"code": "recent_prompt_missing", "message": "no recorded user prompt found; run workflow begin first"})
    attention_packet_present = endpoint_has_attention_packet(
        conn,
        str(endpoint_row["node_id"]) if endpoint_row else None,
        task_ref["node_id"] if task_ref else None,
    )
    if mode == "full" and not attention_packet_present:
        issues.append(
            {
                "code": "attention_packet_missing",
                "gate": "G2",
                "message": "Full/P0/P1 work start requires a current attention packet with hard predicates, forbidden substitutes, and proof required.",
            }
        )
    elif mode == "standard" and not attention_packet_present:
        warnings.append(
            {
                "code": "attention_packet_missing",
                "gate": "G2",
                "message": "Standard work start should carry an attention packet; continuing as warning only.",
            }
        )
    intent = getattr(args, "intent", None) or getattr(args, "summary", None) or ""
    mode_warnings = mode_gate_warnings(mode, intent)
    if any(warning["code"] == "mode_friction_high_risk_light" for warning in mode_warnings):
        issues.extend({**warning, "message": warning["message"]} for warning in mode_warnings if warning["code"] == "mode_friction_high_risk_light")
    else:
        warnings.extend(mode_warnings)
    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "mode": mode,
        "attention_packet_present": attention_packet_present,
        "endpoint_node_id": endpoint_row["node_id"] if endpoint_row else None,
        "task_id": task_ref["task_id"] if task_ref else None,
        "task_node_id": task_ref["node_id"] if task_ref else None,
        "task_input_kind": task_ref["input_kind"] if task_ref else None,
        "recent_prompt_node_id": recent_prompt["node_id"] if recent_prompt else None,
    }


def record_preflight_assumption(conn: sqlite3.Connection, *, preflight: dict[str, Any], reason: str | None, run_node_id: str) -> str:
    body = "exec start preflight was explicitly allowed with issues: " + "; ".join(item["code"] for item in preflight["issues"])
    node_id = create_node(
        conn,
        "assumption",
        "exec start preflight override",
        body[:240],
        {"body": body, "reason": reason, "issues": preflight["issues"]},
    )
    create_edge(conn, node_id, "APPLIES_TO", run_node_id, reason="Preflight override applies to agent run.", created_by="agent")
    if preflight.get("endpoint_node_id"):
        create_edge(conn, node_id, "APPLIES_TO", preflight["endpoint_node_id"], reason="Preflight override applies to endpoint.", created_by="agent")
    if preflight.get("recent_prompt_node_id"):
        create_edge(conn, node_id, "DERIVED_FROM", preflight["recent_prompt_node_id"], reason="Preflight override derived from recent prompt.", created_by="agent")
    return node_id



def link_change_evidence(
    conn: sqlite3.Connection,
    repo: Path,
    change_node_id: str,
    *,
    task_ids: list[str],
    task_node_ids: list[str],
    check_ids: list[str],
    close_checks: bool,
    close_tasks: bool,
    override_evidence_type: bool = False,
    override_reason: str | None = None,
) -> dict[str, Any]:
    require_evidence_node(conn, change_node_id)
    linked_tasks = []
    linked_checks = []
    warnings = []
    task_readiness_items = []
    for task_id in task_ids:
        task = conn.execute("SELECT id, node_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise SystemExit(f"task not found: {task_id}")
        create_edge(conn, change_node_id, "IMPLEMENTS", task["node_id"], reason="Captured change set was linked to task.")
        linked_tasks.append(row_to_dict(task))
        if close_tasks:
            task_readiness_items.append(close_task_if_ready(conn, task_id, change_node_id))
    for task_node_id in task_node_ids:
        task_ref = resolve_task_node_identifier(conn, task_node_id)
        if not task_ref:
            raise SystemExit(f"task id or task node not found: {task_node_id}")
        create_edge(conn, change_node_id, "IMPLEMENTS", task_ref["node_id"], reason="Captured change set was linked to task node.")
        linked_tasks.append({"id": task_ref["task_id"], "node_id": task_ref["node_id"], "input": task_node_id, "input_kind": task_ref["input_kind"]})
    for check_id in check_ids:
        check = conn.execute("SELECT id, node_id, task_id, expected_evidence_type, closed_by_node_id FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone()
        if not check:
            raise SystemExit(f"acceptance check not found: {check_id}")
        if close_checks:
            close_result = close_check_with_evidence(
                conn,
                repo,
                check=check,
                evidence_node_id=change_node_id,
                override=override_evidence_type,
                override_reason=override_reason,
            )
            warnings.extend(close_result["warnings"])
        create_edge(conn, check["node_id"], "VALIDATED_BY", change_node_id, reason="Captured change set was linked as acceptance evidence.")
        linked_checks.append(row_to_dict(check))
        if close_tasks:
            task_readiness_items.append(close_task_if_ready(conn, check["task_id"], change_node_id, last_check_id=str(check["id"])))
        elif close_checks:
            task_readiness_items.append(task_readiness_hint(conn, check["task_id"], evidence_node_id=change_node_id, last_check_id=str(check["id"])))
    return {
        "tasks": linked_tasks,
        "acceptance_checks": linked_checks,
        "closed_checks": close_checks and bool(linked_checks),
        "closed_tasks": close_tasks and bool(linked_tasks or linked_checks),
        "warnings": warnings,
        "task_readiness": aggregate_task_readiness(task_readiness_items),
    }


def link_source_nodes(
    conn: sqlite3.Connection,
    node_id: str,
    source_node_ids: list[str],
    *,
    reason: str,
) -> list[str]:
    edge_ids = []
    for source_node_id in source_node_ids:
        require_node(conn, source_node_id, "source node")
        edge_ids.append(create_edge(conn, node_id, "DERIVED_FROM", source_node_id, reason=reason, created_by="agent"))
    return edge_ids


def require_node(conn: sqlite3.Connection, node_id: str, label: str = "node") -> sqlite3.Row:
    node = conn.execute("SELECT id, type, label FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if not node:
        raise SystemExit(f"{label} not found: {node_id}")
    return node


def link_validated_nodes(
    conn: sqlite3.Connection,
    evidence_node_id: str,
    target_node_ids: list[str],
    *,
    reason: str,
) -> list[str]:
    require_evidence_node(conn, evidence_node_id)
    edge_ids = []
    for target_node_id in target_node_ids:
        require_node(conn, target_node_id, "validated node")
        edge_ids.append(create_edge(conn, target_node_id, "VALIDATED_BY", evidence_node_id, reason=reason, created_by="agent"))
    return edge_ids


def link_evidence_to_checks(
    conn: sqlite3.Connection,
    repo: Path,
    evidence_node_id: str,
    check_ids: list[str],
    *,
    close_checks: bool,
    close_tasks: bool,
    reason: str,
    override_evidence_type: bool = False,
    override_reason: str | None = None,
    override_predicate_coverage: bool = False,
    elevated_predicate_coverage_override: bool = False,
) -> dict[str, Any]:
    require_evidence_node(conn, evidence_node_id)
    linked_checks = []
    warnings = []
    task_readiness_items = []
    predicate_coverage_prevalidated = False
    target_check_ids = existing_check_ids_closed_by_evidence(conn, evidence_node_id) + [str(check_id) for check_id in check_ids]
    if close_checks and len(set(target_check_ids)) > 1:
        warning_node_id = validate_test_result_predicate_coverage(
            conn,
            evidence_node_id=evidence_node_id,
            check_ids=target_check_ids,
            override=override_predicate_coverage,
            override_reason=override_reason,
            elevated_override=elevated_predicate_coverage_override,
        )
        if warning_node_id:
            warnings.append(warning_node_id)
        predicate_coverage_prevalidated = True
    for check_id in check_ids:
        check = conn.execute("SELECT id, node_id, task_id, expected_evidence_type, closed_by_node_id FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone()
        if not check:
            raise SystemExit(f"acceptance check not found: {check_id}")
        if close_checks:
            close_result = close_check_with_evidence(
                conn,
                repo,
                check=check,
                evidence_node_id=evidence_node_id,
                override=override_evidence_type,
                override_reason=override_reason,
                override_predicate_coverage=override_predicate_coverage,
                elevated_predicate_coverage_override=elevated_predicate_coverage_override,
                predicate_coverage_prevalidated=predicate_coverage_prevalidated,
            )
            warnings.extend(close_result["warnings"])
        create_edge(conn, check["node_id"], "VALIDATED_BY", evidence_node_id, reason=reason, created_by="agent")
        linked_checks.append(row_to_dict(check))
        if close_tasks:
            task_readiness_items.append(close_task_if_ready(conn, check["task_id"], evidence_node_id, last_check_id=str(check["id"])))
        elif close_checks:
            task_readiness_items.append(task_readiness_hint(conn, check["task_id"], evidence_node_id=evidence_node_id, last_check_id=str(check["id"])))
    return {
        "acceptance_checks": linked_checks,
        "closed_checks": close_checks and bool(linked_checks),
        "closed_tasks": close_tasks and bool(linked_checks),
        "warnings": warnings,
        "task_readiness": aggregate_task_readiness(task_readiness_items),
    }


EVIDENCE_NODE_TYPES = evidence_policy.EVIDENCE_NODE_TYPES

EXPECTED_EVIDENCE_TYPE_MAP = evidence_policy.EXPECTED_EVIDENCE_TYPE_MAP


def require_evidence_node(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row:
    node = conn.execute("SELECT id, type, label, summary, props FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if not node:
        raise SystemExit(f"evidence node not found: {node_id}")
    if node["type"] not in EVIDENCE_NODE_TYPES:
        allowed = ", ".join(sorted(EVIDENCE_NODE_TYPES))
        raise SystemExit(f"node {node_id} has type {node['type']}; closing acceptance checks requires evidence node type: {allowed}")
    return node


def expected_evidence_allowed(expected: str | None) -> set[str]:
    return evidence_policy.expected_evidence_allowed(expected)


def record_evidence_type_override(
    conn: sqlite3.Connection,
    *,
    check: sqlite3.Row,
    evidence_node: sqlite3.Row,
    reason: str | None,
) -> str:
    body = (
        f"Acceptance check {check['id']} expected {check['expected_evidence_type']!r} "
        f"but was closed with {evidence_node['type']!r}. "
        f"Override reason: {reason or 'No reason provided.'}"
    )
    warning_node_id = create_node(
        conn,
        "audit_finding",
        "evidence type override",
        body[:240],
        {
            "severity": "warning",
            "kind": "evidence_type_override",
            "check_id": check["id"],
            "expected_evidence_type": check["expected_evidence_type"],
            "actual_evidence_type": evidence_node["type"],
            "evidence_node_id": evidence_node["id"],
            "override_reason": reason,
            "body": body,
        },
    )
    create_edge(conn, warning_node_id, "DERIVED_FROM", evidence_node["id"], reason="Evidence type override derived from closing evidence.", created_by="agent")
    create_edge(conn, warning_node_id, "APPLIES_TO", check["node_id"], reason="Evidence type override applies to acceptance check.", created_by="agent")
    return warning_node_id


def _latest_semantic_reason(conn: sqlite3.Connection, node_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT reason
        FROM semantic_lifecycle_events
        WHERE node_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (node_id,),
    ).fetchone()
    return str(row["reason"]) if row and row["reason"] else None


def _override_warning_state(conn: sqlite3.Connection, node_id: str) -> str | None:
    row = conn.execute("SELECT current_state FROM semantic_items WHERE node_id = ?", (node_id,)).fetchone()
    return str(row["current_state"]) if row and row["current_state"] else None


def _override_reason(props: dict[str, Any], lifecycle_reason: str | None) -> str | None:
    return (
        props.get("override_reason")
        or props.get("reason")
        or lifecycle_reason
        or props.get("body")
    )


def _matching_override_warnings(
    conn: sqlite3.Connection,
    *,
    kind: str,
    evidence_node_id: str,
    check_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, props
        FROM nodes
        WHERE type = 'audit_finding'
          AND valid_to IS NULL
        """
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        props = props_dict(row)
        if props.get("kind") != kind or props.get("evidence_node_id") != evidence_node_id:
            continue
        if kind == "evidence_type_override" and props.get("check_id") != check_id:
            continue
        if kind == "predicate_coverage_override" and check_id not in [str(item) for item in props.get("check_ids", [])]:
            continue
        node_id = str(row["id"])
        lifecycle_reason = _latest_semantic_reason(conn, node_id)
        candidates.append(
            evidence_policy.effective_override_interpretation(
                warning_node_id=node_id,
                kind=kind,
                current_state=_override_warning_state(conn, node_id),
                override_reason=_override_reason(props, lifecycle_reason),
            )
        )
    return candidates


def effective_evidence_type_override(conn: sqlite3.Connection, *, check_id: str, evidence_node_id: str) -> dict[str, Any] | None:
    candidates = _matching_override_warnings(
        conn,
        kind="evidence_type_override",
        evidence_node_id=evidence_node_id,
        check_id=check_id,
    )
    for candidate in candidates:
        if candidate.get("effective"):
            return candidate
    return candidates[0] if candidates else None


def has_evidence_type_override(conn: sqlite3.Connection, *, check_id: str, evidence_node_id: str) -> bool:
    override = effective_evidence_type_override(conn, check_id=check_id, evidence_node_id=evidence_node_id)
    return bool(override and override.get("effective"))


def effective_predicate_coverage_override(conn: sqlite3.Connection, *, check_id: str, evidence_node_id: str) -> dict[str, Any] | None:
    candidates = _matching_override_warnings(
        conn,
        kind="predicate_coverage_override",
        evidence_node_id=evidence_node_id,
        check_id=check_id,
    )
    for candidate in candidates:
        if candidate.get("effective"):
            return candidate
    return candidates[0] if candidates else None


def validate_check_evidence_type(
    conn: sqlite3.Connection,
    *,
    check: sqlite3.Row,
    evidence_node_id: str,
    override: bool,
    override_reason: str | None,
) -> str | None:
    evidence_node = require_evidence_node(conn, evidence_node_id)
    allowed = expected_evidence_allowed(check["expected_evidence_type"])
    if evidence_node["type"] in allowed:
        return None
    expected = check["expected_evidence_type"] or ", ".join(sorted(EVIDENCE_NODE_TYPES))
    message = (
        f"acceptance check {check['id']} expects evidence type {expected}; "
        f"node {evidence_node_id} has type {evidence_node['type']}"
    )
    if not override:
        raise SystemExit(message + ". Pass --override-evidence-type with --override-reason to record an explicit warning.")
    return record_evidence_type_override(conn, check=check, evidence_node=evidence_node, reason=override_reason)


PREDICATE_COVERAGE_REQUIRED_FIELDS = evidence_policy.PREDICATE_COVERAGE_REQUIRED_FIELDS
PREDICATE_COVERAGE_PASS_RESULTS = evidence_policy.PREDICATE_COVERAGE_PASS_RESULTS


def normalize_predicate_coverage_matrix_rows(raw_rows: Any, *, source: str) -> list[dict[str, Any]]:
    return evidence_policy.normalize_predicate_coverage_matrix_rows(raw_rows, source=source)


def predicate_coverage_row_passed(row: dict[str, Any]) -> bool:
    return evidence_policy.predicate_coverage_row_passed(row)


def predicate_coverage_matrix_metadata(rows: list[dict[str, Any]]) -> dict[str, Any]:
    covered_check_ids = sorted({row["check_id"] for row in rows if predicate_coverage_row_passed(row)})
    not_covered_check_ids = sorted({row["check_id"] for row in rows if not predicate_coverage_row_passed(row)})
    covered_by_check: dict[str, list[str]] = {}
    not_covered_by_check: dict[str, list[str]] = {}
    rows_by_check: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        check_id = row["check_id"]
        row_summary = {
            "predicate_id": row["predicate_id"],
            "assertion": row["assertion"],
            "result": row["result"],
            "not_covered": row["not_covered"],
            "reason": row["reason"],
        }
        rows_by_check.setdefault(check_id, []).append(row_summary)
        if predicate_coverage_row_passed(row):
            covered_by_check.setdefault(check_id, []).append(row["predicate_id"])
        else:
            not_covered_by_check.setdefault(check_id, []).append(row["predicate_id"])
    for grouped in (covered_by_check, not_covered_by_check):
        for check_id, predicate_ids in grouped.items():
            grouped[check_id] = sorted(set(predicate_ids))
    return {
        "predicate_coverage_matrix": rows,
        "predicate_coverage_matrix_row_count": len(rows),
        "predicate_coverage_matrix_covered_check_ids": covered_check_ids,
        "predicate_coverage_matrix_not_covered_check_ids": not_covered_check_ids,
        "predicate_coverage_matrix_covered_hard_predicate_ids_by_check": dict(sorted(covered_by_check.items())),
        "predicate_coverage_matrix_not_covered_hard_predicate_ids_by_check": dict(sorted(not_covered_by_check.items())),
        "predicate_coverage_matrix_rows_by_check": dict(sorted(rows_by_check.items())),
    }


def load_predicate_coverage_matrix(repo: Path, matrix_arg: str | None) -> dict[str, Any] | None:
    if not matrix_arg:
        return None
    path = Path(matrix_arg)
    if not path.is_absolute():
        path = repo / path
    if not path.exists() or not path.is_file():
        raise SystemExit(f"predicate coverage matrix file not found: {path}")
    data = path.read_bytes()
    try:
        payload = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SystemExit(f"predicate coverage matrix must be UTF-8 JSON: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"predicate coverage matrix is not valid JSON: {path}: {exc}") from exc
    if isinstance(payload, dict):
        if "predicate_coverage_matrix" not in payload:
            raise SystemExit("predicate coverage matrix JSON object must contain predicate_coverage_matrix")
        raw_rows = payload["predicate_coverage_matrix"]
    else:
        raw_rows = payload
    rows = normalize_predicate_coverage_matrix_rows(raw_rows, source=relpath(path, repo))
    metadata = predicate_coverage_matrix_metadata(rows)
    metadata.update(
        {
            "predicate_coverage_matrix_ref": relpath(path, repo),
            "predicate_coverage_matrix_sha256": sha256_bytes(data),
        }
    )
    return metadata


def normalized_coverage_result(row: dict[str, Any]) -> str:
    return evidence_policy.normalized_coverage_result(row)


def persist_predicate_coverage_rows(
    conn: sqlite3.Connection,
    *,
    evidence_node_id: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if not table_exists(conn, "evidence_predicate_coverage"):
        return {"inserted": [], "skipped": [{"reason": "evidence_predicate_coverage table is missing"}], "inserted_count": 0, "skipped_count": 1}
    if not table_exists(conn, "hard_predicates"):
        return {"inserted": [], "skipped": [{"reason": "hard_predicates table is missing"}], "inserted_count": 0, "skipped_count": 1}
    inserted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        check_id = row["check_id"]
        predicate_id = row["predicate_id"]
        if not conn.execute("SELECT 1 FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone():
            skipped.append({"check_id": check_id, "predicate_id": predicate_id, "reason": "check_not_found"})
            continue
        if not conn.execute("SELECT 1 FROM hard_predicates WHERE id = ?", (predicate_id,)).fetchone():
            skipped.append({"check_id": check_id, "predicate_id": predicate_id, "reason": "predicate_not_found"})
            continue
        coverage_id = new_id("predicate_coverage")
        result = normalized_coverage_result(row)
        conn.execute(
            """
            INSERT INTO evidence_predicate_coverage
              (id, evidence_node_id, check_id, predicate_id, assertion, result, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                coverage_id,
                evidence_node_id,
                check_id,
                predicate_id,
                row["assertion"],
                result,
                now_iso(),
                json_dumps(
                    {
                        "reason": row.get("reason") or "",
                        "not_covered": row.get("not_covered") is True,
                        "original_result": row.get("result"),
                    }
                ),
            ),
        )
        inserted.append({"id": coverage_id, "check_id": check_id, "predicate_id": predicate_id, "result": result})
    return {"inserted": inserted, "skipped": skipped, "inserted_count": len(inserted), "skipped_count": len(skipped)}


def existing_check_ids_closed_by_evidence(conn: sqlite3.Connection, evidence_node_id: str) -> list[str]:
    return [
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM acceptance_checks WHERE closed_by_node_id = ? ORDER BY id",
            (evidence_node_id,),
        ).fetchall()
    ]


def open_acceptance_checks_for_task(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT ac.id, ac.node_id, ac.task_id, ac.check_body, ac.expected_evidence_type
            FROM acceptance_checks ac
            JOIN nodes n ON n.id = ac.node_id
            WHERE ac.task_id = ? AND ac.closed_by_node_id IS NULL
            ORDER BY n.created_at ASC, ac.id ASC
            """,
            (task_id,),
        ).fetchall()
    ]


def evidence_closure_command_hint(check: dict[str, Any]) -> dict[str, Any]:
    check_id = str(check["id"])
    expected_type = str(check.get("expected_evidence_type") or "evidence")
    if expected_type == "user_confirmation":
        command = f'python -m shujuan evidence user-confirmation --body "<confirmation>" --check {check_id} --close-check'
    elif expected_type == "artifact":
        command = f'python -m shujuan evidence artifact --path <artifact-path> --check {check_id} --close-check'
    elif expected_type == "test_result":
        command = f"python -m shujuan evidence test-result --check {check_id} --close-check -- <test command>"
    elif expected_type == "change_set":
        command = f"python -m shujuan exec stop --check {check_id} --close-check"
    else:
        command = f"python -m shujuan evidence <type> --check {check_id} --close-check"
    return {
        "kind": "close_remaining_acceptance_check",
        "check_id": check_id,
        "expected_evidence_type": expected_type,
        "command": command,
    }


def task_readiness_hint(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    evidence_node_id: str | None = None,
    last_check_id: str | None = None,
    task_close_attempted: bool = False,
    task_closed_now: bool = False,
    task_close_error: str | None = None,
) -> dict[str, Any]:
    task = conn.execute("SELECT id, node_id, closed_by_node_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        raise SystemExit(f"task not found: {task_id}")
    open_checks = open_acceptance_checks_for_task(conn, task_id)
    remaining_ids = [str(check["id"]) for check in open_checks]
    task_ready_to_close = not remaining_ids
    next_hints = []
    if remaining_ids:
        next_hints = [evidence_closure_command_hint(check) for check in open_checks]
    elif task["closed_by_node_id"]:
        next_hints = [
            {
                "kind": "task_already_closed",
                "task_id": task_id,
                "command": None,
                "note": "No task close command is needed; the task already has closure evidence.",
            }
        ]
    elif evidence_node_id and last_check_id:
        next_hints = [
            {
                "kind": "close_task",
                "task_id": task_id,
                "check_id": last_check_id,
                "evidence_node_id": evidence_node_id,
                "command": f"python -m shujuan acceptance close --check {last_check_id} --evidence-node {evidence_node_id} --close-task",
            }
        ]
    else:
        next_hints = [
            {
                "kind": "close_task",
                "task_id": task_id,
                "command": f"python -m shujuan acceptance close --check <closed-check-id> --evidence-node <evidence-node-id> --close-task",
            }
        ]
    return {
        "task_id": task_id,
        "task_node_id": task["node_id"],
        "task_closed": bool(task["closed_by_node_id"]),
        "task_closed_by_node_id": task["closed_by_node_id"],
        "task_closed_now": bool(task_closed_now),
        "task_close_attempted": bool(task_close_attempted),
        "task_close_error": task_close_error,
        "task_ready_to_close": task_ready_to_close,
        "remaining_open_acceptance_checks": open_checks,
        "remaining_open_checks": open_checks,
        "remaining_open_check_ids": remaining_ids,
        "next_command_hints": next_hints,
        "next_commands": [hint["command"] for hint in next_hints if hint.get("command")],
    }


def aggregate_task_readiness(items: list[dict[str, Any]]) -> dict[str, Any]:
    latest_by_task: dict[str, dict[str, Any]] = {}
    for item in items:
        latest_by_task[str(item["task_id"])] = item
    tasks = list(latest_by_task.values())
    remaining_checks = [
        check
        for item in tasks
        for check in item.get("remaining_open_acceptance_checks", [])
    ]
    next_hints = [
        hint
        for item in tasks
        for hint in item.get("next_command_hints", [])
    ]
    return {
        "task_ready_to_close": bool(tasks) and all(bool(item.get("task_ready_to_close")) for item in tasks),
        "tasks": tasks,
        "remaining_open_acceptance_checks": remaining_checks,
        "remaining_open_checks": remaining_checks,
        "remaining_open_check_ids": [str(check["id"]) for check in remaining_checks],
        "next_command_hints": next_hints,
        "next_commands": [hint["command"] for hint in next_hints if hint.get("command")],
    }


def rows_for_checks(conn: sqlite3.Connection, check_ids: list[str]) -> list[sqlite3.Row]:
    rows = []
    for check_id in check_ids:
        row = conn.execute("SELECT * FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone()
        if not row:
            raise SystemExit(f"acceptance check not found: {check_id}")
        rows.append(row)
    return rows


def linked_hard_predicate_ids_for_checks(conn: sqlite3.Connection, check_ids: list[str]) -> dict[str, list[str]]:
    unique_check_ids = list(dict.fromkeys(str(check_id) for check_id in check_ids))
    if not unique_check_ids or not table_exists(conn, "task_predicate_links") or not table_exists(conn, "hard_predicates"):
        return {}
    placeholders = ",".join("?" for _ in unique_check_ids)
    rows = conn.execute(
        f"""
        SELECT tpl.check_id, tpl.predicate_id
        FROM task_predicate_links tpl
        JOIN hard_predicates hp ON hp.id = tpl.predicate_id
        WHERE tpl.check_id IN ({placeholders})
          AND hp.lifecycle = 'active'
        ORDER BY tpl.check_id ASC, tpl.predicate_id ASC
        """,
        unique_check_ids,
    ).fetchall()
    linked: dict[str, list[str]] = {}
    for row in rows:
        linked.setdefault(str(row["check_id"]), []).append(str(row["predicate_id"]))
    return {check_id: sorted(set(predicate_ids)) for check_id, predicate_ids in linked.items()}


def record_predicate_coverage_override(
    conn: sqlite3.Connection,
    *,
    checks: list[sqlite3.Row],
    evidence_node: sqlite3.Row,
    reason: str | None,
    details: list[str],
    elevated: bool = False,
    prior_override_node_ids: list[str] | None = None,
) -> str:
    if not reason:
        raise SystemExit("predicate coverage override requires --override-reason")
    check_ids = [str(check["id"]) for check in checks]
    prior_override_node_ids = prior_override_node_ids or []
    body = (
        f"Predicate coverage override used for test_result {evidence_node['id']} "
        f"covering acceptance checks {', '.join(check_ids)}. "
        f"Override reason: {reason}. Coverage issue(s): {'; '.join(details) if details else 'none'}"
    )
    if elevated:
        body += f" Elevated repeat override acknowledged. Prior override(s): {', '.join(prior_override_node_ids) or 'none'}."
    warning_node_id = create_node(
        conn,
        "audit_finding",
        "predicate coverage override",
        body[:240],
        {
            "severity": "warning",
            "kind": "predicate_coverage_override",
            "check_ids": check_ids,
            "evidence_node_id": evidence_node["id"],
            "override_reason": reason,
            "details": details,
            "elevated": bool(elevated),
            "prior_override_node_ids": prior_override_node_ids,
            "body": body,
        },
    )
    create_edge(conn, warning_node_id, "DERIVED_FROM", evidence_node["id"], reason="Predicate coverage override derived from closing evidence.", created_by="agent")
    for check in checks:
        create_edge(conn, warning_node_id, "APPLIES_TO", check["node_id"], reason="Predicate coverage override applies to acceptance check.", created_by="agent")
    return warning_node_id


def prior_predicate_coverage_override_node_ids(conn: sqlite3.Connection, checks: list[sqlite3.Row]) -> list[str]:
    check_node_ids = [str(check["node_id"]) for check in checks if check["node_id"]]
    if not check_node_ids:
        return []
    placeholders = ",".join("?" for _ in check_node_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT n.id, n.props, n.created_at
        FROM nodes n
        JOIN edges e ON e.from_node_id = n.id
        WHERE n.type = 'audit_finding'
          AND n.valid_to IS NULL
          AND e.type = 'APPLIES_TO'
          AND e.to_node_id IN ({placeholders})
        ORDER BY n.created_at DESC, n.id DESC
        """,
        check_node_ids,
    ).fetchall()
    override_node_ids = []
    for row in rows:
        props = props_dict(row)
        if props.get("kind") != "predicate_coverage_override":
            continue
        state = conn.execute("SELECT current_state FROM semantic_items WHERE node_id = ?", (row["id"],)).fetchone()
        if state and str(state["current_state"]) in INACTIVE_SEMANTIC_STATES:
            continue
        override_node_ids.append(str(row["id"]))
    return override_node_ids


def validate_test_result_predicate_coverage(
    conn: sqlite3.Connection,
    *,
    evidence_node_id: str,
    check_ids: list[str],
    override: bool,
    override_reason: str | None,
    elevated_override: bool = False,
) -> str | None:
    evidence_node = require_evidence_node(conn, evidence_node_id)
    if evidence_node["type"] != "test_result":
        return None
    target_check_ids = list(dict.fromkeys(str(check_id) for check_id in check_ids))
    if len(target_check_ids) <= 1:
        return None
    checks = rows_for_checks(conn, target_check_ids)
    props = props_dict(evidence_node)
    details: list[str] = []
    raw_rows = props.get("predicate_coverage_matrix")
    if raw_rows is None:
        details.append("missing predicate_coverage_matrix")
    else:
        rows = normalize_predicate_coverage_matrix_rows(raw_rows, source=f"evidence node {evidence_node_id}")
        by_check: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_check.setdefault(row["check_id"], []).append(row)
        linked_predicates = linked_hard_predicate_ids_for_checks(conn, target_check_ids)
        for check_id in target_check_ids:
            check_rows = by_check.get(check_id, [])
            if not check_rows:
                details.append(f"missing coverage row for {check_id}")
                continue
            failed = [row for row in check_rows if not predicate_coverage_row_passed(row)]
            if failed:
                predicate_ids = ", ".join(row["predicate_id"] for row in failed)
                details.append(f"{check_id} has failed or not-covered predicate row(s): {predicate_ids}")
            required_predicate_ids = set(linked_predicates.get(check_id, []))
            if required_predicate_ids:
                covered_predicate_ids = {row["predicate_id"] for row in check_rows if predicate_coverage_row_passed(row)}
                missing_required = sorted(required_predicate_ids - covered_predicate_ids)
                if missing_required:
                    details.append(
                        f"{check_id} missing linked hard predicate coverage row(s): {', '.join(missing_required)}"
                    )
    if not details:
        return None
    if override:
        prior_override_node_ids = prior_predicate_coverage_override_node_ids(conn, checks)
        if prior_override_node_ids and not elevated_override:
            raise SystemExit(
                "repeated predicate coverage override for one or more target checks requires "
                "--elevated-predicate-coverage-override with --override-reason. "
                f"Prior override node(s): {', '.join(prior_override_node_ids)}"
            )
        return record_predicate_coverage_override(
            conn,
            checks=checks,
            evidence_node=evidence_node,
            reason=override_reason,
            details=details,
            elevated=elevated_override,
            prior_override_node_ids=prior_override_node_ids,
        )
    raise SystemExit(
        f"test_result {evidence_node_id} cannot close multiple acceptance checks without a valid "
        f"predicate_coverage_matrix covering every target check: {'; '.join(details)}. "
        "Pass --predicate-coverage-matrix, or pass --override-predicate-coverage with --override-reason to record an audit warning. "
        "Repeated overrides for the same check also require --elevated-predicate-coverage-override."
    )


def evidence_exit_code(evidence_node: sqlite3.Row) -> int | None:
    props = props_dict(evidence_node)
    if evidence_node["type"] != "test_result":
        return None
    try:
        return int(props.get("exit_code", 0))
    except (TypeError, ValueError):
        return 1


def assert_evidence_can_close(conn: sqlite3.Connection, repo: Path | None, evidence_node_id: str) -> sqlite3.Row:
    evidence_node = require_evidence_node(conn, evidence_node_id)
    state = evidence_lifecycle_state(conn, evidence_node_id)
    if state in INACTIVE_SEMANTIC_STATES:
        raise SystemExit(f"evidence node {evidence_node_id} is {state}; only current valid evidence can close checks or tasks")
    if evidence_node["type"] == "test_result" and evidence_exit_code(evidence_node) != 0:
        raise SystemExit(f"failed test_result {evidence_node_id} cannot close acceptance checks or tasks")
    props = props_dict(evidence_node)
    if evidence_node["type"] == "test_result" and props.get("predicate_ok") is False:
        raise SystemExit(f"test_result {evidence_node_id} did not satisfy required predicates and cannot close checks or tasks")
    if repo is not None and evidence_node["type"] in {"artifact", "change_set", "test_result"}:
        checks = verify_evidence_row(repo, conn, evidence_node)
        bad = [check for check in checks if check["status"] in {"tampered", "missing_file", "missing_ref"}]
        if bad:
            labels = ", ".join(f"{item['label']}={item['status']}" for item in bad)
            raise SystemExit(f"evidence node {evidence_node_id} is not closable because verification failed: {labels}")
    return evidence_node


def close_check_with_evidence(
    conn: sqlite3.Connection,
    repo: Path,
    *,
    check: sqlite3.Row,
    evidence_node_id: str,
    override: bool,
    override_reason: str | None,
    override_predicate_coverage: bool = False,
    elevated_predicate_coverage_override: bool = False,
    predicate_coverage_prevalidated: bool = False,
) -> dict[str, Any]:
    existing = check["closed_by_node_id"] if "closed_by_node_id" in check.keys() else None
    if existing:
        if existing == evidence_node_id:
            return {"closed": False, "idempotent": True, "warnings": []}
        raise SystemExit(
            f"acceptance check {check['id']} is already closed by {existing}; "
            "reopen or supersede it explicitly before replacing closure evidence"
        )
    assert_evidence_can_close(conn, repo, evidence_node_id)
    warning_node_ids = []
    warning_node_id = validate_check_evidence_type(
        conn,
        check=check,
        evidence_node_id=evidence_node_id,
        override=override,
        override_reason=override_reason,
    )
    if warning_node_id:
        warning_node_ids.append(warning_node_id)
    if not predicate_coverage_prevalidated:
        target_check_ids = existing_check_ids_closed_by_evidence(conn, evidence_node_id) + [str(check["id"])]
        predicate_warning_node_id = validate_test_result_predicate_coverage(
            conn,
            evidence_node_id=evidence_node_id,
            check_ids=target_check_ids,
            override=override_predicate_coverage,
            override_reason=override_reason,
            elevated_override=elevated_predicate_coverage_override,
        )
        if predicate_warning_node_id:
            warning_node_ids.append(predicate_warning_node_id)
    conn.execute(
        "UPDATE acceptance_checks SET closed_by_node_id = ?, closed_at = ? WHERE id = ?",
        (evidence_node_id, now_iso(), check["id"]),
    )
    return {"closed": True, "idempotent": False, "warnings": warning_node_ids}


def close_task_if_ready(conn: sqlite3.Connection, task_id: str, evidence_node_id: str, *, last_check_id: str | None = None) -> dict[str, Any]:
    assert_evidence_can_close(conn, None, evidence_node_id)
    task = conn.execute("SELECT id, closed_by_node_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        raise SystemExit(f"task not found: {task_id}")
    if task["closed_by_node_id"]:
        if task["closed_by_node_id"] == evidence_node_id:
            return task_readiness_hint(
                conn,
                task_id,
                evidence_node_id=evidence_node_id,
                last_check_id=last_check_id,
                task_close_attempted=True,
            )
        raise SystemExit(
            f"task {task_id} is already closed by {task['closed_by_node_id']}; "
            "reopen or supersede it explicitly before replacing closure evidence"
        )
    open_checks = open_acceptance_checks_for_task(conn, task_id)
    if open_checks:
        open_ids = ", ".join(str(row["id"]) for row in open_checks)
        error = f"task {task_id} cannot close while acceptance checks remain open: {open_ids}"
        raise SystemExit(
            json_dumps(
                {
                    "ok": False,
                    "error": error,
                    "task_readiness": task_readiness_hint(
                        conn,
                        task_id,
                        evidence_node_id=evidence_node_id,
                        last_check_id=last_check_id,
                        task_close_attempted=True,
                        task_close_error=error,
                    ),
                }
            )
        )
    conn.execute(
        "UPDATE tasks SET closed_by_node_id = ?, closed_at = ? WHERE id = ?",
        (evidence_node_id, now_iso(), task_id),
    )
    return task_readiness_hint(
        conn,
        task_id,
        evidence_node_id=evidence_node_id,
        last_check_id=last_check_id,
        task_close_attempted=True,
        task_closed_now=True,
    )


def run_evidence_command(repo: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("test-result requires a command after --")
    return subprocess.run(
        command,
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def evidence_command_argv(command: list[str]) -> list[str]:
    return list(command[1:] if command and command[0] == "--" else command)


def evidence_env_hash() -> str:
    redacted_markers = ("TOKEN", "SECRET", "PASSWORD", "PASS", "KEY", "CREDENTIAL")
    items = []
    for key, value in sorted(os.environ.items()):
        upper = key.upper()
        safe_value = "<redacted>" if any(marker in upper for marker in redacted_markers) else value
        items.append(f"{key}={safe_value}")
    return sha256_text("\n".join(items))


def evaluate_test_result_predicates(args: argparse.Namespace, completed: subprocess.CompletedProcess[str]) -> tuple[bool, list[dict[str, Any]]]:
    predicates: list[dict[str, Any]] = []

    def add(kind: str, passed: bool, **payload: Any) -> None:
        predicates.append({"kind": kind, "passed": bool(passed), **payload})

    add("exit_code", completed.returncode == args.expect_exit_code, expected=args.expect_exit_code, actual=completed.returncode)
    if args.require_stdout:
        add("stdout_nonempty", bool(completed.stdout.strip()))
    if args.require_stderr:
        add("stderr_nonempty", bool(completed.stderr.strip()))
    for expected in args.stdout_contains:
        add("stdout_contains", expected in completed.stdout, expected=expected)
    for expected in args.stderr_contains:
        add("stderr_contains", expected in completed.stderr, expected=expected)
    return all(item["passed"] for item in predicates), predicates


def evidence_lifecycle_state(conn: sqlite3.Connection, evidence_node_id: str) -> str:
    row = conn.execute("SELECT current_state FROM semantic_items WHERE node_id = ?", (evidence_node_id,)).fetchone()
    return str(row["current_state"]) if row else "active"


def register_evidence_lifecycle(conn: sqlite3.Connection, node_id: str, *, source_node: str | None = None, reason: str | None = None) -> str | None:
    node = require_evidence_node(conn, node_id)
    return register_semantic_item(
        conn,
        node_id,
        str(node["type"]),
        state="active",
        source_node=source_node or node_id,
        event_type="created",
        reason=reason or "Evidence node recorded as current evidence.",
    )


def current_evidence_ids(conn: sqlite3.Connection, evidence_ids: list[str]) -> list[str]:
    current = []
    for evidence_id in evidence_ids:
        if evidence_lifecycle_state(conn, evidence_id) not in INACTIVE_SEMANTIC_STATES:
            current.append(evidence_id)
    return current


def clear_closures_for_inactive_evidence(conn: sqlite3.Connection, evidence_node_id: str) -> dict[str, list[str]]:
    checks = [str(row["id"]) for row in conn.execute("SELECT id FROM acceptance_checks WHERE closed_by_node_id = ?", (evidence_node_id,)).fetchall()]
    tasks = [str(row["id"]) for row in conn.execute("SELECT id FROM tasks WHERE closed_by_node_id = ?", (evidence_node_id,)).fetchall()]
    conn.execute("UPDATE acceptance_checks SET closed_by_node_id = NULL, closed_at = NULL WHERE closed_by_node_id = ?", (evidence_node_id,))
    conn.execute("UPDATE tasks SET closed_by_node_id = NULL, closed_at = NULL WHERE closed_by_node_id = ?", (evidence_node_id,))
    return {"acceptance_checks": checks, "tasks": tasks}


def write_artifact_text(repo: Path, name: str, content: str) -> str:
    out_dir = repo / ".shujuan" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_bytes(content.encode("utf-8"))
    return relpath(path, repo)


def normalize_text_newlines(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def normalized_text_hash(content: str) -> str:
    return sha256_text(normalize_text_newlines(content))


def text_artifact_hash_props(content: str, *, normalization: str = "lf-newlines") -> dict[str, Any]:
    data = content.encode("utf-8")
    text_hash = normalized_text_hash(content)
    return {
        "sha256": text_hash,
        "capture_byte_hash": sha256_bytes(data),
        "normalized_text_hash": text_hash,
        "hash_schema_version": 3,
        "text_normalization": normalization,
    }


def file_artifact_props(repo: Path, path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    props = {
        "path": relpath(path, repo),
        "sha256": sha256_bytes(data),
        "capture_byte_hash": sha256_bytes(data),
        "hash_schema_version": 2,
        "size": len(data),
        "is_text": is_text_bytes(data),
    }
    if props["is_text"]:
        text = data.decode("utf-8")
        text_hash = normalized_text_hash(text)
        props["sha256"] = text_hash
        props["normalized_text_hash"] = text_hash
        props["text_normalization"] = "lf-newlines"
        props["hash_schema_version"] = 3
    return props


def capture_artifact_file(repo: Path, path: Path, *, prefix: str = "artifact") -> dict[str, Any]:
    data = path.read_bytes()
    suffix = path.suffix or ".bin"
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.name)[:80] or "artifact"
    capture_name = f"{prefix}_{new_id('capture')}_{safe_name}"
    if suffix and not capture_name.endswith(suffix):
        capture_name += suffix
    out_dir = repo / ".shujuan" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    capture_path = out_dir / capture_name
    capture_path.write_bytes(data)
    props = {
        "original_path": relpath(path, repo),
        "capture_ref": relpath(capture_path, repo),
        "sha256": sha256_bytes(data),
        "capture_byte_hash": sha256_bytes(data),
        "hash_schema_version": 2,
        "size": len(data),
        "is_text": is_text_bytes(data),
    }
    if props["is_text"]:
        text = data.decode("utf-8")
        text_hash = normalized_text_hash(text)
        props["sha256"] = text_hash
        props["normalized_text_hash"] = text_hash
        props["text_normalization"] = "lf-newlines"
        props["hash_schema_version"] = 3
    return props


def resolve_repo_ref(repo: Path, ref: str | None) -> Path | None:
    if not ref:
        return None
    path = Path(str(ref))
    return path if path.is_absolute() else repo / path


def verify_ref_hash(
    repo: Path,
    *,
    node_id: str,
    label: str,
    ref: str | None,
    expected_hash: str | None,
    text_hash: bool = False,
    fallback_expected_hash: str | None = None,
    fallback_text_hash: bool = False,
) -> dict[str, Any]:
    if not ref:
        return {"node_id": node_id, "label": label, "status": "missing_ref", "ref": ref, "expected_hash": expected_hash}
    path = resolve_repo_ref(repo, ref)
    if not path or not path.exists() or not path.is_file():
        return {"node_id": node_id, "label": label, "status": "missing_file", "ref": ref, "expected_hash": expected_hash}
    actual_hash = normalized_text_hash(path.read_text(encoding="utf-8")) if text_hash else sha256_bytes(path.read_bytes())
    if expected_hash and actual_hash != expected_hash:
        fallback_actual_hash = None
        if fallback_expected_hash:
            try:
                fallback_actual_hash = normalized_text_hash(path.read_text(encoding="utf-8")) if fallback_text_hash else sha256_bytes(path.read_bytes())
            except UnicodeDecodeError:
                fallback_actual_hash = None
            if fallback_actual_hash == fallback_expected_hash:
                return {
                    "node_id": node_id,
                    "label": label,
                    "status": "ok",
                    "ref": ref,
                    "expected_hash": expected_hash,
                    "actual_hash": actual_hash,
                    "fallback_expected_hash": fallback_expected_hash,
                    "fallback_actual_hash": fallback_actual_hash,
                    "hash_match": "fallback",
                }
        return {
            "node_id": node_id,
            "label": label,
            "status": "tampered",
            "ref": ref,
            "expected_hash": expected_hash,
            "actual_hash": actual_hash,
            "fallback_expected_hash": fallback_expected_hash,
            "fallback_actual_hash": fallback_actual_hash,
        }
    return {"node_id": node_id, "label": label, "status": "ok", "ref": ref, "expected_hash": expected_hash, "actual_hash": actual_hash}


def explicit_active_direct_evidence_ids(conn: sqlite3.Connection, target_node_ids: list[str]) -> list[str]:
    if not target_node_ids:
        return []
    target_placeholders = ",".join("?" for _ in target_node_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT n.id, n.props, n.created_at
        FROM nodes n
        JOIN edges e ON e.from_node_id = n.id
        WHERE n.type IN ('change_set', 'test_result', 'artifact', 'user_confirmation')
          AND e.type = 'APPLIES_TO'
          AND e.to_node_id IN ({target_placeholders})
        ORDER BY n.created_at DESC
        """,
        target_node_ids,
    ).fetchall()
    active_ids = []
    for row in rows:
        props = props_dict(row)
        if props.get("active_evidence") is True or props.get("evidence_scope") == "active":
            active_ids.append(str(row["id"]))
    return active_ids


def historical_evidence_ids_for_endpoint(conn: sqlite3.Connection, endpoint_name: str) -> list[str]:
    scope = endpoint_scope_facts(conn, query_endpoint(conn, endpoint_name))
    target_node_ids = scope["target_node_ids"]
    if not target_node_ids:
        return []
    placeholders = ",".join("?" for _ in target_node_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT en.id, en.created_at
        FROM nodes en
        JOIN edges e ON e.from_node_id = en.id OR e.to_node_id = en.id
        WHERE en.type IN ('change_set', 'test_result', 'artifact', 'user_confirmation')
          AND (
            (e.type = 'APPLIES_TO' AND e.to_node_id IN ({placeholders}))
            OR (e.type = 'VALIDATED_BY' AND e.from_node_id IN ({placeholders}))
          )
        ORDER BY en.created_at DESC
        """,
        (*target_node_ids, *target_node_ids),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def evidence_nodes_for_endpoint(conn: sqlite3.Connection, endpoint_name: str, *, include_history: bool = False) -> list[sqlite3.Row]:
    status = endpoint_status_payload(conn, endpoint_name)
    ids = [item["id"] for item in status.get("evidence") or []]
    if include_history:
        ids.extend(historical_evidence_ids_for_endpoint(conn, endpoint_name))
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(
        f"SELECT id, type, label, summary, props FROM nodes WHERE id IN ({placeholders}) ORDER BY created_at DESC",
        ids,
    ).fetchall()


def verify_evidence_row(repo: Path, conn: sqlite3.Connection, row: sqlite3.Row) -> list[dict[str, Any]]:
    props = props_dict(row)
    node_id = str(row["id"])
    node_type = str(row["type"])
    checks: list[dict[str, Any]] = []
    if node_type == "test_result":
        checks.append(verify_ref_hash(repo, node_id=node_id, label="stdout_ref", ref=props.get("stdout_ref"), expected_hash=props.get("stdout_hash"), text_hash=True))
        checks.append(verify_ref_hash(repo, node_id=node_id, label="stderr_ref", ref=props.get("stderr_ref"), expected_hash=props.get("stderr_hash"), text_hash=True))
    elif node_type == "artifact":
        ref = props.get("capture_ref") or props.get("path")
        is_text_artifact = bool(props.get("is_text") and props.get("normalized_text_hash"))
        checks.append(
            verify_ref_hash(
                repo,
                node_id=node_id,
                label="artifact_ref",
                ref=ref,
                expected_hash=props.get("capture_byte_hash") or props.get("sha256"),
                fallback_expected_hash=props.get("normalized_text_hash") if is_text_artifact else None,
                fallback_text_hash=is_text_artifact,
            )
        )
    elif node_type == "change_set":
        ref = props.get("patch_ref")
        change = conn.execute("SELECT metadata FROM change_sets WHERE node_id = ?", (node_id,)).fetchone()
        metadata = json.loads(change["metadata"]) if change and change["metadata"] else {}
        checks.append(verify_ref_hash(repo, node_id=node_id, label="patch_ref", ref=ref, expected_hash=metadata.get("text_patch_hash"), text_hash=True))
    elif node_type == "user_confirmation":
        checks.append({"node_id": node_id, "label": "user_confirmation", "status": "ok", "ref": None, "expected_hash": None})
    return checks


EVIDENCE_VERIFY_FAIL_STATUSES = {
    "tampered",
    "missing_file",
    "missing_ref",
    "inactive_evidence",
    "failed_test_result",
    "predicate_failed",
    "evidence_type_mismatch",
    "predicate_coverage_missing",
    "missing_closure_evidence",
}


AUTO_INVALIDATE_VERIFY_STATUSES = {"tampered", "missing_file", "missing_ref"}


def verify_evidence_semantics(conn: sqlite3.Connection, row: sqlite3.Row) -> list[dict[str, Any]]:
    node_id = str(row["id"])
    node_type = str(row["type"])
    props = props_dict(row)
    checks: list[dict[str, Any]] = []
    state = evidence_lifecycle_state(conn, node_id)
    checks.append(
        {
            "node_id": node_id,
            "label": "currentness",
            "status": "ok" if state not in INACTIVE_SEMANTIC_STATES else "inactive_evidence",
            "current_state": canonical_semantic_state(state),
        }
    )
    if node_type == "test_result":
        exit_code = evidence_exit_code(row)
        checks.append(
            {
                "node_id": node_id,
                "label": "exit_code",
                "status": "ok" if exit_code == 0 else "failed_test_result",
                "exit_code": exit_code,
            }
        )
        predicate_failures = [
            item
            for item in props.get("predicates", [])
            if isinstance(item, dict) and item.get("passed") is False
        ]
        predicate_ok = props.get("predicate_ok")
        checks.append(
            {
                "node_id": node_id,
                "label": "predicate_ok",
                "status": "predicate_failed" if predicate_ok is False or predicate_failures else "ok",
                "predicate_ok": predicate_ok,
                "failed_predicates": predicate_failures,
            }
        )
    return checks


def verify_closed_check_evidence_semantics(conn: sqlite3.Connection, check: dict[str, Any]) -> list[dict[str, Any]]:
    check_id = str(check["id"])
    evidence_node_id = check.get("closed_by_node_id")
    if not evidence_node_id:
        return []
    evidence_node = conn.execute("SELECT id, type, label, summary, props FROM nodes WHERE id = ?", (evidence_node_id,)).fetchone()
    if not evidence_node or evidence_node["type"] not in EVIDENCE_NODE_TYPES:
        return [
            {
                "node_id": evidence_node_id,
                "check_id": check_id,
                "label": "closure_evidence",
                "status": "missing_closure_evidence",
            }
        ]
    checks: list[dict[str, Any]] = []
    if evidence_node["type"] not in expected_evidence_allowed(check.get("expected_evidence_type")):
        override = effective_evidence_type_override(conn, check_id=check_id, evidence_node_id=str(evidence_node_id))
        checks.append(
            {
                "node_id": evidence_node_id,
                "check_id": check_id,
                "label": "expected_evidence_type",
                "status": "ok" if override and override.get("effective") else "evidence_type_mismatch",
                "expected_evidence_type": check.get("expected_evidence_type"),
                "actual_evidence_type": evidence_node["type"],
                "override": override,
            }
        )
    for semantic_check in verify_evidence_semantics(conn, evidence_node):
        checks.append({**semantic_check, "check_id": check_id, "label": f"closure_{semantic_check['label']}"})
    linked_predicates = linked_hard_predicate_ids_for_checks(conn, [check_id]).get(check_id, [])
    if linked_predicates and table_exists(conn, "evidence_predicate_coverage"):
        missing = []
        for predicate_id in linked_predicates:
            coverage = conn.execute(
                """
                SELECT 1
                FROM evidence_predicate_coverage
                WHERE evidence_node_id = ?
                  AND check_id = ?
                  AND predicate_id = ?
                  AND result = 'pass'
                LIMIT 1
                """,
                (evidence_node_id, check_id, predicate_id),
            ).fetchone()
            if not coverage:
                missing.append(predicate_id)
        override = effective_predicate_coverage_override(conn, check_id=check_id, evidence_node_id=str(evidence_node_id)) if missing else None
        checks.append(
            {
                "node_id": evidence_node_id,
                "check_id": check_id,
                "label": "predicate_coverage",
                "status": "ok" if missing and override and override.get("effective") else "predicate_coverage_missing" if missing else "ok",
                "missing_predicate_ids": missing,
                "override": override,
            }
        )
    return checks


def evidence_verify_rows_for_args(
    conn: sqlite3.Connection,
    repo: Path,
    *,
    endpoint_name: str | None,
    node_ids: list[str],
    include_history: bool,
) -> tuple[list[sqlite3.Row], list[dict[str, Any]]]:
    closure_checks: list[dict[str, Any]] = []
    if endpoint_name:
        status = endpoint_status_payload(conn, endpoint_name, include_chain=False)
        rows = evidence_nodes_for_endpoint(conn, endpoint_name, include_history=include_history)
        for check in status.get("closed_checks") or []:
            closure_checks.extend(verify_closed_check_evidence_semantics(conn, check))
        if node_ids:
            existing_ids = {str(row["id"]) for row in rows}
            extra_node_ids = [node_id for node_id in node_ids if str(node_id) not in existing_ids]
            if extra_node_ids:
                placeholders = ",".join("?" for _ in extra_node_ids)
                rows = [
                    *rows,
                    *conn.execute(
                        f"SELECT id, type, label, summary, props FROM nodes WHERE id IN ({placeholders})",
                        extra_node_ids,
                    ).fetchall(),
                ]
    elif node_ids:
        placeholders = ",".join("?" for _ in node_ids)
        rows = conn.execute(
            f"SELECT id, type, label, summary, props FROM nodes WHERE id IN ({placeholders})",
            node_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, type, label, summary, props FROM nodes WHERE type IN ('change_set','test_result','artifact','user_confirmation') ORDER BY created_at DESC"
        ).fetchall()
    return rows, closure_checks


def auto_invalidate_failed_current_evidence(conn: sqlite3.Connection, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures_by_node: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        node_id = check.get("node_id")
        if not node_id or check.get("status") not in AUTO_INVALIDATE_VERIFY_STATUSES:
            continue
        failures_by_node.setdefault(str(node_id), []).append(check)

    invalidated = []
    for node_id, failures in sorted(failures_by_node.items()):
        state = evidence_lifecycle_state(conn, node_id)
        if state in INACTIVE_SEMANTIC_STATES:
            continue
        semantic_item_id = transition_semantic_item(
            conn,
            node_id,
            state="invalidated",
            event_type="verification_failed",
            source_node=node_id,
            reason="evidence verify detected tampered or missing evidence artifact/ref.",
            props={"verification_failures": failures},
        )
        cleared = clear_closures_for_inactive_evidence(conn, node_id)
        invalidated.append(
            {
                "node_id": node_id,
                "from_state": canonical_semantic_state(state),
                "to_state": "invalidated",
                "semantic_item_id": semantic_item_id,
                "failure_statuses": sorted({str(item.get("status")) for item in failures}),
                "failures": failures,
                "cleared_closures": cleared,
            }
        )
    return invalidated


def evidence_verify_payload(
    repo: Path,
    conn: sqlite3.Connection,
    endpoint_name: str | None = None,
    *,
    node_ids: list[str] | None = None,
    include_history: bool = False,
) -> dict[str, Any]:
    rows, closure_checks = evidence_verify_rows_for_args(
        conn,
        repo,
        endpoint_name=endpoint_name,
        node_ids=node_ids or [],
        include_history=include_history,
    )
    checks: list[dict[str, Any]] = []
    for row in rows:
        checks.extend(verify_evidence_row(repo, conn, row))
        checks.extend(verify_evidence_semantics(conn, row))
    checks.extend(closure_checks)
    buckets: dict[str, list[dict[str, Any]]] = {status: [] for status in ["ok", *sorted(EVIDENCE_VERIFY_FAIL_STATUSES)]}
    for check in checks:
        buckets.setdefault(str(check["status"]), []).append(check)
    ok = not any(buckets.get(status) for status in EVIDENCE_VERIFY_FAIL_STATUSES)
    return {
        "ok": ok,
        "layer": "evidence",
        "closeout_gate": False,
        "next_strict_doctor_command": (
            f"python -m shujuan endpoint doctor {endpoint_name} --strict-closeout --allow-fail"
            if endpoint_name
            else None
        ),
        "endpoint": endpoint_name,
        "checked_nodes": len(rows),
        "checks": checks,
        "buckets": buckets,
    }


def capture_change_set(
    conn: sqlite3.Connection,
    repo: Path,
    run_id: str,
    summary: str | None,
    *,
    impact: bool,
    impact_timeout: int = 30,
) -> dict[str, Any]:
    before = conn.execute(
        "SELECT * FROM run_snapshots WHERE run_id = ? AND phase = 'before' ORDER BY captured_at DESC, id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    after = conn.execute(
        "SELECT * FROM run_snapshots WHERE run_id = ? AND phase = 'after' ORDER BY captured_at DESC, id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if not after:
        capture_snapshot(conn, repo, run_id, "after")
        after = conn.execute(
            "SELECT * FROM run_snapshots WHERE run_id = ? AND phase = 'after' ORDER BY captured_at DESC, id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    before_state = load_snapshot_state(repo, run_id, "before", before["id"]) if before else {"files": {}}
    after_state = load_snapshot_state(repo, run_id, "after", after["id"]) if after else build_snapshot_state(repo)
    changed_files, patch, fingerprint_evidence = compute_snapshot_delta(before_state, after_state)
    file_classification = classify_change_set_files(changed_files)
    patch_hash = sha256_text(json_dumps(fingerprint_evidence))
    patch_dir = repo / ".shujuan" / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    change_set_id = new_id("change")
    patch_ref = patch_dir / f"{run_id}_{change_set_id}_change_set.patch"
    patch_ref.write_bytes(patch.encode("utf-8"))
    text_patch_hash = sha256_text(patch_ref.read_text(encoding="utf-8"))
    change_node_id = create_node(conn, "change_set", f"change set for {run_id}", summary, {"patch_ref": relpath(patch_ref, repo)})
    register_evidence_lifecycle(conn, change_node_id, source_node=change_node_id, reason="Change set evidence recorded.")
    change_evidence_record_ids = [
        record_evidence_record(
            conn,
            evidence_node_id=change_node_id,
            record_type="patch",
            ref=relpath(patch_ref, repo),
            sha256=text_patch_hash,
            metadata={"hash_kind": "normalized_text_patch"},
        )
    ]
    changed_file_paths = [item["path_new"] or item["path_old"] for item in changed_files]
    implementation_file_paths = file_classification["implementation_files"]
    skipped_impact_metadata = impact_metadata(repo, entrypoint_used="default_skipped_no_impact")
    impact_result = collect_impact(
        conn,
        repo,
        change_node_id,
        implementation_file_paths,
        timeout_seconds=impact_timeout,
    ) if impact else {
        **skipped_impact_metadata,
        "status": "skipped",
        "reason": "explicit_opt_in_required",
        "installed": skipped_impact_metadata["provider_detail"]["installed"],
        "indexed": skipped_impact_metadata["provider_detail"]["indexed"],
        "index_path": skipped_impact_metadata["provider_detail"]["index_path"],
        "input": {
            "changed_files": implementation_file_paths,
            "all_changed_files": changed_file_paths,
            "implementation_files": implementation_file_paths,
            "provider_runtime_files": file_classification["provider_runtime_files"],
            "ignored_runtime_files": file_classification["ignored_runtime_files"],
        },
        "reports": [],
        "provider_material_nodes": [],
    }
    impact_result.setdefault("input", {})
    impact_result["input"].setdefault("changed_files", implementation_file_paths)
    impact_result["input"]["all_changed_files"] = changed_file_paths
    impact_result["input"]["implementation_files"] = implementation_file_paths
    impact_result["input"]["provider_runtime_files"] = file_classification["provider_runtime_files"]
    impact_result["input"]["ignored_runtime_files"] = file_classification["ignored_runtime_files"]
    conn.execute(
        """
        INSERT INTO change_sets
          (id, node_id, run_id, base_snapshot_id, after_snapshot_id, patch_hash, summary, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            change_set_id,
            change_node_id,
            run_id,
            before["id"] if before else None,
            after["id"] if after else None,
            patch_hash,
            summary,
            now_iso(),
            json_dumps(
                {
                    "patch_ref": relpath(patch_ref, repo),
                    "impact": impact_result,
                    "fingerprint_strategy": "patch_hash covers snapshot delta file evidence, including binary path/hash evidence; text_patch_hash covers text hunks only.",
                    "text_patch_hash": text_patch_hash,
                    "file_evidence": fingerprint_evidence["files"],
                    "implementation_files": file_classification["implementation_files"],
                    "provider_runtime_files": file_classification["provider_runtime_files"],
                    "ignored_runtime_files": file_classification["ignored_runtime_files"],
                    "file_classifications": file_classification["file_classifications"],
                    "evidence_record_ids": change_evidence_record_ids,
                }
            ),
        ),
    )
    run_node = conn.execute("SELECT node_id FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    if run_node:
        create_edge(conn, run_node["node_id"], "PRODUCES", change_node_id, reason="Run produced captured diff.")

    hunks_by_path = parse_diff_hunks(patch)
    file_results = []
    for item in changed_files:
        diff_file_id = new_id("diff_file")
        conn.execute(
            """
            INSERT INTO diff_files
              (id, change_set_id, path_old, path_new, change_type, additions,
               deletions, file_hash_before, file_hash_after)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                diff_file_id,
                change_set_id,
                item["path_old"],
                item["path_new"],
                item["change_type"],
                item["additions"],
                item["deletions"],
                item.get("file_hash_before"),
                item.get("file_hash_after"),
            ),
        )
        path_key = item["path_new"] or item["path_old"] or ""
        hunk_records = []
        for hunk in hunks_by_path.get(path_key, []):
            hunk_node_id = create_node(conn, "diff_hunk", f"{path_key}:{hunk['new_start']}", hunk["hunk_header"])
            hunk_id = new_id("hunk")
            conn.execute(
                """
                INSERT INTO diff_hunks
                  (id, diff_file_id, node_id, old_start, old_lines, new_start,
                   new_lines, hunk_header, old_text, new_text, context_text, hunk_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hunk_id,
                    diff_file_id,
                    hunk_node_id,
                    hunk["old_start"],
                    hunk["old_lines"],
                    hunk["new_start"],
                    hunk["new_lines"],
                    hunk["hunk_header"],
                    hunk["old_text"],
                    hunk["new_text"],
                    hunk["context_text"],
                    sha256_text(hunk["raw"]),
                ),
            )
            create_edge(conn, change_node_id, "PRODUCES", hunk_node_id, reason="Change set contains diff hunk.")
            hunk_records.append({"id": hunk_id, "node_id": hunk_node_id, **hunk})
        symbol_links = []
        if item.get("file_lane") == "implementation":
            code_object_id = upsert_file_code_object(conn, repo, path_key)
            conn.execute(
                """
                INSERT INTO change_code_links
                  (id, change_set_id, code_object_id, relation_type, confidence, evidence_hunk_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("code_link"),
                    change_set_id,
                    code_object_id,
                    relation_for_change_type(item["change_type"]),
                    1.0,
                    hunk_records[0]["id"] if hunk_records else None,
                ),
            )
            code_node = conn.execute("SELECT node_id FROM code_objects WHERE id = ?", (code_object_id,)).fetchone()
            if code_node:
                create_edge(conn, change_node_id, "MODIFIES", code_node["node_id"], reason="File-level code object changed.")
            symbol_links = link_python_symbols_for_change(
                conn,
                repo,
                change_set_id=change_set_id,
                change_node_id=change_node_id,
                path=path_key,
                change_type=item["change_type"],
                hunk_records=hunk_records,
                before_state=before_state,
                after_state=after_state,
            )
        file_results.append({"diff_file_id": diff_file_id, **item, "hunks": len(hunk_records), "symbol_links": symbol_links})
    result_file_classification = classify_change_set_files(file_results)
    return {
        "change_set_id": change_set_id,
        "change_set_node_id": change_node_id,
        "patch_hash": patch_hash,
        "patch_ref": relpath(patch_ref, repo),
        "files": file_results,
        "implementation_files": result_file_classification["implementation_files"],
        "provider_runtime_files": result_file_classification["provider_runtime_files"],
        "ignored_runtime_files": result_file_classification["ignored_runtime_files"],
        "file_classifications": result_file_classification["file_classifications"],
        "impact": impact_result,
    }


def snapshot_state_path(repo: Path, run_id: str, phase: str, snapshot_id: str | None = None) -> Path:
    if snapshot_id:
        return repo / ".shujuan" / "patches" / f"{run_id}_{phase}_{snapshot_id}_state.json"
    return repo / ".shujuan" / "patches" / f"{run_id}_{phase}_state.json"


MAX_SNAPSHOT_TEXT_CAPTURE_BYTES = 1_000_000
TEMP_PATH_SUFFIXES = (".tmp", ".temp", ".swp", ".swo", ".bak", "~")
PROVIDER_RUNTIME_PATH_PREFIXES = (".claude/skills/gitnexus",)
IGNORED_RUNTIME_PATH_PREFIXES = (".ai/codegraph", ".codegraph", ".gitnexus")


def normalize_repo_path(path: str | None) -> str:
    normalized = (path or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def path_matches_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def runtime_file_classification(path: str | None) -> str | None:
    normalized = normalize_repo_path(path)
    if any(path_matches_prefix(normalized, prefix) for prefix in PROVIDER_RUNTIME_PATH_PREFIXES):
        return "provider_runtime"
    if any(path_matches_prefix(normalized, prefix) for prefix in IGNORED_RUNTIME_PATH_PREFIXES):
        return "ignored_runtime"
    return None


def append_unique(paths: list[str], path: str | None) -> None:
    if path and path not in paths:
        paths.append(path)


def classify_change_set_files(items: list[dict[str, Any]]) -> dict[str, Any]:
    implementation_files: list[str] = []
    provider_runtime_files: list[str] = []
    ignored_runtime_files: list[str] = []
    file_classifications: list[dict[str, Any]] = []
    for item in items:
        path = item.get("path_new") or item.get("path_old")
        lane = item.get("file_lane") or item.get("runtime_file_classification") or runtime_file_classification(path) or "implementation"
        if lane == "provider_runtime":
            append_unique(provider_runtime_files, path)
        elif lane == "ignored_runtime":
            append_unique(ignored_runtime_files, path)
        else:
            lane = "implementation"
            append_unique(implementation_files, path)
        file_classifications.append(
            {
                "path": path,
                "path_old": item.get("path_old"),
                "path_new": item.get("path_new"),
                "change_type": item.get("change_type"),
                "file_lane": lane,
                "classification": item.get("classification"),
                "runtime_file_classification": item.get("runtime_file_classification"),
            }
        )
    return {
        "implementation_files": implementation_files,
        "provider_runtime_files": provider_runtime_files,
        "ignored_runtime_files": ignored_runtime_files,
        "file_classifications": file_classifications,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def try_sha256_file(path: Path) -> tuple[str | None, str | None]:
    try:
        return sha256_file(path), None
    except OSError as exc:
        return None, f"hash_error:{exc.__class__.__name__}:{exc}"


def is_temporary_path(path: str) -> bool:
    name = Path(path.replace("\\", "/")).name.lower()
    return name.startswith("~$") or name.endswith(TEMP_PATH_SUFFIXES)


def classify_text_sample(sample: bytes) -> tuple[bool, str | None]:
    if b"\x00" in sample:
        return False, "binary"
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False, "non_utf8"
    return True, None


def build_snapshot_state(repo: Path) -> dict[str, Any]:
    paths = sorted(set(list_tracked_files(repo)) | set(list_untracked_files(repo)))
    files: dict[str, dict[str, Any]] = {}
    skipped_text: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for path in paths:
        runtime_classification = runtime_file_classification(path)
        if is_internal_ignored_path(path):
            continue
        full_path = repo / path
        try:
            if not full_path.is_file():
                continue
            size = full_path.stat().st_size
        except OSError as exc:
            warning = f"stat_error:{exc.__class__.__name__}:{exc}"
            temporary = is_temporary_path(path)
            skipped_reason = "provider_runtime_file" if runtime_classification == "provider_runtime" else "unreadable"
            classification = runtime_classification or "unreadable"
            state = {
                "path": path,
                "sha256": None,
                "size": None,
                "is_text": False,
                "is_binary": False,
                "is_temporary": temporary,
                "classification": classification,
                "skipped_text_reason": skipped_reason,
                "runtime_file_classification": runtime_classification,
                "file_lane": runtime_classification or "implementation",
                "warnings": [warning],
            }
            files[path] = state
            skipped_text.append(
                {
                    "path": path,
                    "reason": skipped_reason,
                    "classification": classification,
                    "runtime_file_classification": runtime_classification,
                    "file_lane": runtime_classification or "implementation",
                    "size": None,
                    "sha256": None,
                    "warnings": [warning],
                }
            )
            warnings.append({"path": path, "warning": warning})
            continue

        file_warnings: list[str] = []
        try:
            with full_path.open("rb") as handle:
                sample = handle.read(8192)
        except OSError as exc:
            sample = b""
            file_warnings.append(f"read_sample_error:{exc.__class__.__name__}:{exc}")
        is_text, skipped_reason = classify_text_sample(sample)
        if file_warnings:
            is_text = False
            skipped_reason = "unreadable"
        if runtime_classification == "provider_runtime":
            skipped_reason = "provider_runtime_file"
        temporary = is_temporary_path(path)
        if temporary and is_text and runtime_classification is None:
            skipped_reason = "temporary_path"
        elif is_text and size > MAX_SNAPSHOT_TEXT_CAPTURE_BYTES and runtime_classification is None:
            skipped_reason = "large_file"
        classification = runtime_classification or skipped_reason or "text"
        file_hash, hash_warning = try_sha256_file(full_path)
        if hash_warning:
            file_warnings.append(hash_warning)
            if skipped_reason is None:
                skipped_reason = "unreadable"
                classification = "unreadable"
        state: dict[str, Any] = {
            "path": path,
            "sha256": file_hash,
            "size": size,
            "is_text": is_text,
            "is_binary": not is_text and skipped_reason == "binary",
            "is_temporary": temporary,
            "classification": classification,
            "skipped_text_reason": skipped_reason,
            "runtime_file_classification": runtime_classification,
            "file_lane": runtime_classification or "implementation",
            "warnings": file_warnings,
        }
        if is_text and skipped_reason is None:
            try:
                state["content"] = full_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                state["is_text"] = False
                state["is_binary"] = False
                state["classification"] = "non_utf8"
                state["skipped_text_reason"] = "non_utf8"
            except OSError as exc:
                warning = f"read_text_error:{exc.__class__.__name__}:{exc}"
                state["is_text"] = False
                state["is_binary"] = False
                state["classification"] = "unreadable"
                state["skipped_text_reason"] = "unreadable"
                state["warnings"].append(warning)
        if state.get("skipped_text_reason"):
            skipped_text.append(
                {
                    "path": path,
                    "reason": state["skipped_text_reason"],
                    "classification": state.get("classification"),
                    "runtime_file_classification": state.get("runtime_file_classification"),
                    "file_lane": state.get("file_lane"),
                    "size": size,
                    "sha256": state["sha256"],
                    "warnings": state.get("warnings", []),
                }
            )
        for warning in state.get("warnings", []):
            warnings.append({"path": path, "warning": warning})
        files[path] = state
    return {
        "head_commit": current_head(repo),
        "captured_at": now_iso(),
        "files": files,
        "skipped_text": skipped_text,
        "warnings": warnings,
    }


def load_snapshot_state(repo: Path, run_id: str, phase: str, snapshot_id: str | None = None) -> dict[str, Any]:
    path = snapshot_state_path(repo, run_id, phase, snapshot_id)
    if snapshot_id and not path.exists():
        legacy = snapshot_state_path(repo, run_id, phase)
        path = legacy
    if not path.exists():
        return {"files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def list_tracked_files(repo: Path) -> list[str]:
    output = run_git(repo, ["ls-files"], allow_fail=True)
    return [
        line.replace("\\", "/")
        for line in output.splitlines()
        if line and not is_internal_ignored_path(line.replace("\\", "/"))
    ]


def compute_snapshot_delta(
    before_state: dict[str, Any],
    after_state: dict[str, Any],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    before_files = before_state.get("files", {})
    after_files = after_state.get("files", {})
    before_paths = set(before_files)
    after_paths = set(after_files)
    deleted_paths = before_paths - after_paths
    added_paths = after_paths - before_paths
    modified_paths = {
        path
        for path in before_paths & after_paths
        if before_files[path].get("sha256") != after_files[path].get("sha256")
    }

    rename_pairs: list[tuple[str, str]] = []
    unmatched_deleted = set(deleted_paths)
    unmatched_added = set(added_paths)
    added_by_hash: dict[str, list[str]] = {}
    for path in sorted(added_paths):
        added_by_hash.setdefault(str(after_files[path].get("sha256")), []).append(path)
    for old_path in sorted(deleted_paths):
        candidates = added_by_hash.get(str(before_files[old_path].get("sha256")), [])
        while candidates:
            new_path = candidates.pop(0)
            if new_path in unmatched_added:
                rename_pairs.append((old_path, new_path))
                unmatched_deleted.discard(old_path)
                unmatched_added.discard(new_path)
                break

    items: list[dict[str, Any]] = []
    patches: list[str] = []

    for old_path, new_path in rename_pairs:
        before_file = before_files[old_path]
        after_file = after_files[new_path]
        item, patch = build_delta_item(old_path, new_path, "renamed", before_file, after_file)
        items.append(item)
        if patch:
            patches.append(patch)
    for path in sorted(modified_paths):
        item, patch = build_delta_item(path, path, "modified", before_files[path], after_files[path])
        items.append(item)
        if patch:
            patches.append(patch)
    for path in sorted(unmatched_deleted):
        item, patch = build_delta_item(path, None, "deleted", before_files[path], None)
        items.append(item)
        if patch:
            patches.append(patch)
    for path in sorted(unmatched_added):
        item, patch = build_delta_item(None, path, "added", None, after_files[path])
        items.append(item)
        if patch:
            patches.append(patch)

    patch = "\n".join(patches)
    if patch:
        patch = patch.rstrip() + "\n"
    evidence = {
        "version": 1,
        "strategy": "snapshot_delta_file_evidence",
        "text_patch_hash": sha256_text(patch),
        "files": [
            {
                "path_old": item["path_old"],
                "path_new": item["path_new"],
                "change_type": item["change_type"],
                "file_hash_before": item.get("file_hash_before"),
                "file_hash_after": item.get("file_hash_after"),
                "is_binary": item.get("is_binary", False),
                "is_temporary": item.get("is_temporary", False),
                "classification": item.get("classification"),
                "skipped_text_reason": item.get("skipped_text_reason"),
                "runtime_file_classification": item.get("runtime_file_classification"),
                "file_lane": item.get("file_lane", "implementation"),
                "warnings": item.get("warnings", []),
                "size_before": item.get("size_before"),
                "size_after": item.get("size_after"),
                "additions": item["additions"],
                "deletions": item["deletions"],
            }
            for item in items
        ],
    }
    return items, patch, evidence


def build_delta_item(
    path_old: str | None,
    path_new: str | None,
    change_type: str,
    before_file: dict[str, Any] | None,
    after_file: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    before_text = before_file.get("content") if before_file and before_file.get("is_text") else None
    after_text = after_file.get("content") if after_file and after_file.get("is_text") else None
    runtime_classification = (
        (after_file or {}).get("runtime_file_classification")
        or (before_file or {}).get("runtime_file_classification")
        or runtime_file_classification(path_new)
        or runtime_file_classification(path_old)
    )
    skipped_text_reason = (
        (after_file or {}).get("skipped_text_reason")
        or (before_file or {}).get("skipped_text_reason")
    )
    if runtime_classification == "provider_runtime" and skipped_text_reason is None:
        skipped_text_reason = "provider_runtime_file"
    classification = (
        runtime_classification
        or (after_file or {}).get("classification")
        or (before_file or {}).get("classification")
        or ("binary" if bool((before_file or {}).get("is_binary") or (after_file or {}).get("is_binary")) else "text")
    )
    warnings = [
        *list((before_file or {}).get("warnings") or []),
        *list((after_file or {}).get("warnings") or []),
    ]
    is_binary = bool((before_file or {}).get("is_binary") or (after_file or {}).get("is_binary"))
    patch = ""
    additions = 0
    deletions = 0
    if not is_binary and not skipped_text_reason:
        patch = build_text_delta_patch(path_old, path_new, change_type, before_text or "", after_text or "")
        additions, deletions = count_patch_additions_deletions(patch)
    item = {
        "path_old": path_old,
        "path_new": path_new,
        "change_type": change_type,
        "additions": additions,
        "deletions": deletions,
        "file_hash_before": before_file.get("sha256") if before_file else None,
        "file_hash_after": after_file.get("sha256") if after_file else None,
        "is_binary": is_binary,
        "is_temporary": bool((before_file or {}).get("is_temporary") or (after_file or {}).get("is_temporary")),
        "classification": classification,
        "skipped_text_reason": skipped_text_reason,
        "runtime_file_classification": runtime_classification,
        "file_lane": runtime_classification or "implementation",
        "warnings": warnings,
        "size_before": before_file.get("size") if before_file else None,
        "size_after": after_file.get("size") if after_file else None,
    }
    return item, patch


def build_text_delta_patch(
    path_old: str | None,
    path_new: str | None,
    change_type: str,
    before_text: str,
    after_text: str,
) -> str:
    old_label = "/dev/null" if path_old is None else f"a/{path_old}"
    new_label = "/dev/null" if path_new is None else f"b/{path_new}"
    old_lines = before_text.splitlines(keepends=True)
    new_lines = after_text.splitlines(keepends=True)
    body = list(
        unified_diff(
            old_lines,
            new_lines,
            fromfile=old_label,
            tofile=new_label,
            lineterm="",
        )
    )
    if len(body) <= 2:
        body = []
    header = [f"diff --git a/{path_old or path_new} b/{path_new or path_old}"]
    if change_type == "added":
        header.append("new file mode 100644")
    elif change_type == "deleted":
        header.append("deleted file mode 100644")
    elif change_type == "renamed" and path_old and path_new:
        header.extend([f"rename from {path_old}", f"rename to {path_new}"])
    return "\n".join([*header, *body])


def count_patch_additions_deletions(patch: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return additions, deletions


def build_worktree_patch(repo: Path) -> str:
    tracked_patch = run_git(repo, ["diff", "--binary", "HEAD"], allow_fail=True) or ""
    untracked_patches: list[str] = []
    for path in list_untracked_files(repo):
        if runtime_file_classification(path):
            continue
        full_path = repo / path
        try:
            size = full_path.stat().st_size
        except OSError:
            continue
        if is_text_file(full_path) and not is_temporary_path(path) and size <= MAX_SNAPSHOT_TEXT_CAPTURE_BYTES:
            patch = build_untracked_file_patch(repo, path)
            if patch:
                untracked_patches.append(patch)
    return "\n".join(part for part in [tracked_patch.rstrip(), *untracked_patches] if part).rstrip() + "\n"


def list_untracked_files(repo: Path) -> list[str]:
    output = run_git(repo, ["ls-files", "--others", "--exclude-standard"], allow_fail=True)
    return [
        line.replace("\\", "/")
        for line in output.splitlines()
        if line and not is_internal_ignored_path(line.replace("\\", "/"))
    ]


def is_internal_ignored_path(path: str) -> bool:
    normalized = normalize_repo_path(path)
    if runtime_file_classification(normalized) == "ignored_runtime":
        return True
    parts = Path(normalized).parts
    ignored_roots = {".git", ".shujuan", "__pycache__", ".pytest_cache", ".mypy_cache"}
    return any(part in ignored_roots or part.endswith(".pyc") for part in parts)


def is_text_file(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        sample = path.read_bytes()[:8192]
    except OSError:
        return False
    return is_text_bytes(sample)


def is_text_bytes(sample: bytes) -> bool:
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def count_text_lines(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    if not text:
        return 0
    return len(text.splitlines())


def build_untracked_file_patch(repo: Path, path: str) -> str:
    full_path = repo / path
    try:
        text = full_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    lines = text.splitlines()
    try:
        file_hash = sha256_bytes(full_path.read_bytes())[:12]
    except OSError:
        return ""
    patch_lines = [
        f"diff --git a/{path} b/{path}",
        "new file mode 100644",
        f"index 0000000..{file_hash}",
        "--- /dev/null",
        f"+++ b/{path}",
    ]
    if lines:
        patch_lines.append(f"@@ -0,0 +1,{len(lines)} @@")
        patch_lines.extend(f"+{line}" for line in lines)
    return "\n".join(patch_lines)


def parse_diff_hunks(patch: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    current_path = ""
    current_old_path: str | None = None
    current_new_path: str | None = None
    current_hunk: dict[str, Any] | None = None
    raw_lines: list[str] = []
    old_lines: list[str] = []
    new_lines: list[str] = []
    context_lines: list[str] = []

    def flush() -> None:
        nonlocal current_hunk, raw_lines, old_lines, new_lines, context_lines
        if current_hunk is not None:
            current_hunk["raw"] = "\n".join(raw_lines)
            current_hunk["old_text"] = "\n".join(old_lines)
            current_hunk["new_text"] = "\n".join(new_lines)
            current_hunk["context_text"] = "\n".join(context_lines)
            result.setdefault(current_path, []).append(current_hunk)
        current_hunk = None
        raw_lines = []
        old_lines = []
        new_lines = []
        context_lines = []

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            flush()
            current_path = ""
            current_old_path = None
            current_new_path = None
            parts = line.split()
            if len(parts) >= 4:
                current_old_path = parts[2][2:] if parts[2].startswith("a/") else parts[2]
                current_new_path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                current_path = current_new_path or current_old_path or ""
        elif line.startswith("--- "):
            old_label = line[4:]
            current_old_path = None if old_label == "/dev/null" else old_label[2:] if old_label.startswith("a/") else old_label
            current_path = current_new_path or current_old_path or ""
        elif line.startswith("+++ "):
            new_label = line[4:]
            current_new_path = None if new_label == "/dev/null" else new_label[2:] if new_label.startswith("b/") else new_label
            current_path = current_new_path or current_old_path or ""
        elif line.startswith("@@ "):
            flush()
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            current_hunk = {
                "hunk_header": line,
                "old_start": int(match.group(1)) if match else None,
                "old_lines": int(match.group(2) or "1") if match else None,
                "new_start": int(match.group(3)) if match else None,
                "new_lines": int(match.group(4) or "1") if match else None,
            }
            raw_lines.append(line)
        elif current_hunk is not None:
            raw_lines.append(line)
            if line.startswith("-") and not line.startswith("---"):
                old_lines.append(line[1:])
            elif line.startswith("+") and not line.startswith("+++"):
                new_lines.append(line[1:])
            elif line.startswith(" "):
                context_lines.append(line[1:])
    flush()
    return result


def module_name_from_path(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if not parts:
        return Path(path).stem
    parts[-1] = Path(parts[-1]).stem
    if parts[-1] == "__init__":
        parts = parts[:-1]
    cleaned = []
    for part in parts:
        if not part:
            continue
        value = re.sub(r"\W", "_", part)
        if value and value[0].isdigit():
            value = f"_{value}"
        cleaned.append(value)
    return ".".join(cleaned) or Path(path).stem


def parse_python_symbols(path: str, content: str) -> list[dict[str, Any]]:
    if not path.endswith(".py"):
        return []
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError:
        return []
    module_name = module_name_from_path(path)
    symbols: list[dict[str, Any]] = []

    def visit(body: list[ast.stmt], parents: list[str]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                qualified = ".".join([module_name, *parents, node.name])
                symbols.append(
                    {
                        "type": "class",
                        "symbol_name": node.name,
                        "qualified_name": qualified,
                        "start_line": int(node.lineno),
                        "end_line": int(getattr(node, "end_lineno", node.lineno)),
                        "props": {"decorators": [decorator_name(item) for item in node.decorator_list]},
                    }
                )
                visit(node.body, [*parents, node.name])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = ".".join([module_name, *parents, node.name])
                symbols.append(
                    {
                        "type": "function",
                        "symbol_name": node.name,
                        "qualified_name": qualified,
                        "start_line": int(node.lineno),
                        "end_line": int(getattr(node, "end_lineno", node.lineno)),
                        "props": {
                            "async": isinstance(node, ast.AsyncFunctionDef),
                            "decorators": [decorator_name(item) for item in node.decorator_list],
                        },
                    }
                )
                visit(node.body, [*parents, node.name])

    visit(tree.body, [])
    return symbols


def decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = decorator_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return decorator_name(node.func)
    return type(node).__name__


def upsert_python_symbol_code_objects(
    conn: sqlite3.Connection,
    repo: Path,
    path: str,
    content: str,
    *,
    archived: bool = False,
) -> list[dict[str, Any]]:
    symbols = parse_python_symbols(path, content)
    if not symbols:
        return []
    lines = content.splitlines()
    now = now_iso()
    results: list[dict[str, Any]] = []
    for symbol in symbols:
        start = int(symbol["start_line"])
        end = int(symbol["end_line"])
        segment = "\n".join(lines[start - 1 : end])
        existing = conn.execute(
            """
            SELECT id, node_id
            FROM code_objects
            WHERE type = ? AND path = ? AND qualified_name = ?
            """,
            (symbol["type"], path, symbol["qualified_name"]),
        ).fetchone()
        props = symbol.get("props") or {}
        if existing:
            conn.execute(
                """
                UPDATE code_objects
                SET symbol_name = ?, language = 'python', start_line = ?, end_line = ?,
                    content_hash = ?, last_seen_commit = ?, archived_at = ?, props = ?
                WHERE id = ?
                """,
                (
                    symbol["symbol_name"],
                    start,
                    end,
                    sha256_text(segment),
                    current_head(repo),
                    now if archived else None,
                    json_dumps(props),
                    existing["id"],
                ),
            )
            code_object_id = str(existing["id"])
            node_id = str(existing["node_id"])
        else:
            node_id = create_node(
                conn,
                str(symbol["type"]),
                str(symbol["qualified_name"]),
                f"{symbol['type']} {symbol['qualified_name']} in {path}:{start}-{end}",
                {"path": path, **props},
            )
            code_object_id = new_id("code")
            conn.execute(
                """
                INSERT INTO code_objects
                  (id, node_id, type, path, symbol_name, qualified_name, language,
                   start_line, end_line, content_hash, last_seen_commit, archived_at, props)
                VALUES (?, ?, ?, ?, ?, ?, 'python', ?, ?, ?, ?, ?, ?)
                """,
                (
                    code_object_id,
                    node_id,
                    symbol["type"],
                    path,
                    symbol["symbol_name"],
                    symbol["qualified_name"],
                    start,
                    end,
                    sha256_text(segment),
                    current_head(repo),
                    now if archived else None,
                    json_dumps(props),
                ),
            )
        results.append({**symbol, "id": code_object_id, "node_id": node_id})
    return results


def hunk_overlaps_symbol(hunk: dict[str, Any], symbol: dict[str, Any], change_type: str) -> bool:
    if change_type == "deleted":
        start = hunk.get("old_start")
        length = hunk.get("old_lines")
    else:
        start = hunk.get("new_start")
        length = hunk.get("new_lines")
    if start is None:
        return False
    length = int(length or 1)
    hunk_start = int(start)
    hunk_end = hunk_start + max(length, 1) - 1
    return hunk_start <= int(symbol["end_line"]) and int(symbol["start_line"]) <= hunk_end


def link_python_symbols_for_change(
    conn: sqlite3.Connection,
    repo: Path,
    *,
    change_set_id: str,
    change_node_id: str,
    path: str,
    change_type: str,
    hunk_records: list[dict[str, Any]],
    before_state: dict[str, Any],
    after_state: dict[str, Any],
) -> int:
    if not path.endswith(".py"):
        return 0
    state_files = before_state.get("files", {}) if change_type == "deleted" else after_state.get("files", {})
    file_state = state_files.get(path)
    if not file_state or not file_state.get("is_text"):
        return 0
    symbols = upsert_python_symbol_code_objects(
        conn,
        repo,
        path,
        str(file_state.get("content") or ""),
        archived=change_type == "deleted",
    )
    links = 0
    seen: set[tuple[str, str | None]] = set()
    for symbol in symbols:
        overlapping = [hunk for hunk in hunk_records if hunk_overlaps_symbol(hunk, symbol, change_type)]
        if not overlapping and not hunk_records:
            overlapping = [{"id": None}]
        for hunk in overlapping:
            key = (str(symbol["id"]), hunk.get("id"))
            if key in seen:
                continue
            seen.add(key)
            conn.execute(
                """
                INSERT INTO change_code_links
                  (id, change_set_id, code_object_id, relation_type, confidence, evidence_hunk_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("code_link"),
                    change_set_id,
                    symbol["id"],
                    relation_for_change_type(change_type),
                    0.9 if hunk.get("id") else 0.6,
                    hunk.get("id"),
                ),
            )
            create_edge(
                conn,
                change_node_id,
                "MODIFIES",
                str(symbol["node_id"]),
                reason="Diff hunk overlaps Python symbol code object." if hunk.get("id") else "Python symbol in changed file.",
                confidence=0.9 if hunk.get("id") else 0.6,
            )
            links += 1
    return links


def hash_worktree_file(repo: Path, path: str | None) -> str | None:
    if not path:
        return None
    full_path = repo / path
    if not full_path.exists() or not full_path.is_file():
        return None
    return sha256_bytes(full_path.read_bytes())


def upsert_file_code_object(conn: sqlite3.Connection, repo: Path, path: str) -> str:
    existing = conn.execute(
        "SELECT id, node_id FROM code_objects WHERE type = 'file' AND path = ? AND qualified_name IS NULL",
        (path,),
    ).fetchone()
    content_hash = hash_worktree_file(repo, path)
    language = language_from_path(path)
    if existing:
        conn.execute(
            """
            UPDATE code_objects
            SET language = ?, content_hash = ?, last_seen_commit = ?, archived_at = NULL
            WHERE id = ?
            """,
            (language, content_hash, current_head(repo), existing["id"]),
        )
        return str(existing["id"])
    node_id = create_node(conn, "file", path, f"File code object for {path}")
    code_object_id = new_id("code")
    conn.execute(
        """
        INSERT INTO code_objects
          (id, node_id, type, path, language, content_hash, last_seen_commit)
        VALUES (?, ?, 'file', ?, ?, ?, ?)
        """,
        (code_object_id, node_id, path, language, content_hash, current_head(repo)),
    )
    return code_object_id


def language_from_path(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescriptreact",
        ".jsx": "javascriptreact",
        ".md": "markdown",
        ".json": "json",
        ".sql": "sql",
    }.get(suffix)


def relation_for_change_type(change_type: str) -> str:
    return {
        "added": "creates",
        "deleted": "deletes",
        "renamed": "refactors",
    }.get(change_type, "modifies")


def collect_impact(
    conn: sqlite3.Connection,
    repo: Path,
    change_node_id: str,
    changed_files: list[str | None],
    *,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    files = [item for item in changed_files if item]
    metadata = impact_metadata(repo, entrypoint_used="gitnexus_cli_opt_in")
    provider_detail = metadata["provider_detail"]
    timeout_seconds = max(1, int(timeout_seconds or 30))
    if not files:
        return {
            **metadata,
            "status": "skipped",
            "reason": "no_implementation_files",
            "installed": provider_detail["installed"],
            "indexed": provider_detail["indexed"],
            "index_path": provider_detail["index_path"],
            "fallback": "recorded_changed_files_only",
            "input": {"changed_files": files},
            "reports": [],
            "provider_material_nodes": [],
        }
    if not provider_detail["installed"]:
        return {
            **metadata,
            "status": "provider_missing",
            "installed": False,
            "indexed": provider_detail["indexed"],
            "index_path": provider_detail["index_path"],
            "fallback": "recorded_changed_files_only",
            "input": {"changed_files": files},
            "reports": [],
            "provider_material_nodes": [],
        }
    if not provider_detail["indexed"]:
        return {
            **metadata,
            "status": "provider_index_missing",
            "installed": True,
            "indexed": False,
            "index_path": provider_detail["index_path"],
            "fallback": "recorded_changed_files_only",
            "input": {"changed_files": files},
            "reports": [],
            "provider_material_nodes": [],
        }
    metadata = impact_metadata(repo, entrypoint_used="gitnexus_cli_opt_in", provider_invoked=True)
    try:
        completed = subprocess.run(
            gitnexus_command("detect-changes", "--scope", "all", "--repo", "."),
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        stdout = ""
        stderr = str(exc)
        exit_code = 124 if isinstance(exc, subprocess.TimeoutExpired) else 1
    reports = [
        {
            "scope": "all",
            "changed_files": files,
            "exit_code": exit_code,
            "report_path": None,
            "stdout": stdout[-4000:],
            "stderr": stderr[-4000:],
        }
    ]
    provider_material_nodes = record_impact_evidence(conn, repo, change_node_id, reports)
    status = "executed" if all(int(report.get("exit_code") or 0) == 0 for report in reports) else "failed"
    return {
        **metadata,
        "status": status,
        "installed": True,
        "indexed": True,
        "index_path": metadata["provider_detail"]["index_path"],
        "input": {"changed_files": files, "timeout_seconds": timeout_seconds},
        "reports": reports,
        "provider_material_nodes": provider_material_nodes,
    }


def record_impact_evidence(
    conn: sqlite3.Connection,
    repo: Path,
    change_node_id: str,
    reports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not reports:
        return []
    metadata = impact_metadata(repo, entrypoint_used="gitnexus_cli_opt_in", provider_invoked=True)
    provider_name = str(metadata["provider_detail"]["name"])
    payload = {
        **metadata,
        "status": "executed" if all(int(report.get("exit_code") or 0) == 0 for report in reports) else "failed",
        "reports": reports,
    }
    payload_dir = repo / ".shujuan" / "provider"
    payload_dir.mkdir(parents=True, exist_ok=True)
    payload_path = payload_dir / f"{new_id('provider_payload')}_gitnexus_impact.json"
    payload_path.write_text(json_dumps(payload), encoding="utf-8")
    captured = capture_artifact_file(repo, payload_path, prefix="provider")
    run_node_id = create_node(
        conn,
        "provider_run",
        "provider run: GitNexus",
        payload["status"],
        {
            "provider": provider_name,
            "contract_version": PROVIDER_CONTRACT_VERSION,
            "source": "exec_stop",
            "default_source": metadata["default_source"],
            "entrypoint_used": metadata["entrypoint_used"],
            "closure_evidence_boundary": metadata["closure_evidence_boundary"],
        },
    )
    provider_run_id = new_id("provider_run")
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO provider_runs
          (id, node_id, provider, contract_version, status, command, started_at, ended_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            provider_run_id,
            run_node_id,
            provider_name,
            PROVIDER_CONTRACT_VERSION,
            payload["status"],
            json_dumps(["gitnexus", "detect-changes", "--scope", "all", "--repo", "."]),
            timestamp,
            timestamp,
            json_dumps(
                {
                    "source": "exec_stop",
                    "change_set_node_id": change_node_id,
                    "default_source": metadata["default_source"],
                    "entrypoint_used": metadata["entrypoint_used"],
                    "provider_detail": metadata["provider_detail"],
                    "closure_evidence_boundary": metadata["closure_evidence_boundary"],
                }
            ),
        ),
    )
    artifact_node_id = create_node(
        conn,
        "provider_artifact",
        "provider artifact: GitNexus impact execution",
        captured.get("sha256"),
        {
            **captured,
            "provider": provider_name,
            "contract_version": PROVIDER_CONTRACT_VERSION,
            "default_source": metadata["default_source"],
            "entrypoint_used": metadata["entrypoint_used"],
            "closure_evidence_boundary": metadata["closure_evidence_boundary"],
        },
    )
    provider_artifact_id = new_id("provider_artifact")
    conn.execute(
        """
        INSERT INTO provider_artifacts
          (id, run_id, node_id, path, capture_ref, sha256, content_type, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            provider_artifact_id,
            provider_run_id,
            artifact_node_id,
            captured.get("original_path"),
            captured["capture_ref"],
            captured["sha256"],
            "application/json",
            now_iso(),
            json_dumps(
                {
                    "source": "exec_stop",
                    "size": captured.get("size"),
                    "provider_detail": metadata["provider_detail"],
                    "closure_evidence_boundary": metadata["closure_evidence_boundary"],
                }
            ),
        ),
    )
    create_edge(
        conn,
        artifact_node_id,
        "DERIVED_FROM",
        change_node_id,
        reason="Optional provider artifact is derived from the captured change set.",
        confidence=0.8,
        created_by="provider",
    )
    provider_material_nodes: list[dict[str, Any]] = []
    for report in reports:
        report_path = report.get("report_path")
        fact_node_id = create_node(
            conn,
            "provider_fact",
            "GitNexus impact execution",
            f"exit_code={report.get('exit_code')}",
            {
                "provider": provider_name,
                "changed_files": report.get("changed_files") or [],
                "exit_code": report.get("exit_code"),
                "report_path": report_path,
                "stdout": report.get("stdout"),
                "stderr": report.get("stderr"),
                "classification": "provider_hypothesis",
            },
        )
        semantic_item_id = create_semantic_item(
            conn,
            fact_node_id,
            "provider_fact",
            state=PRODUCT_BACKLOG_STATE,
            source_node=artifact_node_id,
            scope_node=change_node_id,
            event_type="provider_imported",
            reason="Optional provider output is trace material only and cannot close acceptance checks directly.",
            props={"classification": "provider_hypothesis", "provider": provider_name},
        )
        provider_fact_id = new_id("provider_fact")
        conn.execute(
            """
            INSERT INTO provider_facts
              (id, run_id, artifact_id, node_id, external_id, fact_type, summary, confidence, provenance, classification, mapped_node_id, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider_fact_id,
                provider_run_id,
                provider_artifact_id,
                fact_node_id,
                ",".join(str(item) for item in (report.get("changed_files") or [])),
                "impact_execution",
                f"GitNexus impact execution exit_code={report.get('exit_code')}",
                0.8,
                json_dumps({"report_path": report_path, "source": "exec_stop"}),
                "provider_hypothesis",
                None,
                now_iso(),
                json_dumps({"stdout": report.get("stdout"), "stderr": report.get("stderr")}),
            ),
        )
        create_edge(
            conn,
            change_node_id,
            "HAS_IMPACT_FACT",
            fact_node_id,
            reason="Optional GitNexus execution is recorded as provider_hypothesis material.",
            confidence=0.8,
            created_by="provider",
        )
        create_edge(
            conn,
            fact_node_id,
            "DERIVED_FROM",
            artifact_node_id,
            reason="Provider fact derives from the structured provider artifact.",
            confidence=0.8,
            created_by="provider",
        )
        provider_material_nodes.append(
            {
                "node_id": fact_node_id,
                "type": "provider_fact",
                "provider_fact_id": provider_fact_id,
                "semantic_item_id": semantic_item_id,
                "classification": "provider_hypothesis",
                "changed_files": report.get("changed_files") or [],
            }
        )
    return provider_material_nodes


def evidence_stop_check(conn: sqlite3.Connection, *, endpoint_name: str | None = None) -> dict[str, Any]:
    warnings: list[str] = []
    scope_mode = "global_legacy"
    if endpoint_name:
        endpoint = query_endpoint(conn, endpoint_name)
        scope = endpoint_scope_facts(conn, endpoint)
        if not endpoint["root_node_id"]:
            warnings.append("Endpoint has no root_node_id; stop check is scoped to the endpoint and cannot assess task/check completion.")
            mandatory_open = []
            open_checks = []
            scope_mode = "endpoint_without_root"
        elif not scope["contract"]:
            warnings.append("Endpoint root is not a scope_contract; stop check is scoped to the endpoint and found no task/check contract.")
            mandatory_open = []
            open_checks = []
            scope_mode = "endpoint_without_scope_contract"
        else:
            mandatory_open = [row for row in scope["tasks"] if row["is_mandatory"] and row["closed_by_node_id"] is None]
            open_checks = [row for row in scope["checks"] if row["closed_by_node_id"] is None]
            scope_mode = "endpoint_scope"
    else:
        warnings.append("No endpoint supplied; stop check used legacy global scope.")
        mandatory_open = conn.execute(
            """
            SELECT t.id, t.node_id, t.task_body
            FROM tasks t
            JOIN nodes n ON n.id = t.node_id
            WHERE t.is_mandatory = 1 AND t.closed_by_node_id IS NULL
            ORDER BY n.created_at ASC, t.id ASC
            """
        ).fetchall()
        open_checks = conn.execute(
            """
            SELECT ac.id, ac.node_id, ac.task_id, ac.check_body, ac.expected_evidence_type
            FROM acceptance_checks ac
            JOIN tasks t ON t.id = ac.task_id
            JOIN nodes n ON n.id = ac.node_id
            WHERE ac.closed_by_node_id IS NULL
            ORDER BY n.created_at ASC, ac.id ASC
            """
        ).fetchall()
    mandatory_items = [row_to_dict(row) for row in mandatory_open]
    check_items = [row_to_dict(row) for row in open_checks]
    can_assess_completion = scope_mode in {"endpoint_scope", "global_legacy"}
    can_claim_complete = can_assess_completion and not mandatory_open and not open_checks
    return {
        "endpoint": endpoint_name,
        "scope_mode": scope_mode,
        "warnings": warnings,
        "mandatory_tasks_open": mandatory_items,
        "acceptance_checks_open": check_items,
        "mandatory_task_count": len(mandatory_items),
        "open_acceptance_count": len(check_items),
        "can_claim_complete": can_claim_complete,
        "must_not_claim_complete": not can_claim_complete,
        "rule": "Completion claims require endpoint-scoped mandatory tasks and acceptance checks to be closed with evidence nodes.",
        "report": render_stop_check_report(
            mandatory_items,
            check_items,
            can_claim_complete,
            endpoint_name=endpoint_name,
            scope_mode=scope_mode,
            warnings=warnings,
        ),
    }


def render_stop_check_report(
    mandatory_tasks_open: list[dict[str, Any]],
    acceptance_checks_open: list[dict[str, Any]],
    can_claim_complete: bool,
    *,
    endpoint_name: str | None = None,
    scope_mode: str = "global_legacy",
    warnings: list[str] | None = None,
) -> str:
    lines = ["Stop check:"]
    if endpoint_name:
        lines.append(f"- Endpoint scope: {endpoint_name} ({scope_mode})")
    else:
        lines.append(f"- Scope: {scope_mode}")
    for warning in warnings or []:
        lines.append(f"- Warning: {warning}")
    if can_claim_complete:
        lines.append("- All scoped mandatory tasks and acceptance checks are closed by evidence nodes.")
    else:
        lines.append("- Completion cannot be claimed yet for this scope.")
        if mandatory_tasks_open:
            lines.append("- Open scoped mandatory tasks:")
            for task in mandatory_tasks_open:
                lines.append(f"  - {task['id']}: {task['task_body']}")
        if acceptance_checks_open:
            lines.append("- Open scoped acceptance checks:")
            for check in acceptance_checks_open:
                lines.append(f"  - {check['id']} ({check['expected_evidence_type']}): {check['check_body']}")
        lines.append("- Next action: close checks with evidence or record scope_change/defer/assumption/unresolved evidence.")
    return "\n".join(lines)


def upsert_endpoint_body(
    conn: sqlite3.Connection,
    *,
    endpoint_name: str,
    body: str | None,
    description: str | None = None,
    root_node: str | None = None,
    from_node: str | None = None,
    body_props: dict[str, Any] | None = None,
) -> dict[str, Any]:
    endpoint = conn.execute("SELECT * FROM endpoints WHERE name = ?", (endpoint_name,)).fetchone()
    if endpoint:
        endpoint_id = endpoint["id"]
        endpoint_node_id = endpoint["node_id"]
        updates = []
        params: list[Any] = []
        if description is not None:
            updates.append("description = ?")
            params.append(description)
            conn.execute("UPDATE nodes SET summary = ?, updated_at = ? WHERE id = ?", (description, now_iso(), endpoint_node_id))
        if root_node is not None:
            require_node(conn, root_node, "endpoint root node")
            updates.append("root_node_id = ?")
            params.append(root_node)
            create_edge(conn, endpoint_node_id, "ROOTS_AT", root_node, reason="Endpoint root node updated.")
        if updates:
            params.append(endpoint_id)
            conn.execute(f"UPDATE endpoints SET {', '.join(updates)} WHERE id = ?", params)
    else:
        if root_node is not None:
            require_node(conn, root_node, "endpoint root node")
        endpoint_node_id = create_node(conn, "endpoint", endpoint_name, description)
        endpoint_id = new_id("endpoint")
        conn.execute(
            """
            INSERT INTO endpoints
              (id, node_id, name, description, root_node_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (endpoint_id, endpoint_node_id, endpoint_name, description, root_node, now_iso()),
        )
        if root_node:
            create_edge(conn, endpoint_node_id, "ROOTS_AT", root_node, reason="Endpoint root node set at creation.")
    if body is None:
        current = conn.execute("SELECT current_body_id FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone()
        return {"endpoint_id": endpoint_id, "endpoint_body_id": current["current_body_id"], "node_id": endpoint_node_id}
    props = {"source_kind": "manual", **(body_props or {})}
    body_node_id = create_node(conn, "endpoint_body", f"{endpoint_name} body", body[:240], props)
    body_id = new_id("endpoint_body")
    conn.execute(
        """
        INSERT INTO endpoint_bodies
          (id, endpoint_id, node_id, body, created_from_node_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (body_id, endpoint_id, body_node_id, body, from_node, now_iso()),
    )
    conn.execute("UPDATE endpoints SET current_body_id = ? WHERE id = ?", (body_id, endpoint_id))
    create_edge(conn, body_node_id, "APPLIES_TO", endpoint_node_id, reason="Endpoint body is current body for endpoint.")
    if from_node:
        create_edge(conn, body_node_id, "DERIVED_FROM", from_node, reason="Endpoint closeout derived from run/change evidence.")
    return {"endpoint_id": endpoint_id, "endpoint_body_id": body_id, "node_id": body_node_id}


def render_endpoint_closeout_body(
    *,
    run_id: str,
    change_result: dict[str, Any],
    evidence_links: dict[str, Any],
    stop_check: dict[str, Any],
    final_report: str | None,
    summary: str | None,
) -> str:
    file_count = len(change_result.get("files", []))
    implementation_count = len(change_result.get("implementation_files", []))
    provider_runtime_count = len(change_result.get("provider_runtime_files", []))
    ignored_runtime_count = len(change_result.get("ignored_runtime_files", []))
    linked_tasks = len(evidence_links.get("tasks", []))
    linked_checks = len(evidence_links.get("acceptance_checks", []))
    lines = [
        f"Run closeout: {run_id}",
        "",
        f"Summary: {summary or final_report or 'No semantic summary provided.'}",
        f"Change set: {change_result.get('change_set_id')} ({file_count} file change records)",
        f"File lanes: {implementation_count} implementation, {provider_runtime_count} provider runtime, {ignored_runtime_count} ignored runtime",
        f"Evidence links: {linked_tasks} task link(s), {linked_checks} acceptance check link(s)",
        "",
        stop_check["report"],
    ]
    return "\n".join(lines).rstrip() + "\n"


def closeout_endpoint(
    conn: sqlite3.Connection,
    *,
    endpoint_name: str,
    endpoint_body: str | None,
    endpoint_description: str | None,
    run_id: str,
    change_result: dict[str, Any],
    evidence_links: dict[str, Any],
    stop_check: dict[str, Any],
    final_report: str | None,
    summary: str | None,
) -> dict[str, Any]:
    generated = render_endpoint_closeout_body(
        run_id=run_id,
        change_result=change_result,
        evidence_links=evidence_links,
        stop_check=stop_check,
        final_report=final_report,
        summary=summary,
    )
    if endpoint_body:
        body = endpoint_body.rstrip() + "\n\n" + generated
        mode = "agent_body_plus_script_stop_check"
    else:
        body = generated
        mode = "script_generated_stop_check"
    result = upsert_endpoint_body(
        conn,
        endpoint_name=endpoint_name,
        body=body,
        description=endpoint_description,
        from_node=change_result["change_set_node_id"],
        body_props={
            "source_kind": "closeout",
            "generated_by": "exec_stop",
            "generated_at": now_iso(),
            "projection_hash": None,
            "run_id": run_id,
            "change_set_node_id": change_result["change_set_node_id"],
        },
    )
    return {**result, "endpoint": endpoint_name, "mode": mode}





























FOLDED_SOURCE_EDGE_TYPES = {
    "DERIVED_FROM",
    "VALIDATED_BY",
    "APPLIES_TO",
    "IMPLEMENTS",
    "EXECUTES",
    "DECOMPOSES_TO",
    "PRODUCES",
    "HAS_IMPACT_FACT",
    "HAS_IMPACT_ARTIFACT",
}








VIEW_VISUALS = {
    "task": {"color": "#f59e0b", "shape": "rect", "state": "active"},
    "acceptance_check": {"color": "#ef4444", "shape": "diamond", "state": "open"},
    "unresolved_question": {"color": "#f97316", "shape": "hexagon", "state": "active"},
    "agent_run": {"color": "#38bdf8", "shape": "circle", "state": "execution"},
    "discussion_segment": {"color": "#a78bfa", "shape": "round-rect", "state": "discussion"},
    "audit_finding": {"color": "#fb7185", "shape": "triangle", "state": "audit"},
    "evidence": {"color": "#22c55e", "shape": "circle", "state": "evidence"},
    "child_chain": {"color": "#60a5fa", "shape": "round-rect", "state": "chain"},
}
























































SEMANTIC_CANDIDATE_RULES: list[tuple[str, list[str]]] = [
    ("scope_contract", ["scope", "范围", "契约", "完整方案", "不得擅自降级", "non-downgrade"]),
    ("acceptance_check", ["acceptance", "验收", "evidence", "证据", "证明", "must pass", "通过"]),
    ("constraint", ["must not", "should not", "不得", "不能", "禁止", "不要", "不应"]),
    ("decision", ["decision", "决定", "原则", "canonical", "采用", "选择"]),
    ("task", ["task", "任务", "implement", "实现", "新增", "修复", "补齐", "执行"]),
    ("term", ["term", "术语", "定义", "means", "不是", "是指"]),
    ("assumption", ["assumption", "假设", "默认认为", "基于假设"]),
    ("unresolved_question", ["unresolved", "未解决", "问题", "unclear", "待确认", "冲突"]),
    ("scope_change", ["scope change", "范围变更", "缩小范围", "降级", "defer", "推迟"]),
    ("requirement", ["requirement", "需求", "必须", "should", "需要", "目标"]),
]

























































































































































































def build_parser() -> argparse.ArgumentParser:
    parser = ShujuanArgumentParser(
        prog="shujuan",
        description=(
            "Repo-local Agent governance for project-owned PostgreSQL. "
            "Normal setup is `python -m shujuan init --postgres-dev`; SQLite runtime/write and SQLite cutover paths are legacy-disabled."
        ),
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root. Defaults to current directory.")
    parser.add_argument("--database-url", help="PostgreSQL database URL. sqlite:/// URLs are rejected.")
    parser.add_argument("--db-profile", help="Database profile: postgres, postgresql, or product. SQLite profiles are rejected.")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=ShujuanArgumentParser)

    init_handlers = build_init_handlers(globals())
    register_init(sub, handlers=init_handlers)

    migrate_handlers = build_migrate_handlers(globals())
    register_migrate(sub, handlers=migrate_handlers)

    postgres_dev_handlers = build_postgres_dev_handlers(globals())
    register_postgres_dev(sub, handlers=postgres_dev_handlers)

    endpoint_handlers = build_endpoint_handlers(globals())
    tasking_handlers = build_tasking_handlers(globals())
    graph_handlers = build_graph_handlers(globals())
    audit_handlers = build_audit_handlers(globals())
    register_db(sub, handlers=endpoint_handlers)

    adapter_handlers = build_adapter_handlers(globals())
    register_adapter(sub, handlers=adapter_handlers)

    provider_handlers = build_provider_handlers(globals())
    register_provider(sub, handlers=provider_handlers)

    capture_handlers = build_capture_handlers(globals())
    register_capture(sub, handlers=capture_handlers)

    route_handlers = build_route_handlers(globals())
    register_route(sub, handlers=route_handlers)

    discuss_handlers = build_discuss_handlers(globals())
    register_discuss(sub, handlers=discuss_handlers)

    execution_handlers = build_execution_handlers(globals())
    workflow_handlers = build_workflow_handlers(globals(), exec_stop_handler=execution_handlers["stop"])
    register_workflows(sub, handlers=workflow_handlers)

    delegate_boundary = build_delegate_boundary(globals())
    register_delegate(
        sub,
        collaboration_modes=delegate_boundary.collaboration_modes,
        delegate_lifecycle_states=delegate_boundary.delegate_lifecycle_states,
        handlers=delegate_boundary.handlers,
    )

    review_handlers = build_review_handlers(globals())
    register_review(sub, handlers=review_handlers)

    register_execution(sub, handlers=execution_handlers)
    register_diff(sub, handlers=execution_handlers)

    register_center(sub, handlers=endpoint_handlers)
    register_endpoint(sub, handlers=endpoint_handlers)
    register_audit(sub, handlers=audit_handlers)
    register_tasking(sub, handlers=tasking_handlers, semantic_state_type=semantic_state_arg)

    evidence_handlers = build_evidence_handlers(globals())
    register_evidence(sub, handlers=evidence_handlers, state_type=evidence_state_arg)

    register_graph(sub, handlers=graph_handlers)
    register_export(sub, handlers=endpoint_handlers)

    workbench_handlers = build_workbench_handlers(globals())
    register_workbench(sub, handlers=workbench_handlers)

    report_handlers = build_report_handlers(globals())
    register_report(sub, handlers=report_handlers)

    recall_handlers = build_recall_handlers(globals())
    register_recall(sub, handlers=recall_handlers)

    schema_stewardship_handlers = build_schema_stewardship_handlers(globals())
    register_schema_stewardship(sub, handlers=schema_stewardship_handlers)

    plan_to_db_handlers = build_plan_to_db_handlers(globals())
    register_plan_to_db(sub, handlers=plan_to_db_handlers)

    artifact_index_handlers = build_artifact_index_handlers(globals())
    register_artifact_index(sub, handlers=artifact_index_handlers)

    install_layout_handlers = build_install_layout_handlers(globals())
    register_install_layout(sub, handlers=install_layout_handlers)

    register_ready(sub, handlers=endpoint_handlers)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        if getattr(args, "database_url", None):
            if args.database_url.lower().startswith("sqlite:///"):
                raise StructuredCliError(
                    "sqlite_database_url_disabled",
                    "SQLite database URLs are disabled; set --database-url to postgresql://...",
                    read_only=True,
                    safe_next_action="Use a PostgreSQL database URL or the project postgres-dev runtime.",
                )
            os.environ["SHUJUAN_DATABASE_URL"] = args.database_url
        if getattr(args, "db_profile", None):
            normalized_profile = args.db_profile.strip().lower()
            if normalized_profile == "sqlite":
                raise StructuredCliError(
                    "sqlite_db_profile_disabled",
                    "--db-profile sqlite is disabled; shujuan now requires PostgreSQL.",
                    read_only=True,
                    safe_next_action="Use --db-profile postgres, postgresql, or product.",
                )
            if normalized_profile not in {"postgres", "postgresql", "product"}:
                raise StructuredCliError(
                    "unsupported_db_profile",
                    f"unsupported --db-profile: {args.db_profile}",
                    read_only=True,
                    safe_next_action="Use --db-profile postgres, postgresql, or product.",
                )
            os.environ["SHUJUAN_DB_PROFILE"] = args.db_profile
        return args.func(args)
    except BaseException as exc:
        if isinstance(exc, (ShujuanUsageError, StructuredRuntimeError)):
            print_json(exc.payload())
            return 1
        if isinstance(exc, SystemExit):
            raise
        if is_database_constraint_error(exc):
            print_error("database constraint violation: referenced record is missing or invalid")
            print_json(
                json_error_payload(
                    "database_constraint_violation",
                    "database constraint violation: referenced record is missing or invalid",
                    read_only=False,
                    safe_next_action="Check referenced endpoint/task/check/node ids, then rerun the command.",
                )
            )
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
