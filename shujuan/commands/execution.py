from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from typing import Any

from ..services.command_effects import exec_stop_effects
from ..services.dependencies import RuntimeDeps
from ..services.sovereignty_gate import explicit_no_governance_reasons, no_governance_payload

ExecutionHandler = Callable[[argparse.Namespace], int]
EXECUTION_DEPENDENCY_KEYS = (
    "active_run_path",
    "capture_change_set",
    "capture_snapshot",
    "clear_current_work_for_run",
    "closeout_endpoint",
    "connect",
    "create_edge",
    "create_node",
    "current_head",
    "evidence_stop_check",
    "exec_start_preflight",
    "json_dumps",
    "link_change_evidence",
    "mode_contract_payload",
    "new_id",
    "normalize_mode",
    "now_iso",
    "print_json",
    "read_arg_or_stdin",
    "record_preflight_assumption",
    "resolve_check_identifier",
    "resolve_endpoint_identifier",
    "resolve_run_identifier",
    "resolve_task_id_identifier",
    "resolve_task_node_input_identifier",
)


def _execution_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    return RuntimeDeps(deps).require(*EXECUTION_DEPENDENCY_KEYS)


def build_execution_handlers(deps: Mapping[str, Any]) -> dict[str, ExecutionHandler]:
    globals().update(_execution_dependencies(deps))
    return {"start": cmd_exec_start, "stop": cmd_exec_stop, "diff_capture": cmd_diff_capture}


def cmd_exec_start(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    args.mode = normalize_mode(getattr(args, "mode", None) or "standard")
    summary = read_arg_or_stdin(args.summary, file_path=getattr(args, "summary_file", None), label="summary") if (args.summary is not None or getattr(args, "summary_file", None) is not None) else args.summary
    args.summary = summary
    args.intent = " ".join(str(value or "") for value in [summary, args.label])
    no_governance_reasons = explicit_no_governance_reasons(args.intent)
    if no_governance_reasons or args.mode == "no_governance":
        print_json(
            no_governance_payload(
                command="exec start",
                content=args.intent,
                reasons=no_governance_reasons or ["explicit_mode:no_governance"],
                contract=mode_contract_payload("no_governance"),
            )
        )
        return 0
    conn = connect(repo)
    active_path = active_run_path(repo)
    if active_path.exists() and not args.force:
        raise SystemExit(f"active run already exists: {active_path}")
    contract = mode_contract_payload(args.mode)
    preflight = exec_start_preflight(conn, args)
    if not preflight["ok"] and not args.allow_preflight_warning:
        raise SystemExit("exec start preflight failed: " + "; ".join(item["message"] for item in preflight["issues"]) + ". Pass --allow-preflight-warning with --allow-reason to record an explicit assumption.")
    if args.session_id:
        session = conn.execute("SELECT id FROM conversation_sessions WHERE id = ?", (args.session_id,)).fetchone()
        if not session:
            raise SystemExit(f"exec start session not found: {args.session_id}. Run workflow begin or hook user-prompt with this session before starting a run.")
    node_id = create_node(conn, "agent_run", args.label or "agent run", args.summary)
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
            args.session_id,
            args.agent_name,
            args.model_name,
            now_iso(),
            current_head(repo),
            json_dumps(
                {
                    "cwd": str(repo),
                    "task_node": preflight.get("task_node_id") or args.task_node,
                    "task_input": args.task_node,
                    "endpoint": args.endpoint,
                    "mode": args.mode,
                    "contract": contract,
                    "preflight": preflight,
                }
            ),
        ),
    )
    before_snapshot_id = capture_snapshot(conn, repo, run_id, "before")
    if preflight.get("task_node_id"):
        create_edge(conn, node_id, "EXECUTES", preflight["task_node_id"], reason="Run started for task node.")
    if preflight.get("endpoint_node_id"):
        create_edge(conn, node_id, "APPLIES_TO", preflight["endpoint_node_id"], reason="Run started under endpoint.", created_by="agent")
    preflight_assumption_node_id = None
    if preflight["issues"]:
        preflight_assumption_node_id = record_preflight_assumption(conn, preflight=preflight, reason=args.allow_reason, run_node_id=node_id)
    conn.commit()
    active_path.write_text(json_dumps({"run_id": run_id, "node_id": node_id}), encoding="utf-8")
    print_json(
        {
            "ok": True,
            "mode": args.mode,
            "contract": contract,
            "run_id": run_id,
            "run_node_id": node_id,
            "before_snapshot_id": before_snapshot_id,
            "preflight": preflight,
            "preflight_assumption_node_id": preflight_assumption_node_id,
        }
    )
    return 0

