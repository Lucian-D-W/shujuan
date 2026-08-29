from __future__ import annotations

import json
import os
import sqlite3
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


def run(repo: Path, *args: str) -> dict:
    return json.loads(run_cli(repo, *args).stdout)


def run_fails(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_cli(repo, *args, expect_ok=False)


def db(repo: Path) -> sqlite3.Connection:
    return connect(repo)


def check_closed_by(repo: Path, check_id: str) -> str | None:
    conn = db(repo)
    try:
        row = conn.execute("SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone()
        return row["closed_by_node_id"] if row else None
    finally:
        conn.close()


def task_closed_by(repo: Path, task_id: str) -> str | None:
    conn = db(repo)
    try:
        row = conn.execute("SELECT closed_by_node_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row["closed_by_node_id"] if row else None
    finally:
        conn.close()


def node_kind(repo: Path, node_id: str) -> str | None:
    conn = db(repo)
    try:
        row = conn.execute("SELECT type, props FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            return None
        return json.loads(row["props"] or "{}").get("kind") if row["type"] == "audit_finding" else row["type"]
    finally:
        conn.close()


def create_scoped_endpoint(repo: Path, name: str, expected_evidence_type: str = "test_result") -> tuple[dict, dict, dict, dict]:
    (repo / f"{name}.md").write_text(f"# {name}\n\n## Scope\n\nRegression fixture.\n", encoding="utf-8")
    doc = run(repo, "doc", "import", f"{name}.md", "--source-type", "plan")
    scope = run(repo, "scope", "create", "--body", f"{name} scope.", "--source-node", doc["document_node_id"])
    task = run(repo, "task", "add", "--contract", scope["contract_id"], "--body", f"{name} task.", "--from-node", doc["document_node_id"])
    check = run(
        repo,
        "acceptance",
        "add",
        "--task",
        task["task_id"],
        "--body",
        f"{name} check.",
        "--expected-evidence-type",
        expected_evidence_type,
        "--from-node",
        doc["document_node_id"],
    )
    run(repo, "endpoint", "create", name, "--description", f"{name} endpoint.", "--root-node", scope["node_id"])
    return doc, scope, task, check


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-v4-mode-regressions-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        init = run(
            repo,
            "init",
            "--name",
            "v4-mode-regressions",
            "--postgres-dev",
            "--postgres-dev-port",
            str(free_port()),
        )
        postgres_started = True
        if init["database"]["backend"] != "postgres":
            raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

        capture_missing = run_fails(repo, "work", "start", "--mode", "capture", "--content", "capture without endpoint")
        if "--endpoint" not in capture_missing.stderr or "endpoint not found: None" in capture_missing.stderr:
            raise AssertionError(f"capture without endpoint failed unclearly: {capture_missing.stderr}")
        explore_missing = run_fails(repo, "work", "start", "--mode", "explore", "--content", "explore without endpoint")
        if "--endpoint" not in explore_missing.stderr or "endpoint not found: None" in explore_missing.stderr:
            raise AssertionError(f"explore without endpoint failed unclearly: {explore_missing.stderr}")

        mixed_a = run(repo, "mode", "suggest", "--intent", "summarize status, then implement the schema fix")
        mixed_b = run(repo, "mode", "suggest", "--intent", "summarize status, then implement the schema fix")
        if mixed_a != mixed_b or mixed_a["suggested_mode"] != "explore":
            raise AssertionError(f"mixed-intent mode suggestion was not deterministic Explore: {mixed_a}, {mixed_b}")
        if "mode_intent_ambiguous" not in {item["code"] for item in mixed_a.get("warnings") or []}:
            raise AssertionError(f"mixed-intent suggestion did not expose ambiguity: {mixed_a}")
        if mixed_a["contract"]["creates_run"]:
            raise AssertionError(f"mixed-intent suggestion silently escalated to execution: {mixed_a}")

        doc, _scope, task, check = create_scoped_endpoint(repo, "aliases", expected_evidence_type="change_set")
        run(repo, "alias", "set", "--kind", "endpoint", "--name", "main", "--target", "aliases")
        run(repo, "alias", "set", "--kind", "task", "--name", "main", "--target", task["task_id"])
        run(repo, "alias", "set", "--kind", "check", "--name", "main", "--target", check["acceptance_check_id"])
        run(repo, "workflow", "begin", "--session-id", "alias-session", "--endpoint", "aliases", "--content", "Alias close.")
        started = run(repo, "work", "start", "--mode", "light", "--endpoint", "@alias.main", "--task", "@alias.main", "--session-id", "alias-session")
        (repo / "alias-change.txt").write_text("alias change\n", encoding="utf-8")
        stopped = run(
            repo,
            "work",
            "close",
            "--apply",
            "--mode",
            "light",
            "--endpoint",
            "@current.endpoint",
            "--task",
            "@current.task",
            "--check",
            "@current.check",
            "--close-check",
        )
        if stopped["run_id"] != started["run_id"] or check_closed_by(repo, check["acceptance_check_id"]) != stopped["change_set"]["change_set_node_id"]:
            raise AssertionError(f"work close did not resolve current aliases consistently: {stopped}")

        _doc2, _scope2, task2, check2 = create_scoped_endpoint(repo, "exec-aliases", expected_evidence_type="change_set")
        run(repo, "alias", "set", "--kind", "endpoint", "--name", "exec", "--target", "exec-aliases")
        run(repo, "alias", "set", "--kind", "task", "--name", "exec", "--target", task2["node_id"])
        run(repo, "alias", "set", "--kind", "check", "--name", "exec", "--target", check2["node_id"])
        run(repo, "workflow", "begin", "--session-id", "exec-alias-session", "--endpoint", "exec-aliases", "--content", "Exec alias stop.")
        exec_started = run(repo, "exec", "start", "--endpoint", "exec-aliases", "--task-node", task2["task_id"], "--session-id", "exec-alias-session", "--summary", "Exec alias start")
        (repo / "exec-alias-change.txt").write_text("exec alias change\n", encoding="utf-8")
        exec_stopped = run(repo, "exec", "stop", "--endpoint", "@alias.exec", "--task", "@alias.exec", "--check", "@alias.exec", "--close-check", "--summary", "Exec alias stop")
        if exec_stopped["run_id"] != exec_started["run_id"] or check_closed_by(repo, check2["acceptance_check_id"]) != exec_stopped["change_set"]["change_set_node_id"]:
            raise AssertionError(f"exec stop did not resolve aliases consistently: {exec_stopped}")

        doc3, _scope3, task3, check3 = create_scoped_endpoint(repo, "full-gate")
        bad_predicate = run(
            repo,
            "evidence",
            "test-result",
            "--allow-fail",
            "--require-stdout",
            "--from-node",
            doc3["document_node_id"],
            "--",
            sys.executable,
            "-c",
            "pass",
        )
        bad_verify = run_fails(repo, "evidence", "verify", "--node", bad_predicate["node_id"])
        if "predicate_failed" not in bad_verify.stdout:
            raise AssertionError(f"evidence verify did not validate predicate failure: {bad_verify.stdout}")
        run(repo, "evidence", "set-state", "--node", bad_predicate["node_id"], "--state", "invalidated", "--source-node", doc3["document_node_id"])
        invalid_verify = run_fails(repo, "evidence", "verify", "--node", bad_predicate["node_id"])
        if "inactive_evidence" not in invalid_verify.stdout:
            raise AssertionError(f"evidence verify did not validate currentness: {invalid_verify.stdout}")

        run(repo, "workflow", "begin", "--session-id", "full-open-session", "--endpoint", "full-gate", "--content", "Full gate open fixture.")
        run(repo, "exec", "start", "--endpoint", "full-gate", "--task-node", task3["task_id"], "--session-id", "full-open-session", "--summary", "Full open start")
        open_refused = run_fails(repo, "work", "close", "--apply", "--mode", "full", "--endpoint", "full-gate", "--summary", "Should refuse")
        if "Full mode close/apply refused" not in open_refused.stderr or "open_obligations" not in open_refused.stderr:
            raise AssertionError(f"Full gate did not refuse strict doctor failure: {open_refused.stderr}")

        good_evidence = run(
            repo,
            "evidence",
            "test-result",
            "--check",
            check3["acceptance_check_id"],
            "--close-check",
            "--close-task",
            "--from-node",
            doc3["document_node_id"],
            "--",
            sys.executable,
            "-c",
            "print('full gate ok')",
        )
        if check_closed_by(repo, check3["acceptance_check_id"]) != good_evidence["node_id"] or task_closed_by(repo, task3["task_id"]) != good_evidence["node_id"]:
            raise AssertionError(f"good evidence did not close full-gate task/check: {good_evidence}")
        run(repo, "endpoint", "refresh", "full-gate")
        stdout_path = repo / good_evidence["stdout_ref"]
        original_stdout = stdout_path.read_text(encoding="utf-8")
        stdout_path.write_text("tampered\n", encoding="utf-8")
        tampered_refused = run_fails(repo, "work", "close", "--apply", "--mode", "full", "--endpoint", "full-gate", "--summary", "Tampered refuse")
        if "evidence verify failed" not in tampered_refused.stderr:
            raise AssertionError(f"Full gate did not refuse evidence verify failure: {tampered_refused.stderr}")

        override = run(
            repo,
            "work",
            "close",
            "--apply",
            "--mode",
            "full",
            "--endpoint",
            "full-gate",
            "--summary",
            "Override tampered evidence for regression fixture.",
            "--override-closeout",
            "--override-reason",
            "Regression fixture proves overrides leave an audit warning.",
        )
        warning_id = override["full_closeout_gate"]["override_warning_node_id"]
        if node_kind(repo, warning_id) != "full_closeout_override":
            raise AssertionError(f"Full override did not record explicit audit warning: {override}")

        run(repo, "workflow", "begin", "--session-id", "full-pass-session", "--endpoint", "full-gate", "--content", "Full gate pass fixture.")
        run(repo, "exec", "start", "--force", "--endpoint", "full-gate", "--task-node", task3["task_id"], "--session-id", "full-pass-session", "--summary", "Full pass start")
        stdout_path.write_text(original_stdout, encoding="utf-8")
        run(repo, "semantic", "set-state", "--node", warning_id, "--state", "resolved", "--source-node", doc3["document_node_id"])
        run(repo, "endpoint", "refresh", "full-gate")
        passed = run(repo, "work", "close", "--apply", "--mode", "full", "--endpoint", "full-gate", "--summary", "Full gate pass")
        if not passed["full_closeout_gate"]["doctor_ok"] or not passed["full_closeout_gate"]["evidence_verify_ok"]:
            raise AssertionError(f"Full gate did not report both gates passing: {passed}")

        print(json.dumps({"ok": True}, indent=2, sort_keys=True))
        if postgres_started:
            run_cli(repo, "postgres-dev", "stop")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
