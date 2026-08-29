from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.store import connect
from tests.helpers.postgres_fixture import postgres_fixture


def row_value(repo: Path, table: str, row_id: str, column: str) -> str | None:
    conn = connect(repo)
    try:
        row = conn.execute(f"SELECT {column} FROM {table} WHERE id = ?", (row_id,)).fetchone()
        return row[column] if row else None
    finally:
        conn.close()


def setup_fixture(fixture) -> dict:
    repo = fixture.repo
    (repo / "plan.md").write_text("# P0-06\n\nCloseout worksheet canonicalization fixture.\n", encoding="utf-8")
    doc = fixture.run_json("doc", "import", "plan.md", "--source-type", "plan")
    source_node = doc["document_node_id"]
    scope = fixture.run_json("scope", "create", "--body", "P0-06 worksheet contract.", "--source-node", source_node)
    endpoint = "closeout-worksheet"
    fixture.run_json("endpoint", "create", endpoint, "--description", "Worksheet endpoint.", "--root-node", scope["node_id"])

    blocked_task = fixture.run_json(
        "task",
        "add",
        "--contract",
        scope["contract_id"],
        "--body",
        "Closeout task has mismatched evidence and another open check.",
        "--from-node",
        source_node,
    )
    mismatch_check = fixture.run_json(
        "acceptance",
        "add",
        "--task",
        blocked_task["task_id"],
        "--body",
        "This check needs a test_result, not a dry-run change_set.",
        "--expected-evidence-type",
        "test_result",
        "--from-node",
        source_node,
    )
    open_artifact_check = fixture.run_json(
        "acceptance",
        "add",
        "--task",
        blocked_task["task_id"],
        "--body",
        "This open artifact check blocks task closure.",
        "--expected-evidence-type",
        "artifact",
        "--from-node",
        source_node,
    )

    warning_task = fixture.run_json(
        "task",
        "add",
        "--contract",
        scope["contract_id"],
        "--body",
        "Task remains open after its only check is closed.",
        "--from-node",
        source_node,
    )
    closed_check = fixture.run_json(
        "acceptance",
        "add",
        "--task",
        warning_task["task_id"],
        "--body",
        "Closed check leaves task ready for controller review.",
        "--expected-evidence-type",
        "artifact",
        "--from-node",
        source_node,
    )
    (repo / "proof.txt").write_text("closed-check proof\n", encoding="utf-8")
    closed_evidence = fixture.run_json(
        "evidence",
        "artifact",
        "--path",
        "proof.txt",
        "--from-node",
        source_node,
        "--check",
        closed_check["acceptance_check_id"],
        "--close-check",
    )

    audit = fixture.run_json(
        "audit",
        "record",
        "--endpoint",
        endpoint,
        "--source-node",
        source_node,
        "--body",
        "Active closeout blocker body.",
        "--finding",
        "Active blocker remains until controller resolves it.",
    )

    return {
        "endpoint": endpoint,
        "source_node": source_node,
        "blocked_task_id": blocked_task["task_id"],
        "warning_task_id": warning_task["task_id"],
        "mismatch_check_id": mismatch_check["acceptance_check_id"],
        "open_artifact_check_id": open_artifact_check["acceptance_check_id"],
        "closed_check_id": closed_check["acceptance_check_id"],
        "closed_evidence_node_id": closed_evidence["node_id"],
        "audit_finding_node_id": audit["audit_finding_node_ids"][0],
    }


