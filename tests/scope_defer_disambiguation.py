from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import postgres_fixture


def setup_scope(fixture) -> dict:
    repo = fixture.repo
    (repo / "plan.md").write_text("# Scope/defer\n\nDisambiguate scope notes and defers.\n", encoding="utf-8")
    doc = fixture.run_json("doc", "import", "plan.md", "--source-type", "plan")
    scope = fixture.run_json(
        "scope",
        "create",
        "--body",
        "Scope/defer disambiguation fixture.",
        "--source-node",
        doc["document_node_id"],
    )
    task = fixture.run_json(
        "task",
        "add",
        "--body",
        "Keep active until an explicit defer-like command targets the task.",
        "--contract",
        scope["contract_id"],
        "--from-node",
        doc["document_node_id"],
    )
    endpoint = fixture.run_json("endpoint", "create", "scope-defer", "--root-node", scope["node_id"])
    return {"doc": doc, "scope": scope, "task": task, "endpoint": endpoint}


def task_ids(status: dict, key: str) -> set[str]:
    return {item["id"] for item in status[key]}


def main() -> int:
    fixture_pair = postgres_fixture("scope-defer-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    temp, fixture = fixture_pair
    try:
        setup = setup_scope(fixture)
        task_id = setup["task"]["task_id"]
        task_node_id = setup["task"]["node_id"]
        source_node_id = setup["doc"]["document_node_id"]

        before = fixture.run_json("endpoint", "status", "scope-defer")
        if task_id not in task_ids(before, "current_tasks") or task_id in task_ids(before, "deferred_tasks"):
            raise AssertionError(f"fixture task did not start active/current: {before}")

        scope_note = fixture.run_json(
            "scope",
            "change",
            "--body",
            "Record a scope note against the task without deferring it.",
            "--source-node",
            source_node_id,
            "--applies-to",
            task_node_id,
        )
        note_effects = scope_note["state_effects"]
        if note_effects["applies_to_targets"]["endpoint_lifecycle_effect"] != "unchanged":
            raise AssertionError(f"scope change --applies-to reported a lifecycle change: {scope_note}")
        if note_effects["applies_to_targets"]["deferred_by_edge_added"]:
            raise AssertionError(f"scope change --applies-to reported a DEFERRED_BY edge: {scope_note}")
        after_note = fixture.run_json("endpoint", "status", "scope-defer")
        if task_id not in task_ids(after_note, "current_tasks") or task_id in task_ids(after_note, "deferred_tasks"):
            raise AssertionError(f"scope change --applies-to changed active/deferred task state: {after_note}")

        blocked_scope_task = fixture.run_json(
            "scope",
            "change",
            "--body",
            "Attempt a defer-like scope change without the explicit acknowledgement flags.",
            "--source-node",
            source_node_id,
            "--task",
            task_id,
            expect_ok=False,
        )
        if blocked_scope_task["error"]["code"] != "scope_change_requires_explicit_state_ack":
            raise AssertionError(f"scope change --task did not fail closed with a structured error: {blocked_scope_task}")

        scope_task = fixture.run_json(
            "scope",
            "change",
            "--body",
            "Narrow scope in a way that defers the task.",
            "--source-node",
            source_node_id,
            "--task",
            task_id,
            "--state-changing",
            "--ack-defer-like",
        )
        task_effects = scope_task["state_effects"]
        if task_effects["task_targets"]["endpoint_lifecycle_effect"] != "treated_as_deferred_non_active":
            raise AssertionError(f"scope change --task did not report deferred/non-active effect: {scope_task}")
        if not task_effects["task_targets"]["deferred_by_edge_added"]:
            raise AssertionError(f"scope change --task did not report DEFERRED_BY edge creation: {scope_task}")
        notes = "\n".join(scope_task["command_effects"]["notes"])
        if "prefer `task defer --task`" not in notes:
            raise AssertionError(f"scope change --task did not recommend task defer for ordinary defers: {scope_task}")

        after_task = fixture.run_json("endpoint", "status", "scope-defer")
        if task_id in task_ids(after_task, "current_tasks") or task_id not in task_ids(after_task, "deferred_tasks"):
            raise AssertionError(f"scope change --task did not move the task to deferred endpoint facts: {after_task}")

        print(json.dumps({"ok": True, "scope_defer_disambiguation": "passed", "fixture_writes": fixture.writes}))
        return 0
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
