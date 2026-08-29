from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW_ERROR_MARKERS = (
    "Traceback",
    "sqlite3.IntegrityError",
    "psycopg",
    "ForeignKeyViolation",
    "FOREIGN KEY",
)


def run(repo: Path, *args: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if completed.returncode:
        raise AssertionError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def run_fails(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def assert_concise_failure(completed: subprocess.CompletedProcess[str], expected: str) -> None:
    stderr = completed.stderr.strip()
    if expected not in stderr:
        raise AssertionError(f"expected {expected!r} in stderr:\n{stderr}")
    leaked = [marker for marker in RAW_ERROR_MARKERS if marker in stderr]
    if leaked:
        raise AssertionError(f"raw error markers leaked {leaked}:\n{stderr}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-cli-errors-") as temp:
        repo = Path(temp)
        run(repo, "init", "--name", "cli-error-boundaries")
        prompt = run(repo, "hook", "user-prompt", "--session-id", "errors", "--content", "source prompt")
        task = run(repo, "task", "add", "--from-node", prompt["node_id"], "--body", "task body")
        check = run(
            repo,
            "acceptance",
            "add",
            "--task",
            task["task_id"],
            "--from-node",
            prompt["node_id"],
            "--body",
            "artifact check",
            "--expected-evidence-type",
            "artifact",
        )

        bad_source = run_fails(
            repo,
            "evidence",
            "user-confirmation",
            "--check",
            check["acceptance_check_id"],
            "--from-node",
            "node_doesnotexist",
            "--body",
            "confirmed",
        )
        assert_concise_failure(bad_source, "source node not found: node_doesnotexist")

        bad_defer_source = run_fails(
            repo,
            "task",
            "defer",
            "--task",
            task["task_id"],
            "--source-node",
            "node_doesnotexist",
            "--body",
            "defer it",
        )
        assert_concise_failure(bad_defer_source, "defer decision source node not found: node_doesnotexist")

        bad_task = run_fails(
            repo,
            "task",
            "defer",
            "--task",
            "task_doesnotexist",
            "--source-node",
            prompt["node_id"],
            "--body",
            "defer it",
        )
        assert_concise_failure(bad_task, "task not found: task_doesnotexist")

        bad_check = run_fails(repo, "evidence", "user-confirmation", "--check", "check_doesnotexist", "--body", "confirmed")
        assert_concise_failure(bad_check, "acceptance check not found: check_doesnotexist")

        bad_evidence = run_fails(
            repo,
            "acceptance",
            "close",
            "--check",
            check["acceptance_check_id"],
            "--evidence-node",
            "node_doesnotexist",
        )
        assert_concise_failure(bad_evidence, "evidence node not found: node_doesnotexist")

        confirmation = run(repo, "evidence", "user-confirmation", "--body", "confirmed")
        mismatch = run_fails(
            repo,
            "acceptance",
            "close",
            "--check",
            check["acceptance_check_id"],
            "--evidence-node",
            confirmation["node_id"],
        )
        assert_concise_failure(mismatch, "expects evidence type artifact")
    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
