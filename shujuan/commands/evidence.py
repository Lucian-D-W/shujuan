from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


EvidenceHandler = Callable[[argparse.Namespace], int]
StateArg = Callable[[str], str]
EVIDENCE_HANDLER_KEYS = (
    "test_result",
    "artifact",
    "user_confirmation",
    "verify",
    "status",
    "set_state",
)
EVIDENCE_DEPENDENCY_KEYS = (
    "connect",
    "run_evidence_command",
    "now_iso",
    "new_id",
    "write_artifact_text",
    "evidence_command_argv",
    "evaluate_test_result_predicates",
    "load_predicate_coverage_matrix",
    "require_node",
    "create_node",
    "create_edge",
    "sha256_text",
    "evidence_env_hash",
    "register_evidence_lifecycle",
    "record_evidence_record",
    "persist_predicate_coverage_rows",
    "link_source_nodes",
    "link_validated_nodes",
    "link_evidence_to_checks",
    "print_json",
    "capture_artifact_file",
    "read_arg_or_stdin",
    "row_to_dict",
    "canonical_semantic_state",
    "display_semantic_row",
    "display_lifecycle_event",
    "require_evidence_node",
    "transition_semantic_item",
    "clear_closures_for_inactive_evidence",
    "INACTIVE_SEMANTIC_STATES",
    "resolve_endpoint_identifier",
    "evidence_verify_payload",
    "auto_invalidate_failed_current_evidence",
    "append_trace_event",
)


def _validate_handlers(handlers: Mapping[str, EvidenceHandler]) -> None:
    missing = [key for key in EVIDENCE_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"evidence command boundary is missing: {', '.join(missing)}")


def _evidence_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in EVIDENCE_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"evidence handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in EVIDENCE_DEPENDENCY_KEYS}