def cmd_exec_stop(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
    task_ids = [resolve_task_id_identifier(conn, repo, task_id) for task_id in args.task]
    task_node_ids = [resolve_task_node_input_identifier(conn, repo, task_node_id) for task_node_id in args.task_node]
    check_ids = [resolve_check_identifier(conn, repo, check_id) for check_id in args.check]
    run_id = resolve_run_identifier(conn, repo, args.run)
    active_path = active_run_path(repo)
    if not run_id:
        if not active_path.exists():
            raise SystemExit("no active run; pass --run or run `shujuan exec start` first")
        run_id = json.loads(active_path.read_text(encoding="utf-8"))["run_id"]
    if not conn.execute("SELECT 1 FROM agent_runs WHERE id = ?", (run_id,)).fetchone():
        raise SystemExit(f"run not found: {run_id}")
    summary = read_arg_or_stdin(args.summary, file_path=getattr(args, "summary_file", None), label="summary") if (args.summary is not None or getattr(args, "summary_file", None) is not None) else args.summary
    args.summary = summary
    after_snapshot_id = capture_snapshot(conn, repo, run_id, "after")
    conn.execute(
        """
        UPDATE agent_runs
        SET ended_at = ?, end_head_commit = ?, final_report = ?
        WHERE id = ?
        """,
        (now_iso(), current_head(repo), args.final_report, run_id),
    )
    change_result = capture_change_set(
        conn,
        repo,
        run_id,
        args.summary,
        impact=args.impact and not args.no_impact,
        impact_timeout=args.impact_timeout,
    )
    evidence_links = link_change_evidence(
        conn,
        repo,
        change_result["change_set_node_id"],
        task_ids=task_ids,
        task_node_ids=task_node_ids,
        check_ids=check_ids,
        close_checks=args.close_check,
        close_tasks=args.close_task,
        override_evidence_type=args.override_evidence_type,
        override_reason=args.override_reason,
    )
    stop_check = evidence_stop_check(conn, endpoint_name=endpoint_name)
    endpoint_result = closeout_endpoint(
        conn,
        endpoint_name=endpoint_name,
        endpoint_body=args.endpoint_body,
        endpoint_description=args.endpoint_description,
        run_id=run_id,
        change_result=change_result,
        evidence_links=evidence_links,
        stop_check=stop_check,
        final_report=args.final_report,
        summary=args.summary,
    )
    conn.commit()
    if active_path.exists():
        active = json.loads(active_path.read_text(encoding="utf-8"))
        if active.get("run_id") == run_id:
            active_path.unlink()
    current_work_cleared = clear_current_work_for_run(repo, run_id)
    print_json(
        {
            "ok": True,
            "run_id": run_id,
            "after_snapshot_id": after_snapshot_id,
            "change_set": change_result,
            "evidence_links": evidence_links,
            "stop_check": stop_check,
            "endpoint_closeout": endpoint_result,
            "full_closeout_gate": getattr(args, "full_closeout_gate", None),
            "current_work_cleared": current_work_cleared,
            "command_effects": exec_stop_effects(
                close_check=bool(args.close_check),
                close_task=bool(args.close_task),
                impact=bool(args.impact),
                no_impact=bool(args.no_impact),
            ),
        }
    )
    return 0

def cmd_diff_capture(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    summary = read_arg_or_stdin(args.summary, file_path=getattr(args, "summary_file", None), label="summary") if (args.summary is not None or getattr(args, "summary_file", None) is not None) else args.summary
    result = capture_change_set(
        conn,
        repo,
        args.run,
        summary,
        impact=args.impact and not args.no_impact,
        impact_timeout=args.impact_timeout,
    )
    result["evidence_links"] = link_change_evidence(
        conn,
        repo,
        result["change_set_node_id"],
        task_ids=args.task,
        task_node_ids=args.task_node,
        check_ids=args.check,
        close_checks=args.close_check,
        close_tasks=args.close_task,
        override_evidence_type=args.override_evidence_type,
        override_reason=args.override_reason,
    )
    conn.commit()
    print_json(result)
    return 0


def register_execution(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, ExecutionHandler],
) -> None:
    exec_parser = subparsers.add_parser("exec")
    exec_sub = exec_parser.add_subparsers(dest="exec_command", required=True)
    exec_start = exec_sub.add_parser("start")
    exec_start.add_argument("--task-node")
    exec_start.add_argument("--session-id")
    exec_start.add_argument("--agent-name", default="codex")
    exec_start.add_argument("--model-name")
    exec_start.add_argument("--mode", default="standard")
    exec_start.add_argument("--label")
    exec_start.add_argument("--summary")
    exec_start.add_argument("--summary-file", help="Read run summary text from a UTF-8 file.")
    exec_start.add_argument("--endpoint")
    exec_start.add_argument("--allow-preflight-warning", action="store_true")
    exec_start.add_argument("--allow-reason")
    exec_start.add_argument("--force", action="store_true")
    exec_start.set_defaults(func=handlers["start"])
    exec_stop = exec_sub.add_parser("stop")
    exec_stop.add_argument("--run")
    exec_stop.add_argument("--summary")
    exec_stop.add_argument("--summary-file", help="Read stop summary text from a UTF-8 file.")
    exec_stop.add_argument("--final-report")
    exec_stop.add_argument("--impact", action="store_true", help="Explicitly run the optional bounded impact provider.")
    exec_stop.add_argument("--impact-timeout", type=int, default=30, help="Seconds per changed file for optional provider execution.")
    exec_stop.add_argument("--no-impact", action="store_true", help=argparse.SUPPRESS)
    exec_stop.add_argument("--endpoint", required=True)
    exec_stop.add_argument("--endpoint-body")
    exec_stop.add_argument("--endpoint-description")
    exec_stop.add_argument("--task", action="append", default=[])
    exec_stop.add_argument("--task-node", action="append", default=[])
    exec_stop.add_argument("--check", action="append", default=[])
    exec_stop.add_argument("--close-check", action="store_true")
    exec_stop.add_argument("--close-task", action="store_true")
    exec_stop.add_argument("--override-evidence-type", action="store_true")
    exec_stop.add_argument("--override-reason")
    exec_stop.set_defaults(func=handlers["stop"])


def register_diff(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, ExecutionHandler],
) -> None:
    diff = subparsers.add_parser("diff")
    diff_sub = diff.add_subparsers(dest="diff_command", required=True)
    diff_capture = diff_sub.add_parser("capture")
    diff_capture.add_argument("--run", required=True)
    diff_capture.add_argument("--summary")
    diff_capture.add_argument("--summary-file", help="Read diff summary text from a UTF-8 file.")
    diff_capture.add_argument("--impact", action="store_true", help="Explicitly run the optional bounded impact provider.")
    diff_capture.add_argument("--impact-timeout", type=int, default=30, help="Seconds per changed file for optional provider execution.")
    diff_capture.add_argument("--no-impact", action="store_true", help=argparse.SUPPRESS)
    diff_capture.add_argument("--task", action="append", default=[])
    diff_capture.add_argument("--task-node", action="append", default=[])
    diff_capture.add_argument("--check", action="append", default=[])
    diff_capture.add_argument("--close-check", action="store_true")
    diff_capture.add_argument("--close-task", action="store_true")
    diff_capture.add_argument("--override-evidence-type", action="store_true")
    diff_capture.add_argument("--override-reason")
    diff_capture.set_defaults(func=handlers["diff_capture"])


__all__ = ["build_execution_handlers", "register_diff", "register_execution"]
