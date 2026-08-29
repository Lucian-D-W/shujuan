from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / ".agents" / "skills" / "shujuan-core" / "references" / "plan-to-db-task-chain-hygiene.md"


REQUIRED_REFERENCE_FRAGMENTS = [
    "P0",
    "P1",
    "P2",
    "non-goal",
    "Make phase and order explicit",
    "Keep the golden path minimal",
    "`source`",
    "`successor`",
    "`lineage`",
    "`blocker`",
    "single-intent",
    "Align evidence type with the check body",
    "reviewer, provider, and delegate outputs as material only",
    "Record controller adoption or rejection of feedback",
    "Avoid false closeout",
    "Implicit phase order",
    "P1/P2 leakage into P0 golden path",
    "Relation-as-blocker mistake",
    "Evidence/body mismatch",
    "Reviewer/provider material treated as closure",
    "Unsynchronized decomposition artifacts",
    "Non-Compression Rule",
    "Required Decomposition Output Shape",
    "promotion_rule",
    "reopen_rule",
    "absorbed",
    "superseded",
    "indirectly_dissolved",
    "plan-to-db verify-artifact",
    "plan-to-db lifecycle-reconcile",
]


GOOD_CHAIN = {
    "classifications": {
        "task_parse_source": "P0",
        "task_reference_doc": "P0",
        "task_validation_fixture": "P0",
        "task_provider_matrix": "P2",
        "task_multi_agent_planner": "non-goal",
    },
    "tasks": [
        {"id": "task_parse_source", "priority": "P0", "phase": 1, "golden_path": True},
        {"id": "task_reference_doc", "priority": "P0", "phase": 2, "golden_path": True},
        {"id": "task_validation_fixture", "priority": "P0", "phase": 3, "golden_path": True},
        {"id": "task_provider_matrix", "priority": "P2", "phase": None, "golden_path": False},
    ],
    "relations": [
        {"from": "source_plan", "to": "task_reference_doc", "kind": "source", "active": False},
        {"from": "v5", "to": "v6", "kind": "successor", "active": False},
        {"from": "v4", "to": "v6", "kind": "lineage", "active": False},
        {"from": "check_validation_fixture", "to": "task_validation_fixture", "kind": "blocker", "active": True},
    ],
    "checks": [
        {
            "id": "check_reference_doc",
            "task": "task_reference_doc",
            "body": "Add plan-to-DB hygiene documentation in the shujuan-core reference surface.",
            "expected_evidence_type": "change_set",
        },
        {
            "id": "check_validation_fixture",
            "task": "task_validation_fixture",
            "body": "Run a deterministic validation fixture that catches the hygiene failure modes.",
            "expected_evidence_type": "test_result",
        },
    ],
    "materials": [
        {"kind": "reviewer_output", "controller_decision": "adopted", "result_ref": "check_validation_fixture"},
        {"kind": "provider_fact", "controller_decision": "rejected", "reason": "not needed for lightweight scope"},
        {"kind": "delegate_packet", "controller_decision": "material_only"},
    ],
    "artifacts": [
        {"name": "reference", "classifications": {"task_reference_doc": "P0"}, "phases": {"task_reference_doc": 2}},
        {"name": "fixture", "classifications": {"task_reference_doc": "P0"}, "phases": {"task_reference_doc": 2}},
    ],
    "closed_by_decomposition": False,
}


BAD_CASES = {
    "implicit_phase_order": {
        **GOOD_CHAIN,
        "tasks": [
            {"id": "task_parse_source", "priority": "P0", "phase": None, "golden_path": True},
            {"id": "task_reference_doc", "priority": "P0", "phase": None, "golden_path": True},
        ],
    },
    "p1_p2_leakage_into_p0": {
        **GOOD_CHAIN,
        "tasks": [
            *GOOD_CHAIN["tasks"],
            {"id": "task_provider_matrix", "priority": "P2", "phase": 4, "golden_path": True},
        ],
    },
    "relation_as_blocker_mistake": {
        **GOOD_CHAIN,
        "relations": [{"from": "v5", "to": "v6", "kind": "successor", "active": True}],
    },
    "evidence_body_mismatch": {
        **GOOD_CHAIN,
        "checks": [
            {
                "id": "check_mismatch",
                "task": "task_validation_fixture",
                "body": "Run a deterministic validation fixture that catches the hygiene failure modes.",
                "expected_evidence_type": "change_set",
            }
        ],
    },
    "reviewer_provider_material_treated_as_closure": {
        **GOOD_CHAIN,
        "materials": [{"kind": "provider_fact", "controller_decision": "closes_check", "result_ref": "check_reference_doc"}],
    },
    "unsynchronized_decomposition_artifacts": {
        **GOOD_CHAIN,
        "artifacts": [
            {"name": "reference", "classifications": {"task_reference_doc": "P0"}, "phases": {"task_reference_doc": 2}},
            {"name": "fixture", "classifications": {"task_reference_doc": "P1"}, "phases": {"task_reference_doc": 3}},
        ],
    },
}


