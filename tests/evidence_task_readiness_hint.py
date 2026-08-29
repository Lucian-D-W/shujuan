from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import postgres_fixture


def setup_task_with_checks(fixture, *, body: str, check_bodies: list[str]) -> dict:
    (fixture.repo / "plan.md").write_text("# P0-07\n\nEvidence readiness hint fixture.\n", encoding="utf-8")
    doc = fixture.run_json("doc", "import", "plan.md", "--source-type", "plan")
    scope = fixture.run_json("scope", "create", "--body", "P0-07 readiness contract.", "--source-node", doc["document_node_id"])
    task = fixture.run_json(
        "task",
        "add",
        "--contract",
        scope["contract_id"],
        "--body",
        body,
        "--from-node",
        doc["document_node_id"],
    )
    checks = [
        fixture.run_json(
            "acceptance",
            "add",
            "--task",
            task["task_id"],
            "--body",
            check_body,
            "--expected-evidence-type",
            "user_confirmation",
            "--from-node",
            doc["document_node_id"],
        )
        for check_body in check_bodies
    ]
    return {"doc": doc, "scope": scope, "task": task, "checks": checks}


def readiness(payload: dict) -> dict:
    result = payload.get("check_links", payload).get("task_readiness")
    if not isinstance(result, dict):
        raise AssertionError(f"missing task_readiness payload: {payload}")
    for key in ("task_ready_to_close", "remaining_open_acceptance_checks", "remaining_open_check_ids", "next_command_hints", "next_commands"):
        if key not in result:
            raise AssertionError(f"task_readiness omitted {key}: {result}")
    return result


def assert_non_final_check_hint(fixture, setup: dict) -> None:
    first = fixture.run_json(
        "evidence",
        "user-confirmation",
        "--body",
        "first check confirmed",
        "--check",
        setup["checks"][0]["acceptance_check_id"],
        "--close-check",
    )
    hint = readiness(first)
    remaining_id = setup["checks"][1]["acceptance_check_id"]
    if hint["task_ready_to_close"] is not False:
        raise AssertionError(f"non-final check reported ready: {hint}")
    if hint["remaining_open_check_ids"] != [remaining_id]:
        raise AssertionError(f"non-final check omitted remaining open check: {hint}")
    command_text = "\n".join(hint["next_commands"])
    if remaining_id not in command_text or "evidence user-confirmation" not in command_text or "--close-check" not in command_text:
        raise AssertionError(f"non-final check omitted actionable next command: {hint}")


def assert_final_check_hint(fixture, setup: dict) -> None:
    second = fixture.run_json(
        "evidence",
        "user-confirmation",
        "--body",
        "second check confirmed",
        "--check",
        setup["checks"][1]["acceptance_check_id"],
        "--close-check",
    )
    hint = readiness(second)
    if hint["task_ready_to_close"] is not True:
        raise AssertionError(f"final check did not report task_ready_to_close=true: {hint}")
    if hint["remaining_open_check_ids"] or hint["remaining_open_acceptance_checks"]:
        raise AssertionError(f"final check still reported open checks: {hint}")
    command_text = "\n".join(hint["next_commands"])
    if setup["checks"][1]["acceptance_check_id"] not in command_text or second["node_id"] not in command_text or "--close-task" not in command_text:
        raise AssertionError(f"final check omitted close-task command hint: {hint}")


def assert_manual_acceptance_close_hint(fixture) -> None:
    setup = setup_task_with_checks(fixture, body="Manual acceptance close readiness task.", check_bodies=["Manual close check."])
    evidence = fixture.run_json("evidence", "user-confirmation", "--body", "manual evidence")
    closed = fixture.run_json(
        "acceptance",
        "close",
        "--check",
        setup["checks"][0]["acceptance_check_id"],
        "--evidence-node",
        evidence["node_id"],
    )
    hint = readiness(closed)
    if hint["task_ready_to_close"] is not True or hint["remaining_open_check_ids"]:
        raise AssertionError(f"manual acceptance close did not expose ready task: {hint}")


def main() -> int:
    fixture_pair = postgres_fixture("evidence-readiness-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    temp, fixture = fixture_pair
    try:
        setup = setup_task_with_checks(
            fixture,
            body="Evidence closure readiness task.",
            check_bodies=["First readiness check.", "Second readiness check."],
        )
        assert_non_final_check_hint(fixture, setup)
        assert_final_check_hint(fixture, setup)
        assert_manual_acceptance_close_hint(fixture)
        print(json.dumps({"ok": True, "evidence_task_readiness_hint": "passed", "fixture_writes": fixture.writes}))
        return 0
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
