from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..services.review_state import load_review_state, write_review_state


ReviewHandler = Callable[[argparse.Namespace], int]
REVIEW_HANDLER_KEYS = ("start", "submit", "packet", "record_return", "adopt")
REVIEW_DEPENDENCY_KEYS = (
    "connect",
    "resolve_endpoint_identifier",
    "query_endpoint",
    "endpoint_agcp_predicate_rows",
    "endpoint_forbidden_substitute_rows",
    "evidence_nodes_for_endpoint",
    "endpoint_status_payload",
    "endpoint_report_payload",
    "row_to_dict",
    "relpath",
    "sha256_bytes",
    "create_node",
    "register_evidence_lifecycle",
    "new_id",
    "now_iso",
    "json_dumps",
    "print_json",
    "ensure_layout",
    "append_trace_event",
)


def _validate_handlers(handlers: Mapping[str, ReviewHandler]) -> None:
    missing = [key for key in REVIEW_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"review command boundary is missing: {', '.join(missing)}")


def _review_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in REVIEW_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"review handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in REVIEW_DEPENDENCY_KEYS}


def _table_exists(conn: Any, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?",
            (name,),
        ).fetchone()
    )


def build_review_handlers(deps: Mapping[str, Any]) -> dict[str, ReviewHandler]:
    """Build review handlers from cli.py-owned shared helpers without importing cli.py."""
    boundary = _review_dependencies(deps)
    connect = boundary["connect"]
    resolve_endpoint_identifier = boundary["resolve_endpoint_identifier"]
    query_endpoint = boundary["query_endpoint"]
    endpoint_agcp_predicate_rows = boundary["endpoint_agcp_predicate_rows"]
    endpoint_forbidden_substitute_rows = boundary["endpoint_forbidden_substitute_rows"]
    evidence_nodes_for_endpoint = boundary["evidence_nodes_for_endpoint"]
    endpoint_status_payload = boundary["endpoint_status_payload"]
    endpoint_report_payload = boundary["endpoint_report_payload"]
    row_to_dict = boundary["row_to_dict"]
    relpath = boundary["relpath"]
    sha256_bytes = boundary["sha256_bytes"]
    create_node = boundary["create_node"]
    register_evidence_lifecycle = boundary["register_evidence_lifecycle"]
    new_id = boundary["new_id"]
    now_iso = boundary["now_iso"]
    json_dumps = boundary["json_dumps"]
    print_json = boundary["print_json"]
    ensure_layout = boundary["ensure_layout"]
    append_trace_event = boundary["append_trace_event"]

    def review_start(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        conn = connect(repo)
        endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
        endpoint = query_endpoint(conn, endpoint_name)
        predicate_rows = endpoint_agcp_predicate_rows(conn, endpoint["id"])
        predicate_ids = [str(row["id"]) for row in predicate_rows]
        evidence_rows = evidence_nodes_for_endpoint(conn, endpoint_name, include_history=args.include_history)
        check_ids = list(dict.fromkeys(args.check))
        if not check_ids:
            check_ids = [str(row["id"]) for row in endpoint_status_payload(conn, endpoint_name, include_chain=False).get("open_checks") or []]
        coverage_rows = []
        coverage_status = "contracted_absent_expected"
        if check_ids and _table_exists(conn, "evidence_predicate_coverage"):
            coverage_status = "legacy_present_pending_contraction"
            placeholders = ",".join("?" for _ in check_ids)
            coverage_rows = conn.execute(
                f"""
                SELECT * FROM evidence_predicate_coverage
                WHERE check_id IN ({placeholders})
                ORDER BY created_at ASC, id ASC
                """,
                check_ids,
            ).fetchall()
        print_json(
            {
                "ok": True,
                "review": "start",
                "read_only": True,
                "db_writes": 0,
                "endpoint": endpoint_name,
                "work_chain_id": args.work_chain,
                "contracted_tables_absent": [
                    table
                    for table in (
                        "source_promises",
                        "hard_predicates",
                        "forbidden_substitutes",
                        "work_chains",
                        "evidence_predicate_coverage",
                    )
                    if not _table_exists(conn, table)
                ],
                "checks": check_ids,
                "mandatory_input_bundle": {
                    "legacy_source_promises": [row["source_promise_id"] for row in predicate_rows],
                    "legacy_predicates": predicate_ids,
                    "legacy_forbidden_substitutes": [row_to_dict(row) for row in endpoint_forbidden_substitute_rows(conn, endpoint["id"])],
                    "workchain_slice": args.work_chain,
                    "attention_packet": "Use work focus for the read-only attention packet.",
                    "diff_change_set": "Provide change_set node id, git diff ref, or patch artifact.",
                    "test_result_stdout_stderr": [row["id"] for row in evidence_rows if row["type"] == "test_result"],
                    "artifact_refs": [row["id"] for row in evidence_rows if row["type"] == "artifact"],
                    "predicate_coverage_matrix": [row_to_dict(row) for row in coverage_rows],
                    "predicate_coverage_matrix_status": coverage_status,
                    "review_material_status": "advisory_material_until_controller_adoption",
                    "review_results_table_status": "contracted_not_default_truth",
                    "controller_adoption_path": "controller may import reviewer material as artifact/evidence/semantic item before any closure decision",
                    "endpoint_active_report": endpoint_report_payload(conn, endpoint_name, active_only=True),
                    "verify_doctor_output": "Controller must provide evidence verify and endpoint doctor output when reviewing for closure.",
                },
                "explicit_do_not_close": [
                    "Review start is a read-only bundle and cannot close checks or tasks.",
                    "Worker prose alone is not evidence.",
                    "Doctor or verify output can support review but cannot infer predicate coverage by itself.",
                ],
            }
        )
        return 0

    def review_submit(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        conn = connect(repo)
        endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
        endpoint = query_endpoint(conn, endpoint_name)
        if args.result == "accept" and not args.read_only_attested:
            raise SystemExit("review submit --result accept requires --read-only-attested")
        if args.work_chain and _table_exists(conn, "work_chains") and not conn.execute("SELECT 1 FROM work_chains WHERE id = ?", (args.work_chain,)).fetchone():
            raise SystemExit(f"work chain not found: {args.work_chain}")
        if not _table_exists(conn, "review_results"):
            artifact_ref = None
            artifact_sha = None
            if args.artifact:
                artifact_path = Path(args.artifact)
                if not artifact_path.is_absolute():
                    artifact_path = repo / artifact_path
                if not artifact_path.exists() or not artifact_path.is_file():
                    raise SystemExit(f"review artifact not found: {artifact_path}")
                data = artifact_path.read_bytes()
                artifact_ref = relpath(artifact_path, repo)
                artifact_sha = sha256_bytes(data)
            print_json(
                {
                    "ok": False,
                    "review": "submit",
                    "endpoint": endpoint_name,
                    "status": "contracted_legacy_command_disabled",
                    "diagnostic_only": True,
                    "material_only": True,
                    "db_writes": 0,
                    "review_result_id": None,
                    "result": args.result,
                    "summary": args.summary,
                    "artifact_node_id": None,
                    "artifact_ref": artifact_ref,
                    "artifact_sha256": artifact_sha,
                    "read_only_attested": args.read_only_attested,
                    "controller_close_allowed": args.controller_close_allowed,
                    "closure_claim": False,
                    "exit_code_policy": "nonzero_not_success",
                    "replacement_path": "reviewer material enters semantic/evidence/artifact only by controller adoption",
                    "next_action": "return review material to the controller for explicit adoption; this command does not persist review_results rows",
                }
            )
            return 1
        artifact_node_id = None
        artifact_ref = None
        artifact_sha = None
        if args.artifact:
            artifact_path = Path(args.artifact)
            if not artifact_path.is_absolute():
                artifact_path = repo / artifact_path
            if not artifact_path.exists() or not artifact_path.is_file():
                raise SystemExit(f"review artifact not found: {artifact_path}")
            data = artifact_path.read_bytes()
            artifact_ref = relpath(artifact_path, repo)
            artifact_sha = sha256_bytes(data)
            artifact_node_id = create_node(
                conn,
                "artifact",
                args.label or f"review result: {endpoint_name}",
                args.summary[:240],
                {
                    "artifact_type": "review_result",
                    "path": artifact_ref,
                    "sha256": artifact_sha,
                    "result": args.result,
                    "read_only_attested": args.read_only_attested,
                },
            )
            register_evidence_lifecycle(
                conn,
                artifact_node_id,
                source_node=artifact_node_id,
                reason="Review result artifact recorded as current evidence material.",
            )
        review_id = args.review_id or new_id("review_result")
        conn.execute(
            """
            INSERT INTO review_results
              (id, endpoint_id, work_chain_id, reviewer_agent, reviewer_model, result, summary, artifact_node_id, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                endpoint["id"],
                args.work_chain,
                args.reviewer_agent,
                args.reviewer_model,
                args.result,
                args.summary,
                artifact_node_id,
                now_iso(),
                json_dumps(
                    {
                        "read_only_attested": args.read_only_attested,
                        "controller_close_allowed": args.controller_close_allowed,
                        "coverage_summary": args.coverage_summary,
                        "artifact_ref": artifact_ref,
                        "artifact_sha256": artifact_sha,
                        "check_ids": args.check,
                    }
                ),
            ),
        )
        conn.commit()
        print_json(
            {
                "ok": True,
                "review": "submit",
                "endpoint": endpoint_name,
                "review_result_id": review_id,
                "result": args.result,
                "artifact_node_id": artifact_node_id,
                "artifact_ref": artifact_ref,
                "read_only_attested": args.read_only_attested,
                "controller_close_allowed": args.controller_close_allowed,
                "closure_claim": False,
                "note": "Review submission records review state only; it does not close checks or tasks.",
            }
        )
        return 0

    def review_packet(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        endpoint_name = args.endpoint
        state = load_review_state(repo, endpoint_name)
        state.update(
            {
                "packet_requested": True,
                "packet_generated": True,
                "reviewer_executed": False,
                "controller_adopted": False,
                "evidence_imported": False,
                "reviewer_role": args.role,
                "review_questions": args.question,
                "material_only": True,
            }
        )
        packet_path = None
        if args.save_artifact:
            packet_path = Path(args.save_artifact)
            if not packet_path.is_absolute():
                packet_path = repo / packet_path
            packet_path.parent.mkdir(parents=True, exist_ok=True)
            packet_path.write_text(
                "\n".join(
                    [
                        f"endpoint: {endpoint_name}",
                        f"role: {args.role}",
                        "material_only: true",
                        "reviewer_executed: false",
                        "questions:",
                        *[f"- {question}" for question in args.question],
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
        state_path = write_review_state(repo, endpoint_name, state)
        append_trace_event(repo, event_type="review_packet", endpoint=endpoint_name, read_only=False, status="packet_generated", details={"role": args.role})
        print_json(
            {
                "ok": True,
                "endpoint": endpoint_name,
                "packet_generated": True,
                "reviewer_executed": False,
                "controller_adopted": False,
                "evidence_imported": False,
                "material_only": True,
                "packet_path": str(packet_path.relative_to(repo)) if packet_path else None,
                "state_path": str(state_path.relative_to(repo)),
            }
        )
        return 0

    def review_record_return(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        endpoint_name = args.endpoint
        artifact_path = Path(args.return_artifact)
        if not artifact_path.is_absolute():
            artifact_path = repo / artifact_path
        if not artifact_path.exists():
            print_json({"ok": False, "error": {"code": "missing_reviewer_return_artifact", "message": f"review artifact not found: {artifact_path}"}, "read_only": True})
            return 1
        state = load_review_state(repo, endpoint_name)
        state.update({"packet_requested": True, "packet_generated": True, "reviewer_executed": True, "return_artifact": str(artifact_path.relative_to(repo)).replace("\\", "/")})
        state_path = write_review_state(repo, endpoint_name, state)
        append_trace_event(repo, event_type="review_record_return", endpoint=endpoint_name, read_only=False, status="reviewer_executed", details={"artifact": state["return_artifact"]})
        print_json({"ok": True, "endpoint": endpoint_name, **{key: state[key] for key in ("packet_requested", "packet_generated", "reviewer_executed", "controller_adopted", "evidence_imported")}, "state_path": str(state_path.relative_to(repo))})
        return 0

    def review_adopt(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        endpoint_name = args.endpoint
        state = load_review_state(repo, endpoint_name)
        if not state.get("reviewer_executed"):
            print_json({"ok": False, "error": {"code": "review_not_executed", "message": "cannot adopt review material before reviewer_executed=true"}, "read_only": True})
            return 1
        state["controller_adopted"] = True
        state["adoption_decision"] = args.decision
        state["evidence_imported"] = False
        state_path = write_review_state(repo, endpoint_name, state)
        append_trace_event(repo, event_type="review_adopt", endpoint=endpoint_name, read_only=False, status="controller_adopted", details={"decision": args.decision})
        print_json({"ok": True, "endpoint": endpoint_name, **{key: state[key] for key in ("packet_requested", "packet_generated", "reviewer_executed", "controller_adopted", "evidence_imported")}, "closure_claim": False, "state_path": str(state_path.relative_to(repo))})
        return 0

    return {
        "start": review_start,
        "submit": review_submit,
        "packet": review_packet,
        "record_return": review_record_return,
        "adopt": review_adopt,
    }


def register_review(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, ReviewHandler],
) -> None:
    """Register the review command family while cli.py keeps global dispatch."""
    _validate_handlers(handlers)

    review = subparsers.add_parser("review")
    review_sub = review.add_subparsers(dest="review_command", required=True)

    review_start = review_sub.add_parser("start")
    review_start.add_argument("--endpoint", required=True)
    review_start.add_argument("--work-chain")
    review_start.add_argument("--check", action="append", default=[])
    review_start.add_argument("--include-history", action="store_true")
    review_start.set_defaults(func=handlers["start"])

    review_submit = review_sub.add_parser("submit")
    review_submit.add_argument("--endpoint", required=True)
    review_submit.add_argument("--review-id")
    review_submit.add_argument("--work-chain")
    review_submit.add_argument("--check", action="append", default=[])
    review_submit.add_argument("--reviewer-agent", default="reviewer")
    review_submit.add_argument("--reviewer-model")
    review_submit.add_argument("--result", required=True, choices=["accept", "reject", "partial", "needs_user_decision"])
    review_submit.add_argument("--summary", required=True)
    review_submit.add_argument("--artifact")
    review_submit.add_argument("--label")
    review_submit.add_argument("--read-only-attested", action="store_true")
    review_submit.add_argument("--controller-close-allowed", action="store_true")
    review_submit.add_argument("--coverage-summary")
    review_submit.set_defaults(func=handlers["submit"])

    review_packet = review_sub.add_parser("packet")
    review_packet.add_argument("--endpoint", required=True)
    review_packet.add_argument("--role", required=True)
    review_packet.add_argument("--question", action="append", default=[])
    review_packet.add_argument("--save-artifact")
    review_packet.set_defaults(func=handlers["packet"])

    review_record_return = review_sub.add_parser("record-return")
    review_record_return.add_argument("--endpoint", required=True)
    review_record_return.add_argument("--return-artifact", required=True)
    review_record_return.set_defaults(func=handlers["record_return"])

    review_adopt = review_sub.add_parser("adopt")
    review_adopt.add_argument("--endpoint", required=True)
    review_adopt.add_argument("--decision", required=True)
    review_adopt.set_defaults(func=handlers["adopt"])

__all__ = ["REVIEW_HANDLER_KEYS", "build_review_handlers", "register_review"]