GOOD_ARTIFACT = {
    "declares_no_closure": True,
    "source_items": [
        {
            "id": "deliverable_parser",
            "source_ref": "plan.md#parser",
            "classification": "P0",
            "status": "active",
            "graph_destination": {"kind": "task", "id": "task_parser"},
            "task_ids": ["task_parser"],
            "check_ids": ["check_parser"],
            "rationale": "Parser deliverable maps to a dedicated task and check.",
            "promotion_rule": "Already active.",
            "reopen_rule": "Reopen by restoring task_parser/check_parser if superseded.",
        },
        {
            "id": "deliverable_legacy_report",
            "source_ref": "plan.md#legacy-report",
            "classification": "P1",
            "status": "absorbed",
            "graph_destination": {"kind": "task", "id": "task_parser"},
            "absorbed_by": "task_parser",
            "rationale": "The parser task consumes this legacy report requirement.",
            "promotion_rule": "Promote only by creating a new explicit task/check.",
            "reopen_rule": "Reopen from plan.md#legacy-report with the absorption rationale preserved.",
        },
        {
            "id": "deliverable_old_contract",
            "source_ref": "plan.md#old-contract",
            "classification": "P1",
            "status": "superseded",
            "graph_destination": {"kind": "task", "id": "task_parser"},
            "superseded_by": "task_parser",
            "rationale": "The parser contract supersedes the old contract row.",
            "promotion_rule": "Promote only after source review confirms it is not superseded.",
            "reopen_rule": "Reopen as an explicit task/check pair if the old contract becomes active again.",
        },
        {
            "id": "deliverable_indirect",
            "source_ref": "plan.md#indirect",
            "classification": "P2",
            "status": "indirectly_dissolved",
            "graph_destination": {"kind": "defer_decision", "id": "defer_indirect"},
            "dissolved_by": "defer_indirect",
            "rationale": "The active P0 path removes the need for this indirect follow-up.",
            "promotion_rule": "Promote only through a new scope decision.",
            "reopen_rule": "Reopen with a source-backed defer or task row.",
        },
    ],
    "closed_by_decomposition": False,
}


BAD_ARTIFACT_CASES = {
    "compressed_named_deliverables": (
        {
            "source_items": [
                {
                    **GOOD_ARTIFACT["source_items"][0],
                    "id": "compressed_parent",
                    "named_deliverables": ["parser", "validator"],
                    "decomposed_items": ["parser"],
                }
            ]
        },
        "compressed_named_deliverables",
    ),
    "artifact_only_slice": (
        {
            "source_items": [
                {
                    **GOOD_ARTIFACT["source_items"][0],
                    "id": "artifact_only",
                    "graph_destination": {"kind": "artifact", "id": "docs/report.md"},
                    "task_ids": [],
                    "check_ids": [],
                }
            ]
        },
        "artifact_only_slice",
    ),
    "unsafe_broad_parent_promotion": (
        {
            "source_items": [
                {
                    **GOOD_ARTIFACT["source_items"][0],
                    "id": "broad_parent",
                    "graph_destination": {"kind": "umbrella", "id": "task_parent"},
                }
            ]
        },
        "unsafe_broad_parent_promotion",
    ),
    "unlinked_inactive_item": (
        {
            "source_items": [
                {
                    **GOOD_ARTIFACT["source_items"][1],
                    "id": "absorbed_without_destination",
                    "graph_destination": {"kind": "task"},
                    "absorbed_by": None,
                }
            ]
        },
        "unlinked_inactive_item",
    ),
    "false_closeout_claim": ({**GOOD_ARTIFACT, "closed_by_decomposition": True}, "false_closeout_claim"),
}