def build_evidence_handlers(deps: Mapping[str, Any]) -> dict[str, EvidenceHandler]:
    """Build evidence handlers from cli.py-owned shared helpers without importing cli.py."""
    boundary = _evidence_dependencies(deps)
    connect = boundary["connect"]
    run_evidence_command = boundary["run_evidence_command"]
    now_iso = boundary["now_iso"]
    new_id = boundary["new_id"]
    write_artifact_text = boundary["write_artifact_text"]
    evidence_command_argv = boundary["evidence_command_argv"]
    evaluate_test_result_predicates = boundary["evaluate_test_result_predicates"]
    load_predicate_coverage_matrix = boundary["load_predicate_coverage_matrix"]
    require_node = boundary["require_node"]
    create_node = boundary["create_node"]
    create_edge = boundary["create_edge"]
    sha256_text = boundary["sha256_text"]
    evidence_env_hash = boundary["evidence_env_hash"]
    register_evidence_lifecycle = boundary["register_evidence_lifecycle"]
    record_evidence_record = boundary["record_evidence_record"]
    persist_predicate_coverage_rows = boundary["persist_predicate_coverage_rows"]
    link_source_nodes = boundary["link_source_nodes"]
    link_validated_nodes = boundary["link_validated_nodes"]
    link_evidence_to_checks = boundary["link_evidence_to_checks"]
    print_json = boundary["print_json"]
    capture_artifact_file = boundary["capture_artifact_file"]
    read_arg_or_stdin = boundary["read_arg_or_stdin"]
    row_to_dict = boundary["row_to_dict"]
    canonical_semantic_state = boundary["canonical_semantic_state"]
    display_semantic_row = boundary["display_semantic_row"]
    display_lifecycle_event = boundary["display_lifecycle_event"]
    require_evidence_node = boundary["require_evidence_node"]
    transition_semantic_item = boundary["transition_semantic_item"]
    clear_closures_for_inactive_evidence = boundary["clear_closures_for_inactive_evidence"]
    inactive_semantic_states = boundary["INACTIVE_SEMANTIC_STATES"]
    resolve_endpoint_identifier = boundary["resolve_endpoint_identifier"]
    evidence_verify_payload = boundary["evidence_verify_payload"]
    auto_invalidate_failed_current_evidence = boundary["auto_invalidate_failed_current_evidence"]
    append_trace_event = boundary["append_trace_event"]

    def evidence_test_result(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        completed = run_evidence_command(repo, args.command)
        timestamp = now_iso().replace(":", "").replace("+", "Z")
        result_uid = new_id("test_result")
        stdout_ref = write_artifact_text(repo, f"{result_uid}_{timestamp}_stdout.txt", completed.stdout)
        stderr_ref = write_artifact_text(repo, f"{result_uid}_{timestamp}_stderr.txt", completed.stderr)
        argv = evidence_command_argv(args.command)
        command_text = " ".join(argv)
        predicate_ok, predicates = evaluate_test_result_predicates(args, completed)
        predicate_coverage_matrix = load_predicate_coverage_matrix(repo, args.predicate_coverage_matrix)
        predicate_coverage_props = predicate_coverage_matrix or {}
        conn = connect(repo)
        for source_node_id in args.from_node:
            require_node(conn, source_node_id, "source node")
        node_id = create_node(
            conn,
            "test_result",
            args.label or command_text[:120] or "test result",
            f"exit_code={completed.returncode}",
            {
                "command": command_text,
                "argv": argv,
                "cwd": str(repo),
                "cwd_hash": sha256_text(str(repo)),
                "env_hash": evidence_env_hash(),
                "exit_code": completed.returncode,
                "expected_exit_code": args.expect_exit_code,
                "predicates": predicates,
                "predicate_ok": predicate_ok,
                "stdout_ref": stdout_ref,
                "stderr_ref": stderr_ref,
                "stdout_hash": sha256_text(completed.stdout),
                "stderr_hash": sha256_text(completed.stderr),
                **predicate_coverage_props,
            },
        )
        semantic_item_id = register_evidence_lifecycle(
            conn,
            node_id,
            source_node=args.from_node[0] if args.from_node else node_id,
            reason="Test result evidence recorded.",
        )
        evidence_record_ids = [
            record_evidence_record(conn, evidence_node_id=node_id, record_type="stdout", ref=stdout_ref, sha256=sha256_text(completed.stdout)),
            record_evidence_record(conn, evidence_node_id=node_id, record_type="stderr", ref=stderr_ref, sha256=sha256_text(completed.stderr)),
            record_evidence_record(
                conn,
                evidence_node_id=node_id,
                record_type="command",
                metadata={
                    "argv": argv,
                    "cwd": str(repo),
                    "exit_code": completed.returncode,
                    "predicates": predicates,
                    "predicate_ok": predicate_ok,
                },
            ),
        ]
        if predicate_coverage_matrix:
            evidence_record_ids.append(
                record_evidence_record(
                    conn,
                    evidence_node_id=node_id,
                    record_type="predicate_coverage_matrix",
                    ref=predicate_coverage_matrix["predicate_coverage_matrix_ref"],
                    sha256=predicate_coverage_matrix["predicate_coverage_matrix_sha256"],
                    metadata=predicate_coverage_matrix,
                )
            )
        predicate_coverage_persistence = (
            persist_predicate_coverage_rows(
                conn,
                evidence_node_id=node_id,
                rows=predicate_coverage_matrix["predicate_coverage_matrix"],
            )
            if predicate_coverage_matrix
            else {"inserted": [], "skipped": [], "inserted_count": 0, "skipped_count": 0}
        )
        source_edges = link_source_nodes(conn, node_id, args.from_node, reason="Test result derived from source evidence node.")
        validation_edges = link_validated_nodes(conn, node_id, args.validates_node, reason="Node validated by test result evidence.")
        successful = predicate_ok
        close_skipped = {
            "skipped": bool((args.close_check or args.close_task) and not successful),
            "reason": None if successful else "test command or required predicates failed; test_result evidence is recorded but cannot close checks or tasks",
        }
        check_links = link_evidence_to_checks(
            conn,
            repo,
            node_id,
            args.check,
            close_checks=args.close_check and successful,
            close_tasks=args.close_task and successful,
            reason="Acceptance check validated by test result evidence.",
            override_evidence_type=args.override_evidence_type,
            override_reason=args.override_reason,
            override_predicate_coverage=args.override_predicate_coverage,
            elevated_predicate_coverage_override=args.elevated_predicate_coverage_override,
        )
        conn.commit()
        print_json(
            {
                "ok": True,
                "node_id": node_id,
                "exit_code": completed.returncode,
                "expected_exit_code": args.expect_exit_code,
                "predicate_ok": predicate_ok,
                "predicates": predicates,
                "stdout_ref": stdout_ref,
                "stderr_ref": stderr_ref,
                "predicate_coverage_matrix": predicate_coverage_matrix,
                "predicate_coverage_persistence": predicate_coverage_persistence,
                "semantic_item_id": semantic_item_id,
                "evidence_record_ids": evidence_record_ids,
                "source_edges": source_edges,
                "validation_edges": validation_edges,
                "check_links": check_links,
                "close_skipped": close_skipped,
            }
        )
        return 0 if successful or args.allow_fail else (completed.returncode if completed.returncode else 1)

    def evidence_artifact(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        path = Path(args.path)
        if not path.is_absolute():
            path = repo / path
        if not path.exists() or not path.is_file():
            raise SystemExit(f"artifact file not found: {path}")
        props = capture_artifact_file(repo, path)
        conn = connect(repo)
        for source_node_id in args.from_node:
            require_node(conn, source_node_id, "source node")
        node_id = create_node(conn, "artifact", args.label or props["original_path"], args.summary or props["sha256"], props)
        semantic_item_id = register_evidence_lifecycle(
            conn,
            node_id,
            source_node=args.from_node[0] if args.from_node else node_id,
            reason="Artifact evidence recorded.",
        )
        evidence_record_ids = [
            record_evidence_record(
                conn,
                evidence_node_id=node_id,
                record_type="artifact",
                ref=props.get("capture_ref") or props.get("path"),
                sha256=props.get("sha256"),
                metadata=props,
            )
        ]
        source_edges = link_source_nodes(conn, node_id, args.from_node, reason="Artifact derived from source evidence node.")
        validation_edges = link_validated_nodes(conn, node_id, args.validates_node, reason="Node validated by artifact evidence.")
        if args.produced_by_run:
            run = conn.execute("SELECT node_id FROM agent_runs WHERE id = ?", (args.produced_by_run,)).fetchone()
            if not run:
                raise SystemExit(f"run not found: {args.produced_by_run}")
            create_edge(conn, run["node_id"], "PRODUCES", node_id, reason="Run produced artifact evidence.")
        check_links = link_evidence_to_checks(
            conn,
            repo,
            node_id,
            args.check,
            close_checks=args.close_check,
            close_tasks=args.close_task,
            reason="Acceptance check validated by artifact evidence.",
            override_evidence_type=args.override_evidence_type,
            override_reason=args.override_reason,
        )
        conn.commit()
        print_json(
            {
                "ok": True,
                "node_id": node_id,
                "artifact": props,
                "semantic_item_id": semantic_item_id,
                "evidence_record_ids": evidence_record_ids,
                "source_edges": source_edges,
                "validation_edges": validation_edges,
                "check_links": check_links,
            }
        )
        return 0

    def evidence_user_confirmation(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        body = read_arg_or_stdin(args.body)
        conn = connect(repo)
        for source_node_id in args.from_node:
            require_node(conn, source_node_id, "source node")
        node_id = create_node(
            conn,
            "user_confirmation",
            args.label or "user confirmation",
            body[:240],
            {"body": body},
        )
        semantic_item_id = register_evidence_lifecycle(
            conn,
            node_id,
            source_node=args.from_node[0] if args.from_node else node_id,
            reason="User confirmation evidence recorded.",
        )
        evidence_record_ids = [
            record_evidence_record(conn, evidence_node_id=node_id, record_type="confirmation", sha256=sha256_text(body), metadata={"body_hash": sha256_text(body)})
        ]
        source_edges = link_source_nodes(conn, node_id, args.from_node, reason="User confirmation tied to source evidence node.")
        validation_edges = link_validated_nodes(conn, node_id, args.validates_node, reason="Node validated by user confirmation evidence.")
        check_links = link_evidence_to_checks(
            conn,
            repo,
            node_id,
            args.check,
            close_checks=args.close_check,
            close_tasks=args.close_task,
            reason="Acceptance check validated by user confirmation evidence.",
            override_evidence_type=args.override_evidence_type,
            override_reason=args.override_reason,
        )
        conn.commit()
        print_json(
            {
                "ok": True,
                "node_id": node_id,
                "semantic_item_id": semantic_item_id,
                "evidence_record_ids": evidence_record_ids,
                "source_edges": source_edges,
                "validation_edges": validation_edges,
                "check_links": check_links,
            }
        )
        return 0

    def evidence_status(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        conn = connect(repo)
        node = require_evidence_node(conn, args.node)
        lifecycle = conn.execute(
            """
            SELECT si.id, si.current_state, si.item_type, si.source_node_id, si.updated_at
            FROM semantic_items si
            WHERE si.node_id = ?
            """,
            (args.node,),
        ).fetchone()
        events = conn.execute(
            """
            SELECT event_type, from_state, to_state, source_node_id, reason, created_at, props
            FROM semantic_lifecycle_events
            WHERE node_id = ?
            ORDER BY created_at
            """,
            (args.node,),
        ).fetchall()
        closures = {
            "acceptance_checks": [
                row_to_dict(row)
                for row in conn.execute("SELECT id, node_id, task_id FROM acceptance_checks WHERE closed_by_node_id = ?", (args.node,)).fetchall()
            ],
            "tasks": [
                row_to_dict(row)
                for row in conn.execute("SELECT id, node_id FROM tasks WHERE closed_by_node_id = ?", (args.node,)).fetchall()
            ],
        }
        print_json(
            {
                "ok": True,
                "node": row_to_dict(node),
                "current_state": canonical_semantic_state(lifecycle["current_state"]) if lifecycle else "active",
                "semantic_item": display_semantic_row(lifecycle),
                "events": [display_lifecycle_event(row) for row in events],
                "closures": closures,
            }
        )
        return 0

    def evidence_set_state(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        conn = connect(repo)
        require_evidence_node(conn, args.node)
        if args.source_node:
            require_node(conn, args.source_node, "evidence lifecycle source node")
        semantic_item_id = transition_semantic_item(
            conn,
            args.node,
            state=args.state,
            event_type=args.state,
            source_node=args.source_node or args.node,
            reason=args.reason or f"Evidence marked {args.state}.",
        )
        cleared = {"acceptance_checks": [], "tasks": []}
        if args.state in inactive_semantic_states:
            cleared = clear_closures_for_inactive_evidence(conn, args.node)
        conn.commit()
        print_json({"ok": True, "node_id": args.node, "state": args.state, "semantic_item_id": semantic_item_id, "cleared_closures": cleared})
        return 0

    def evidence_verify(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        conn = connect(repo)
        endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint) if args.endpoint else None
        payload = evidence_verify_payload(
            repo,
            conn,
            endpoint_name,
            node_ids=args.node,
            include_history=args.include_history,
        )
        auto_invalidated = auto_invalidate_failed_current_evidence(conn, payload["checks"])
        if auto_invalidated:
            conn.commit()
            invalidated_node_ids = [str(item["node_id"]) for item in auto_invalidated if item.get("node_id")]
            payload = evidence_verify_payload(
                repo,
                conn,
                endpoint_name,
                node_ids=[*args.node, *invalidated_node_ids],
                include_history=args.include_history,
            )
            payload["post_invalidation_recomputed"] = True
            payload["auto_invalidated_evidence"] = auto_invalidated
        else:
            payload["post_invalidation_recomputed"] = False
            payload["auto_invalidated_evidence"] = []
        append_trace_event(
            repo,
            event_type="evidence_verify",
            endpoint=endpoint_name,
            read_only=True,
            status="verified" if payload.get("ok") else "failed",
            details={"node_count": len(args.node), "include_history": bool(args.include_history)},
        )
        print_json(payload)
        ok = bool(payload["ok"])
        return 0 if ok or args.allow_fail else 1

    return {
        "test_result": evidence_test_result,
        "artifact": evidence_artifact,
        "user_confirmation": evidence_user_confirmation,
        "verify": evidence_verify,
        "status": evidence_status,
        "set_state": evidence_set_state,
    }


def register_evidence(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, EvidenceHandler],
    state_type: StateArg,
) -> None:
    """Register the evidence command family while cli.py keeps global dispatch."""
    _validate_handlers(handlers)

    evidence = subparsers.add_parser("evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)

    evidence_test = evidence_sub.add_parser("test-result")
    evidence_test.add_argument("--label")
    evidence_test.add_argument("--from-node", action="append", default=[])
    evidence_test.add_argument("--validates-node", action="append", default=[])
    evidence_test.add_argument("--check", action="append", default=[])
    evidence_test.add_argument("--close-check", action="store_true")
    evidence_test.add_argument("--close-task", action="store_true")
    evidence_test.add_argument("--override-evidence-type", action="store_true")
    evidence_test.add_argument("--override-predicate-coverage", action="store_true")
    evidence_test.add_argument("--elevated-predicate-coverage-override", action="store_true")
    evidence_test.add_argument("--override-reason")
    evidence_test.add_argument("--predicate-coverage-matrix")
    evidence_test.add_argument("--allow-fail", action="store_true")
    evidence_test.add_argument("--expect-exit-code", type=int, default=0)
    evidence_test.add_argument("--require-stdout", action="store_true")
    evidence_test.add_argument("--require-stderr", action="store_true")
    evidence_test.add_argument("--stdout-contains", action="append", default=[])
    evidence_test.add_argument("--stderr-contains", action="append", default=[])
    evidence_test.add_argument("command", nargs=argparse.REMAINDER)
    evidence_test.set_defaults(func=handlers["test_result"])

    evidence_artifact = evidence_sub.add_parser("artifact")
    evidence_artifact.add_argument("--path", required=True)
    evidence_artifact.add_argument("--label")
    evidence_artifact.add_argument("--summary")
    evidence_artifact.add_argument("--from-node", action="append", default=[])
    evidence_artifact.add_argument("--validates-node", action="append", default=[])
    evidence_artifact.add_argument("--produced-by-run")
    evidence_artifact.add_argument("--check", action="append", default=[])
    evidence_artifact.add_argument("--close-check", action="store_true")
    evidence_artifact.add_argument("--close-task", action="store_true")
    evidence_artifact.add_argument("--override-evidence-type", action="store_true")
    evidence_artifact.add_argument("--override-reason")
    evidence_artifact.set_defaults(func=handlers["artifact"])

    evidence_confirmation = evidence_sub.add_parser("user-confirmation")
    evidence_confirmation.add_argument("--body", required=True)
    evidence_confirmation.add_argument("--label")
    evidence_confirmation.add_argument("--from-node", action="append", default=[])
    evidence_confirmation.add_argument("--validates-node", action="append", default=[])
    evidence_confirmation.add_argument("--check", action="append", default=[])
    evidence_confirmation.add_argument("--close-check", action="store_true")
    evidence_confirmation.add_argument("--close-task", action="store_true")
    evidence_confirmation.add_argument("--override-evidence-type", action="store_true")
    evidence_confirmation.add_argument("--override-reason")
    evidence_confirmation.set_defaults(func=handlers["user_confirmation"])

    evidence_verify = evidence_sub.add_parser("verify")
    evidence_verify.add_argument("--endpoint")
    evidence_verify.add_argument("--node", action="append", default=[])
    evidence_verify.add_argument("--include-history", action="store_true")
    evidence_verify.add_argument("--allow-fail", action="store_true")
    evidence_verify.set_defaults(func=handlers["verify"])

    evidence_status = evidence_sub.add_parser("status")
    evidence_status.add_argument("--node", required=True)
    evidence_status.set_defaults(func=handlers["status"])

    evidence_set_state = evidence_sub.add_parser("set-state")
    evidence_set_state.add_argument("--node", required=True)
    evidence_set_state.add_argument("--state", required=True, type=state_type, metavar="STATE")
    evidence_set_state.add_argument("--source-node")
    evidence_set_state.add_argument("--reason")
    evidence_set_state.set_defaults(func=handlers["set_state"])


__all__ = ["EVIDENCE_HANDLER_KEYS", "build_evidence_handlers", "register_evidence"]
