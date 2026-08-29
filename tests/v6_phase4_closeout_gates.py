from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.store import connect


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
        raise AssertionError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def run(repo: Path, *args: str) -> dict:
    return json.loads(run_cli(repo, *args).stdout)


def run_fails(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_cli(repo, *args, expect_ok=False)


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


def check_closed_by(repo: Path, check_id: str) -> str | None:
    conn = connect(repo)
    try:
        row = conn.execute("SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone()
        return row["closed_by_node_id"] if row else None
    finally:
        conn.close()


def task_closed_by(repo: Path, task_id: str) -> str | None:
    conn = connect(repo)
    try:
        row = conn.execute("SELECT closed_by_node_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row["closed_by_node_id"] if row else None
    finally:
        conn.close()


def doctor_codes(payload: dict) -> set[str]:
    return {
        item["code"]
        for bucket in payload["severity_buckets"].values()
        for item in bucket
    }


def bootstrap_fixture(repo: Path) -> dict:
    init = run(
        repo,
        "init",
        "--name",
        "v6-phase4-closeout-gates",
        "--postgres-dev",
        "--postgres-dev-port",
        str(free_port()),
    )
    if init["database"]["backend"] != "postgres":
        raise AssertionError(f"fixture did not use PostgreSQL: {init}")
    (repo / "plan.md").write_text(
        "# V6 Phase 4\n\nCloseout gates must preview blockers before apply.\n",
        encoding="utf-8",
    )
    doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
    source_node = doc["document_node_id"]
    contract = run(repo, "scope", "create", "--body", "Phase 4 closeout gate fixture.", "--source-node", source_node)
    task = run(
        repo,
        "task",
        "add",
        "--contract",
        contract["contract_id"],
        "--body",
        "Implement closeout gate matrix.",
        "--from-node",
        source_node,
    )
    test_check = run(
        repo,
        "acceptance",
        "add",
        "--task",
        task["task_id"],
        "--body",
        "A passing test_result is required for the closeout gate matrix.",
        "--expected-evidence-type",
        "test_result",
        "--from-node",
        source_node,
    )
    artifact_check = run(
        repo,
        "acceptance",
        "add",
        "--task",
        task["task_id"],
        "--body",
        "An artifact-backed check remains open to block task closure.",
        "--expected-evidence-type",
        "artifact",
        "--from-node",
        source_node,
    )
    endpoint_name = "v6-phase4-closeout"
    run(repo, "endpoint", "create", endpoint_name, "--description", "Phase 4 gate fixture.", "--root-node", contract["node_id"])
    run(
        repo,
        "work",
        "intake",
        "--endpoint",
        endpoint_name,
        "--source-node",
        source_node,
        "--source-locator",
        "plan.md#V6 Phase 4",
        "--promise-id",
        "SP-V6-PHASE4",
        "--text",
        "Closeout must expose a V6 dry-run matrix before apply.",
        "--predicate",
        "HP-V6-PHASE4::dry-run matrix lists closure gates and stop reasons",
    )
    return {
        "endpoint": endpoint_name,
        "source_node": source_node,
        "task_id": task["task_id"],
        "task_node_id": task["node_id"],
        "test_check_id": test_check["acceptance_check_id"],
        "test_check_node_id": test_check["node_id"],
        "artifact_check_id": artifact_check["acceptance_check_id"],
    }


def assert_gate_matrix(repo: Path, fixture: dict) -> None:
    payload = run(
        repo,
        "work",
        "close",
        "--dry-run",
        "--mode",
        "full",
        "--endpoint",
        fixture["endpoint"],
        "--check",
        fixture["test_check_id"],
        "--close-check",
        "--task",
        fixture["task_id"],
        "--close-task",
    )
    matrix = payload.get("gate_matrix")
    if not matrix or matrix["version"] != "activation.v6.closeout_gate_matrix":
        raise AssertionError(f"dry-run omitted V6 gate matrix: {payload}")
    if not matrix["dry_run_non_mutating"] or not payload["dry_run"]:
        raise AssertionError(f"gate matrix did not remain dry-run: {matrix}")
    if fixture["test_check_id"] not in {item["id"] for item in matrix["target_check_closures"]}:
        raise AssertionError(f"target check closure missing: {matrix}")
    if fixture["task_id"] not in {item["id"] for item in matrix["target_task_closures"]}:
        raise AssertionError(f"target task closure missing: {matrix}")
    if fixture["test_check_id"] not in {item["check_id"] for item in matrix["missing_evidence"]}:
        raise AssertionError(f"missing evidence was not visible: {matrix}")
    if fixture["test_check_id"] not in {item["check_id"] for item in matrix["expected_evidence_mismatches"]}:
        raise AssertionError(f"expected evidence mismatch was not visible: {matrix}")
    if not matrix["predicate_coverage_gaps"]:
        raise AssertionError(f"predicate coverage gaps were not visible: {matrix}")
    if not matrix["active_blockers"]:
        raise AssertionError(f"active blockers were not visible: {matrix}")
    if fixture["artifact_check_id"] not in {
        check_id for item in matrix["task_closure_blockers"] for check_id in item["open_check_ids"]
    }:
        raise AssertionError(f"task closure did not report open checks: {matrix}")
    required_stop_reasons = {
        "missing_evidence",
        "expected_evidence_mismatch",
        "predicate_coverage_gap",
        "task_has_open_checks",
        "active_blockers_present",
    }
    if not required_stop_reasons <= set(matrix["stop_reasons"]):
        raise AssertionError(f"stop reasons missing: {matrix}")
    for action in ("close_target_checks", "close_target_tasks", "run_endpoint_doctor_strict_closeout", "run_evidence_verify"):
        if action not in matrix["apply_actions"]:
            raise AssertionError(f"apply action {action} missing: {matrix}")
    if check_closed_by(repo, fixture["test_check_id"]) or task_closed_by(repo, fixture["task_id"]):
        raise AssertionError("work close dry-run mutated check or task closure state")


def assert_closure_safeguards(repo: Path, fixture: dict) -> None:
    (repo / "artifact.txt").write_text("artifact proof", encoding="utf-8")
    artifact = run(repo, "evidence", "artifact", "--path", "artifact.txt", "--from-node", fixture["source_node"])
    mismatch = run_fails(
        repo,
        "acceptance",
        "close",
        "--check",
        fixture["test_check_id"],
        "--evidence-node",
        artifact["node_id"],
    )
    if "expects evidence type test_result" not in mismatch.stderr:
        raise AssertionError(f"evidence type mismatch did not block closure: {mismatch.stderr}")

    failed = run(
        repo,
        "evidence",
        "test-result",
        "--check",
        fixture["test_check_id"],
        "--close-check",
        "--allow-fail",
        "--from-node",
        fixture["source_node"],
        "--",
        sys.executable,
        "-c",
        "import sys; sys.exit(1)",
    )
    if not failed["close_skipped"]["skipped"] or check_closed_by(repo, fixture["test_check_id"]):
        raise AssertionError(f"failed test_result closed a check or missed skip metadata: {failed}")

    passed = run(
        repo,
        "evidence",
        "test-result",
        "--check",
        fixture["test_check_id"],
        "--from-node",
        fixture["source_node"],
        "--",
        sys.executable,
        "-c",
        "print('phase4 pass')",
    )
    task_block = run_fails(
        repo,
        "acceptance",
        "close",
        "--check",
        fixture["test_check_id"],
        "--evidence-node",
        passed["node_id"],
        "--close-task",
    )
    if "cannot close while acceptance checks remain open" not in task_block.stderr:
        raise AssertionError(f"task closure was not blocked by open checks: {task_block.stderr}")
    if task_closed_by(repo, fixture["task_id"]):
        raise AssertionError("task closed while an acceptance check remained open")

    provider_path = repo / "provider.json"
    provider_path.write_text(
        json.dumps(
            {
                "provider": "phase4-provider",
                "contract_version": "shujuan.impact_provider.v1",
                "status": "executed",
                "facts": [
                    {
                        "external_id": "check:test",
                        "fact_type": "impact",
                        "summary": "Provider material cannot close acceptance checks.",
                        "classification": "provider_fact",
                    }
                ],
                "entity_map": [
                    {
                        "external_id": "check:test",
                        "node_id": fixture["test_check_node_id"],
                        "confidence": 0.9,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    provider = run(
        repo,
        "provider",
        "import-json",
        "--path",
        "provider.json",
        "--endpoint",
        fixture["endpoint"],
        "--source-node",
        fixture["source_node"],
    )
    provider_close = run_fails(
        repo,
        "acceptance",
        "close",
        "--check",
        fixture["test_check_id"],
        "--evidence-node",
        provider["facts"][0]["node_id"],
    )
    if "closing acceptance checks requires evidence node type" not in provider_close.stderr:
        raise AssertionError(f"provider fact was not rejected as closure evidence: {provider_close.stderr}")

    (repo / "review.md").write_text("read_only_attestation: true\nverdict: accept\n", encoding="utf-8")
    review = run(
        repo,
        "review",
        "submit",
        "--endpoint",
        fixture["endpoint"],
        "--check",
        fixture["test_check_id"],
        "--result",
        "accept",
        "--summary",
        "Read-only review acceptance is material only.",
        "--artifact",
        "review.md",
        "--read-only-attested",
    )
    if review["closure_claim"] or check_closed_by(repo, fixture["test_check_id"]):
        raise AssertionError(f"review accept closed a check: {review}")
    review_close = run_fails(
        repo,
        "acceptance",
        "close",
        "--check",
        fixture["test_check_id"],
        "--evidence-node",
        review["artifact_node_id"],
    )
    if "expects evidence type test_result" not in review_close.stderr:
        raise AssertionError(f"review artifact did not remain subject to evidence gates: {review_close.stderr}")


def assert_strict_doctor_blocks_active_work(repo: Path, fixture: dict) -> None:
    doctor = run(repo, "endpoint", "doctor", fixture["endpoint"], "--strict-closeout", "--allow-fail")
    if doctor["ok"]:
        raise AssertionError(f"strict doctor passed despite active blockers: {doctor}")
    severities = {severity for severity, items in doctor["severity_buckets"].items() if items}
    if not ({"P0", "P1"} & severities):
        raise AssertionError(f"strict doctor did not surface P0/P1 blockers: {doctor}")
    codes = doctor_codes(doctor)
    if not {"open_obligations", "hard_predicate_without_task_link"} & codes:
        raise AssertionError(f"strict doctor missed expected active blockers: {doctor}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-v6-phase4-gates-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        try:
            fixture = bootstrap_fixture(repo)
            postgres_started = True
            assert_gate_matrix(repo, fixture)
            assert_closure_safeguards(repo, fixture)
            assert_strict_doctor_blocks_active_work(repo, fixture)
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")
    print(json.dumps({"ok": True, "v6_phase4_closeout_gates": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
