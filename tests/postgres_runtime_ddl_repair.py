from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan import store
from shujuan.schema import SCHEMA_VERSION


MISSING = object()


class Rows:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class FakeRaw:
    def __init__(self, *, missing_runtime_tables: set[str] | None = None, lock_results: list[bool] | None = None) -> None:
        self.sql: list[str] = []
        self.params: list[tuple[Any, ...]] = []
        self.missing_runtime_tables = missing_runtime_tables or set()
        self.lock_results = list(lock_results or [])
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, sql: str, params: Any = MISSING) -> Rows:
        self.sql.append(sql)
        self.params.append(params)
        param_tuple = () if params is MISSING else tuple(params)
        compact = " ".join(sql.lower().split())
        if "pg_try_advisory_lock" in compact:
            acquired = self.lock_results.pop(0) if self.lock_results else False
            return Rows([{"acquired": acquired}])
        if "pg_advisory_unlock" in compact:
            released = self.lock_results.pop(0) if self.lock_results else False
            return Rows([{"released": released}])
        if "information_schema.tables" in compact:
            base_tables = {"project_meta", "applied_migrations"}
            runtime_tables = set(store.RUNTIME_REQUIRED_TABLES) - self.missing_runtime_tables
            return Rows([{"table_name": name} for name in sorted(base_tables | runtime_tables)])
        if "select distinct schema_version from project_meta" in compact:
            return Rows([{"schema_version": SCHEMA_VERSION}])
        if "information_schema.columns" in compact and "discussion_messages" in compact:
            return Rows([{"column_name": name} for name in sorted(store.RUNTIME_DISCUSSION_MESSAGE_REQUIRED_COLUMNS)])
        if "from pg_proc" in compact:
            return Rows([{"proname": name} for name in sorted(store.POSTGRES_RUNTIME_REQUIRED_FUNCTIONS)])
        if "from pg_trigger" in compact:
            trigger = param_tuple[0] if param_tuple else None
            if trigger in store.POSTGRES_RUNTIME_REQUIRED_TRIGGERS:
                return Rows([{"tgname": trigger}])
            return Rows([])
        return Rows([])

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
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
    if completed.returncode:
        raise AssertionError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
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


def assert_no_runtime_ddl(raw: FakeRaw) -> None:
    forbidden = ("create ", "alter ", "drop ", "pg_advisory_xact_lock", "pg_try_advisory_lock", "pg_advisory_unlock")
    offenders = [sql for sql in raw.sql if any(token in " ".join(sql.lower().split()) for token in forbidden)]
    if offenders:
        raise AssertionError(f"ordinary runtime path executed DDL or advisory-lock SQL: {offenders}")


def assert_percent_sql_does_not_receive_empty_params() -> None:
    raw = FakeRaw()
    conn = store.PostgresConnection(raw)
    conn.execute("CREATE OR REPLACE FUNCTION sample() RETURNS void AS $$ BEGIN RAISE EXCEPTION 'item % requires proof'; END; $$ LANGUAGE plpgsql")
    if raw.params != [MISSING]:
        raise AssertionError(f"no-parameter PostgreSQL SQL should not receive an empty params tuple: {raw.params!r}")


def assert_runtime_connect_is_validation_only() -> None:
    raw = FakeRaw()
    conn = store.PostgresConnection(raw)
    with tempfile.TemporaryDirectory(prefix="shujuan-runtime-connect-") as temp:
        original_open = store.open_db_raw
        try:
            store.open_db_raw = lambda repo_root: conn  # type: ignore[assignment]
            opened = store.connect_runtime(Path(temp))
        finally:
            store.open_db_raw = original_open  # type: ignore[assignment]
    if opened is not conn:
        raise AssertionError("connect_runtime did not return the opened connection")
    if not conn.runtime_schema_validated:
        raise AssertionError("connect_runtime did not mark runtime schema validation")
    assert_no_runtime_ddl(raw)


def assert_missing_runtime_schema_fails_fast_without_ddl() -> None:
    raw = FakeRaw(missing_runtime_tables={"semantic_items"})
    conn = store.PostgresConnection(raw)
    with tempfile.TemporaryDirectory(prefix="shujuan-runtime-missing-") as temp:
        original_open = store.open_db_raw
        try:
            store.open_db_raw = lambda repo_root: conn  # type: ignore[assignment]
            try:
                store.connect_runtime(Path(temp))
            except SystemExit as exc:
                message = str(exc)
            else:
                raise AssertionError("missing runtime table did not fail fast")
        finally:
            store.open_db_raw = original_open  # type: ignore[assignment]
    if "shujuan runtime schema is incomplete" not in message or "migrate apply" not in message:
        raise AssertionError(f"missing runtime schema diagnostic was unclear: {message}")
    assert_no_runtime_ddl(raw)


