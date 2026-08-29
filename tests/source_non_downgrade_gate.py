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


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-source-nondowngrade-") as temp:
        repo = Path(temp)
        try:
            init = run(
                repo,
                "init",
                "--name",
                "source-nondowngrade",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

            (repo / "plan.md").write_text(
                "\n".join(
                    [
                        "# Source-to-DB Non-Downgrade",
                        "",
                        "## T5",
                        "",
                        "The acceptance check must preserve AntV G6, source drawer, artifact/diff preview, scenario 1, and scenario 2.",
                    ]
                ),
                encoding="utf-8",
            )
            doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            source_node = doc["document_node_id"]
            contract = run(repo, "scope", "create", "--body", "T5 source-to-DB non-downgrade contract.", "--source-node", source_node)
            task = run(
                repo,
                "task",
                "add",
                "--contract",
                contract["contract_id"],
                "--body",
                "Build a graph visualization from the source plan.",
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
                "Test that the graph visualization handles the scenario list.",
                "--expected-evidence-type",
                "test_result",
                "--from-node",
                source_node,
            )
            endpoint = "source-nondowngrade"
            run(repo, "endpoint", "create", endpoint, "--description", "T5 gate endpoint.", "--root-node", contract["node_id"])

            intake = run(
                repo,
                "work",
                "intake",
                "--endpoint",
                endpoint,
                "--source-node",
                source_node,
                "--source-locator",
                "plan.md#T5",
                "--promise-id",
                "SP-T5-SOURCE-DB-NONDOWNGRADE-001",
                "--text",
                "AntV G6, source drawer, artifact/diff preview, scenario 1, and scenario 2 must survive into acceptance checks.",
                "--predicate",
                "HP-NAMED-TECH-PRESERVATION::named technology such as AntV G6 cannot be generalized",
                "--predicate",
                "HP-MUST-TERM-PRESERVATION::must/include UI terms survive into tasks/checks",
                "--predicate",
                "HP-ENUMERATED-LIST-PRESERVATION::enumerated lists cannot compress into aggregate wording",
                "--named-term",
                "HP-NAMED-TECH-PRESERVATION::AntV G6",
                "--must-term",
                "HP-MUST-TERM-PRESERVATION::source drawer",
                "--must-term",
                "HP-MUST-TERM-PRESERVATION::artifact/diff preview",
                "--enumerated-item",
                "HP-ENUMERATED-LIST-PRESERVATION::scenario 1",
                "--enumerated-item",
                "HP-ENUMERATED-LIST-PRESERVATION::scenario 2",
                "--forbidden-substitute",
                "HP-NAMED-TECH-PRESERVATION::graph visualization::Abstract graph visualization cannot replace AntV G6",
                "--forbidden-substitute",
                "HP-ENUMERATED-LIST-PRESERVATION::scenario list::Aggregate list wording hides missing items",
            )
            if len(intake["hard_predicates"]) != 3:
                raise AssertionError(f"intake did not preserve predicate metadata: {intake}")

            for predicate_id in (
                "HP-NAMED-TECH-PRESERVATION",
                "HP-MUST-TERM-PRESERVATION",
                "HP-ENUMERATED-LIST-PRESERVATION",
            ):
                run(
                    repo,
                    "work",
                    "split",
                    "--endpoint",
                    endpoint,
                    "--name",
                    f"{predicate_id} source audit slice",
                    "--task",
                    task["task_id"],
                    "--check",
                    check["acceptance_check_id"],
                    "--predicate",
                    predicate_id,
                )

            failed = run_fails(repo, "work", "audit-source", "--endpoint", endpoint, "--fail-on-findings")
            if not failed.stdout.strip():
                raise AssertionError(f"source audit failure did not emit JSON\nSTDERR:\n{failed.stderr}")
            audit = json.loads(failed.stdout)
            codes = {finding["code"] for finding in audit["findings"]}
            expected_codes = {
                "missing_named_term_in_acceptance_check",
                "missing_must_term_in_acceptance_check",
                "missing_enumerated_item_in_acceptance_check",
                "forbidden_substitute_in_task_or_check",
            }
            if not expected_codes <= codes:
                raise AssertionError(f"source audit missed non-downgrade findings: {audit}")
            if not audit["source_promise_matrix"][0]["hard_predicates"][0]["acceptance_check_links"]:
                raise AssertionError(f"source audit did not map predicates to task/check links: {audit}")

            doctor = run(repo, "endpoint", "doctor", endpoint, "--strict-closeout", "--allow-fail")
            if "source_non_downgrade_findings" not in doctor_codes(doctor):
                raise AssertionError(f"strict doctor did not refuse non-downgrade closeout: {doctor}")

            close = run(repo, "work", "close", "--mode", "full", "--endpoint", endpoint)
            visibility = close["agcp_closeout_visibility"]
            if visibility["source_non_downgrade_finding_count"] < len(expected_codes):
                raise AssertionError(f"work close did not expose source non-downgrade visibility: {close}")

            run(
                repo,
                "scope",
                "change",
                "--body",
                "Source-backed authorization for the deliberately downgraded weak acceptance fixture.",
                "--source-node",
                source_node,
                "--applies-to",
                check["node_id"],
            )
            authorized = run(repo, "work", "audit-source", "--endpoint", endpoint, "--fail-on-findings")
            if not authorized["ok"] or authorized["finding_count"] != 0:
                raise AssertionError(f"source-backed authorization did not clear downgrade findings: {authorized}")

            print(json.dumps({"ok": True, "findings_before_authorization": sorted(codes)}, indent=2, sort_keys=True))
            return 0
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")


if __name__ == "__main__":
    raise SystemExit(main())