def run_cli(repo: Path, *args: str, expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
        env.pop(key, None)
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if expect_ok and completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def run_json(repo: Path, *args: str) -> dict:
    return json.loads(run_cli(repo, *args).stdout)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def has_postgres_bins() -> bool:
    candidates = []
    env_bin = os.environ.get("SHUJUAN_POSTGRES_BIN")
    if env_bin:
        candidates.append(Path(env_bin))
    candidates.append(Path(r"C:\Program Files\PostgreSQL\17\bin"))
    return any((path / "initdb.exe").exists() or (path / "initdb").exists() for path in candidates)


def assert_reference_surface() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    missing = [fragment for fragment in REQUIRED_REFERENCE_FRAGMENTS if fragment not in text]
    if missing:
        raise AssertionError(f"plan-to-DB hygiene reference is missing required fragments: {missing}")
    if "not a planner framework" not in text:
        raise AssertionError("reference must keep the method boundary lightweight")


def validate_plan_chain(chain: dict) -> list[str]:
    errors: list[str] = []
    task_by_id = {task["id"]: task for task in chain["tasks"]}
    classifications = chain.get("classifications", {})

    for task in chain["tasks"]:
        task_id = task["id"]
        priority = task.get("priority")
        if priority != classifications.get(task_id):
            errors.append(f"{task_id}: classification is not synchronized with task priority")
        if task.get("golden_path") and priority != "P0":
            errors.append(f"{task_id}: non-P0 task leaked into P0 golden path")

    p0_tasks = [task for task in chain["tasks"] if task.get("priority") == "P0"]
    if len(p0_tasks) > 1 and any(task.get("phase") is None for task in p0_tasks):
        errors.append("implicit phase order: P0 chain has multiple tasks without explicit phases")

    for relation in chain["relations"]:
        if relation.get("active") and relation.get("kind") != "blocker":
            errors.append(f"{relation['from']}->{relation['to']}: {relation['kind']} relation was treated as a blocker")

    for check in chain["checks"]:
        body = check.get("body", "").lower()
        expected = check.get("expected_evidence_type")
        if check.get("task") not in task_by_id:
            errors.append(f"{check['id']}: acceptance check references a missing task")
        if re.search(r"\b(run|test|fixture|command)\b", body) and expected != "test_result":
            errors.append(f"{check['id']}: test-like body expects {expected}")
        if re.search(r"\b(add|edit|documentation|file|reference)\b", body) and "run" not in body and expected != "change_set":
            errors.append(f"{check['id']}: change-like body expects {expected}")
        if " and " in body and expected in {"change_set", "test_result", "artifact", "user_confirmation"}:
            errors.append(f"{check['id']}: acceptance check may have mixed intent")

    for material in chain["materials"]:
        if material.get("kind") in {"reviewer_output", "provider_fact", "provider_hypothesis", "delegate_packet"}:
            decision = material.get("controller_decision")
            if decision in {None, "", "closes_check", "closes_task", "closure"}:
                errors.append(f"{material['kind']}: material output lacks controller adoption/rejection boundary")

    artifact_baseline = chain["artifacts"][0]
    for artifact in chain["artifacts"][1:]:
        if artifact.get("classifications") != artifact_baseline.get("classifications"):
            errors.append(f"{artifact['name']}: classification disagrees with {artifact_baseline['name']}")
        if artifact.get("phases") != artifact_baseline.get("phases"):
            errors.append(f"{artifact['name']}: phase map disagrees with {artifact_baseline['name']}")

    if chain.get("closed_by_decomposition"):
        errors.append("plan decomposition must not claim closure")

    return errors


def assert_fixture_validator() -> None:
    good_errors = validate_plan_chain(GOOD_CHAIN)
    if good_errors:
        raise AssertionError(f"good hygiene fixture failed validation: {good_errors}")

    for name, fixture in BAD_CASES.items():
        errors = validate_plan_chain(fixture)
        if not errors:
            raise AssertionError(f"bad hygiene fixture did not fail: {name}")


def assert_plan_to_db_artifact_gate() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-plan-to-db-artifact-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        good_path = repo / "good.json"
        good_path.write_text(json.dumps(GOOD_ARTIFACT), encoding="utf-8")
        good = json.loads(run_cli(repo, "plan-to-db", "verify-artifact", "--artifact", str(good_path)).stdout)
        if not good["ok"] or good["checked_source_items"] != len(GOOD_ARTIFACT["source_items"]):
            raise AssertionError(f"good plan-to-DB artifact failed verification: {good}")

        for name, (artifact, expected_code) in BAD_ARTIFACT_CASES.items():
            path = repo / f"{name}.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")
            failed = json.loads(
                run_cli(repo, "plan-to-db", "verify-artifact", "--artifact", str(path), "--allow-fail").stdout
            )
            codes = {item["code"] for item in failed["violations"]}
            if failed["ok"] or expected_code not in codes:
                raise AssertionError(f"bad artifact {name} did not surface {expected_code}: {failed}")