def assert_runtime_ddl_is_migration_owned() -> None:
    migration = (ROOT / "migrations" / "shujuan" / "003_v5_runtime_schema_ownership.sql").read_text(encoding="utf-8")
    required_fragments = [
        "CREATE TABLE IF NOT EXISTS interaction_events",
        "CREATE TABLE IF NOT EXISTS discussion_messages",
        "CREATE TABLE IF NOT EXISTS provider_runs",
        "CREATE TABLE IF NOT EXISTS semantic_items",
        "CREATE OR REPLACE FUNCTION shujuan_validate_semantic_item",
        "CREATE CONSTRAINT TRIGGER shujuan_task_closure_guard",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in migration]
    if missing:
        raise AssertionError(f"runtime migration is missing required DDL fragments: {missing}")
    source_text = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in ("shujuan/store.py", "shujuan/cli.py")
    )
    if "ensure_runtime_tables" in source_text or "pg_advisory_xact_lock" in source_text:
        raise AssertionError("obsolete hidden runtime DDL path is still present in source files")


def assert_ddl_lock_diagnostics_are_bounded() -> None:
    raw = FakeRaw(lock_results=[False, False, False])
    conn = store.PostgresConnection(raw)
    try:
        store.acquire_postgres_ddl_lock(conn, purpose="test bounded lock", timeout_seconds=0.01, retry_seconds=0.001)
    except SystemExit as exc:
        message = str(exc)
    else:
        raise AssertionError("DDL lock acquisition unexpectedly succeeded")
    if "could not acquire shujuan PostgreSQL DDL lock" not in message or "No schema DDL was reported as successful" not in message:
        raise AssertionError(f"DDL lock failure diagnostic was unclear: {message}")

    release_raw = FakeRaw(lock_results=[False])
    release_conn = store.PostgresConnection(release_raw)
    try:
        store.release_postgres_ddl_lock(release_conn, purpose="test release")
    except SystemExit as exc:
        release_message = str(exc)
    else:
        raise AssertionError("DDL lock release unexpectedly succeeded")
    if "failed to release shujuan PostgreSQL DDL lock" not in release_message:
        raise AssertionError(f"DDL lock release failure diagnostic was unclear: {release_message}")


def assert_concurrent_read_doctor_status_smoke() -> str:
    if not has_postgres_bins():
        return "skipped: native PostgreSQL binaries not found"
    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-runtime-concurrency-") as temp:
        repo = Path(temp)
        try:
            run_cli(repo, "init", "--name", "runtime-concurrency", "--postgres-dev", "--postgres-dev-port", str(free_port()))
            postgres_started = True
            (repo / "plan.md").write_text("# Runtime Concurrency\n\n## Scope\n\nRead-only runtime commands.\n", encoding="utf-8")
            doc = json.loads(run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan").stdout)
            scope = json.loads(
                run_cli(repo, "scope", "create", "--body", "Runtime concurrency scope.", "--source-node", doc["document_node_id"]).stdout
            )
            run_cli(repo, "endpoint", "create", "runtime-concurrency", "--description", "Runtime concurrency endpoint.", "--root-node", scope["node_id"])
            commands = [
                ("report", "endpoint", "runtime-concurrency", "--active-only", "--markdown"),
                ("endpoint", "doctor", "runtime-concurrency", "--strict-closeout", "--allow-fail"),
                ("migrate", "status"),
            ]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
                env.pop(key, None)
            processes = [
                subprocess.Popen(
                    [sys.executable, "-m", "shujuan", "--repo", str(repo), *command],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                for command in commands
            ]
            results = [process.communicate(timeout=45) + (process.returncode,) for process in processes]
            failures = []
            for command, (stdout, stderr, returncode) in zip(commands, results):
                combined = f"{stdout}\n{stderr}"
                if returncode != 0 or "DeadlockDetected" in combined or "deadlock detected" in combined.lower():
                    failures.append({"command": command, "returncode": returncode, "stdout": stdout, "stderr": stderr})
            if failures:
                raise AssertionError(f"concurrent runtime read/doctor/status command failure: {failures}")
            return "passed"
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")


def main() -> int:
    assert_percent_sql_does_not_receive_empty_params()
    assert_runtime_connect_is_validation_only()
    assert_missing_runtime_schema_fails_fast_without_ddl()
    assert_runtime_ddl_is_migration_owned()
    assert_ddl_lock_diagnostics_are_bounded()
    concurrency = assert_concurrent_read_doctor_status_smoke()
    print(json.dumps({"ok": True, "concurrency": concurrency}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
