from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from typing import Any

from ..services.command_effects import work_close_dry_run_effects
from ..services.dependencies import RuntimeDeps
from ..services.sovereignty_gate import explicit_no_governance_reasons, no_governance_payload


WorkflowHandler = Callable[[argparse.Namespace], int]


_WORK_CLOSE_EXPECTED_EVIDENCE_TYPE_MAP = {
    "diff": {"change_set"},
    "change_set": {"change_set"},
    "test": {"test_result"},
    "test_result": {"test_result"},
    "artifact": {"artifact"},
    "file": {"artifact"},
    "doc_update": {"artifact", "change_set"},
    "user_confirmation": {"user_confirmation"},
    "confirmation": {"user_confirmation"},
}
_WORK_CLOSE_EVIDENCE_NODE_TYPES = {"change_set", "test_result", "artifact", "user_confirmation"}


WORKFLOW_DEPENDENCY_KEYS = (
    "connect",
    "create_discussion_capture",
    "create_edge",
    "create_node",
    "current_head",
    "exec_start_preflight",
    "record_preflight_assumption",
    "capture_snapshot",
    "active_run_path",
    "read_arg_or_stdin",
    "resolve_alias",
    "resolve_endpoint_identifier",
    "query_endpoint",
    "require_node",
    "mode_contract_payload",
    "normalize_mode",
    "mode_gate_warnings",
    "print_json",
    "new_id",
    "now_iso",
    "json_dumps",
    "split_cli_id_text",
    "parse_forbidden_substitute_arg",
    "parse_predicate_scoped_value",
    "append_unique_metadata_value",
    "parse_work_split_link",
    "insert_task_predicate_link",
    "endpoint_source_nondowngrade_audit",
    "endpoint_agcp_predicate_rows",
    "endpoint_forbidden_substitute_rows",
    "endpoint_report_payload",
    "endpoint_work_chain_rows",
    "predicate_link_rows",
    "row_to_dict",
    "require_evidence_node",
    "existing_check_ids_closed_by_evidence",
    "link_evidence_to_checks",
    "rows_for_checks",
    "validate_check_evidence_type",
    "validate_test_result_predicate_coverage",
    "acceptance_template_for_mode",
    "endpoint_agcp_doctor_findings",
    "full_closeout_gate",
    "exec_stop_handler",
)


def _workflow_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    return RuntimeDeps(deps).require(*WORKFLOW_DEPENDENCY_KEYS)


def build_workflow_handlers(
    deps: Mapping[str, Any],
    *,
    exec_stop_handler: WorkflowHandler | None = None,
) -> dict[str, WorkflowHandler]:
    workflow_deps = dict(deps)
    if exec_stop_handler is not None:
        workflow_deps["exec_stop_handler"] = exec_stop_handler
    globals().update(_workflow_dependencies(workflow_deps))
    return {
        "start": cmd_work_start,
        "close": cmd_work_close,
        "current": cmd_work_current,
        "acceptance_template": cmd_work_acceptance_template,
        "intake": cmd_work_intake,
        "split": cmd_work_split,
        "focus": cmd_work_focus,
        "audit_source": cmd_work_audit_source,
        "prove": cmd_work_prove,
    }


def _expected_evidence_allows(expected: str | None, evidence_type: str) -> bool:
    if not expected:
        return evidence_type in _WORK_CLOSE_EVIDENCE_NODE_TYPES
    normalized = str(expected).strip().lower().replace("-", "_")
    return evidence_type in _WORK_CLOSE_EXPECTED_EVIDENCE_TYPE_MAP.get(normalized, {normalized})


def _compact_row(row: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: row[key] for key in keys if key in row.keys()}


def _table_exists(conn: Any, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?",
            (name,),
        ).fetchone()
    )


def _contracted_workflow_payload(
    *,
    args: argparse.Namespace,
    mode: str,
    endpoint_name: str,
    command: str,
    replacement_path: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "ok": False,
        "workflow": args.workflow_kind,
        "mode": mode,
        "endpoint": endpoint_name,
        "command": command,
        "status": "contracted_legacy_command_disabled",
        "diagnostic_only": True,
        "material_only": True,
        "db_writes": 0,
        "replacement_path": replacement_path,
        "next_action": "create explicit task/check/evidence rows or record semantic scope/defer/unresolved decision through controller authority",
        "closure_claim": False,
        "exit_code_policy": "nonzero_not_success",
    }
    if extra:
        payload.update(extra)
    return payload


