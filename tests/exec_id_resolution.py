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


def db(repo: Path):
    return connect(repo)


def scalar_count(row) -> int:
    try:
        return int(row[0])
    except (KeyError, TypeError):
        return int(next(iter(row.values())))


def executes_target(repo: Path, run_node_id: str) -> str | None:
    conn = db(repo)
    try:
        row = conn.execute(
            "SELECT to_node_id FROM edges WHERE from_node_id = ? AND type = 'EXECUTES'",
            (run_node_id,),
        ).fetchone()
        return row["to_node_id"] if row else None
    finally:
        conn.close()


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-exec-id-") as temp:
        repo = Path(temp)
        try:
            run(repo, "init", "--name", "exec-id-resolution", "--postgres-dev", "--postgres-dev-port", str(free_port()))
            postgres_started = True
            (repo / "plan.md").write_text("# Plan\n\n## Scope\n\nResolve task ids.\n", encoding="utf-8")
            doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            scope = run(repo, "scope", "create", "--body", "Resolve execution ids.", "--source-node", doc["document_node_id"])
            task = run(
                repo,
                "task",
                "add",
                "--contract",
                scope["contract_id"],
                "--body",
                "Execution accepts task ids and task node ids.",
                "--from-node",
                doc["document_node_id"],
            )
            run(repo, "endpoint", "create", "exec-id", "--description", "Exec id endpoint.", "--root-node", scope["node_id"])
            run(repo, "workflow", "begin", "--session-id", "session_exec_id", "--endpoint", "exec-id", "--content", "Use task ids naturally.")

            by_task_id = run(
                repo,
                "exec",
                "start",
                "--endpoint",
                "exec-id",
                "--summary",
                "Start by task id",
                "--task-node",
                task["task_id"],
            )
            if not by_task_id["preflight"]["ok"] or by_task_id["preflight"]["task_node_id"] != task["node_id"]:
                raise AssertionError(f"task id did not resolve through preflight: {by_task_id}")
            if by_task_id["preflight"]["task_input_kind"] != "task_id":
                raise AssertionError(f"task id input kind was not recorded: {by_task_id}")
            if executes_target(repo, by_task_id["run_node_id"]) != task["node_id"]:
                raise AssertionError("task id EXECUTES edge did not point at task node")

            by_task_node = run(
                repo,
                "exec",
                "start",
                "--force",
                "--endpoint",
                "exec-id",
                "--summary",
                "Start by task node id",
                "--task-node",
                task["node_id"],
            )
            if not by_task_node["preflight"]["ok"] or by_task_node["preflight"]["task_node_id"] != task["node_id"]:
                raise AssertionError(f"task node id did not remain valid: {by_task_node}")
            if by_task_node["preflight"]["task_input_kind"] != "task_node_id":
                raise AssertionError(f"task node input kind was not recorded: {by_task_node}")
            if executes_target(repo, by_task_node["run_node_id"]) != task["node_id"]:
                raise AssertionError("task node EXECUTES edge did not point at task node")

            missing = run_fails(
                repo,
                "exec",
                "start",
                "--force",
                "--endpoint",
                "exec-id",
                "--summary",
                "Unknown task id",
                "--task-node",
                "task_missing_exec_id",
            )
            if "task id or task node not found: task_missing_exec_id" not in missing.stderr:
                raise AssertionError(f"unknown task id failed unclearly: {missing.stderr}")
            conn = db(repo)
            try:
                bad_edges = scalar_count(
                    conn.execute("SELECT COUNT(*) FROM edges WHERE to_node_id = ?", ("task_missing_exec_id",)).fetchone()
                )
            finally:
                conn.close()
            if bad_edges:
                raise AssertionError("unknown task id created a bad edge")
        finally:
            if postgres_started:
                run(repo, "postgres-dev", "stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