def assert_lifecycle_reconciliation_gate() -> str | None:
    if not has_postgres_bins():
        return "native PostgreSQL binaries not found"

    with tempfile.TemporaryDirectory(prefix="shujuan-plan-to-db-lifecycle-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        postgres_started = False
        try:
            init = run_json(
                repo,
                "init",
                "--name",
                "plan-to-db-lifecycle",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")
            source = repo / "plan.md"
            source.write_text("# Plan\n\nResidual item should be resolved by a source-backed edge.\n", encoding="utf-8")
            doc = run_json(repo, "doc", "import", "plan.md", "--source-type", "plan")
            scope = run_json(repo, "scope", "create", "--body", "Lifecycle fixture scope.", "--source-node", doc["document_node_id"])
            task = run_json(
                repo,
                "task",
                "add",
                "--contract",
                scope["contract_id"],
                "--body",
                "Lifecycle fixture task.",
                "--from-node",
                doc["document_node_id"],
            )
            run_json(repo, "endpoint", "create", "fixture", "--description", "Lifecycle fixture.", "--root-node", scope["node_id"])
            residual = run_json(
                repo,
                "unresolved",
                "add",
                "--body",
                "Legacy residual that has already been resolved by graph evidence.",
                "--source-node",
                doc["document_node_id"],
                "--applies-to",
                scope["node_id"],
            )
            task_residual = run_json(
                repo,
                "unresolved",
                "add",
                "--body",
                "Task-scoped residual that endpoint dry-run must still see.",
                "--source-node",
                doc["document_node_id"],
                "--applies-to",
                task["node_id"],
            )
            run_json(
                repo,
                "graph",
                "link",
                "--from-node",
                doc["document_node_id"],
                "--to-node",
                residual["node_id"],
                "--type",
                "RESOLVES",
                "--reason",
                "Source plan resolution consumes this residual.",
            )
            run_json(
                repo,
                "graph",
                "link",
                "--from-node",
                doc["document_node_id"],
                "--to-node",
                task_residual["node_id"],
                "--type",
                "RESOLVES",
                "--reason",
                "Source plan resolution consumes the task-scoped residual.",
            )
            failed_gate = json.loads(
                run_cli(
                    repo,
                    "plan-to-db",
                    "lifecycle-reconcile",
                    "--endpoint",
                    "fixture",
                    "--allow-fail",
                ).stdout
            )
            if failed_gate["ok"] or failed_gate["candidate_count"] != 2:
                raise AssertionError(f"dry-run reconciliation gate did not flag active residual: {failed_gate}")
            candidates = {candidate["affected_node_id"]: candidate for candidate in failed_gate["candidates"]}
            if candidates[residual["node_id"]]["target_state"] != "resolved":
                raise AssertionError(f"dry-run candidate had wrong shape: {failed_gate}")
            if candidates[task_residual["node_id"]]["target_state"] != "resolved":
                raise AssertionError(f"dry-run candidate had wrong shape: {failed_gate}")

            run_cli(repo, "plan-to-db", "lifecycle-reconcile", "--endpoint", "fixture", expect_ok=False)
            applied = run_json(repo, "plan-to-db", "lifecycle-reconcile", "--endpoint", "fixture", "--apply")
            if not applied["ok"] or applied["applied_count"] != 2:
                raise AssertionError(f"apply did not reconcile active residual: {applied}")
            lifecycle = run_json(repo, "report", "lifecycle", "--item", residual["node_id"])
            if lifecycle["current_state"] != "resolved":
                raise AssertionError(f"reconciliation did not update semantic lifecycle through lifecycle state: {lifecycle}")
            task_lifecycle = run_json(repo, "report", "lifecycle", "--item", task_residual["node_id"])
            if task_lifecycle["current_state"] != "resolved":
                raise AssertionError(f"task-scoped reconciliation did not update semantic lifecycle: {task_lifecycle}")
        finally:
            if postgres_started:
                try:
                    run_cli(repo, "postgres-dev", "stop")
                except AssertionError:
                    pass
    return None


def main() -> int:
    assert_reference_surface()
    assert_fixture_validator()
    assert_plan_to_db_artifact_gate()
    lifecycle_skip = assert_lifecycle_reconciliation_gate()
    print(json.dumps({"ok": True, "plan_to_db_task_chain_hygiene": "passed", "lifecycle_skip": lifecycle_skip}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
