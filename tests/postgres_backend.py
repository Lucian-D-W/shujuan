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

from shujuan.schema import SCHEMA_SQL
from shujuan.services.errors import StructuredRuntimeError
from shujuan.store import RUNTIME_SEMANTIC_SQL, postgres_schema_sql, qmark_to_psycopg, resolve_database_config, schema_objects_sql, sqlite_sql_to_postgres


def run_cli(repo: Path, *args: str, env_extra: dict[str, str] | None = None, expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
        env.pop(key, None)
    if env_extra:
        env.update(env_extra)
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


def command_output(completed: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (completed.stdout, completed.stderr) if part)


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


def assert_postgres_dev_lifecycle(payload: dict, *, running: bool, ready: bool, state: str) -> None:
    for key in ("pg_ctl", "readiness", "warnings", "state"):
        if key not in payload:
            raise AssertionError(f"postgres-dev lifecycle payload missing {key}: {payload}")
    if payload["running"] != running or payload["ready"] != ready or payload["state"] != state:
        raise AssertionError(f"postgres-dev lifecycle state mismatch: {payload}")
    if payload["pg_ctl"]["running"] != payload["running"]:
        raise AssertionError(f"postgres-dev running disagrees with pg_ctl detail: {payload}")
    if payload["readiness"]["ready"] != payload["ready"]:
        raise AssertionError(f"postgres-dev readiness disagrees with pg_isready detail: {payload}")
    if payload["state"] in {"running_not_ready", "ready_without_pg_ctl"} and not payload["warnings"]:
        raise AssertionError(f"postgres-dev contradictory state did not include diagnostics: {payload}")


def run_explicit_postgres_url_smoke(temp_root: Path, smoke_url: str, *, label: str) -> None:
    pg_repo = temp_root / label
    pg_repo.mkdir()
    pg_init = json.loads(run_cli(pg_repo, "init", "--name", "postgres-smoke", env_extra={"SHUJUAN_DATABASE_URL": smoke_url}).stdout)
    if pg_init["database"]["backend"] != "postgres":
        raise AssertionError(f"PostgreSQL init did not report postgres backend: {pg_init}")
    pg_status = json.loads(run_cli(pg_repo, "migrate", "status", env_extra={"SHUJUAN_DATABASE_URL": smoke_url}).stdout)
    if pg_status["backend"] != "postgres" or pg_status["schema_state"] != "current":
        raise AssertionError(f"PostgreSQL status smoke failed: {pg_status}")


def main() -> int:
    converted = qmark_to_psycopg("SELECT * FROM nodes WHERE id = ? AND label = '?' AND summary = ?")
    if converted != "SELECT * FROM nodes WHERE id = %s AND label = '?' AND summary = %s":
        raise AssertionError(f"qmark conversion changed string literals or placeholders incorrectly: {converted}")
    try:
        sqlite_sql_to_postgres("SELECT * FROM tasks t WHERE t.id = ? ORDER BY t.rowid, rowid")
    except RuntimeError as exc:
        if "does not rewrite SQLite rowid" not in str(exc):
            raise AssertionError(f"rowid rejection used the wrong diagnostic: {exc}") from exc
    else:
        raise AssertionError("PostgreSQL SQL conversion still accepted SQLite rowid ordering")

    pg_schema = postgres_schema_sql()
    forbidden_fragments = ["PRAGMA", "sqlite_master", "BEGIN IMMEDIATE"]
    for fragment in forbidden_fragments:
        if fragment in pg_schema:
            raise AssertionError(f"PostgreSQL DDL still contains SQLite-only fragment {fragment!r}")
    for fragment in ["CREATE TABLE IF NOT EXISTS project_meta", "CREATE TABLE IF NOT EXISTS nodes", "CREATE INDEX IF NOT EXISTS idx_nodes_type"]:
        if fragment not in pg_schema:
            raise AssertionError(f"PostgreSQL DDL lost required fragment {fragment!r}")
    if "FOREIGN KEY (node_id) REFERENCES nodes(id)" not in pg_schema:
        raise AssertionError("PostgreSQL DDL lost runtime foreign key constraints")
    if "endpoints_current_body_id_fkey" not in pg_schema or "FOREIGN KEY (current_body_id) REFERENCES endpoint_bodies(id)" not in pg_schema:
        raise AssertionError("PostgreSQL DDL lost endpoints.current_body_id foreign key restoration")
    expected_semantic = schema_objects_sql(
        SCHEMA_SQL,
        tables={"semantic_items", "semantic_lifecycle_events"},
        indexes={"idx_semantic_items_state", "idx_semantic_lifecycle_item"},
    )
    if RUNTIME_SEMANTIC_SQL != expected_semantic:
        raise AssertionError("runtime semantic DDL drifted from schema.py definitions")

    with tempfile.TemporaryDirectory(prefix="shujuan-pg-backend-") as temp:
        repo = Path(temp)
        no_config_status = run_cli(repo, "migrate", "status", expect_ok=False)
        if "SQLite fallback is disabled" not in command_output(no_config_status):
            raise AssertionError("missing PostgreSQL config did not refuse SQLite fallback")
        no_config_init = run_cli(repo, "init", "--name", "no-sqlite", expect_ok=False)
        if "SQLite fallback is disabled" not in command_output(no_config_init):
            raise AssertionError("plain init without PostgreSQL did not refuse SQLite fallback")
        sqlite_profile = run_cli(repo, "migrate", "status", env_extra={"SHUJUAN_DB_PROFILE": "sqlite"}, expect_ok=False)
        if "SHUJUAN_DB_PROFILE=sqlite is disabled" not in command_output(sqlite_profile):
            raise AssertionError("sqlite profile was not rejected clearly")
        sqlite_url = run_cli(repo, "migrate", "status", env_extra={"SHUJUAN_DATABASE_URL": "sqlite:///tmp/shujuan.db"}, expect_ok=False)
        if "SQLite database URLs are disabled" not in command_output(sqlite_url):
            raise AssertionError("sqlite URL was not rejected clearly")
        sqlite_cli_arg = run_cli(repo, "--database-url", "sqlite:///tmp/shujuan.db", "migrate", "status", expect_ok=False)
        if "SQLite database URLs are disabled" not in command_output(sqlite_cli_arg):
            raise AssertionError("--database-url sqlite was not rejected clearly")
        sqlite_profile_arg = run_cli(repo, "--db-profile", "sqlite", "migrate", "status", expect_ok=False)
        if "--db-profile sqlite is disabled" not in command_output(sqlite_profile_arg):
            raise AssertionError("--db-profile sqlite was not rejected clearly")

        old_profile = os.environ.get("SHUJUAN_DB_PROFILE")
        old_url = os.environ.get("SHUJUAN_DATABASE_URL")
        old_database_url = os.environ.get("DATABASE_URL")
        try:
            os.environ["SHUJUAN_DB_PROFILE"] = "postgres"
            os.environ.pop("SHUJUAN_DATABASE_URL", None)
            os.environ.pop("DATABASE_URL", None)
            try:
                resolve_database_config(repo)
            except StructuredRuntimeError as exc:
                if "requires SHUJUAN_DATABASE_URL" not in str(exc):
                    raise AssertionError(f"postgres profile failed for the wrong reason: {exc}") from exc
            else:
                raise AssertionError("postgres profile without URL silently fell back")
        finally:
            if old_profile is None:
                os.environ.pop("SHUJUAN_DB_PROFILE", None)
            else:
                os.environ["SHUJUAN_DB_PROFILE"] = old_profile
            if old_url is None:
                os.environ.pop("SHUJUAN_DATABASE_URL", None)
            else:
                os.environ["SHUJUAN_DATABASE_URL"] = old_url
            if old_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = old_database_url

        unavailable = run_cli(
            repo,
            "migrate",
            "status",
            env_extra={"SHUJUAN_DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:1/shujuan?connect_timeout=1"},  # pragma: allowlist secret
            expect_ok=False,
        )
        if "could not connect to PostgreSQL" not in command_output(unavailable):
            raise AssertionError("PostgreSQL unavailable path was not explicit")

        smoke_url = os.environ.get("SHUJUAN_TEST_POSTGRES_URL")
        smoke_url_source = "env:SHUJUAN_TEST_POSTGRES_URL" if smoke_url else None
        self_provisioned_url_repo: Path | None = None
        real_pg = "skipped"
        try:
            if not smoke_url and has_postgres_bins() and os.environ.get("SHUJUAN_SKIP_POSTGRES_DEV_SMOKE") != "1":
                self_provisioned_url_repo = Path(temp) / "explicit-url-postgres"
                self_provisioned_url_repo.mkdir()
                explicit_url_port = free_port()
                run_cli(
                    self_provisioned_url_repo,
                    "postgres-dev",
                    "init",
                    "--port",
                    str(explicit_url_port),
                )
                started_url_server = json.loads(run_cli(self_provisioned_url_repo, "postgres-dev", "start").stdout)
                assert_postgres_dev_lifecycle(started_url_server, running=True, ready=True, state="ready")
                smoke_url = json.loads(run_cli(self_provisioned_url_repo, "postgres-dev", "url").stdout)["database_url"]
                smoke_url_source = "self_provisioned_postgres_dev_url"
            if smoke_url:
                run_explicit_postgres_url_smoke(Path(temp), smoke_url, label="real-pg")
                real_pg = "passed"
        finally:
            if self_provisioned_url_repo is not None:
                run_cli(self_provisioned_url_repo, "postgres-dev", "stop")

        project_owned_pg = "skipped"
        cutover_pg = "skipped"
        if has_postgres_bins() and os.environ.get("SHUJUAN_SKIP_POSTGRES_DEV_SMOKE") != "1":
            pg_dev_repo = Path(temp) / "project-owned-pg"
            pg_dev_repo.mkdir()
            port = free_port()
            try:
                pg_init = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "init",
                        "--name",
                        "project-owned-postgres",
                        "--postgres-dev",
                        "--postgres-dev-port",
                        str(port),
                    ).stdout
                )
                if pg_init["database"]["backend"] != "postgres":
                    raise AssertionError(f"init --postgres-dev did not use postgres backend: {pg_init}")
                postgres_dev = pg_init["postgres_dev"]
                if postgres_dev["port"] != port or "postgres-dev" not in postgres_dev["data_dir"]:
                    raise AssertionError(f"init --postgres-dev did not use project-owned data dir/port: {pg_init}")
                if not str(postgres_dev["database"]).startswith("shujuan_project_owned_pg_"):
                    raise AssertionError(f"project PostgreSQL database was not repo-derived: {postgres_dev}")
                status_after_init = json.loads(run_cli(pg_dev_repo, "postgres-dev", "status").stdout)
                if not status_after_init["running"] or status_after_init["database"] != postgres_dev["database"]:
                    raise AssertionError(f"init --postgres-dev should leave the project database running: {status_after_init}")
                assert_postgres_dev_lifecycle(status_after_init, running=True, ready=True, state="ready")
                url_payload = json.loads(run_cli(pg_dev_repo, "postgres-dev", "url").stdout)
                if f"127.0.0.1:{port}" not in url_payload["database_url"]:
                    raise AssertionError(f"postgres-dev url did not target project-owned port: {url_payload}")
                stop_payload = json.loads(run_cli(pg_dev_repo, "postgres-dev", "stop").stdout)
                if stop_payload["running"] or not stop_payload["stopped"]:
                    raise AssertionError(f"postgres-dev stop did not report stopped cluster: {stop_payload}")
                status_after_stop = json.loads(run_cli(pg_dev_repo, "postgres-dev", "status").stdout)
                assert_postgres_dev_lifecycle(status_after_stop, running=False, ready=False, state="stopped")
                start_payload = json.loads(run_cli(pg_dev_repo, "postgres-dev", "start").stdout)
                assert_postgres_dev_lifecycle(start_payload, running=True, ready=True, state="ready")
                status_after_start = json.loads(run_cli(pg_dev_repo, "postgres-dev", "status").stdout)
                assert_postgres_dev_lifecycle(status_after_start, running=True, ready=True, state="ready")
                pg_status = json.loads(run_cli(pg_dev_repo, "migrate", "status").stdout)
                if pg_status["backend"] != "postgres" or pg_status["schema_state"] != "current":
                    raise AssertionError(f"fresh project-owned PostgreSQL status failed: {pg_status}")
                (pg_dev_repo / "workflow.md").write_text(
                    "# PostgreSQL Workflow\n\n## Scope\n\nExercise prompt, session, run, and endpoint reads on project-owned PostgreSQL.\n",
                    encoding="utf-8",
                )
                workflow_doc = json.loads(run_cli(pg_dev_repo, "doc", "import", "workflow.md", "--source-type", "plan").stdout)
                workflow_scope = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "scope",
                        "create",
                        "--body",
                        "PostgreSQL workflow runtime scope.",
                        "--source-node",
                        workflow_doc["document_node_id"],
                    ).stdout
                )
                workflow_task = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "task",
                        "add",
                        "--body",
                        "Exercise project-owned PostgreSQL workflow begin and exec start.",
                        "--contract",
                        workflow_scope["contract_id"],
                        "--from-node",
                        workflow_doc["document_node_id"],
                    ).stdout
                )
                workflow_check = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "acceptance",
                        "add",
                        "--task",
                        workflow_task["task_id"],
                        "--body",
                        "Project-owned PostgreSQL readiness gate captures argv evidence and closes the task.",
                        "--expected-evidence-type",
                        "test_result",
                        "--from-node",
                        workflow_doc["document_node_id"],
                    ).stdout
                )
                workflow_endpoint = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "endpoint",
                        "create",
                        "pg-workflow",
                        "--description",
                        "PostgreSQL workflow endpoint.",
                        "--root-node",
                        workflow_scope["node_id"],
                    ).stdout
                )
                workflow = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "workflow",
                        "begin",
                        "--session-id",
                        "pg-workflow-session",
                        "--endpoint",
                        "pg-workflow",
                        "--content",
                        "Continue project-owned PostgreSQL workflow runtime chain.",
                    ).stdout
                )
                if workflow["session_id"] != "pg-workflow-session" or not workflow["context"]["ranked_context"]:
                    raise AssertionError(f"workflow begin did not return PostgreSQL context: {workflow}")
                workflow_url = json.loads(run_cli(pg_dev_repo, "postgres-dev", "url").stdout)["database_url"]
                import psycopg

                workflow_conn = psycopg.connect(workflow_url)
                try:
                    session_count = workflow_conn.execute(
                        "SELECT COUNT(*) FROM conversation_sessions WHERE id = %s",
                        ("pg-workflow-session",),
                    ).fetchone()[0]
                    message_node = workflow_conn.execute(
                        "SELECT node_id FROM messages WHERE session_id = %s AND actor = 'user'",
                        ("pg-workflow-session",),
                    ).fetchone()
                    node_count = workflow_conn.execute(
                        "SELECT COUNT(*) FROM nodes WHERE id = %s",
                        (message_node[0] if message_node else None,),
                    ).fetchone()[0]
                finally:
                    workflow_conn.close()
                if session_count != 1 or not message_node or node_count != 1:
                    raise AssertionError("workflow begin did not persist session/message/node rows in PostgreSQL")
                exec_start = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "exec",
                        "start",
                        "--session-id",
                        "pg-workflow-session",
                        "--task-node",
                        workflow_task["node_id"],
                        "--endpoint",
                        "pg-workflow",
                        "--summary",
                        "PostgreSQL workflow runtime test run.",
                    ).stdout
                )
                if not exec_start["ok"] or not exec_start["preflight"]["ok"]:
                    raise AssertionError(f"exec start did not pass after workflow begin session capture: {exec_start}")
                workflow_conn = psycopg.connect(workflow_url)
                try:
                    run_count = workflow_conn.execute(
                        "SELECT COUNT(*) FROM agent_runs WHERE id = %s AND session_id = %s",
                        (exec_start["run_id"], "pg-workflow-session"),
                    ).fetchone()[0]
                finally:
                    workflow_conn.close()
                if run_count != 1:
                    raise AssertionError("exec start did not persist agent_run with the workflow session FK")
                readiness_evidence = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "evidence",
                        "test-result",
                        "--check",
                        workflow_check["acceptance_check_id"],
                        "--close-check",
                        "--close-task",
                        "--require-stdout",
                        "--stdout-contains",
                        "readiness-ok",
                        "--from-node",
                        workflow_doc["document_node_id"],
                        "--",
                        sys.executable,
                        "-c",
                        "print('readiness-ok')",
                    ).stdout
                )
                if not readiness_evidence["predicate_ok"] or readiness_evidence["close_skipped"]["skipped"]:
                    raise AssertionError(f"readiness argv evidence did not close the PostgreSQL check: {readiness_evidence}")
                exec_stop = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "exec",
                        "stop",
                        "--run",
                        exec_start["run_id"],
                        "--endpoint",
                        "pg-workflow",
                        "--summary",
                        "PostgreSQL readiness exec stop.",
                        "--no-impact",
                    ).stdout
                )
                if exec_stop["run_id"] != exec_start["run_id"] or not exec_stop["after_snapshot_id"]:
                    raise AssertionError(f"exec stop did not close the PostgreSQL run: {exec_stop}")
                active_audit = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "audit",
                        "record",
                        "--endpoint",
                        "pg-workflow",
                        "--source-node",
                        workflow_doc["document_node_id"],
                        "--body",
                        "Temporary readiness audit blocker.",
                        "--finding",
                        "Temporary readiness audit blocker.",
                    ).stdout
                )
                blocked_consume = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "audit",
                        "consume",
                        "--endpoint",
                        "pg-workflow",
                        "--require-zero",
                        expect_ok=False,
                    ).stdout
                )
                if blocked_consume["ok"] or blocked_consume["active_count"] != 1:
                    raise AssertionError(f"audit consume --require-zero did not block active finding: {blocked_consume}")
                resolved = json.loads(
                    run_cli(
                        pg_dev_repo,
                        "semantic",
                        "set-state",
                        "--node",
                        active_audit["audit_finding_node_ids"][0],
                        "--state",
                        "resolved",
                        "--source-node",
                        active_audit["artifact_node_id"],
                        "--endpoint",
                        "pg-workflow",
                        "--reason",
                        "Temporary readiness audit blocker resolved in test.",
                    ).stdout
                )
                if resolved["state"] != "resolved":
                    raise AssertionError(f"semantic resolution did not resolve audit blocker: {resolved}")
                clean_consume = json.loads(run_cli(pg_dev_repo, "audit", "consume", "--endpoint", "pg-workflow", "--require-zero").stdout)
                if not clean_consume["ok"] or clean_consume["active_count"] != 0:
                    raise AssertionError(f"audit consume still reported resolved findings: {clean_consume}")
                refresh = json.loads(run_cli(pg_dev_repo, "endpoint", "refresh", "pg-workflow").stdout)
                if not refresh["ok"]:
                    raise AssertionError(f"endpoint refresh failed before readiness gate: {refresh}")
                workflow_status = json.loads(run_cli(pg_dev_repo, "endpoint", "status", "pg-workflow").stdout)
                if workflow_status["endpoint"]["node_id"] != workflow_endpoint["node_id"]:
                    raise AssertionError(f"endpoint status could not read PostgreSQL workflow endpoint: {workflow_status}")
                workflow_doctor = json.loads(run_cli(pg_dev_repo, "endpoint", "doctor", "pg-workflow", "--strict-closeout").stdout)
                if workflow_doctor["endpoint"] != "pg-workflow" or not workflow_doctor["ok"]:
                    raise AssertionError(f"endpoint strict doctor did not pass after readiness closure: {workflow_doctor}")
                active_report = json.loads(run_cli(pg_dev_repo, "report", "endpoint", "pg-workflow", "--active-only").stdout)
                full_report = json.loads(run_cli(pg_dev_repo, "report", "endpoint", "pg-workflow", "--full").stdout)
                if (
                    not active_report["ok"]
                    or active_report["next_valid_entry_point"]["active_obligation_count"] != 0
                    or active_report["direction"]["scope_contract"]["id"] != workflow_scope["contract_id"]
                    or active_report["closure_state"]["closed_check_count"] < 1
                    or full_report["closure_state"]["closed_checks"][0]["id"] != workflow_check["acceptance_check_id"]
                ):
                    raise AssertionError(f"active-only endpoint report was not answerable for a new agent: {active_report}")
                if any(
                    active_audit["audit_finding_node_ids"][0] == item.get("id")
                    for bucket in active_report["active_obligations"].values()
                    for item in bucket
                ):
                    raise AssertionError(f"resolved audit finding leaked into active-only obligations: {active_report}")
                inactive_ids = {item["node_id"] for item in workflow_status["semantic_projection"]["inactive"]}
                if active_audit["audit_finding_node_ids"][0] not in inactive_ids:
                    raise AssertionError(f"resolved audit finding was not historically traceable in endpoint status: {workflow_status['semantic_projection']}")
                evidence_verify = json.loads(run_cli(pg_dev_repo, "evidence", "verify", "--endpoint", "pg-workflow").stdout)
                if not evidence_verify["ok"] or evidence_verify["checked_nodes"] < 1:
                    raise AssertionError(f"evidence verify failed on PostgreSQL readiness endpoint: {evidence_verify}")
                readiness = json.loads(run_cli(pg_dev_repo, "ready", "new-project", "--endpoint", "pg-workflow").stdout)
                if not readiness["ok"]:
                    raise AssertionError(f"new-project readiness gate did not pass on project-owned PostgreSQL: {readiness}")
                required_names = {item["name"] for item in readiness["requirements"]}
                for required in {
                    "postgres_backend_current",
                    "source_backed_scope_task_check",
                    "prompt_session_context",
                    "exec_start_stop",
                    "argv_evidence",
                    "audit_consume_require_zero",
                    "endpoint_refresh_fixed_sections",
                    "strict_closeout",
                    "active_only_report",
                    "evidence_verify",
                    "provider_default_off",
                }:
                    if required not in required_names:
                        raise AssertionError(f"readiness gate missing requirement {required}: {readiness}")
                project_owned_pg = "passed"
            finally:
                run_cli(pg_dev_repo, "postgres-dev", "stop")
            cutover_repo = Path(temp) / "current-repo-cutover"
            cutover_repo.mkdir()
            disabled_cutover = run_cli(cutover_repo, "postgres-dev", "cutover", expect_ok=False)
            if "cutover from SQLite is disabled" not in command_output(disabled_cutover):
                raise AssertionError("legacy SQLite cutover did not fail closed")
            if (cutover_repo / ".shujuan" / "shujuan.db").exists():
                raise AssertionError("disabled cutover created a SQLite database file")
            cutover_pg = "disabled_no_sqlite_entry"

        print(
            json.dumps(
                {
                    "ok": True,
                    "postgres_unavailable": "passed",
                    "real_postgres": real_pg,
                    "real_postgres_source": smoke_url_source,
                    "project_owned_postgres": project_owned_pg,
                    "project_owned_cutover": cutover_pg,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
