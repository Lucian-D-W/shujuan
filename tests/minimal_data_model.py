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

from shujuan.store import connect, create_node, json_dumps, now_iso


AGCP10_TABLES = [
    "source_promises",
    "hard_predicates",
    "forbidden_substitutes",
    "work_chains",
    "task_predicate_links",
    "evidence_predicate_coverage",
    "review_results",
    "endpoint_inherited_blockers",
]


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


def table_names(repo: Path) -> set[str]:
    conn = connect(repo)
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()").fetchall()
    conn.close()
    return {str(row["table_name"]) for row in rows}


def assert_tables_exist(repo: Path, label: str) -> None:
    missing = sorted(set(AGCP10_TABLES) - table_names(repo))
    if missing:
        raise AssertionError(f"{label} missing AGCP10 tables: {missing}")


def write_repo_migration(repo: Path) -> None:
    migration_dir = repo / "migrations" / "shujuan"
    migration_dir.mkdir(parents=True, exist_ok=True)
    source = ROOT / "migrations" / "shujuan" / "001_agcp10_minimal_data_model.sql"
    (migration_dir / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def drop_agcp10_tables(repo: Path) -> None:
    conn = connect(repo)
    for table in reversed(AGCP10_TABLES):
        conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    conn.commit()
    conn.close()


def expect_fk_failure(repo: Path, sql: str, params: tuple[str, ...]) -> None:
    conn = connect(repo)
    try:
        try:
            conn.execute(sql, params)
            conn.commit()
        except Exception:
            conn.rollback()
            return
        raise AssertionError("expected FK/constraint failure did not occur")
    finally:
        conn.close()


def expect_constraint_failure(repo: Path, sql: str, params: tuple) -> None:
    conn = connect(repo)
    try:
        try:
            conn.execute(sql, params)
            conn.commit()
        except Exception:
            conn.rollback()
            return
        raise AssertionError("expected constraint failure did not occur")
    finally:
        conn.close()


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-minimal-data-model-") as temp:
        repo = Path(temp)
        try:
            init = run(
                repo,
                "init",
                "--name",
                "minimal-data-model",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")
            assert_tables_exist(repo, "fresh init")

            write_repo_migration(repo)
            drop_agcp10_tables(repo)
            missing_after_drop = sorted(set(AGCP10_TABLES) & table_names(repo))
            if missing_after_drop:
                raise AssertionError(f"drop fixture failed; tables still present: {missing_after_drop}")
            status_before = run(repo, "migrate", "status")
            if "001_agcp10_minimal_data_model.sql" not in {item["filename"] for item in status_before["pending"]}:
                raise AssertionError(f"AGCP10 migration was not pending: {status_before}")
            apply_result = run(repo, "migrate", "apply")
            if "001_agcp10_minimal_data_model.sql" not in {item["filename"] for item in apply_result["applied"]}:
                raise AssertionError(f"AGCP10 migration did not apply: {apply_result}")
            assert_tables_exist(repo, "migration apply")

            (repo / "plan.md").write_text(
                "# Minimal Data Model\n\n## Acceptance\n\nPredicate rows must persist into AGCP10 tables.\n",
                encoding="utf-8",
            )
            doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            source_node = doc["document_node_id"]
            contract = run(repo, "scope", "create", "--body", "AGCP10 focused contract.", "--source-node", source_node)
            task = run(repo, "task", "add", "--contract", contract["contract_id"], "--body", "AGCP10 task.", "--from-node", source_node)
            check_a = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task["task_id"],
                "--body",
                "Predicate A is proven.",
                "--expected-evidence-type",
                "test_result",
                "--from-node",
                source_node,
            )
            check_b = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task["task_id"],
                "--body",
                "Predicate B is proven.",
                "--expected-evidence-type",
                "test_result",
                "--from-node",
                source_node,
            )
            endpoint_name = "minimal-data-model-endpoint"
            endpoint = run(repo, "endpoint", "create", endpoint_name, "--description", "AGCP10 endpoint.", "--root-node", contract["node_id"])

            conn = connect(repo)
            endpoint_row = conn.execute("SELECT id, node_id FROM endpoints WHERE name = ?", (endpoint_name,)).fetchone()
            promise_id = "sp_agcp10_minimal_001"
            predicate_a = "hp_agcp10_minimal_a"
            predicate_b = "hp_agcp10_minimal_b"
            work_chain_id = "wc_agcp10_minimal"
            conn.execute(
                """
                INSERT INTO source_promises
                  (id, endpoint_id, source_node_id, source_locator, kind, text, hardness, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (promise_id, endpoint_row["id"], source_node, "plan.md#Acceptance", "source_plan", "AGCP10 tables exist and integrate.", "hard", now_iso(), json_dumps({})),
            )
            conn.execute(
                """
                INSERT INTO hard_predicates
                  (id, source_promise_id, claim, proof_required, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (predicate_a, promise_id, "Predicate A must be covered.", json_dumps(["test_result"]), now_iso(), json_dumps({})),
            )
            conn.execute(
                """
                INSERT INTO hard_predicates
                  (id, source_promise_id, claim, proof_required, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (predicate_b, promise_id, "Predicate B must be covered.", json_dumps(["test_result"]), now_iso(), json_dumps({})),
            )
            conn.execute(
                """
                INSERT INTO forbidden_substitutes
                  (id, predicate_id, substitute_text, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("fs_agcp10_minimal_a", predicate_a, "node props only", "Normalized coverage table must also exist.", now_iso()),
            )
            conn.execute(
                """
                INSERT INTO work_chains
                  (id, endpoint_id, name, mode, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (work_chain_id, endpoint_row["id"], "AGCP10 minimal data model", "full", now_iso(), json_dumps({})),
            )
            conn.execute(
                """
                INSERT INTO task_predicate_links
                  (task_id, check_id, predicate_id, relationship)
                VALUES (?, ?, ?, ?)
                """,
                (task["task_id"], check_a["acceptance_check_id"], predicate_a, "proves"),
            )
            conn.execute(
                """
                INSERT INTO task_predicate_links
                  (task_id, check_id, predicate_id, relationship)
                VALUES (?, ?, ?, ?)
                """,
                (task["task_id"], check_b["acceptance_check_id"], predicate_b, "proves"),
            )
            artifact_node_id = create_node(conn, "artifact", "review artifact", "read-only review result")
            conn.execute(
                """
                INSERT INTO review_results
                  (id, endpoint_id, work_chain_id, reviewer_agent, reviewer_model, result, summary, artifact_node_id, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("rr_agcp10_minimal", endpoint_row["id"], work_chain_id, "reviewer", "model", "partial", "Review table accepts linked artifact.", artifact_node_id, now_iso(), json_dumps({})),
            )
            conn.commit()
            conn.close()

            matrix_path = repo / "predicate_matrix.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "predicate_coverage_matrix": [
                            {
                                "check_id": check_a["acceptance_check_id"],
                                "predicate_id": predicate_a,
                                "assertion": "Predicate A covered.",
                                "result": "pass",
                                "not_covered": False,
                                "reason": "",
                            },
                            {
                                "check_id": check_b["acceptance_check_id"],
                                "predicate_id": predicate_b,
                                "assertion": "Predicate B covered.",
                                "result": "pass",
                                "not_covered": False,
                                "reason": "",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            test_result = run(
                repo,
                "evidence",
                "test-result",
                "--predicate-coverage-matrix",
                "predicate_matrix.json",
                "--check",
                check_a["acceptance_check_id"],
                "--check",
                check_b["acceptance_check_id"],
                "--from-node",
                source_node,
                "--",
                sys.executable,
                "-c",
                "print('agcp10 matrix persistence')",
            )
            if test_result["predicate_coverage_persistence"]["inserted_count"] != 2:
                raise AssertionError(f"predicate coverage rows did not persist: {test_result}")
            conn = connect(repo)
            coverage_rows = conn.execute(
                "SELECT check_id, predicate_id, result FROM evidence_predicate_coverage WHERE evidence_node_id = ? ORDER BY predicate_id",
                (test_result["node_id"],),
            ).fetchall()
            conn.close()
            if len(coverage_rows) != 2 or {row["result"] for row in coverage_rows} != {"pass"}:
                raise AssertionError(f"persisted evidence_predicate_coverage rows were wrong: {coverage_rows}")

            child_endpoint = run(repo, "endpoint", "create", "minimal-data-model-child", "--description", "Child endpoint.", "--root-node", task["node_id"])
            run(repo, "endpoint", "link-child", "--parent", endpoint_name, "--child", "minimal-data-model-child")
            audit = run(
                repo,
                "audit",
                "record",
                "--endpoint",
                endpoint_name,
                "--source-node",
                source_node,
                "--body",
                "AGCP10 inherited blocker representation.",
                "--finding",
                "Inherited blocker can be represented in endpoint_inherited_blockers.",
                "--check",
                check_a["acceptance_check_id"],
            )
            conn = connect(repo)
            child_row = conn.execute("SELECT id FROM endpoints WHERE name = ?", ("minimal-data-model-child",)).fetchone()
            source_row = conn.execute("SELECT id FROM endpoints WHERE name = ?", (endpoint_name,)).fetchone()
            conn.execute(
                """
                INSERT INTO endpoint_inherited_blockers
                  (id, child_endpoint_id, source_endpoint_id, finding_node_id, target_kind, target_id, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "eib_agcp10_minimal",
                    child_row["id"],
                    source_row["id"],
                    audit["audit_finding_node_ids"][0],
                    "check",
                    check_a["acceptance_check_id"],
                    now_iso(),
                    json_dumps({"source": "focused_test"}),
                ),
            )
            conn.commit()
            conn.close()

            expect_fk_failure(
                repo,
                "INSERT INTO task_predicate_links (task_id, check_id, predicate_id, relationship) VALUES (?, ?, ?, ?)",
                (task["task_id"], check_a["acceptance_check_id"], "missing_predicate", "proves"),
            )
            expect_constraint_failure(
                repo,
                """
                INSERT INTO review_results
                  (id, endpoint_id, work_chain_id, reviewer_agent, reviewer_model, result, summary, artifact_node_id, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("rr_missing_summary", source_row["id"], work_chain_id, "reviewer", "model", "accept", None, artifact_node_id, now_iso(), json_dumps({})),
            )

            print(
                json.dumps(
                    {
                        "ok": True,
                        "results": {
                            "fresh_postgres_schema_tables_exist": True,
                            "tracked_migration_recreates_tables": True,
                            "fk_backed_predicate_records_insert": True,
                            "predicate_coverage_matrix_persisted_rows": True,
                            "review_results_links_artifact": True,
                            "endpoint_inherited_blocker_table_represents_47_fact": True,
                            "fk_constraints_reject_bad_predicate_link": True,
                            "review_results_summary_not_null": True,
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
