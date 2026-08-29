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


def run_cli_completed(repo: Path, *args: str, expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
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


def run_cli(repo: Path, *args: str) -> dict[str, object]:
    return json.loads(run_cli_completed(repo, *args).stdout)


def run_cli_fails(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_cli_completed(repo, *args, expect_ok=False)


def run_git(repo: Path, *args: str) -> None:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise AssertionError(f"git failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")


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


def fixture_scope(repo: Path) -> tuple[str, str]:
    (repo / "plan.md").write_text("# Trust fixture\n\nAcceptance checks.\n", encoding="utf-8")
    doc = run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan")
    scope = run_cli(repo, "scope", "create", "--body", "Trust regression scope.", "--source-node", str(doc["document_node_id"]))
    task = run_cli(repo, "task", "add", "--contract", str(scope["contract_id"]), "--body", "Trust regression task.", "--from-node", str(doc["document_node_id"]))
    return str(doc["document_node_id"]), str(task["task_id"])


def add_check(repo: Path, task_id: str, source_node_id: str, body: str, evidence_type: str = "test_result") -> str:
    check = run_cli(
        repo,
        "acceptance",
        "add",
        "--task",
        task_id,
        "--body",
        body,
        "--expected-evidence-type",
        evidence_type,
        "--from-node",
        source_node_id,
    )
    return str(check["acceptance_check_id"])


def warning_props(repo: Path, node_id: str) -> dict[str, object]:
    from shujuan.store import connect

    conn = connect(repo)
    try:
        row = conn.execute("SELECT props FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            raise AssertionError(f"warning node not found: {node_id}")
        return json.loads(row["props"])
    finally:
        conn.close()


def assert_repeated_predicate_override_requires_elevation(repo: Path, task_id: str, source_node_id: str) -> None:
    check_a = add_check(repo, task_id, source_node_id, "Override source check.")
    check_b = add_check(repo, task_id, source_node_id, "Override target check.")
    check_c = add_check(repo, task_id, source_node_id, "Repeated override target check.")

    evidence = run_cli(
        repo,
        "evidence",
        "test-result",
        "--check",
        check_a,
        "--close-check",
        "--",
        sys.executable,
        "-c",
        "print('ok')",
    )
    first_override = run_cli(
        repo,
        "acceptance",
        "close",
        "--check",
        check_b,
        "--evidence-node",
        str(evidence["node_id"]),
        "--override-predicate-coverage",
        "--override-reason",
        "First explicit override for broad evidence reuse.",
    )
    first_props = warning_props(repo, str(first_override["warning_node_ids"][0]))
    if first_props.get("kind") != "predicate_coverage_override" or first_props.get("elevated"):
        raise AssertionError(f"first predicate override warning had wrong props: {first_props}")

    repeated = run_cli_fails(
        repo,
        "acceptance",
        "close",
        "--check",
        check_c,
        "--evidence-node",
        str(evidence["node_id"]),
        "--override-predicate-coverage",
        "--override-reason",
        "Repeat override without elevation must fail.",
    )
    if "--elevated-predicate-coverage-override" not in repeated.stderr:
        raise AssertionError(f"repeat override failure did not require elevation: {repeated.stderr}")

    elevated = run_cli(
        repo,
        "acceptance",
        "close",
        "--check",
        check_c,
        "--evidence-node",
        str(evidence["node_id"]),
        "--override-predicate-coverage",
        "--elevated-predicate-coverage-override",
        "--override-reason",
        "Elevated repeat override records an explicit risk signal.",
    )
    elevated_props = warning_props(repo, str(elevated["warning_node_ids"][0]))
    if not elevated_props.get("elevated") or not elevated_props.get("prior_override_node_ids"):
        raise AssertionError(f"elevated override did not retain prior override signal: {elevated_props}")


def assert_evidence_verify_invalidates_tampered_artifact(repo: Path, task_id: str, source_node_id: str) -> None:
    check_id = add_check(repo, task_id, source_node_id, "Tampered artifact must lose closure trust.", "artifact")
    artifact_path = repo / "artifact.md"
    artifact_path.write_text("trusted artifact\n", encoding="utf-8")
    evidence = run_cli(
        repo,
        "evidence",
        "artifact",
        "--path",
        "artifact.md",
        "--check",
        check_id,
        "--close-check",
        "--from-node",
        source_node_id,
    )
    capture_ref = evidence["artifact"]["capture_ref"]
    (repo / str(capture_ref)).write_text("tampered artifact\n", encoding="utf-8")

    verify_completed = run_cli_completed(repo, "evidence", "verify", "--node", str(evidence["node_id"]), expect_ok=False)
    verify = json.loads(verify_completed.stdout)
    invalidated = verify.get("auto_invalidated_evidence") or []
    if verify.get("ok") is not False or not invalidated:
        raise AssertionError(f"tampered artifact verify did not fail and auto-invalidate: {verify}")
    if invalidated[0]["node_id"] != evidence["node_id"] or "tampered" not in invalidated[0]["failure_statuses"]:
        raise AssertionError(f"auto-invalidation did not report the tampered evidence: {invalidated}")
    if check_id not in invalidated[0]["cleared_closures"]["acceptance_checks"]:
        raise AssertionError(f"auto-invalidation did not clear closure: {invalidated}")

    status = run_cli(repo, "evidence", "status", "--node", str(evidence["node_id"]))
    event_types = {event["event_type"] for event in status["events"]}
    if status["current_state"] != "invalidated" or status["closures"]["acceptance_checks"]:
        raise AssertionError(f"invalidated evidence still had current closure trust: {status}")
    if "verification_failed" not in event_types:
        raise AssertionError(f"evidence lifecycle did not record verification_failed: {status}")

    refused = run_cli_fails(
        repo,
        "acceptance",
        "close",
        "--check",
        check_id,
        "--evidence-node",
        str(evidence["node_id"]),
    )
    if "only current valid evidence" not in refused.stderr:
        raise AssertionError(f"invalidated evidence was not rejected for later closure: {refused.stderr}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    with tempfile.TemporaryDirectory(prefix="shujuan-vf-governance-trust-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        run_git(repo, "init")
        run_git(repo, "config", "user.email", "test@example.invalid")
        run_git(repo, "config", "user.name", "Test User")
        run_cli(repo, "init", "--name", "vf-governance-trust", "--postgres-dev", "--postgres-dev-port", str(free_port()))
        source_node_id, task_id = fixture_scope(repo)
        assert_repeated_predicate_override_requires_elevation(repo, task_id, source_node_id)
        assert_evidence_verify_invalidates_tampered_artifact(repo, task_id, source_node_id)
    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
