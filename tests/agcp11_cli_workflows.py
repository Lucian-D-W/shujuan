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


def doctor_codes(payload: dict) -> set[str]:
    return {
        item["code"]
        for bucket in payload["severity_buckets"].values()
        for item in bucket
    }


def check_closed_by(repo: Path, check_id: str) -> str | None:
    conn = connect(repo)
    try:
        row = conn.execute("SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone()
        return row["closed_by_node_id"] if row else None
    finally:
        conn.close()


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-agcp11-cli-") as temp:
        repo = Path(temp)
        try:
            init = run(
                repo,
                "init",
                "--name",
                "agcp11-cli-workflows",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

            (repo / "plan.md").write_text(
                "# AGCP11 CLI Workflows\n\n## Acceptance\n\nIntake, split, focus, prove, review, close dry-run, and doctor enhancement commands exist.\n",
                encoding="utf-8",
            )
            doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            source_node = doc["document_node_id"]
            contract = run(repo, "scope", "create", "--body", "AGCP11 focused contract.", "--source-node", source_node)
            task = run(
                repo,
                "task",
                "add",
                "--contract",
                contract["contract_id"],
                "--body",
                "AGCP11 focused task.",
                "--from-node",
                source_node,
            )
            check = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task["task_id"],
                "--body",
                "Repeatable tests prove AGCP11 CLI workflows.",
                "--expected-evidence-type",
                "test_result",
                "--from-node",
                source_node,
            )
            endpoint_name = "agcp11-cli"
            run(repo, "endpoint", "create", endpoint_name, "--description", "AGCP11 endpoint.", "--root-node", contract["node_id"])

            intake = run(
                repo,
                "work",
                "intake",
                "--endpoint",
                endpoint_name,
                "--source-node",
                source_node,
                "--source-locator",
                "plan.md#Acceptance",
                "--promise-id",
                "SP-AGCP11-CLI",
                "--text",
                "AGCP11 CLI workflows must be executable and not prose-only.",
                "--predicate",
                "HP-AGCP11-CLI::intake split focus prove review close and doctor commands exist",
                "--forbidden-substitute",
                "HP-AGCP11-CLI::prose-only workflow::CLI must be repeatable",
            )
            if intake["source_promise_id"] != "SP-AGCP11-CLI" or not intake["hard_predicates"]:
                raise AssertionError(f"work intake did not create source promise and predicate: {intake}")

            unmapped_doctor = run(repo, "endpoint", "doctor", endpoint_name, "--strict-closeout", "--allow-fail")
            if "hard_predicate_without_task_link" not in doctor_codes(unmapped_doctor):
                raise AssertionError(f"doctor did not flag unmapped hard predicate: {unmapped_doctor}")

            split = run(
                repo,
                "work",
                "split",
                "--endpoint",
                endpoint_name,
                "--name",
                "AGCP11 CLI workflow slice",
                "--chain-id",
                "WC-AGCP11-CLI",
                "--task",
                task["task_id"],
                "--check",
                check["acceptance_check_id"],
                "--predicate",
                "HP-AGCP11-CLI",
            )
            if split["work_chain_id"] != "WC-AGCP11-CLI" or not split["task_predicate_links"]:
                raise AssertionError(f"work split did not create chain and predicate link: {split}")

            focus = run(repo, "work", "focus", "--endpoint", endpoint_name, "--work-chain", "WC-AGCP11-CLI")
            if not focus["read_only"] or focus["db_writes"] != 0 or not focus["hard_predicates"]:
                raise AssertionError(f"work focus did not produce a read-only attention packet: {focus}")

            downgrade = run_fails(
                repo,
                "work",
                "start",
                "--mode",
                "light",
                "--endpoint",
                endpoint_name,
                "--task",
                task["task_id"],
                "--content",
                "schema endpoint review evidence closeout work",
            )
            if "High-risk work must not silently downgrade to Light" not in downgrade.stderr:
                raise AssertionError(f"mode router non-downgrade failure was unclear: {downgrade.stderr}")

            run(repo, "workflow", "begin", "--session-id", "session_agcp11", "--endpoint", endpoint_name, "--content", "Run AGCP11 workflow test.")
            started = run(
                repo,
                "work",
                "start",
                "--mode",
                "standard",
                "--endpoint",
                endpoint_name,
                "--task",
                task["task_id"],
                "--session-id",
                "session_agcp11",
            )
            if not started["run_id"] or not started["contract"]["creates_run"]:
                raise AssertionError(f"work start standard did not create an execution run: {started}")

            default_close = run(repo, "work", "close", "--mode", "full", "--endpoint", endpoint_name)
            if not default_close["dry_run"] or not default_close["default_dry_run"]:
                raise AssertionError(f"work close did not default to dry-run: {default_close}")
            if "endpoint doctor --strict-closeout" not in default_close["full_closeout_requirements"]:
                raise AssertionError(f"full close dry-run did not list doctor requirement: {default_close}")
            agcp_visibility = default_close.get("agcp_closeout_visibility")
            if not agcp_visibility:
                raise AssertionError(f"work close dry-run did not expose AGCP visibility: {default_close}")
            if agcp_visibility["active_hard_predicate_count"] != 1:
                raise AssertionError(f"AGCP visibility lost active hard predicate count: {agcp_visibility}")
            if agcp_visibility["unmapped_hard_predicate_count"] != 0 or agcp_visibility["unmapped_hard_predicate_ids"]:
                raise AssertionError(f"AGCP visibility did not reflect split predicate mapping: {agcp_visibility}")
            if agcp_visibility["linked_task_check_predicate_count"] != 1:
                raise AssertionError(f"AGCP visibility lost task/check predicate links: {agcp_visibility}")
            if agcp_visibility["closed_checks_missing_predicate_coverage_count"] != 0:
                raise AssertionError(f"AGCP visibility reported unexpected missing predicate coverage: {agcp_visibility}")
            if agcp_visibility["non_accepting_review_result_count"] != 0:
                raise AssertionError(f"AGCP visibility reported unexpected review blockers before review submit: {agcp_visibility}")
            if "not closure evidence" not in agcp_visibility["note"] or "does not close checks or tasks" not in agcp_visibility["note"]:
                raise AssertionError(f"AGCP visibility omitted no-closure note: {agcp_visibility}")

            matrix_path = repo / "agcp11_matrix.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "predicate_coverage_matrix": [
                            {
                                "check_id": check["acceptance_check_id"],
                                "predicate_id": "HP-AGCP11-CLI",
                                "assertion": "AGCP11 command workflow test reached the proof lane.",
                                "result": "pass",
                                "not_covered": False,
                                "reason": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            evidence = run(
                repo,
                "evidence",
                "test-result",
                "--predicate-coverage-matrix",
                "agcp11_matrix.json",
                "--check",
                check["acceptance_check_id"],
                "--from-node",
                source_node,
                "--",
                sys.executable,
                "-c",
                "print('agcp11 proof evidence')",
            )
            dry_prove = run(
                repo,
                "work",
                "prove",
                "--endpoint",
                endpoint_name,
                "--evidence-node",
                evidence["node_id"],
                "--check",
                check["acceptance_check_id"],
                "--close-check",
            )
            if not dry_prove["dry_run"] or check_closed_by(repo, check["acceptance_check_id"]):
                raise AssertionError(f"work prove dry-run mutated closure state: {dry_prove}")

            review_bundle = run(repo, "review", "start", "--endpoint", endpoint_name, "--work-chain", "WC-AGCP11-CLI")
            if not review_bundle["read_only"] or not review_bundle["mandatory_input_bundle"]["hard_predicates"]:
                raise AssertionError(f"review start did not produce read-only hard predicate bundle: {review_bundle}")

            review_artifact = repo / "review_result.md"
            review_artifact.write_text("read_only_attestation: true\nverdict: partial\n", encoding="utf-8")
            review = run(
                repo,
                "review",
                "submit",
                "--endpoint",
                endpoint_name,
                "--work-chain",
                "WC-AGCP11-CLI",
                "--result",
                "partial",
                "--summary",
                "Partial review keeps controller closure authority.",
                "--artifact",
                "review_result.md",
                "--read-only-attested",
            )
            if review["closure_claim"] or review["result"] != "partial":
                raise AssertionError(f"review submit claimed closure or lost result: {review}")
            review_doctor = run(repo, "endpoint", "doctor", endpoint_name, "--strict-closeout", "--allow-fail")
            if "review_not_accepting_closeout" not in doctor_codes(review_doctor):
                raise AssertionError(f"doctor did not surface partial review blocker: {review_doctor}")

            applied_prove = run(
                repo,
                "work",
                "prove",
                "--apply",
                "--endpoint",
                endpoint_name,
                "--evidence-node",
                evidence["node_id"],
                "--check",
                check["acceptance_check_id"],
                "--close-check",
            )
            if not applied_prove["proof"]["closed_checks"] or check_closed_by(repo, check["acceptance_check_id"]) != evidence["node_id"]:
                raise AssertionError(f"work prove apply did not close with matching test_result evidence: {applied_prove}")
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
