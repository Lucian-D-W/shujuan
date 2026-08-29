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


def run_fails(repo: Path, *args: str) -> dict:
    return json.loads(run_cli(repo, *args, expect_ok=False).stdout)


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
    return {item["code"] for bucket in payload["severity_buckets"].values() for item in bucket}


def hidden_kinds(payload: dict) -> set[str]:
    return {item["kind"] for item in (payload.get("readiness") or {}).get("hidden_blocking_refs") or []}


def assert_readiness(payload: dict, *, reason_code: str) -> None:
    readiness = payload.get("readiness") or {}
    if readiness.get("schema") != "endpoint_readiness.v1":
        raise AssertionError(f"readiness schema missing: {payload}")
    if readiness.get("stored_as_completion_state") is not False or readiness.get("diagnostic_only") is not True:
        raise AssertionError(f"readiness is not diagnostic-only: {readiness}")
    if readiness.get("closeout_ready") is not False or readiness.get("blocking_reason_code") != reason_code:
        raise AssertionError(f"readiness did not block for {reason_code}: {readiness}")
    for key in ("execution_ready", "blocking_reason", "next_safe_action", "authority_boundary"):
        if readiness.get(key) in (None, "", False):
            raise AssertionError(f"readiness missing {key}: {readiness}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-readiness-first-screen-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        try:
            init = run(
                repo,
                "init",
                "--name",
                "readiness-first-screen",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

            (repo / "plan.md").write_text("# Readiness\n\nReadiness fixture source.\n", encoding="utf-8")
            doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            source_node = doc["document_node_id"]
            contract = run(repo, "scope", "create", "--body", "Readiness first-screen contract.", "--source-node", source_node)
            endpoint = "readiness-main"
            run(repo, "endpoint", "create", endpoint, "--description", "Readiness main endpoint.", "--root-node", contract["node_id"])

            unlinked_task = run(repo, "task", "add", "--body", "Unlinked remediation candidate.", "--from-node", source_node)
            audit = run(
                repo,
                "audit",
                "record",
                "--endpoint",
                endpoint,
                "--source-node",
                source_node,
                "--body",
                "Readiness audit body.",
                "--finding",
                "Active finding blocks closeout even when scoped tasks and checks are clear.",
            )
            finding_id = audit["audit_finding_node_ids"][0]

            (repo / "child.md").write_text("# Child\n\nChild endpoint source.\n", encoding="utf-8")
            child_doc = run(repo, "doc", "import", "child.md", "--source-type", "plan")
            child_contract = run(repo, "scope", "create", "--body", "Child active contract.", "--source-node", child_doc["document_node_id"])
            child_task = run(
                repo,
                "task",
                "add",
                "--contract",
                child_contract["contract_id"],
                "--body",
                "Child open task.",
                "--from-node",
                child_doc["document_node_id"],
            )
            run(
                repo,
                "acceptance",
                "add",
                "--task",
                child_task["task_id"],
                "--body",
                "Child open check.",
                "--expected-evidence-type",
                "artifact",
                "--from-node",
                child_doc["document_node_id"],
            )
            child_endpoint = "readiness-child"
            run(repo, "endpoint", "create", child_endpoint, "--description", "Readiness child endpoint.", "--root-node", child_contract["node_id"])
            run(repo, "endpoint", "link-child", "--parent", endpoint, "--child", child_endpoint)

            report = run(repo, "report", "endpoint", endpoint, "--active-only")
            assert_readiness(report, reason_code="active_audit_findings")
            if report["active_obligations"].get("current_tasks") or report["active_obligations"].get("open_checks"):
                raise AssertionError(f"scoped tasks/checks should be clear in active-only report: {report}")
            if finding_id not in {item["id"] for item in report["active_obligations"].get("audit_findings") or []}:
                raise AssertionError(f"active audit finding missing from report: {report}")
            if unlinked_task["task_id"] not in {item["ref"] for item in report["readiness"]["hidden_blocking_refs"]}:
                raise AssertionError(f"unlinked remediation task missing from hidden refs: {report['readiness']}")
            if not {"child_chain_blocker", "unlinked_remediation_task"} <= hidden_kinds(report):
                raise AssertionError(f"folded child/remediation blockers missing from report readiness: {report['readiness']}")

            brief = run(repo, "endpoint", "brief", endpoint, "--role", "worker_agent")
            assert_readiness(brief, reason_code="active_audit_findings")
            if "worker_agent" not in brief["readiness"]["authority_boundary"]:
                raise AssertionError(f"brief did not surface worker authority boundary: {brief['readiness']}")
            if not {"child_chain_blocker", "unlinked_remediation_task"} <= hidden_kinds(brief):
                raise AssertionError(f"brief hidden refs missing folded blockers: {brief['readiness']}")

            doctor = run_fails(repo, "endpoint", "doctor", endpoint, "--strict-closeout", "--read-only")
            assert_readiness(doctor, reason_code="active_audit_findings")
            if not {"active_audit_findings", "active_child_chain_obligations"} <= doctor_codes(doctor):
                raise AssertionError(f"read-only doctor did not expose active blockers: {doctor}")

            (repo / "task-open.md").write_text("# Task Open\n\nClosed check with open task fixture.\n", encoding="utf-8")
            task_doc = run(repo, "doc", "import", "task-open.md", "--source-type", "plan")
            task_contract = run(repo, "scope", "create", "--body", "Task-open contract.", "--source-node", task_doc["document_node_id"])
            task = run(
                repo,
                "task",
                "add",
                "--contract",
                task_contract["contract_id"],
                "--body",
                "Task remains open after its check is closed.",
                "--from-node",
                task_doc["document_node_id"],
            )
            check = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task["task_id"],
                "--body",
                "Closed check while task remains open.",
                "--expected-evidence-type",
                "artifact",
                "--from-node",
                task_doc["document_node_id"],
            )
            (repo / "proof.txt").write_text("check-only closure proof\n", encoding="utf-8")
            run(
                repo,
                "evidence",
                "artifact",
                "--path",
                "proof.txt",
                "--from-node",
                task_doc["document_node_id"],
                "--check",
                check["acceptance_check_id"],
                "--close-check",
            )
            task_endpoint = "readiness-task-open"
            run(repo, "endpoint", "create", task_endpoint, "--description", "Task-open endpoint.", "--root-node", task_contract["node_id"])
            task_doctor = run_fails(repo, "endpoint", "doctor", task_endpoint, "--strict-closeout", "--read-only")
            if "checks_closed_task_open" not in doctor_codes(task_doctor):
                raise AssertionError(f"closed-check/open-task warning missing from doctor: {task_doctor}")
            warning_codes = {item["code"] for item in task_doctor["readiness"].get("warnings") or []}
            if "checks_closed_task_open" not in warning_codes:
                raise AssertionError(f"closed-check/open-task readiness warning missing: {task_doctor['readiness']}")

            print(
                json.dumps(
                    {
                        "ok": True,
                        "readiness_first_screen": "passed",
                        "fixture_writes": "temporary postgres-dev repo only",
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
