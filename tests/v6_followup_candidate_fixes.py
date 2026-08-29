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
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def run(repo: Path, *args: str) -> dict:
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


def assert_fixture_linkage_surface(repo: Path) -> tuple[dict, dict, dict]:
    (repo / "fixture.md").write_text("# Fixture\n\n## Scope\n\nFollow-up candidate fixture.\n", encoding="utf-8")
    doc = run(repo, "doc", "import", "fixture.md", "--source-type", "plan")
    scope = run(repo, "scope", "create", "--body", "Fixture scope.", "--source-node", doc["document_node_id"])
    run(repo, "endpoint", "create", "fixture", "--description", "Fixture endpoint.", "--root-node", scope["node_id"])
    unlinked_task = run(repo, "task", "add", "--body", "Unlinked same-source fixture task.", "--from-node", doc["document_node_id"])
    unlinked_check = run(
        repo,
        "acceptance",
        "add",
        "--task",
        unlinked_task["task_id"],
        "--body",
        "Unlinked same-source fixture check.",
        "--expected-evidence-type",
        "test_result",
        "--from-node",
        doc["document_node_id"],
    )
    evidence = run(
        repo,
        "evidence",
        "test-result",
        "--check",
        unlinked_check["acceptance_check_id"],
        "--close-check",
        "--close-task",
        "--from-node",
        doc["document_node_id"],
        "--",
        sys.executable,
        "-c",
        "print('fixture linkage ok')",
    )
    run(repo, "endpoint", "refresh", "fixture")
    status = run(repo, "endpoint", "status", "fixture")
    candidates = status["unlinked_scope_candidates"]
    if unlinked_task["task_id"] not in {task["id"] for task in candidates["tasks"]}:
        raise AssertionError(f"endpoint status did not surface unlinked same-source task: {status}")
    if unlinked_check["acceptance_check_id"] not in {check["id"] for check in candidates["checks"]}:
        raise AssertionError(f"endpoint status did not surface unlinked same-source check: {status}")
    if evidence["node_id"] not in {item["id"] for item in candidates["evidence"]}:
        raise AssertionError(f"endpoint status did not surface unlinked closure evidence: {status}")
    markdown = run_cli(repo, "endpoint", "status", "fixture", "--markdown").stdout
    if "Unlinked scope candidates:" not in markdown or "do not count as endpoint-scoped closure" not in markdown:
        raise AssertionError(f"endpoint markdown did not explain unlinked scope candidates:\n{markdown}")
    scoped_task = run(repo, "task", "add", "--contract", scope["contract_id"], "--body", "Scoped mode task.", "--from-node", doc["document_node_id"])
    return doc, scope, scoped_task


def assert_mode_boundary_surface(repo: Path, scoped_task: dict) -> None:
    suggested = run(repo, "mode", "suggest", "--intent", "implement a scoped fix")
    boundary = suggested["contract"].get("side_effect_boundary") or {}
    if boundary.get("selected_mode") != "standard" or "direct_exec_default" not in boundary or not boundary.get("allowed_side_effects"):
        raise AssertionError(f"mode suggest did not expose side-effect boundary: {suggested}")
    workflow = run(repo, "workflow", "begin", "--session-id", "mode-boundary", "--endpoint", "fixture", "--content", "Implement a scoped fix.")
    workflow_boundary = workflow["contract"].get("side_effect_boundary") or {}
    if "diagnostic_route" not in workflow_boundary:
        raise AssertionError(f"workflow begin did not expose diagnostic mode route: {workflow}")
    started = run(
        repo,
        "exec",
        "start",
        "--endpoint",
        "fixture",
        "--task-node",
        scoped_task["node_id"],
        "--session-id",
        "mode-boundary",
        "--summary",
        "Direct exec start boundary check.",
    )
    exec_boundary = started["contract"].get("side_effect_boundary") or {}
    if started.get("mode") != "standard" or exec_boundary.get("direct_exec_default") is None:
        raise AssertionError(f"direct exec start did not make default mode boundary visible: {started}")


def assert_return_capsule_top_level(repo: Path) -> None:
    payload = run(
        repo,
        "delegate",
        "capsule",
        "--role",
        "worker",
        "--endpoint",
        "fixture",
        "--task",
        "task-demo",
        "--check",
        "check-demo",
        "--owned-hunk-or-path",
        "shujuan/commands/delegate_handlers.py::delegate_return_capsule",
        "--pre-existing-dirty-path",
        "README.md",
        "--fixture-write",
        "isolated temp repo write",
        "--blocked-check",
        "check-demo",
        "--unresolved-risk",
        "controller still owns closure",
        "--assumption",
        "capsule is material-only",
        "--provider-output",
        "GitNexus impact: low",
    )
    capsule = payload["capsule"]["return_capsule"]
    expected_top_level = {
        "changed_files",
        "owned_hunks_or_paths",
        "pre_existing_dirty_paths",
        "fixture_writes",
        "tests",
        "tests_run",
        "blocked_checks",
        "unresolved_risks",
        "assumptions",
        "provider_outputs",
        "check_status",
        "identity_boundary",
        "no_closure_attestation",
    }
    missing = expected_top_level - set(capsule)
    if missing:
        raise AssertionError(f"return capsule missed top-level fields {missing}: {payload}")
    if capsule["provider_outputs"] != ["GitNexus impact: low"]:
        raise AssertionError(f"provider outputs were not top-level return material: {payload}")
    if capsule["check_status"]["closed_by_delegate"] is not False:
        raise AssertionError(f"return capsule leaked closure authority: {payload}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    with tempfile.TemporaryDirectory(prefix="shujuan-v6-followup-fixes-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        postgres_started = False
        try:
            init = run(repo, "init", "--name", "v6-followup-fixes", "--postgres-dev", "--postgres-dev-port", str(free_port()))
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")
            _doc, _scope, scoped_task = assert_fixture_linkage_surface(repo)
            assert_mode_boundary_surface(repo, scoped_task)
            assert_return_capsule_top_level(repo)
        finally:
            if postgres_started:
                try:
                    run_cli(repo, "postgres-dev", "stop")
                except AssertionError:
                    pass
    print(json.dumps({"ok": True, "v6_followup_candidate_fixes": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