def assert_worksheet(repo: Path, fixture, setup: dict) -> None:
    before_check = row_value(repo, "acceptance_checks", setup["mismatch_check_id"], "closed_by_node_id")
    before_task = row_value(repo, "tasks", setup["blocked_task_id"], "closed_by_node_id")
    payload = fixture.run_json(
        "work",
        "close",
        "--dry-run",
        "--mode",
        "full",
        "--endpoint",
        setup["endpoint"],
        "--check",
        setup["mismatch_check_id"],
        "--close-check",
        "--task",
        setup["blocked_task_id"],
        "--close-task",
    )
    worksheet = payload.get("closeout_worksheet") or {}
    if worksheet.get("version") != "activation.v7.closeout_worksheet" or not worksheet.get("canonical"):
        raise AssertionError(f"canonical worksheet missing: {payload}")
    if not worksheet.get("dry_run_non_mutating") or not payload.get("dry_run"):
        raise AssertionError(f"worksheet did not report dry-run non-mutation: {worksheet}")
    if setup["mismatch_check_id"] not in {item["check_id"] for item in worksheet["missing_evidence"]}:
        raise AssertionError(f"missing evidence absent from worksheet: {worksheet}")
    mismatch = [item for item in worksheet["expected_evidence_mismatches"] if item["check_id"] == setup["mismatch_check_id"]]
    if not mismatch or mismatch[0].get("expected_evidence_type") != "test_result":
        raise AssertionError(f"expected evidence mismatch absent from worksheet: {worksheet}")
    if setup["open_artifact_check_id"] not in {
        check_id for item in worksheet["task_closure_blockers"] for check_id in item["open_check_ids"]
    }:
        raise AssertionError(f"task closure blocker absent from worksheet: {worksheet}")
    warning = [
        item for item in worksheet["checks_closed_task_open_warnings"] if item.get("task_id") == setup["warning_task_id"]
    ]
    if not warning:
        raise AssertionError(f"checks_closed_task_open warning absent from worksheet: {worksheet}")
    if setup["closed_check_id"] not in warning[0].get("closed_check_ids", []):
        raise AssertionError(f"closed check ids absent from warning: {warning}")
    blocker_refs = {item.get("ref") for item in worksheet["active_blockers"]}
    if setup["audit_finding_node_id"] not in blocker_refs:
        raise AssertionError(f"active blocker ref absent from worksheet: {worksheet}")
    commands = worksheet["proposed_commands"]
    command_text = "\n".join(item["command"] for item in commands)
    for expected in [
        f"python -m shujuan evidence test-result --check {setup['mismatch_check_id']} --close-check -- <test command>",
        f"python -m shujuan evidence artifact --path <file> --check {setup['open_artifact_check_id']} --close-check",
        f"python -m shujuan acceptance close --check {setup['closed_check_id']} --evidence-node {setup['closed_evidence_node_id']} --close-task",
        f"python -m shujuan endpoint doctor {setup['endpoint']} --strict-closeout --read-only",
        f"python -m shujuan evidence verify --endpoint {setup['endpoint']}",
    ]:
        if expected not in command_text:
            raise AssertionError(f"proposed command missing {expected}: {commands}")
    if "checks_closed_task_open" not in worksheet["stop_reasons"] or not worksheet["blocks_closeout"]:
        raise AssertionError(f"worksheet did not block closeout for warnings/blockers: {worksheet}")
    for action in ("close_target_checks", "close_target_tasks", "run_endpoint_doctor_strict_closeout", "run_evidence_verify"):
        if action not in worksheet["apply_actions"]:
            raise AssertionError(f"apply action {action} missing: {worksheet}")
    after_check = row_value(repo, "acceptance_checks", setup["mismatch_check_id"], "closed_by_node_id")
    after_task = row_value(repo, "tasks", setup["blocked_task_id"], "closed_by_node_id")
    if (before_check, before_task) != (after_check, after_task):
        raise AssertionError("work close --dry-run mutated check or task closure state")


def assert_readiness_parity(fixture, setup: dict) -> None:
    report = fixture.run_json("report", "endpoint", setup["endpoint"], "--active-only")
    close = fixture.run_json("work", "close", "--dry-run", "--mode", "full", "--endpoint", setup["endpoint"])
    worksheet = close["closeout_worksheet"]
    report_readiness = report["readiness"]
    worksheet_readiness = worksheet["readiness"]
    for key in ("blocking_reason_code", "blocking_reason", "next_safe_action"):
        if report_readiness.get(key) != worksheet_readiness.get(key):
            raise AssertionError(f"readiness {key} diverged: report={report_readiness} worksheet={worksheet_readiness}")
    report_refs = {
        item.get("ref")
        for item in (report_readiness.get("visible_blocking_refs") or []) + (report_readiness.get("hidden_blocking_refs") or [])
    }
    worksheet_refs = {item.get("ref") for item in worksheet.get("active_blockers") or []}
    if not report_refs <= worksheet_refs:
        raise AssertionError(f"worksheet active blockers diverged from readiness refs: {worksheet}")
    report_commands = set(report["next_valid_entry_point"].get("commands") or [])
    worksheet_commands = {item["command"] for item in worksheet["proposed_commands"]}
    if not report_commands <= worksheet_commands:
        raise AssertionError(f"worksheet omitted readiness next commands: {worksheet}")


def main() -> int:
    fixture_pair = postgres_fixture("closeout-worksheet-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    temp, fixture = fixture_pair
    try:
        setup = setup_fixture(fixture)
        assert_worksheet(fixture.repo, fixture, setup)
        assert_readiness_parity(fixture, setup)
        print(json.dumps({"ok": True, "closeout_worksheet_canonicalization": "passed", "fixture_writes": fixture.writes}))
        return 0
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