def _work_close_task_rows(conn: Any, task_ids: list[str]) -> list[Any]:
    rows = []
    for task_id in task_ids:
        row = conn.execute("SELECT id, node_id, closed_by_node_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise SystemExit(f"task not found: {task_id}")
        rows.append(row)
    return rows


def _work_close_task_node_rows(conn: Any, task_node_ids: list[str]) -> list[dict[str, Any]]:
    rows = []
    for task_node_id in task_node_ids:
        row = conn.execute("SELECT id, node_id, closed_by_node_id FROM tasks WHERE node_id = ?", (task_node_id,)).fetchone()
        if not row:
            raise SystemExit(f"task node not found: {task_node_id}")
        item = _compact_row(row, ("id", "node_id", "closed_by_node_id"))
        item["input"] = task_node_id
        rows.append(item)
    return rows


def _open_check_ids_for_task(conn: Any, task_id: str) -> list[str]:
    return [
        str(row["id"])
        for row in conn.execute(
            """
            SELECT id
            FROM acceptance_checks
            WHERE task_id = ? AND closed_by_node_id IS NULL
            ORDER BY id ASC
            """,
            (task_id,),
        ).fetchall()
    ]


def _active_blocker_items(active_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    blockers = []
    readiness = (active_report or {}).get("readiness") or {}
    reason_code = readiness.get("blocking_reason_code")
    reason = readiness.get("blocking_reason")
    next_action = readiness.get("next_safe_action")
    for ref in (readiness.get("visible_blocking_refs") or []) + (readiness.get("hidden_blocking_refs") or []):
        blockers.append(
            {
                "kind": ref.get("kind"),
                "ref": ref.get("ref"),
                "id": ref.get("ref"),
                "hidden": bool(ref.get("hidden")),
                "detail_ref": ref.get("detail_ref"),
                "label": ref.get("summary"),
                "reason_code": reason_code,
                "reason": reason,
                "next_action": next_action,
            }
        )
    if blockers:
        return blockers
    obligations = (active_report or {}).get("active_obligations") or {}
    for bucket, items in obligations.items():
        for item in items or []:
            blockers.append(
                {
                    "bucket": bucket,
                    "id": item.get("id") or item.get("node_id") or item.get("task_id") or item.get("check_id"),
                    "node_id": item.get("node_id"),
                    "label": item.get("label") or item.get("body") or item.get("summary"),
                }
            )
    return blockers


def _work_close_command(endpoint_name: str | None, mode: str, args: argparse.Namespace, *extra: str) -> str:
    endpoint = endpoint_name or "<endpoint>"
    base = ["python -m shujuan", args.workflow_kind, "close", "--apply", "--endpoint", endpoint, "--mode", mode]
    base.extend(extra)
    return " ".join(base)


def _item_get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    if hasattr(item, "keys") and key in item.keys():
        return item[key]
    return default


def _expected_evidence_command(check: Any, endpoint_name: str | None, mode: str, args: argparse.Namespace) -> str:
    check_id = str(_item_get(check, "id") or "<check_id>")
    expected = str(_item_get(check, "expected_evidence_type") or "").strip().lower().replace("-", "_")
    allowed = _WORK_CLOSE_EXPECTED_EVIDENCE_TYPE_MAP.get(expected, {expected} if expected else _WORK_CLOSE_EVIDENCE_NODE_TYPES)
    if "test_result" in allowed:
        return f"python -m shujuan evidence test-result --check {check_id} --close-check -- <test command>"
    if "artifact" in allowed:
        return f"python -m shujuan evidence artifact --path <file> --check {check_id} --close-check"
    if "user_confirmation" in allowed:
        return f'python -m shujuan evidence user-confirmation --body "<confirmation>" --check {check_id} --close-check'
    return _work_close_command(endpoint_name, mode, args, "--check", check_id, "--close-check")


def _append_proposed_command(
    commands: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    *,
    action: str,
    command: str,
    reason: str,
    ref: str | None = None,
    blocks_closeout: bool = True,
) -> None:
    key = (action, ref or "", command)
    if key in seen:
        return
    seen.add(key)
    commands.append(
        {
            "action": action,
            "ref": ref,
            "command": command,
            "reason": reason,
            "blocks_closeout": blocks_closeout,
        }
    )


def _checks_closed_task_open_warnings(conn: Any | None, readiness: dict[str, Any] | None) -> list[dict[str, Any]]:
    warnings = []
    for warning in (readiness or {}).get("warnings") or []:
        if warning.get("code") != "checks_closed_task_open":
            continue
        item = dict(warning)
        if conn and item.get("closed_check_ids"):
            rows = rows_for_checks(conn, [str(check_id) for check_id in item["closed_check_ids"]])
            item["closed_checks"] = [
                _compact_row(row, ("id", "node_id", "task_id", "expected_evidence_type", "closed_by_node_id"))
                for row in rows
            ]
        warnings.append(item)
    return warnings


def _work_closeout_worksheet(
    *,
    conn: Any | None,
    endpoint_name: str | None,
    args: argparse.Namespace,
    mode: str,
    gate_matrix: dict[str, Any],
    active_report: dict[str, Any] | None,
    check_rows: list[Any],
) -> dict[str, Any]:
    readiness = (active_report or {}).get("readiness") or {}
    next_entry = (active_report or {}).get("next_valid_entry_point") or {}
    check_by_id = {str(check["id"]): check for check in check_rows}
    proposed_commands: list[dict[str, Any]] = []
    proposed_seen: set[tuple[str, str, str]] = set()
    missing_actions: list[dict[str, Any]] = []

    for item in gate_matrix["missing_evidence"]:
        check_id = str(item["check_id"])
        check = check_by_id.get(check_id, {"id": check_id, "expected_evidence_type": item.get("expected_evidence_type")})
        command = _expected_evidence_command(check, endpoint_name, mode, args)
        action = {
            "kind": "provide_missing_evidence",
            "check_id": check_id,
            "expected_evidence_type": _item_get(check, "expected_evidence_type"),
            "reason": item.get("reason"),
            "command": command,
        }
        missing_actions.append(action)
        _append_proposed_command(
            proposed_commands,
            proposed_seen,
            action="provide_missing_evidence",
            ref=check_id,
            command=command,
            reason=item.get("reason") or "Acceptance check needs matching closure evidence.",
        )

    for item in gate_matrix["expected_evidence_mismatches"]:
        check_id = str(item["check_id"])
        check = check_by_id.get(check_id, {"id": check_id, "expected_evidence_type": item.get("expected_evidence_type")})
        command = _expected_evidence_command(check, endpoint_name, mode, args)
        action = {
            "kind": "provide_matching_evidence",
            "check_id": check_id,
            "expected_evidence_type": item.get("expected_evidence_type"),
            "prospective_evidence_type": item.get("prospective_evidence_type"),
            "command": command,
        }
        missing_actions.append(action)
        _append_proposed_command(
            proposed_commands,
            proposed_seen,
            action="provide_matching_evidence",
            ref=check_id,
            command=command,
            reason="Prospective work close evidence does not match the check contract.",
        )

    for blocker in gate_matrix["task_closure_blockers"]:
        open_check_rows = rows_for_checks(conn, [str(check_id) for check_id in blocker["open_check_ids"]]) if conn else []
        open_by_id = {str(check["id"]): check for check in open_check_rows}
        for check_id in blocker["open_check_ids"]:
            check = open_by_id.get(str(check_id), {"id": check_id})
            command = _expected_evidence_command(check, endpoint_name, mode, args)
            action = {
                "kind": "close_or_rescope_open_check",
                "task_id": blocker["task_id"],
                "check_id": str(check_id),
                "command": command,
            }
            missing_actions.append(action)
            _append_proposed_command(
                proposed_commands,
                proposed_seen,
                action="close_or_rescope_open_check",
                ref=str(check_id),
                command=command,
                reason=f"Task {blocker['task_id']} cannot close while this acceptance check remains open.",
            )

    checks_closed_task_open = _checks_closed_task_open_warnings(conn, readiness)
    for warning in checks_closed_task_open:
        closed_checks = warning.get("closed_checks") or []
        first_closed_check = closed_checks[0] if closed_checks else {}
        evidence_node_id = first_closed_check.get("closed_by_node_id") or "<evidence_node_id>"
        check_id = str(first_closed_check.get("id") or (warning.get("closed_check_ids") or ["<check_id>"])[0])
        command = f"python -m shujuan acceptance close --check {check_id} --evidence-node {evidence_node_id} --close-task"
        action = {
            "kind": "close_or_rescope_task_with_closed_checks",
            "task_id": warning.get("task_id"),
            "closed_check_ids": warning.get("closed_check_ids") or [],
            "command": command,
        }
        missing_actions.append(action)
        _append_proposed_command(
            proposed_commands,
            proposed_seen,
            action="close_or_rescope_task_with_closed_checks",
            ref=warning.get("task_id"),
            command=command,
            reason=warning.get("recommendation") or "Task has closed checks but remains open.",
        )

    for command in next_entry.get("commands") or []:
        _append_proposed_command(
            proposed_commands,
            proposed_seen,
            action="inspect_or_resolve_active_blockers",
            ref=endpoint_name,
            command=command,
            reason=readiness.get("blocking_reason") or next_entry.get("recommendation") or "Inspect endpoint blockers before closeout.",
            blocks_closeout=bool(gate_matrix["active_blockers"]),
        )
    if mode == "full" and endpoint_name:
        for action, command in [
            ("verify_evidence", f"python -m shujuan evidence verify --endpoint {endpoint_name}"),
            ("strict_closeout_doctor", f"python -m shujuan endpoint doctor {endpoint_name} --strict-closeout"),
        ]:
            _append_proposed_command(
                proposed_commands,
                proposed_seen,
                action=action,
                ref=endpoint_name,
                command=command,
                reason="Controller closeout verification step for Full mode.",
                blocks_closeout=False,
            )

    stop_reasons = set(gate_matrix["stop_reasons"])
    if checks_closed_task_open:
        stop_reasons.add("checks_closed_task_open")
    return {
        "version": "activation.v7.closeout_worksheet",
        "canonical": True,
        "controller_default": True,
        "dry_run_non_mutating": True,
        "endpoint": endpoint_name,
        "readiness": {
            "schema": readiness.get("schema"),
            "closeout_ready": readiness.get("closeout_ready"),
            "blocking_reason_code": readiness.get("blocking_reason_code"),
            "blocking_reason": readiness.get("blocking_reason"),
            "next_safe_action": readiness.get("next_safe_action"),
            "visible_blocking_refs": readiness.get("visible_blocking_refs") or [],
            "hidden_blocking_refs": readiness.get("hidden_blocking_refs") or [],
        },
        "missing_evidence": gate_matrix["missing_evidence"],
        "expected_evidence_mismatches": gate_matrix["expected_evidence_mismatches"],
        "checks_closed_task_open_warnings": checks_closed_task_open,
        "task_closure_blockers": gate_matrix["task_closure_blockers"],
        "active_blockers": gate_matrix["active_blockers"],
        "predicate_coverage_gaps": gate_matrix["predicate_coverage_gaps"],
        "missing_actions": missing_actions,
        "proposed_commands": proposed_commands,
        "apply_actions": gate_matrix["apply_actions"],
        "stop_reasons": sorted(stop_reasons),
        "blocks_closeout": bool(stop_reasons),
        "notes": [
            "Dry-run is a controller worksheet only; it does not close checks, close tasks, refresh endpoints, or write governance facts.",
            "Provider facts, reviewer accepts, and projection outputs are material only; they are not accepted evidence nodes for closure.",
        ],
    }


def _work_close_gate_matrix(
    conn: Any | None,
    *,
    endpoint_name: str | None,
    args: argparse.Namespace,
    mode: str,
    active: dict[str, Any] | None,
    agcp_visibility: dict[str, Any] | None,
    active_report: dict[str, Any] | None,
) -> dict[str, Any]:
    would_create_change_set = bool(active and mode in {"light", "standard", "full"})
    check_rows = rows_for_checks(conn, args.check) if conn and args.check else []
    task_rows = _work_close_task_rows(conn, args.task) if conn and args.task else []
    task_node_rows = _work_close_task_node_rows(conn, args.task_node) if conn and args.task_node else []
    target_check_ids = {str(check["id"]) for check in check_rows if args.close_check and not check["closed_by_node_id"]}
    target_task_ids = {str(row["id"]) for row in task_rows}
    target_task_ids.update(str(row["id"]) for row in task_node_rows)
    if args.close_task:
        target_task_ids.update(str(check["task_id"]) for check in check_rows)

    accepted_evidence_nodes = []
    missing_evidence = []
    expected_mismatches = []
    for check in check_rows:
        if check["closed_by_node_id"]:
            accepted_evidence_nodes.append(
                {
                    "check_id": check["id"],
                    "evidence_node_id": check["closed_by_node_id"],
                    "source": "existing_closed_check",
                }
            )
            continue
        if args.close_check and not would_create_change_set:
            missing_evidence.append(
                {
                    "check_id": check["id"],
                    "required": "change_set from work close --apply active run",
                    "reason": "no active Light/Standard/Full run is available to capture a change_set",
                }
            )
        if args.close_check and not _expected_evidence_allows(check["expected_evidence_type"], "change_set"):
            expected_mismatches.append(
                {
                    "check_id": check["id"],
                    "expected_evidence_type": check["expected_evidence_type"],
                    "prospective_evidence_type": "change_set",
                    "overridden": bool(args.override_evidence_type),
                    "blocks_without_override": not bool(args.override_evidence_type),
                }
            )

    predicate_gaps = []
    if agcp_visibility:
        for predicate_id in agcp_visibility.get("unmapped_hard_predicate_ids") or []:
            predicate_gaps.append({"kind": "unmapped_hard_predicate", "predicate_id": predicate_id})
        for check_id in agcp_visibility.get("closed_checks_missing_predicate_coverage_ids") or []:
            predicate_gaps.append({"kind": "closed_check_missing_predicate_coverage", "check_id": check_id})

    task_blockers = []
    for task_id in sorted(target_task_ids):
        open_check_ids = _open_check_ids_for_task(conn, task_id) if conn else []
        remaining_open = sorted(check_id for check_id in open_check_ids if check_id not in target_check_ids)
        if remaining_open:
            task_blockers.append({"task_id": task_id, "open_check_ids": remaining_open})

    active_blockers = _active_blocker_items(active_report)
    stop_reasons = []
    if missing_evidence:
        stop_reasons.append("missing_evidence")
    if any(item.get("blocks_without_override") for item in expected_mismatches):
        stop_reasons.append("expected_evidence_mismatch")
    if predicate_gaps:
        stop_reasons.append("predicate_coverage_gap")
    if task_blockers:
        stop_reasons.append("task_has_open_checks")
    if active_blockers:
        stop_reasons.append("active_blockers_present")

    return {
        "version": "activation.v6.closeout_gate_matrix",
        "dry_run_non_mutating": True,
        "endpoint": endpoint_name,
        "target_check_closures": [
            {
                **_compact_row(check, ("id", "node_id", "task_id", "expected_evidence_type", "closed_by_node_id")),
                "requested_closure": bool(args.close_check),
            }
            for check in check_rows
        ],
        "target_task_closures": [
            {**_compact_row(row, ("id", "node_id", "closed_by_node_id")), "requested_closure": bool(args.close_task)}
            for row in task_rows
        ]
        + [{**row, "requested_closure": bool(args.close_task)} for row in task_node_rows],
        "accepted_evidence_nodes": accepted_evidence_nodes,
        "prospective_evidence": {
            "type": "change_set",
            "source": "work close --apply captures the active run change_set",
            "available": would_create_change_set,
        },
        "missing_evidence": missing_evidence,
        "expected_evidence_mismatches": expected_mismatches,
        "predicate_coverage_gaps": predicate_gaps,
        "task_closure_blockers": task_blockers,
        "active_blockers": active_blockers,
        "would_do": {
            "create_change_set": would_create_change_set,
            "close_checks": bool(args.check and args.close_check),
            "close_tasks": bool((args.task or args.task_node or args.check) and args.close_task),
            "run_full_closeout_gate": bool(mode == "full" and endpoint_name),
        },
        "apply_actions": [
            action
            for action, enabled in (
                ("capture_change_set", would_create_change_set),
                ("link_target_checks", bool(args.check)),
                ("close_target_checks", bool(args.check and args.close_check)),
                ("close_target_tasks", bool((args.task or args.task_node or args.check) and args.close_task)),
                ("run_endpoint_doctor_strict_closeout", bool(mode == "full" and endpoint_name)),
                ("run_evidence_verify", bool(mode == "full" and endpoint_name)),
            )
            if enabled
        ],
        "stop_reasons": sorted(set(stop_reasons)),
        "notes": [
            "Provider facts, reviewer accepts, and projection outputs are material only; they are not accepted evidence nodes for closure.",
            f"Apply command: {args.workflow_kind} close --apply --endpoint <endpoint> --mode {mode}",
        ],
    }


WORKFLOW_HANDLER_KEYS = (
    "start",
    "close",
    "current",
    "acceptance_template",
    "intake",
    "split",
    "focus",
    "audit_source",
    "prove",
)


def _validate_handlers(handlers: Mapping[str, WorkflowHandler]) -> None:
    missing = [key for key in WORKFLOW_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"workflow command boundary is missing: {', '.join(missing)}")


def _register_workflow_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    workflow_kind: str,
    handlers: Mapping[str, WorkflowHandler],
) -> None:
    workflow_parser = subparsers.add_parser(name)
    workflow_sub = workflow_parser.add_subparsers(dest=f"{name}_command", required=True)

    start = workflow_sub.add_parser("start")
    start.add_argument("--endpoint")
    start.add_argument("--task")
    start.add_argument("--task-node")
    start.add_argument("--mode", default="standard")
    start.add_argument("--content")
    start.add_argument("--content-file", help="Read long prompt/content text from a UTF-8 file.")
    start.add_argument("--actor", default="user")
    start.add_argument("--session-id")
    start.add_argument("--agent-name", default="codex")
    start.add_argument("--model-name")
    start.add_argument("--source")
    start.add_argument("--title")
    start.add_argument("--label")
    start.add_argument("--summary")
    start.add_argument("--allow-preflight-warning", action="store_true")
    start.add_argument("--allow-reason")
    start.add_argument("--force", action="store_true")
    start.set_defaults(func=handlers["start"], workflow_kind=workflow_kind)

    close = workflow_sub.add_parser("close")
    close.add_argument("--endpoint")
    close.add_argument("--mode", default="standard")
    close.add_argument("--dry-run", action="store_true")
    close.add_argument("--apply", action="store_true")
    close.add_argument("--run")
    close.add_argument("--summary")
    close.add_argument("--summary-file", help="Read closeout summary text from a UTF-8 file.")
    close.add_argument("--final-report")
    close.add_argument("--impact", action="store_true", help="Explicitly run the optional bounded impact provider.")
    close.add_argument("--impact-timeout", type=int, default=30, help="Seconds per changed file for optional provider execution.")
    close.add_argument("--no-impact", action="store_true", help=argparse.SUPPRESS)
    close.add_argument("--endpoint-body")
    close.add_argument("--endpoint-description")
    close.add_argument("--task", action="append", default=[])
    close.add_argument("--task-node", action="append", default=[])
    close.add_argument("--check", action="append", default=[])
    close.add_argument("--close-check", action="store_true")
    close.add_argument("--close-task", action="store_true")
    close.add_argument("--override-evidence-type", action="store_true")
    close.add_argument("--override-closeout", action="store_true")
    close.add_argument("--override-reason")
    close.set_defaults(func=handlers["close"], workflow_kind=workflow_kind)

    current = workflow_sub.add_parser("current")
    current.set_defaults(func=handlers["current"], workflow_kind=workflow_kind)

    acceptance_template = workflow_sub.add_parser("acceptance-template")
    acceptance_template.add_argument("--mode", default="standard")
    acceptance_template.set_defaults(func=handlers["acceptance_template"], workflow_kind=workflow_kind)

    intake = workflow_sub.add_parser("intake")
    intake.add_argument("--endpoint", required=True)
    intake.add_argument("--source-node", required=True)
    intake.add_argument("--source-locator")
    intake.add_argument("--kind", default="source_plan")
    intake.add_argument("--text")
    intake.add_argument("--text-file", help="Read source promise text from a UTF-8 file.")
    intake.add_argument("--promise-id")
    intake.add_argument("--hardness", default="hard", choices=["hard", "soft", "optional"])
    intake.add_argument("--downgrade-policy", default="requires_user_scope_change")
    intake.add_argument("--predicate", action="append", default=[], help="PREDICATE_ID::claim or claim")
    intake.add_argument("--proof-required", action="append", default=["test_result"])
    intake.add_argument("--required-term", action="append", default=[], help="TERM or PREDICATE_ID::TERM that must survive into acceptance checks")
    intake.add_argument("--named-term", action="append", default=[], help="Named technology/product term that must not be generalized")
    intake.add_argument("--must-term", action="append", default=[], help="Must/include term that must remain visible in acceptance checks")
    intake.add_argument("--enumerated-item", action="append", default=[], help="Per-item list entry that must remain visible in acceptance checks")
    intake.add_argument("--forbidden-substitute", action="append", default=[], help="TEXT, PREDICATE_ID::TEXT, or PREDICATE_ID::TEXT::REASON")
    intake.add_argument("--mode", default="standard")
    intake.set_defaults(func=handlers["intake"], workflow_kind=workflow_kind)

    split = workflow_sub.add_parser("split")
    split.add_argument("--endpoint", required=True)
    split.add_argument("--name", required=True)
    split.add_argument("--chain-id")
    split.add_argument("--parent-chain")
    split.add_argument("--slice")
    split.add_argument("--mode", default="standard")
    split.add_argument("--link", action="append", default=[], help="TASK_ID::CHECK_ID::PREDICATE_ID[::RELATIONSHIP]")
    split.add_argument("--task", action="append", default=[])
    split.add_argument("--check", action="append", default=[])
    split.add_argument("--predicate", action="append", default=[])
    split.add_argument("--relationship", default="proves", choices=["implements", "proves", "guards", "negative_test"])
    split.set_defaults(func=handlers["split"], workflow_kind=workflow_kind)

    focus = workflow_sub.add_parser("focus")
    focus.add_argument("--endpoint", required=True)
    focus.add_argument("--work-chain")
    focus.set_defaults(func=handlers["focus"], workflow_kind=workflow_kind)

    audit_source = workflow_sub.add_parser("audit-source")
    audit_source.add_argument("--endpoint", required=True)
    audit_source.add_argument("--fail-on-findings", action="store_true")
    audit_source.set_defaults(func=handlers["audit_source"], workflow_kind=workflow_kind)

    prove = workflow_sub.add_parser("prove")
    prove.add_argument("--endpoint")
    prove.add_argument("--mode", default="standard")
    prove.add_argument("--evidence-node", required=True)
    prove.add_argument("--check", action="append", required=True)
    prove.add_argument("--dry-run", action="store_true")
    prove.add_argument("--apply", action="store_true")
    prove.add_argument("--close-check", action="store_true")
    prove.add_argument("--close-task", action="store_true")
    prove.add_argument("--override-evidence-type", action="store_true")
    prove.add_argument("--override-predicate-coverage", action="store_true")
    prove.add_argument("--elevated-predicate-coverage-override", action="store_true")
    prove.add_argument("--override-reason")
    prove.add_argument("--reason")
    prove.set_defaults(func=handlers["prove"], workflow_kind=workflow_kind)


def cmd_work_start(args: argparse.Namespace) -> int:
    mode = normalize_mode(args.mode)
    contract = mode_contract_payload(mode)
    repo = args.repo.resolve()
    content = read_arg_or_stdin(args.content, file_path=getattr(args, "content_file", None), label="content") if (args.content is not None or getattr(args, "content_file", None) is not None) else None
    intent_text = " ".join(str(value or "") for value in [content, args.summary, args.title, args.label])
    no_governance_reasons = explicit_no_governance_reasons(intent_text)
    if no_governance_reasons:
        print_json(
            no_governance_payload(
                command=f"{args.workflow_kind} start",
                content=intent_text,
                reasons=no_governance_reasons,
                contract=mode_contract_payload("no_governance"),
                workflow=args.workflow_kind,
            )
        )
        return 0
    mode_warnings = mode_gate_warnings(mode, intent_text)
    hard_mode_warnings = [warning for warning in mode_warnings if warning["code"] == "mode_friction_high_risk_light"]
    if hard_mode_warnings:
        raise SystemExit(
            "work start mode gate failed: "
            + "; ".join(warning["message"] for warning in hard_mode_warnings)
            + " Use Standard/Full or provide source-backed scope that lowers the risk."
        )
    if mode == "no_governance":
        print_json(
            {
                "ok": True,
                "workflow": args.workflow_kind,
                "mode": mode,
                "contract": contract,
                "db_writes": 0,
                "capture_claim": False,
                "current_handle": None,
                "note": "No Governance returned without connecting to or mutating the shujuan DB.",
            }
        )
        return 0
    if mode in {"capture", "explore"}:
        if not args.endpoint:
            raise SystemExit(
                f"work start --mode {mode} requires --endpoint so captured discussion has an explicit scope. "
                "Pass --endpoint <name>, --endpoint @current.endpoint, or use --mode no-governance for no DB writes."
            )
        content = read_arg_or_stdin(args.content, file_path=getattr(args, "content_file", None), label="content")
        conn = connect(repo)
        endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
        result = create_discussion_capture(
            conn,
            endpoint_name=endpoint_name,
            content=content,
            actor=args.actor,
            session_id=args.session_id,
            agent_name=args.agent_name,
            model_name=args.model_name,
            source=args.source or args.workflow_kind,
            title=args.title,
            mode=mode,
        )
        conn.commit()
        print_json({"ok": True, "workflow": args.workflow_kind, "mode": mode, "contract": contract, **result})
        return 0
    endpoint_name = None
    if args.endpoint:
        conn_for_endpoint = connect(repo)
        endpoint_name = resolve_endpoint_identifier(conn_for_endpoint, repo, args.endpoint)
        conn_for_endpoint.close()
    exec_args = argparse.Namespace(
        repo=repo,
        task_node=resolve_alias(repo, "task", args.task or args.task_node),
        session_id=args.session_id,
        agent_name=args.agent_name,
        model_name=args.model_name,
        label=args.label or f"{args.workflow_kind} {mode}",
        summary=args.summary or content or f"{args.workflow_kind} start in {mode} mode",
        endpoint=endpoint_name,
        mode=mode,
        intent=intent_text,
        allow_preflight_warning=args.allow_preflight_warning,
        allow_reason=args.allow_reason,
        force=args.force,
    )
    conn = connect(repo)
    active_path = active_run_path(repo)
    if active_path.exists() and not exec_args.force:
        raise SystemExit(f"active run already exists: {active_path}")
    preflight = exec_start_preflight(conn, exec_args)
    if not preflight["ok"] and not exec_args.allow_preflight_warning:
        raise SystemExit("work start preflight failed: " + "; ".join(item["message"] for item in preflight["issues"]) + ". Pass --allow-preflight-warning with --allow-reason to record an explicit assumption.")
    if exec_args.session_id:
        session = conn.execute("SELECT id FROM conversation_sessions WHERE id = ?", (exec_args.session_id,)).fetchone()
        if not session:
            raise SystemExit(f"work start session not found: {exec_args.session_id}. Run workflow begin or hook user-prompt with this session before starting a run.")
    node_id = create_node(conn, "agent_run", exec_args.label, exec_args.summary)
    run_id = new_id("run")
    conn.execute(
        """
        INSERT INTO agent_runs
          (id, node_id, session_id, agent_name, model_name, started_at, base_commit, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            node_id,
            exec_args.session_id,
            exec_args.agent_name,
            exec_args.model_name,
            now_iso(),
            current_head(repo),
            json_dumps(
                {
                    "cwd": str(repo),
                    "task_node": preflight.get("task_node_id") or exec_args.task_node,
                    "task_input": exec_args.task_node,
                    "endpoint": exec_args.endpoint,
                    "preflight": preflight,
                    "workflow": args.workflow_kind,
                    "mode": mode,
                    "contract": contract,
                }
            ),
        ),
    )
    before_snapshot_id = capture_snapshot(conn, repo, run_id, "before")
    if preflight.get("task_node_id"):
        create_edge(conn, node_id, "EXECUTES", preflight["task_node_id"], reason=f"{args.workflow_kind} started for task node.")
    if preflight.get("endpoint_node_id"):
        create_edge(conn, node_id, "APPLIES_TO", preflight["endpoint_node_id"], reason=f"{args.workflow_kind} started under endpoint.", created_by="agent")
    preflight_assumption_node_id = None
    if preflight["issues"]:
        preflight_assumption_node_id = record_preflight_assumption(conn, preflight=preflight, reason=exec_args.allow_reason, run_node_id=node_id)
    conn.commit()
    handle = {
        "run_id": run_id,
        "node_id": node_id,
        "workflow": args.workflow_kind,
        "mode": mode,
        "endpoint": exec_args.endpoint,
        "task": preflight.get("task_id"),
        "task_node": preflight.get("task_node_id"),
    }
    active_path.write_text(json_dumps({"run_id": run_id, "node_id": node_id}), encoding="utf-8")
    (repo / ".shujuan" / "current_work.json").write_text(json_dumps(handle), encoding="utf-8")
    print_json(
        {
            "ok": True,
            "workflow": args.workflow_kind,
            "mode": mode,
            "contract": contract,
            "run_id": run_id,
            "run_node_id": node_id,
            "before_snapshot_id": before_snapshot_id,
            "preflight": {**preflight, "warnings": [*preflight.get("warnings", []), *mode_warnings]},
            "preflight_assumption_node_id": preflight_assumption_node_id,
            "current_handle": handle,
        }
    )
    return 0

def cmd_work_current(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    active = active_run_path(repo)
    work = repo / ".shujuan" / "current_work.json"
    print_json(
        {
            "ok": True,
            "active_run": json.loads(active.read_text(encoding="utf-8")) if active.exists() else None,
            "current_work": json.loads(work.read_text(encoding="utf-8")) if work.exists() else None,
        }
    )
    return 0

def cmd_work_acceptance_template(args: argparse.Namespace) -> int:
    mode = normalize_mode(args.mode)
    print_json({"ok": True, "mode": mode, "template": acceptance_template_for_mode(mode), "contract": mode_contract_payload(mode)})
    return 0

def cmd_work_intake(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    mode = normalize_mode(args.mode)
    if mode == "no_governance":
        print_json(
            {
                "ok": True,
                "workflow": args.workflow_kind,
                "mode": mode,
                "db_writes": 0,
                "capture_claim": False,
                "note": "No Governance returned without creating source promises or predicates.",
            }
        )
        return 0
    if mode in {"capture", "explore"}:
        raise SystemExit("work intake records source promises and requires Light, Standard, or Full mode")
    conn = connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
    endpoint = query_endpoint(conn, endpoint_name)
    source_node = require_node(conn, args.source_node, "source node")
    promise_id = args.promise_id or new_id("source_promise")
    timestamp = now_iso()
    parsed_predicates = [split_cli_id_text(raw_predicate, generated_prefix="hard_predicate") for raw_predicate in args.predicate]
    predicate_ids = [predicate_id for predicate_id, _claim in parsed_predicates]
    default_predicate_id = predicate_ids[0] if predicate_ids else None
    predicate_metadata: dict[str, dict[str, list[str]]] = {}
    for raw_term in args.required_term:
        parsed = parse_predicate_scoped_value(raw_term, default_predicate_id=default_predicate_id, label="required term")
        append_unique_metadata_value(predicate_metadata, parsed["predicate_id"], "required_terms", parsed["text"])
    for raw_term in args.named_term:
        parsed = parse_predicate_scoped_value(raw_term, default_predicate_id=default_predicate_id, label="named term")
        append_unique_metadata_value(predicate_metadata, parsed["predicate_id"], "named_terms", parsed["text"])
    for raw_term in args.must_term:
        parsed = parse_predicate_scoped_value(raw_term, default_predicate_id=default_predicate_id, label="must term")
        append_unique_metadata_value(predicate_metadata, parsed["predicate_id"], "must_terms", parsed["text"])
    for raw_item in args.enumerated_item:
        parsed = parse_predicate_scoped_value(raw_item, default_predicate_id=default_predicate_id, label="enumerated item")
        append_unique_metadata_value(predicate_metadata, parsed["predicate_id"], "enumerated_items", parsed["text"])
    unknown_metadata_predicates = sorted(set(predicate_metadata) - set(predicate_ids))
    if unknown_metadata_predicates:
        raise SystemExit(f"predicate-scoped source term refers to predicate not created by this intake: {', '.join(unknown_metadata_predicates)}")
    contracted_tables = ["source_promises", "hard_predicates", "forbidden_substitutes"]
    missing_tables = [table for table in contracted_tables if not _table_exists(conn, table)]
    if missing_tables:
        print_json(
            _contracted_workflow_payload(
                args=args,
                mode=mode,
                endpoint_name=endpoint_name,
                command="work intake",
                replacement_path=(
                    "source promises and predicate commitments are now carried by source artifacts, "
                    "semantic_items.props, edges, acceptance_checks, and evidence_records"
                ),
                extra={
                    "source_promise_id": None,
                    "source_node_id": source_node["id"],
                    "hard_predicates": [],
                    "forbidden_substitutes": [],
                    "contracted_tables": missing_tables,
                    "requested_predicates": [
                        {"id": predicate_id, "claim": claim, "metadata": predicate_metadata.get(predicate_id, {})}
                        for predicate_id, claim in parsed_predicates
                    ],
                },
            )
        )
        return 1
    conn.execute(
        """
        INSERT INTO source_promises
          (id, endpoint_id, source_node_id, source_locator, kind, text, hardness, downgrade_policy, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            promise_id,
            endpoint["id"],
            source_node["id"],
            args.source_locator,
            args.kind,
            read_arg_or_stdin(args.text, file_path=getattr(args, "text_file", None), label="text"),
            args.hardness,
            args.downgrade_policy,
            timestamp,
            json_dumps({"workflow": args.workflow_kind, "mode": mode, "contract": mode_contract_payload(mode)}),
        ),
    )
    predicates = []
    for predicate_id, claim in parsed_predicates:
        metadata = {"workflow": args.workflow_kind, "mode": mode}
        metadata.update(predicate_metadata.get(predicate_id, {}))
        conn.execute(
            """
            INSERT INTO hard_predicates
              (id, source_promise_id, claim, proof_required, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                predicate_id,
                promise_id,
                claim,
                json_dumps(args.proof_required),
                timestamp,
                json_dumps(metadata),
            ),
        )
        predicates.append({"id": predicate_id, "claim": claim, "proof_required": args.proof_required, "metadata": predicate_metadata.get(predicate_id, {})})
    forbidden_substitutes = []
    for raw_forbidden in args.forbidden_substitute:
        parsed = parse_forbidden_substitute_arg(raw_forbidden, default_predicate_id=default_predicate_id)
        if not conn.execute("SELECT 1 FROM hard_predicates WHERE id = ?", (parsed["predicate_id"],)).fetchone():
            raise SystemExit(f"forbidden substitute predicate not found: {parsed['predicate_id']}")
        forbidden_id = new_id("forbidden_substitute")
        conn.execute(
            """
            INSERT INTO forbidden_substitutes
              (id, predicate_id, substitute_text, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (forbidden_id, parsed["predicate_id"], parsed["substitute_text"], parsed["reason"], timestamp),
        )
        forbidden_substitutes.append({"id": forbidden_id, **parsed})
    conn.commit()
    print_json(
        {
            "ok": True,
            "workflow": args.workflow_kind,
            "mode": mode,
            "endpoint": endpoint_name,
            "source_promise_id": promise_id,
            "source_node_id": source_node["id"],
            "hard_predicates": predicates,
            "forbidden_substitutes": forbidden_substitutes,
            "closure_claim": False,
        }
    )
    return 0

def cmd_work_split(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    mode = normalize_mode(args.mode)
    if mode in {"no_governance", "capture", "explore"}:
        raise SystemExit("work split creates workchain/task predicate records and requires Light, Standard, or Full mode")
    conn = connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
    endpoint = query_endpoint(conn, endpoint_name)
    contracted_tables = ["work_chains", "task_predicate_links", "hard_predicates"]
    missing_tables = [table for table in contracted_tables if not _table_exists(conn, table)]
    if missing_tables:
        print_json(
            _contracted_workflow_payload(
                args=args,
                mode=mode,
                endpoint_name=endpoint_name,
                command="work split",
                replacement_path="work chains derive from endpoints, tasks, acceptance_checks, semantic_items, edges, and evidence_records",
                extra={
                    "work_chain_id": None,
                    "name": args.name,
                    "task_predicate_links": [],
                    "contracted_tables": missing_tables,
                    "requested_links": [parse_work_split_link(raw_link) for raw_link in args.link],
                    "requested_task_ids": list(args.task),
                    "requested_check_ids": list(args.check),
                    "requested_predicate_ids": list(args.predicate),
                },
            )
        )
        return 1
    parent_chain_id = args.parent_chain
    if parent_chain_id and not conn.execute("SELECT 1 FROM work_chains WHERE id = ?", (parent_chain_id,)).fetchone():
        raise SystemExit(f"parent work chain not found: {parent_chain_id}")
    chain_id = args.chain_id or new_id("work_chain")
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO work_chains
          (id, endpoint_id, name, parent_chain_id, mode, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chain_id,
            endpoint["id"],
            args.name,
            parent_chain_id,
            mode,
            timestamp,
            json_dumps({"workflow": args.workflow_kind, "slice": args.slice, "contract": mode_contract_payload(mode)}),
        ),
    )
    links = []
    for raw_link in args.link:
        links.append(insert_task_predicate_link(conn, **parse_work_split_link(raw_link)))
    for task_id in args.task:
        for check_id in args.check:
            for predicate_id in args.predicate:
                links.append(
                    insert_task_predicate_link(
                        conn,
                        task_id=task_id,
                        check_id=check_id,
                        predicate_id=predicate_id,
                        relationship=args.relationship,
                    )
                )
    conn.commit()
    print_json(
        {
            "ok": True,
            "workflow": args.workflow_kind,
            "mode": mode,
            "endpoint": endpoint_name,
            "work_chain_id": chain_id,
            "name": args.name,
            "task_predicate_links": links,
            "closure_claim": False,
        }
    )
    return 0

def cmd_work_audit_source(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
    endpoint = query_endpoint(conn, endpoint_name)
    audit = endpoint_source_nondowngrade_audit(conn, endpoint["id"])
    payload = {
        "ok": audit["ok"],
        "workflow": args.workflow_kind,
        "read_only": True,
        "db_writes": 0,
        "endpoint": endpoint_name,
        "acceptance_contract": "Source promises, hard predicates, required terms, enumerated items, and forbidden substitutes must survive into acceptance checks unless source-backed scope_change/defer authorizes a downgrade.",
        **audit,
    }
    print_json(payload)
    if args.fail_on_findings and audit["findings"]:
        return 1
    return 0

def cmd_work_focus(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
    endpoint = query_endpoint(conn, endpoint_name)
    status = endpoint_report_payload(conn, endpoint_name, active_only=True)
    predicates = endpoint_agcp_predicate_rows(conn, endpoint["id"])
    predicate_ids = [str(row["id"]) for row in predicates]
    links = predicate_link_rows(conn, predicate_ids) if predicate_ids else []
    chains = endpoint_work_chain_rows(conn, endpoint["id"], args.work_chain)
    if args.work_chain and _table_exists(conn, "work_chains") and not chains:
        raise SystemExit(f"active work chain not found for endpoint {endpoint_name}: {args.work_chain}")
    print_json(
        {
            "ok": True,
            "workflow": args.workflow_kind,
            "read_only": True,
            "derived": True,
            "db_writes": 0,
            "endpoint": endpoint_name,
            "derived_work_chains": [row_to_dict(row) for row in chains],
            "legacy_predicates": [row_to_dict(row) for row in predicates],
            "legacy_forbidden_substitutes": [row_to_dict(row) for row in endpoint_forbidden_substitute_rows(conn, endpoint["id"])],
            "legacy_task_predicate_links": [row_to_dict(row) for row in links],
            "contracted_predicates_absent": not _table_exists(conn, "hard_predicates"),
            "legacy_contract": {
                "hard_predicates_current_db_truth": False,
                "work_chains_current_db_truth": False,
                "task_predicate_links_current_db_truth": False,
                "replacement_path": "derive attention from endpoints, tasks, acceptance_checks, semantic_items, edges, and evidence_records",
            },
            "active_report": status,
            "explicit_do_not_close": [
                "Focus is a read-only attention packet and cannot close checks or tasks.",
                "Endpoint doctor output may surface blockers but is not closure evidence.",
                "Closure still requires matching current evidence and controller authority.",
            ],
        }
    )
    return 0

def cmd_work_prove(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    mode = normalize_mode(args.mode)
    if mode in {"no_governance", "capture", "explore"}:
        raise SystemExit("work prove validates or links evidence and requires Light, Standard, or Full mode")
    conn = connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint) if args.endpoint else None
    evidence_node = require_evidence_node(conn, args.evidence_node)
    check_rows = rows_for_checks(conn, args.check)
    dry_run = args.dry_run or not args.apply
    warnings = []
    for check in check_rows:
        warning_node_id = validate_check_evidence_type(
            conn,
            check=check,
            evidence_node_id=args.evidence_node,
            override=args.override_evidence_type and not dry_run,
            override_reason=args.override_reason,
        )
        if warning_node_id:
            warnings.append(warning_node_id)
    if args.close_check:
        target_check_ids = existing_check_ids_closed_by_evidence(conn, args.evidence_node) + [str(check_id) for check_id in args.check]
        warning_node_id = validate_test_result_predicate_coverage(
            conn,
            evidence_node_id=args.evidence_node,
            check_ids=target_check_ids,
            override=args.override_predicate_coverage and not dry_run,
            override_reason=args.override_reason,
            elevated_override=args.elevated_predicate_coverage_override,
        )
        if warning_node_id:
            warnings.append(warning_node_id)
    if dry_run:
        print_json(
            {
                "ok": True,
                "workflow": args.workflow_kind,
                "mode": mode,
                "dry_run": True,
                "default_dry_run": not args.apply,
                "endpoint": endpoint_name,
                "evidence_node": row_to_dict(evidence_node),
                "checks": [row_to_dict(row) for row in check_rows],
                "would_link_evidence": True,
                "would_close_check": bool(args.close_check),
                "would_close_task": bool(args.close_task),
                "warnings": warnings,
                "closure_claim": False,
            }
        )
        return 0
    result = link_evidence_to_checks(
        conn,
        repo,
        args.evidence_node,
        args.check,
        close_checks=args.close_check,
        close_tasks=args.close_task,
        reason=args.reason or "Work proof linked evidence to acceptance check.",
        override_evidence_type=args.override_evidence_type,
        override_reason=args.override_reason,
        override_predicate_coverage=args.override_predicate_coverage,
        elevated_predicate_coverage_override=args.elevated_predicate_coverage_override,
    )
    conn.commit()
    print_json(
        {
            "ok": True,
            "workflow": args.workflow_kind,
            "mode": mode,
            "dry_run": False,
            "endpoint": endpoint_name,
            "evidence_node_id": args.evidence_node,
            "proof": result,
            "warnings": [*warnings, *result.get("warnings", [])],
            "closure_claim": bool(args.close_check or args.close_task),
        }
    )
    return 0

def cmd_work_close(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    mode = normalize_mode(args.mode)
    contract = mode_contract_payload(mode)
    active_path = active_run_path(repo)
    active = json.loads(active_path.read_text(encoding="utf-8")) if active_path.exists() else None
    dry_run = args.dry_run or not getattr(args, "apply", False)
    if dry_run:
        agcp_visibility = None
        active_report = None
        endpoint_name = args.endpoint
        conn = None
        if args.endpoint:
            conn = connect(repo)
            endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
            endpoint = query_endpoint(conn, endpoint_name)
            findings = endpoint_agcp_doctor_findings(conn, endpoint["id"])
            active_report = endpoint_report_payload(conn, endpoint_name, active_only=True)
            agcp_visibility = {
                "endpoint": endpoint_name,
                "active_hard_predicate_count": findings["active_predicate_count"],
                "unmapped_hard_predicate_count": len(findings["unmapped_predicates"]),
                "unmapped_hard_predicate_ids": [item["id"] for item in findings["unmapped_predicates"]],
                "linked_task_check_predicate_count": findings["task_predicate_link_count"],
                "closed_checks_missing_predicate_coverage_count": len(findings["closed_checks_missing_predicate_coverage"]),
                "closed_checks_missing_predicate_coverage_ids": [
                    item["check_id"] for item in findings["closed_checks_missing_predicate_coverage"]
                ],
                "non_accepting_review_result_count": len(findings["non_accepting_reviews"]),
                "non_accepting_review_result_ids": [item["id"] for item in findings["non_accepting_reviews"]],
                "source_non_downgrade_finding_count": len(findings["source_non_downgrade_findings"]),
                "source_non_downgrade_finding_codes": [
                    item["code"] for item in findings["source_non_downgrade_findings"]
                ],
                "note": "AGCP closeout visibility is not closure evidence and does not close checks or tasks.",
            }
        elif args.check or args.task or args.task_node:
            conn = connect(repo)
        gate_matrix = _work_close_gate_matrix(
            conn,
            endpoint_name=endpoint_name,
            args=args,
            mode=mode,
            active=active,
            agcp_visibility=agcp_visibility,
            active_report=active_report,
        )
        check_rows = rows_for_checks(conn, args.check) if conn and args.check else []
        closeout_worksheet = _work_closeout_worksheet(
            conn=conn,
            endpoint_name=endpoint_name,
            args=args,
            mode=mode,
            gate_matrix=gate_matrix,
            active_report=active_report,
            check_rows=check_rows,
        )
        if conn:
            conn.close()
        print_json(
            {
                "ok": True,
                "dry_run": True,
                "workflow": args.workflow_kind,
                "mode": mode,
                "contract": contract,
                "active_run": active,
                "would_create_change_set": bool(active and mode in {"light", "standard", "full"}),
                "would_close_check": bool(args.check and args.close_check),
                "required_evidence": acceptance_template_for_mode(mode),
                "default_dry_run": not getattr(args, "apply", False),
                "apply_command": f"{args.workflow_kind} close --apply --endpoint <endpoint> --mode {mode}",
                "full_closeout_requirements": ["evidence verify --endpoint", "endpoint doctor --strict-closeout"] if mode == "full" else [],
                "agcp_closeout_visibility": agcp_visibility,
                "closeout_worksheet": closeout_worksheet,
                "gate_matrix": gate_matrix,
                "command_effects": work_close_dry_run_effects(
                    mode=mode,
                    endpoint=endpoint_name,
                    has_active_run=bool(active),
                ),
            }
        )
        return 0
    if mode == "full" and not args.endpoint:
        raise SystemExit("Full mode close/apply requires --endpoint for strict doctor and evidence verification.")
    endpoint_name = args.endpoint
    full_gate = None
    if args.endpoint:
        conn_for_endpoint = connect(repo)
        endpoint_name = resolve_endpoint_identifier(conn_for_endpoint, repo, args.endpoint)
        if mode == "full":
            full_gate = full_closeout_gate(
                conn_for_endpoint,
                repo,
                endpoint_name=endpoint_name,
                override=args.override_closeout,
                override_reason=args.override_reason,
            )
        conn_for_endpoint.close()
    stop_args = argparse.Namespace(
        repo=repo,
        run=args.run,
        summary=read_arg_or_stdin(args.summary, file_path=getattr(args, "summary_file", None), label="summary") if (args.summary is not None or getattr(args, "summary_file", None) is not None) else args.summary,
        final_report=args.final_report,
        impact=args.impact,
        impact_timeout=args.impact_timeout,
        no_impact=args.no_impact,
        endpoint=endpoint_name,
        endpoint_body=args.endpoint_body,
        endpoint_description=args.endpoint_description,
        task=args.task,
        task_node=args.task_node,
        check=args.check,
        close_check=args.close_check,
        close_task=args.close_task,
        override_evidence_type=args.override_evidence_type,
        override_reason=args.override_reason,
        full_closeout_gate=full_gate,
    )
    return exec_stop_handler(stop_args)


def register_workflows(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, WorkflowHandler],
) -> None:
    """Register work/fix command families while cli.py keeps global flags and dispatch."""
    _validate_handlers(handlers)
    _register_workflow_parser(subparsers, "work", "work", handlers)
    _register_workflow_parser(subparsers, "fix", "fix", handlers)


__all__ = ["WORKFLOW_HANDLER_KEYS", "build_workflow_handlers", "register_workflows"]
