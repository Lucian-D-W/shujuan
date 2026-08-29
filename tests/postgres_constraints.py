from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from postgres_backend import command_output, free_port, has_postgres_bins, run_cli


def expect_pg_reject(conn, label: str, fragment: str, operation) -> None:
    try:
        operation()
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if fragment not in str(exc):
            raise AssertionError(f"{label} failed with the wrong error: {exc}") from exc
        return
    raise AssertionError(f"{label} unexpectedly committed")


def main() -> int:
    postgres_constraints = "skipped"
    sqlite_runtime_disabled = False
    with tempfile.TemporaryDirectory(prefix="shujuan-pg-constraints-") as temp:
        temp_root = Path(temp)
        sqlite_repo = temp_root / "sqlite"
        sqlite_repo.mkdir()
        plain_init = run_cli(sqlite_repo, "init", "--name", "sqlite-dev-shim", expect_ok=False)
        plain_init_output = command_output(plain_init)
        if "SQLite fallback is disabled" not in plain_init_output or "init --postgres-dev" not in plain_init_output:
            raise AssertionError("plain init without PostgreSQL did not clearly reject SQLite runtime fallback")
        if (sqlite_repo / ".shujuan" / "shujuan.db").exists():
            raise AssertionError("plain init without PostgreSQL created a SQLite runtime database")
        sqlite_runtime_disabled = True
        if has_postgres_bins() and os.environ.get("SHUJUAN_SKIP_POSTGRES_DEV_SMOKE") != "1":
            pg_repo = temp_root / "pg"
            pg_repo.mkdir()
            port = free_port()
            try:
                run_cli(pg_repo, "init", "--name", "pg-constraints", "--postgres-dev", "--postgres-dev-port", str(port))
                (pg_repo / "plan.md").write_text("# Constraint Plan\n\nPostgreSQL constraint scope.\n", encoding="utf-8")
                doc = json.loads(run_cli(pg_repo, "doc", "import", "plan.md", "--source-type", "plan").stdout)
                scope = json.loads(run_cli(pg_repo, "scope", "create", "--body", "Constraint scope.", "--source-node", doc["document_node_id"]).stdout)
                task = json.loads(run_cli(pg_repo, "task", "add", "--body", "Constraint task.", "--contract", scope["contract_id"], "--from-node", doc["document_node_id"]).stdout)
                check = json.loads(
                    run_cli(
                        pg_repo,
                        "acceptance",
                        "add",
                        "--task",
                        task["task_id"],
                        "--body",
                        "Constraint check.",
                        "--expected-evidence-type",
                        "test_result",
                        "--from-node",
                        doc["document_node_id"],
                    ).stdout
                )
                url = json.loads(run_cli(pg_repo, "postgres-dev", "url").stdout)["database_url"]
                import psycopg

                conn = psycopg.connect(url)
                try:
                    timestamp = "2026-05-18T00:00:00+00:00"

                    def bad_semantic():
                        conn.execute(
                            "INSERT INTO nodes (id, type, label, summary, created_at, updated_at, valid_from, props) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                            ("node_pg_bad_semantic", "assumption", "bad", "missing source/applies", timestamp, timestamp, timestamp, "{}"),
                        )
                        conn.execute(
                            "INSERT INTO semantic_items (id, node_id, item_type, current_state, created_at, updated_at, props) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            ("semantic_pg_bad", "node_pg_bad_semantic", "assumption", "active", timestamp, timestamp, "{}"),
                        )

                    expect_pg_reject(conn, "active semantic without source/applies", "requires source_node_id", bad_semantic)

                    def closed_by_non_evidence():
                        conn.execute(
                            "UPDATE acceptance_checks SET closed_by_node_id = %s, closed_at = %s WHERE id = %s",
                            (task["node_id"], timestamp, check["acceptance_check_id"]),
                        )

                    expect_pg_reject(conn, "closed check without evidence node", "requires evidence node closure", closed_by_non_evidence)

                    conn.execute(
                        "INSERT INTO nodes (id, type, label, summary, created_at, updated_at, valid_from, props) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        ("node_pg_artifact", "artifact", "artifact", "wrong type", timestamp, timestamp, timestamp, "{}"),
                    )
                    conn.commit()

                    def mismatch_evidence():
                        conn.execute(
                            "UPDATE acceptance_checks SET closed_by_node_id = %s, closed_at = %s WHERE id = %s",
                            ("node_pg_artifact", timestamp, check["acceptance_check_id"]),
                        )

                    expect_pg_reject(conn, "closure evidence type mismatch", "expected test_result", mismatch_evidence)

                    conn.execute(
                        "INSERT INTO nodes (id, type, label, summary, created_at, updated_at, valid_from, props) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        ("node_pg_test_result", "test_result", "test", "valid evidence shape", timestamp, timestamp, timestamp, "{\"exit_code\": 0, \"predicate_ok\": true}"),
                    )
                    conn.commit()

                    def task_closed_with_open_check():
                        conn.execute(
                            "UPDATE tasks SET closed_by_node_id = %s, closed_at = %s WHERE id = %s",
                            ("node_pg_test_result", timestamp, task["task_id"]),
                        )

                    expect_pg_reject(conn, "task closure with open checks", "cannot close while acceptance checks remain open", task_closed_with_open_check)

                    def dangling_endpoint_body():
                        conn.execute(
                            "UPDATE endpoints SET current_body_id = %s WHERE name = %s",
                            ("endpoint_body_missing", "pg-constraints"),
                        )

                    endpoint = json.loads(run_cli(pg_repo, "endpoint", "create", "pg-constraints", "--root-node", scope["node_id"]).stdout)
                    if endpoint["endpoint_body_id"] is not None:
                        raise AssertionError(f"new endpoint unexpectedly had a body before refresh: {endpoint}")
                    expect_pg_reject(conn, "dangling endpoint current body", "violates foreign key constraint", dangling_endpoint_body)
                finally:
                    conn.close()
                postgres_constraints = "passed"
            finally:
                run_cli(pg_repo, "postgres-dev", "stop")
        print(json.dumps({"ok": True, "postgres_constraints": postgres_constraints, "sqlite_runtime_disabled": sqlite_runtime_disabled}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
