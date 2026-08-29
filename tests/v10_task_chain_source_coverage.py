from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import postgres_fixture


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _base_payload() -> dict:
    return {
        "declares_no_closure": True,
        "closed_by_decomposition": False,
        "endpoint": {"name": "v10-chain"},
        "tasks": [
            {"key": "T01", "title": "Primary task", "body": "Implement the source-backed task.", "phase": "P0", "order": 10, "mandatory": True}
        ],
        "checks": [
            {"key": "C01", "task_key": "T01", "body": "Verify the primary task.", "expected_evidence_type": "test_result"}
        ],
        "source_items": [
            {
                "id": "S01",
                "classification": "P0",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "T01"},
                "task_ids": ["T01"],
                "check_ids": ["C01"],
                "rationale": "Primary imported commitment.",
                "promotion_rule": "Already active.",
                "reopen_rule": "Restore T01/C01.",
            }
        ],
    }


def main() -> int:
    fixture_pair = postgres_fixture("v10-chain-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    temp, fixture = fixture_pair
    try:
        source = fixture.repo / "source.md"
        source.write_text("# v10 chain\n\nsource\n", encoding="utf-8")
        doc = fixture.run_json("doc", "import", "source.md", "--source-type", "plan")
        scope = fixture.run_json("scope", "create", "--body", "v10 chain scope", "--source-node", doc["document_node_id"])
        fixture.run_json("endpoint", "create", "v10-chain", "--root-node", scope["node_id"])

        uncovered_task = _base_payload()
        uncovered_task["tasks"].append({"key": "T02", "title": "Orphan", "body": "No source item coverage.", "phase": "P0", "order": 20, "mandatory": True})
        bad_task = _write_json(fixture.repo / "uncovered_task.json", uncovered_task)
        task_payload = fixture.run_json(
            "plan-to-db",
            "import-task-chain",
            "--artifact",
            str(bad_task),
            "--endpoint",
            "v10-chain",
            "--dry-run",
            expect_ok=False,
        )
        if task_payload["error"]["code"] != "task_chain_source_coverage_gap":
            raise AssertionError(f"uncovered task did not report a coverage error: {task_payload}")
        task_codes = {item["code"] for item in task_payload["violations"]}
        if "uncovered_task_without_source_item" not in task_codes:
            raise AssertionError(f"uncovered task violation missing: {task_payload}")

        uncovered_check = _base_payload()
        uncovered_check["checks"].append({"key": "C02", "task_key": "T01", "body": "No source item coverage.", "expected_evidence_type": "artifact"})
        bad_check = _write_json(fixture.repo / "uncovered_check.json", uncovered_check)
        check_payload = fixture.run_json(
            "plan-to-db",
            "import-task-chain",
            "--artifact",
            str(bad_check),
            "--endpoint",
            "v10-chain",
            "--dry-run",
            expect_ok=False,
        )
        check_codes = {item["code"] for item in check_payload["violations"]}
        if "uncovered_check_without_source_item" not in check_codes:
            raise AssertionError(f"uncovered check violation missing: {check_payload}")

        duplicate_source = _base_payload()
        duplicate_source["source_items"] = [
            {
                "id": "S01",
                "classification": "P0",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "T01"},
                "task_ids": ["T01"],
                "check_ids": ["C01"],
                "rationale": "Primary imported commitment.",
                "promotion_rule": "Already active.",
                "reopen_rule": "Restore T01/C01.",
            },
            {
                "id": "S01",
                "classification": "P1",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "T02"},
                "task_ids": ["T02"],
                "check_ids": ["C02"],
                "rationale": "Conflicting duplicate id.",
                "promotion_rule": "Already active.",
                "reopen_rule": "Restore T02/C02.",
            },
        ]
        duplicate_source["tasks"].append(
            {"key": "T02", "title": "Duplicate source ref task", "body": "Covered by a duplicate source id.", "phase": "P1", "order": 20, "mandatory": True}
        )
        duplicate_source["checks"].append(
            {"key": "C02", "task_key": "T02", "body": "Covered by a duplicate source id.", "expected_evidence_type": "artifact"}
        )
        duplicate_path = _write_json(fixture.repo / "duplicate_source_item.json", duplicate_source)
        duplicate_payload = fixture.run_json(
            "plan-to-db",
            "import-task-chain",
            "--artifact",
            str(duplicate_path),
            "--endpoint",
            "v10-chain",
            "--dry-run",
            expect_ok=False,
        )
        duplicate_codes = {item["code"] for item in duplicate_payload["violations"]}
        if duplicate_payload["error"]["code"] != "task_chain_source_coverage_gap":
            raise AssertionError(f"duplicate source id did not fail as source coverage gap: {duplicate_payload}")
        if "duplicate_source_item_id" not in duplicate_codes:
            raise AssertionError(f"duplicate source id violation missing: {duplicate_payload}")
        if "ambiguous_source_ref" not in duplicate_codes:
            raise AssertionError(f"ambiguous source ref violation missing: {duplicate_payload}")

        synthetic = _base_payload()
        synthetic["tasks"].append(
            {
                "key": "T_SYN",
                "title": "Controller integration task",
                "body": "Explicit synthetic task.",
                "phase": "P0",
                "order": 20,
                "mandatory": True,
                "synthetic": True,
                "controller_allowed_synthetic": True,
                "synthetic_rationale": "Controller-added integration step derived from the imported source item.",
                "derived_from_source_items": ["S01"],
            }
        )
        synthetic["checks"].append(
            {
                "key": "C_SYN",
                "task_key": "T_SYN",
                "body": "Synthetic check remains source-backed.",
                "expected_evidence_type": "artifact",
                "synthetic": True,
                "controller_allowed_synthetic": True,
                "synthetic_rationale": "Controller-added verification derived from the imported source item.",
                "derived_from_source_items": ["S01"],
            }
        )
        good_path = _write_json(fixture.repo / "synthetic_ok.json", synthetic)
        preview = fixture.run_json(
            "plan-to-db",
            "import-task-chain",
            "--artifact",
            str(good_path),
            "--endpoint",
            "v10-chain",
            "--dry-run",
        )
        if preview["source_coverage"]["tasks"]["T_SYN"]["synthetic"] is not True:
            raise AssertionError(f"synthetic task was not separated in source coverage: {preview}")
        if preview["source_coverage"]["uncovered_tasks"] or preview["source_coverage"]["uncovered_checks"]:
            raise AssertionError(f"synthetic source coverage still reported gaps: {preview}")

        valid_parent = _base_payload()
        valid_parent["tasks"].append(
            {
                "key": "T02",
                "title": "Child task",
                "body": "Child task with a valid parent.",
                "phase": "P0",
                "order": 20,
                "mandatory": True,
                "parent_key": "T01",
            }
        )
        valid_parent["checks"].append(
            {"key": "C02", "task_key": "T02", "body": "Verify the child task.", "expected_evidence_type": "artifact"}
        )
        valid_parent["source_items"].append(
            {
                "id": "S02",
                "classification": "P0",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "T02"},
                "task_ids": ["T02"],
                "check_ids": ["C02"],
                "rationale": "Child imported commitment.",
                "promotion_rule": "Already active.",
                "reopen_rule": "Restore T02/C02.",
            }
        )
        valid_parent_path = _write_json(fixture.repo / "valid_parent.json", valid_parent)
        valid_parent_preview = fixture.run_json(
            "plan-to-db",
            "import-task-chain",
            "--artifact",
            str(valid_parent_path),
            "--endpoint",
            "v10-chain",
            "--dry-run",
        )
        if {"parent_key": "T01", "child_key": "T02", "status": "ok"} not in valid_parent_preview["parent_links"]:
            raise AssertionError(f"valid parent link was not exposed in preview: {valid_parent_preview}")

        unknown_parent = _base_payload()
        unknown_parent["tasks"].append(
            {
                "key": "T02",
                "title": "Missing parent child",
                "body": "Child task with a missing parent.",
                "phase": "P0",
                "order": 20,
                "mandatory": True,
                "parent_key": "T_MISSING",
            }
        )
        unknown_parent["checks"].append(
            {"key": "C02", "task_key": "T02", "body": "Verify missing parent rejection.", "expected_evidence_type": "artifact"}
        )
        unknown_parent["source_items"].append(
            {
                "id": "S02",
                "classification": "P0",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "T02"},
                "task_ids": ["T02"],
                "check_ids": ["C02"],
                "rationale": "Child imported commitment.",
                "promotion_rule": "Already active.",
                "reopen_rule": "Restore T02/C02.",
            }
        )
        unknown_parent_path = _write_json(fixture.repo / "unknown_parent.json", unknown_parent)
        unknown_parent_preview = fixture.run_json(
            "plan-to-db",
            "import-task-chain",
            "--artifact",
            str(unknown_parent_path),
            "--endpoint",
            "v10-chain",
            "--dry-run",
            expect_ok=False,
        )
        unknown_codes = {item["code"] for item in unknown_parent_preview["violations"]}
        if "unknown_parent_task_key" not in unknown_codes or unknown_parent_preview["ok"] is not False:
            raise AssertionError(f"unknown parent_key did not fail structurally: {unknown_parent_preview}")
        if {"parent_key": "T_MISSING", "child_key": "T02", "status": "unknown_parent_task_key"} not in unknown_parent_preview["parent_links"]:
            raise AssertionError(f"unknown parent link was not exposed in preview: {unknown_parent_preview}")

        cycle_parent = _base_payload()
        cycle_parent["tasks"][0]["parent_key"] = "T02"
        cycle_parent["tasks"].append(
            {
                "key": "T02",
                "title": "Cycle task",
                "body": "Task with cyclic parent topology.",
                "phase": "P0",
                "order": 20,
                "mandatory": True,
                "parent_key": "T01",
            }
        )
        cycle_parent["checks"].append(
            {"key": "C02", "task_key": "T02", "body": "Verify cycle rejection.", "expected_evidence_type": "artifact"}
        )
        cycle_parent["source_items"].append(
            {
                "id": "S02",
                "classification": "P0",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "T02"},
                "task_ids": ["T02"],
                "check_ids": ["C02"],
                "rationale": "Cycle imported commitment.",
                "promotion_rule": "Already active.",
                "reopen_rule": "Restore T02/C02.",
            }
        )
        cycle_path = _write_json(fixture.repo / "cycle_parent.json", cycle_parent)
        cycle_preview = fixture.run_json(
            "plan-to-db",
            "import-task-chain",
            "--artifact",
            str(cycle_path),
            "--endpoint",
            "v10-chain",
            "--dry-run",
            expect_ok=False,
        )
        if "cycle_parent_task_key" not in {item["code"] for item in cycle_preview["violations"]}:
            raise AssertionError(f"cycle parent_key did not fail structurally: {cycle_preview}")

        applied = fixture.run_json(
            "plan-to-db",
            "import-task-chain",
            "--artifact",
            str(good_path),
            "--endpoint",
            "v10-chain",
            "--apply",
        )
        if applied["mapping"]["source_coverage"]["tasks"]["T_SYN"]["synthetic"] is not True:
            raise AssertionError(f"apply mapping dropped synthetic separation: {applied}")

        print(json.dumps({"ok": True, "v10_task_chain_source_coverage": "passed"}))
        return 0
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
