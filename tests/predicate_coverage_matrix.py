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


def fetch_check_closure(repo: Path, check_id: str) -> str | None:
    conn = connect(repo)
    row = conn.execute("SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone()
    conn.close()
    return row["closed_by_node_id"] if row else None


def fetch_node_props(repo: Path, node_id: str) -> dict:
    conn = connect(repo)
    row = conn.execute("SELECT props FROM nodes WHERE id = ?", (node_id,)).fetchone()
    conn.close()
    return json.loads(row["props"]) if row else {}


def fetch_warning_kind(repo: Path, node_id: str) -> str | None:
    conn = connect(repo)
    row = conn.execute("SELECT type, props FROM nodes WHERE id = ?", (node_id,)).fetchone()
    conn.close()
    if not row or row["type"] != "audit_finding":
        return None
    return json.loads(row["props"]).get("kind")


def fetch_coverage_rows(repo: Path, node_id: str) -> list[tuple[str, str, str]]:
    conn = connect(repo)
    rows = conn.execute(
        """
        SELECT check_id, predicate_id, result
        FROM evidence_predicate_coverage
        WHERE evidence_node_id = ?
        ORDER BY check_id ASC, predicate_id ASC
        """,
        (node_id,),
    ).fetchall()
    conn.close()
    return [(row["check_id"], row["predicate_id"], row["result"]) for row in rows]


def matrix_file(repo: Path, name: str, rows: list[dict]) -> str:
    path = repo / name
    path.write_text(json.dumps({"predicate_coverage_matrix": rows}), encoding="utf-8")
    return name


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-predicate-matrix-") as temp:
        repo = Path(temp)
        try:
            init = run(
                repo,
                "init",
                "--name",
                "predicate-coverage-matrix",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

            (repo / "plan.md").write_text(
                "# Predicate Coverage Matrix\n\n## Acceptance\n\nBroad test results need per-check predicate coverage.\n",
                encoding="utf-8",
            )
            doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            source_node = doc["document_node_id"]
            contract = run(repo, "scope", "create", "--body", "Predicate coverage focused contract.", "--source-node", source_node)
            task = run(repo, "task", "add", "--contract", contract["contract_id"], "--body", "Predicate coverage focused task.", "--from-node", source_node)
            endpoint_name = "predicate-matrix-endpoint"
            run(repo, "endpoint", "create", endpoint_name, "--description", "Predicate matrix endpoint.", "--root-node", contract["node_id"])

            def add_check(body: str) -> dict:
                return run(
                    repo,
                    "acceptance",
                    "add",
                    "--task",
                    task["task_id"],
                    "--body",
                    body,
                    "--expected-evidence-type",
                    "test_result",
                    "--from-node",
                    source_node,
                )

            def passing_test(*extra: str) -> dict:
                return run(
                    repo,
                    "evidence",
                    "test-result",
                    *extra,
                    "--from-node",
                    source_node,
                    "--",
                    sys.executable,
                    "-c",
                    "print('predicate matrix smoke')",
                )

            broad_a = add_check("Broad no-matrix check A.")
            broad_b = add_check("Broad no-matrix check B.")
            broad_without_matrix = run_fails(
                repo,
                "evidence",
                "test-result",
                "--check",
                broad_a["acceptance_check_id"],
                "--check",
                broad_b["acceptance_check_id"],
                "--close-check",
                "--from-node",
                source_node,
                "--",
                sys.executable,
                "-c",
                "print('broad without matrix')",
            )
            if "predicate_coverage_matrix" not in broad_without_matrix.stderr:
                raise AssertionError(f"broad no-matrix rejection was not explicit: {broad_without_matrix.stderr}")
            if fetch_check_closure(repo, broad_a["acceptance_check_id"]) or fetch_check_closure(repo, broad_b["acceptance_check_id"]):
                raise AssertionError("broad no-matrix evidence partially closed a check")

            missing_a = add_check("Missing not_covered row check A.")
            missing_b = add_check("Missing not_covered row check B.")
            missing_path = repo / "missing_not_covered.json"
            missing_path.write_text(
                json.dumps(
                    {
                        "predicate_coverage_matrix": [
                            {
                                "check_id": missing_a["acceptance_check_id"],
                                "predicate_id": "HP-MISSING-A",
                                "assertion": "A is covered.",
                                "result": "pass",
                                "reason": "",
                            },
                            {
                                "check_id": missing_b["acceptance_check_id"],
                                "predicate_id": "HP-MISSING-B",
                                "assertion": "B is covered.",
                                "result": "pass",
                                "reason": "",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            missing_not_covered = run_fails(
                repo,
                "evidence",
                "test-result",
                "--predicate-coverage-matrix",
                "missing_not_covered.json",
                "--check",
                missing_a["acceptance_check_id"],
                "--check",
                missing_b["acceptance_check_id"],
                "--close-check",
                "--from-node",
                source_node,
                "--",
                sys.executable,
                "-c",
                "print('missing field')",
            )
            if "not_covered" not in missing_not_covered.stderr:
                raise AssertionError(f"missing not_covered rejection was not explicit: {missing_not_covered.stderr}")

            failed_a = add_check("Failed row check A.")
            failed_b = add_check("Failed row check B.")
            matrix_file(
                repo,
                "failed_row.json",
                [
                    {
                        "check_id": failed_a["acceptance_check_id"],
                        "predicate_id": "HP-FAILED-A",
                        "assertion": "A is covered.",
                        "result": "pass",
                        "not_covered": False,
                        "reason": "",
                    },
                    {
                        "check_id": failed_b["acceptance_check_id"],
                        "predicate_id": "HP-FAILED-B",
                        "assertion": "B is not covered.",
                        "result": "fail",
                        "not_covered": True,
                        "reason": "Predicate B did not run.",
                    },
                ],
            )
            failed_row = run_fails(
                repo,
                "evidence",
                "test-result",
                "--predicate-coverage-matrix",
                "failed_row.json",
                "--check",
                failed_a["acceptance_check_id"],
                "--check",
                failed_b["acceptance_check_id"],
                "--close-check",
                "--from-node",
                source_node,
                "--",
                sys.executable,
                "-c",
                "print('failed row')",
            )
            if "failed or not-covered" not in failed_row.stderr:
                raise AssertionError(f"failed/not-covered rejection was not explicit: {failed_row.stderr}")

            valid_a = add_check("Valid matrix check A.")
            valid_b = add_check("Valid matrix check B.")
            matrix_file(
                repo,
                "valid_matrix.json",
                [
                    {
                        "check_id": valid_a["acceptance_check_id"],
                        "predicate_id": "HP-VALID-A",
                        "assertion": "A is covered.",
                        "result": "pass",
                        "not_covered": False,
                        "reason": "",
                    },
                    {
                        "check_id": valid_b["acceptance_check_id"],
                        "predicate_id": "HP-VALID-B",
                        "assertion": "B is covered.",
                        "result": "pass",
                        "not_covered": False,
                        "reason": "",
                    },
                ],
            )
            valid = passing_test(
                "--predicate-coverage-matrix",
                "valid_matrix.json",
                "--check",
                valid_a["acceptance_check_id"],
                "--check",
                valid_b["acceptance_check_id"],
                "--close-check",
            )
            if fetch_check_closure(repo, valid_a["acceptance_check_id"]) != valid["node_id"] or fetch_check_closure(repo, valid_b["acceptance_check_id"]) != valid["node_id"]:
                raise AssertionError(f"valid matrix did not close both checks: {valid}")
            valid_props = fetch_node_props(repo, valid["node_id"])
            if (
                valid_props.get("predicate_coverage_matrix_row_count") != 2
                or set(valid_props.get("predicate_coverage_matrix_covered_check_ids", []))
                != {valid_a["acceptance_check_id"], valid_b["acceptance_check_id"]}
                or valid_props.get("predicate_coverage_matrix_not_covered_check_ids")
                or not valid_props.get("predicate_coverage_matrix_ref")
                or not valid_props.get("predicate_coverage_matrix_sha256")
            ):
                raise AssertionError(f"valid matrix metadata was not stored: {valid_props}")
            expected_valid_covered = {
                valid_a["acceptance_check_id"]: ["HP-VALID-A"],
                valid_b["acceptance_check_id"]: ["HP-VALID-B"],
            }
            if valid_props.get("predicate_coverage_matrix_covered_hard_predicate_ids_by_check") != expected_valid_covered:
                raise AssertionError(f"valid matrix did not report covered hard predicates by check: {valid_props}")

            linked_a = add_check("Linked hard predicate check A.")
            linked_b = add_check("Linked hard predicate check B.")
            run(
                repo,
                "work",
                "intake",
                "--endpoint",
                endpoint_name,
                "--source-node",
                source_node,
                "--source-locator",
                "plan.md#Acceptance",
                "--text",
                "Linked hard predicates must be explicitly covered per acceptance check.",
                "--predicate",
                "HP-LINK-A::Check A linked hard predicate must be covered.",
                "--predicate",
                "HP-LINK-B::Check B linked hard predicate must be covered.",
            )
            run(
                repo,
                "work",
                "split",
                "--endpoint",
                endpoint_name,
                "--name",
                "linked predicate matrix",
                "--link",
                f"{task['task_id']}::{linked_a['acceptance_check_id']}::HP-LINK-A",
                "--link",
                f"{task['task_id']}::{linked_b['acceptance_check_id']}::HP-LINK-B",
            )
            matrix_file(
                repo,
                "linked_missing_required.json",
                [
                    {
                        "check_id": linked_a["acceptance_check_id"],
                        "predicate_id": "HP-UNLINKED-A",
                        "assertion": "A has a generic row but not the linked hard predicate.",
                        "result": "pass",
                        "not_covered": False,
                        "reason": "",
                    },
                    {
                        "check_id": linked_b["acceptance_check_id"],
                        "predicate_id": "HP-LINK-B",
                        "assertion": "B linked predicate is covered.",
                        "result": "pass",
                        "not_covered": False,
                        "reason": "",
                    },
                ],
            )
            linked_missing = run_fails(
                repo,
                "evidence",
                "test-result",
                "--predicate-coverage-matrix",
                "linked_missing_required.json",
                "--check",
                linked_a["acceptance_check_id"],
                "--check",
                linked_b["acceptance_check_id"],
                "--close-check",
                "--from-node",
                source_node,
                "--",
                sys.executable,
                "-c",
                "print('linked missing required')",
            )
            if "missing linked hard predicate coverage" not in linked_missing.stderr or "HP-LINK-A" not in linked_missing.stderr:
                raise AssertionError(f"linked hard predicate omission was not explicit: {linked_missing.stderr}")

            matrix_file(
                repo,
                "linked_valid.json",
                [
                    {
                        "check_id": linked_a["acceptance_check_id"],
                        "predicate_id": "HP-LINK-A",
                        "assertion": "A linked predicate is covered.",
                        "result": "pass",
                        "not_covered": False,
                        "reason": "",
                    },
                    {
                        "check_id": linked_b["acceptance_check_id"],
                        "predicate_id": "HP-LINK-B",
                        "assertion": "B linked predicate is covered.",
                        "result": "pass",
                        "not_covered": False,
                        "reason": "",
                    },
                ],
            )
            linked_valid = passing_test(
                "--predicate-coverage-matrix",
                "linked_valid.json",
                "--check",
                linked_a["acceptance_check_id"],
                "--check",
                linked_b["acceptance_check_id"],
                "--close-check",
            )
            if linked_valid["predicate_coverage_persistence"]["inserted_count"] != 2 or linked_valid["predicate_coverage_persistence"]["skipped_count"] != 0:
                raise AssertionError(f"linked hard predicate rows were not persisted cleanly: {linked_valid}")
            linked_props = fetch_node_props(repo, linked_valid["node_id"])
            expected_linked_covered = {
                linked_a["acceptance_check_id"]: ["HP-LINK-A"],
                linked_b["acceptance_check_id"]: ["HP-LINK-B"],
            }
            if linked_props.get("predicate_coverage_matrix_covered_hard_predicate_ids_by_check") != expected_linked_covered:
                raise AssertionError(f"linked valid matrix did not report covered hard predicates by check: {linked_props}")
            expected_coverage_rows = {
                (linked_a["acceptance_check_id"], "HP-LINK-A", "pass"),
                (linked_b["acceptance_check_id"], "HP-LINK-B", "pass"),
            }
            actual_coverage_rows = set(fetch_coverage_rows(repo, linked_valid["node_id"]))
            if actual_coverage_rows != expected_coverage_rows:
                raise AssertionError(f"linked hard predicate coverage rows were not persisted: {fetch_coverage_rows(repo, linked_valid['node_id'])}")

            reuse_a = add_check("Manual reuse check A.")
            reuse_b = add_check("Manual reuse check B.")
            single = passing_test("--check", reuse_a["acceptance_check_id"], "--close-check")
            reuse_rejected = run_fails(repo, "acceptance", "close", "--check", reuse_b["acceptance_check_id"], "--evidence-node", single["node_id"])
            if "predicate_coverage_matrix" not in reuse_rejected.stderr:
                raise AssertionError(f"manual reuse rejection was not explicit: {reuse_rejected.stderr}")
            if fetch_check_closure(repo, reuse_b["acceptance_check_id"]):
                raise AssertionError("manual reuse without matrix closed the second check")

            override_a = add_check("Override source check.")
            override_b = add_check("Override target check.")
            override_evidence = passing_test("--check", override_a["acceptance_check_id"], "--close-check")
            override_close = run(
                repo,
                "acceptance",
                "close",
                "--check",
                override_b["acceptance_check_id"],
                "--evidence-node",
                override_evidence["node_id"],
                "--override-predicate-coverage",
                "--override-reason",
                "Focused test confirms explicit predicate coverage override warning.",
            )
            if fetch_warning_kind(repo, override_close["warning_node_ids"][0]) != "predicate_coverage_override":
                raise AssertionError(f"predicate coverage override did not record audit warning: {override_close}")

            replace_source = add_check("Replace source check.")
            replace_target = add_check("Replace target check.")
            replace_source_evidence = passing_test("--check", replace_source["acceptance_check_id"], "--close-check")
            old_target_evidence = passing_test("--check", replace_target["acceptance_check_id"], "--close-check")
            replace_rejected = run_fails(
                repo,
                "acceptance",
                "replace-closure",
                "--check",
                replace_target["acceptance_check_id"],
                "--evidence-node",
                replace_source_evidence["node_id"],
                "--reason",
                "Attempt broad replacement without matrix.",
            )
            if "predicate_coverage_matrix" not in replace_rejected.stderr:
                raise AssertionError(f"replace-closure reuse rejection was not explicit: {replace_rejected.stderr}")
            if fetch_check_closure(repo, replace_target["acceptance_check_id"]) != old_target_evidence["node_id"]:
                raise AssertionError("replace-closure without matrix changed the target closure")

            replace_override_source = add_check("Replace override source check.")
            replace_override_target = add_check("Replace override target check.")
            replace_override_evidence = passing_test("--check", replace_override_source["acceptance_check_id"], "--close-check")
            passing_test("--check", replace_override_target["acceptance_check_id"], "--close-check")
            replace_override = run(
                repo,
                "acceptance",
                "replace-closure",
                "--check",
                replace_override_target["acceptance_check_id"],
                "--evidence-node",
                replace_override_evidence["node_id"],
                "--reason",
                "Allow broad replacement with explicit predicate coverage override.",
                "--override-predicate-coverage",
                "--override-reason",
                "Focused test confirms replace-closure override warning.",
            )
            if fetch_check_closure(repo, replace_override_target["acceptance_check_id"]) != replace_override_evidence["node_id"]:
                raise AssertionError(f"replace-closure override did not update closure: {replace_override}")
            if fetch_warning_kind(repo, replace_override["warning_node_ids"][0]) != "predicate_coverage_override":
                raise AssertionError(f"replace-closure override did not record predicate warning: {replace_override}")

            print(
                json.dumps(
                    {
                        "ok": True,
                        "results": {
                            "broad_without_matrix_rejected": True,
                            "missing_not_covered_rejected": True,
                            "failed_row_rejected": True,
                            "valid_matrix_closed": True,
                            "covered_hard_predicates_reported_by_check": True,
                            "linked_hard_predicate_missing_rejected": True,
                            "linked_hard_predicate_rows_persisted": True,
                            "manual_reuse_rejected": True,
                            "override_warning_recorded": True,
                            "replace_closure_rejected_without_matrix": True,
                            "replace_closure_override_warning_recorded": True,
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")


if __name__ == "__main__":
    raise SystemExit(main())
