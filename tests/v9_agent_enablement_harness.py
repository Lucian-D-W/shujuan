from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import postgres_fixture


def setup_endpoint(fixture) -> dict:
    repo = fixture.repo
    (repo / "plan.md").write_text("# V9 harness\n\nTask-chain importer fixture.\n", encoding="utf-8")
    doc = fixture.run_json("doc", "import", "plan.md", "--source-type", "plan")
    scope = fixture.run_json("scope", "create", "--body", "V9 harness scope.", "--source-node", doc["document_node_id"])
    fixture.run_json("endpoint", "create", "v9-harness", "--root-node", scope["node_id"])
    return {"doc": doc, "scope": scope}


def write_task_chain(repo: Path) -> Path:
    payload = {
        "declares_no_closure": True,
        "closed_by_decomposition": False,
        "endpoint": {"name": "v9-harness"},
        "tasks": [
            {"key": "T01", "title": "First surface", "body": "Add the first-surface route card.", "phase": "P0", "order": 10, "mandatory": True},
            {"key": "T02", "title": "Route guard", "body": "Add the read-only route guard.", "phase": "P1", "order": 20, "mandatory": True},
        ],
        "checks": [
            {"key": "C01", "task_key": "T01", "body": "Verify the first-surface card exists.", "expected_evidence_type": "change_set"},
            {"key": "C02", "task_key": "T02", "body": "Run a route-guard regression command.", "expected_evidence_type": "test_result"},
        ],
        "review": {
            "packet_requested": True,
            "reviewer_role": "xhigh_reviewer",
            "review_questions": ["coverage", "ambiguity"],
            "packet_generated": False,
            "reviewer_executed": False,
            "controller_adopted": False,
        },
        "source_items": [
            {
                "id": "SR01",
                "classification": "P0",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "T01"},
                "task_ids": ["T01"],
                "check_ids": ["C01"],
                "rationale": "The first surface stays visible as an explicit imported task.",
                "promotion_rule": "Already active.",
                "reopen_rule": "Reopen by restoring T01/C01.",
            },
            {
                "id": "SR02",
                "classification": "P1",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "T02"},
                "task_ids": ["T02"],
                "check_ids": ["C02"],
                "rationale": "The route guard verification remains explicit in the imported chain.",
                "promotion_rule": "Already active.",
                "reopen_rule": "Reopen by restoring T02/C02.",
            },
        ],
    }
    path = repo / "task_chain.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def main() -> int:
    fixture_pair = postgres_fixture("v9-agent-enable-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    temp, fixture = fixture_pair
    try:
        repo = fixture.repo
        setup_endpoint(fixture)
        chain_path = write_task_chain(repo)
        return_artifact = repo / "review_return.md"
        return_artifact.write_text("read_only_attested: true\nverdict: accept\n", encoding="utf-8")

        route = fixture.run_json("route", "guard", "--endpoint", "v9-harness", "--intent", "turn this long plan into a task chain and then ask a reviewer to inspect it")
        if route["recommended_route"] != "Delegate" or route["read_only"] is not True:
            raise AssertionError(f"route guard did not keep the delegate/material boundary visible: {route}")
        close_guard = fixture.run_json(
            "route",
            "guard",
            "--endpoint",
            "v9-harness",
            "--intent",
            "close this check now",
            expect_ok=False,
        )
        if close_guard["ok"] or close_guard["error"]["code"] != "missing_closeout_inputs" or close_guard["exit_brake"]["stop_writes"] is not True:
            raise AssertionError(f"route guard did not fail closed on missing closeout inputs: {close_guard}")
        importer_guard = fixture.run_json(
            "route",
            "guard",
            "--endpoint",
            "v9-harness",
            "--intent",
            "import this long plan into the DB as tasks and checks",
        )
        if "import-task-chain" not in importer_guard["safe_next_action"] or importer_guard["exit_brake"]["stop_writes"] is not True:
            raise AssertionError(f"route guard did not hard-stop toward the formal importer: {importer_guard}")

        preview = fixture.run_json("plan-to-db", "import-task-chain", "--artifact", str(chain_path), "--endpoint", "v9-harness", "--dry-run")
        if not preview["read_only"] or preview["counts"]["tasks"] != 2 or not preview["warnings"]:
            raise AssertionError(f"task-chain dry-run preview was incomplete: {preview}")

        applied = fixture.run_json(
            "plan-to-db",
            "import-task-chain",
            "--artifact",
            str(chain_path),
            "--endpoint",
            "v9-harness",
            "--apply",
            "--idempotency-key",
            "fixture-key",
        )
        if applied["counts"]["tasks"] != 2 or applied["counts"]["checks"] != 2 or applied["closure_side_effects"] != 0:
            raise AssertionError(f"task-chain apply did not report the expected mapping surface: {applied}")

        idempotent = fixture.run_json(
            "plan-to-db",
            "import-task-chain",
            "--artifact",
            str(chain_path),
            "--endpoint",
            "v9-harness",
            "--apply",
            "--idempotency-key",
            "fixture-key",
        )
        if idempotent.get("idempotent") is not True:
            raise AssertionError(f"idempotency replay did not short-circuit cleanly: {idempotent}")

        task_row = next(iter(applied["mapping"]["task_mapping"].values()))
        blocked_scope = fixture.run_json(
            "scope",
            "change",
            "--body",
            "clarification should not defer the imported task",
            "--source-node",
            applied["mapping"]["task_mapping"]["T01"]["node_id"],
            "--task",
            task_row["task_id"],
            expect_ok=False,
        )
        if blocked_scope["error"]["code"] != "scope_change_requires_explicit_state_ack":
            raise AssertionError(f"scope-change guard did not fail closed: {blocked_scope}")

        packet = fixture.run_json(
            "review",
            "packet",
            "--endpoint",
            "v9-harness",
            "--role",
            "xhigh_reviewer",
            "--question",
            "coverage",
            "--question",
            "ambiguity",
            "--save-artifact",
            "review_packet.md",
        )
        if not packet["packet_generated"] or packet["reviewer_executed"]:
            raise AssertionError(f"review packet state was wrong: {packet}")

        returned = fixture.run_json("review", "record-return", "--endpoint", "v9-harness", "--return-artifact", str(return_artifact))
        adopted = fixture.run_json("review", "adopt", "--endpoint", "v9-harness", "--decision", "accept")
        if not returned["reviewer_executed"] or not adopted["controller_adopted"] or adopted["closure_claim"]:
            raise AssertionError(f"review state machine leaked closure semantics: returned={returned}, adopted={adopted}")

        (repo / "old_report.md").write_text("superseded report\n", encoding="utf-8")
        artifact_index = fixture.run_json(
            "artifact",
            "index",
            "refresh",
            "--endpoint",
            "v9-harness",
            "--current",
            "task_chain.json",
            "--mapping",
            applied["out"],
            "--review-material",
            "review_packet.md",
            "--supersede",
            "old_report.md",
            "--evidence",
            "review_return.md",
        )
        if (
            not artifact_index["authoritative"]
            or not artifact_index["review_material"]
            or not artifact_index["db_mapping"]
            or artifact_index["schema_version"] != "shujuan.artifact_index.v2"
        ):
            raise AssertionError(f"artifact index did not separate the categories: {artifact_index}")
        verify_index = fixture.run_json("artifact", "index", "verify", "--endpoint", "v9-harness")
        if not verify_index["ok"]:
            raise AssertionError(f"artifact index verification failed: {verify_index}")

        suggest = fixture.run_json("endpoint", "suggest", "--from-prompt", "please continue v9-harness and inspect the harness endpoint", "--top", "3")
        if (
            not suggest["candidates"]
            or suggest["candidates"][0]["endpoint"] != "v9-harness"
            or suggest["write_allowed"] is not False
            or not isinstance(suggest["candidates"][0]["first_surface"], list)
        ):
            raise AssertionError(f"endpoint suggest missed the obvious endpoint: {suggest}")
        center = fixture.run_json("center", "suggest", "--from-prompt", "recover the project center before choosing an endpoint")
        if not center["candidates"] or center["write_allowed"] is not False:
            raise AssertionError(f"center suggest did not keep the contract read-only: {center}")

        layout = fixture.run_json("install-layout", "doctor")
        if "postgres_ready" not in layout or "schema_roles_verification" not in layout or "postgres_runtime" not in layout:
            raise AssertionError(f"install-layout doctor did not expose readiness/schema fields: {layout}")

        trace = fixture.run_json("workflow", "trace", "--endpoint", "v9-harness", "--json", "--since", "2000-01-01T00:00:00+00:00")
        if not trace["route_transitions"] or not trace["review_status_changes"] or not trace["artifact_index_changes"] or trace["json"] is not True:
            raise AssertionError(f"workflow trace missed key v9 events: {trace}")

        print(json.dumps({"ok": True, "v9_agent_enablement_harness": "passed", "fixture_writes": fixture.writes}))
        return 0
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
