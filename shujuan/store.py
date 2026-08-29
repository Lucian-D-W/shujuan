from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from .schema import SCHEMA_SQL, SCHEMA_VERSION
from .services.errors import StructuredRuntimeError


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


POSTGRES_DEV_DIR = Path(".shujuan") / "postgres-dev"
POSTGRES_DEV_CONFIG = POSTGRES_DEV_DIR / "config.json"
POSTGRES_DEV_CREDENTIALS = POSTGRES_DEV_DIR / "credentials.json"
POSTGRES_REQUIRED_MESSAGE = (
    "No shujuan PostgreSQL database is configured. Run `python -m shujuan init --postgres-dev` "
    "or set SHUJUAN_DATABASE_URL to a postgresql:// URL. SQLite fallback is disabled."
)
SQLITE_DISABLED_MESSAGE = (
    "SQLite is disabled as a shujuan runtime/write backend. Use project-owned PostgreSQL "
    "via `python -m shujuan init --postgres-dev` or SHUJUAN_DATABASE_URL=postgresql://..."
)


def _raise_runtime_error(code: str, message: str, *, safe_next_action: str, **extra: Any) -> None:
    raise StructuredRuntimeError(code, message, read_only=True, safe_next_action=safe_next_action, **extra)


def postgres_dev_config_path(repo_root: Path) -> Path:
    return repo_root / POSTGRES_DEV_CONFIG


def postgres_dev_credentials_path(repo_root: Path) -> Path:
    return repo_root / POSTGRES_DEV_CREDENTIALS


def postgres_dev_database_url(repo_root: Path) -> str | None:
    config_path = postgres_dev_config_path(repo_root)
    credentials_path = postgres_dev_credentials_path(repo_root)
    if not config_path.exists() or not credentials_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    user = quote(str(config.get("user") or credentials.get("user") or ""), safe="")
    password = quote(str(credentials.get("password") or ""), safe="")
    database = quote(str(config.get("database") or ""), safe="")
    host = str(config.get("host") or "127.0.0.1")
    port = int(config.get("port") or 55432)
    if not user or not password or not database:
        return None
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


@dataclass(frozen=True)
class DatabaseConfig:
    backend: str
    url: str | None
    profile: str
    explicit: bool
    source: str


def resolve_database_config(repo_root: Path) -> DatabaseConfig:
    profile_raw = os.environ.get("SHUJUAN_DB_PROFILE")
    profile = profile_raw.strip().lower() if profile_raw else None
    if profile == "sqlite":
        _raise_runtime_error(
            "migration_runtime_ddl_hazard",
            "SHUJUAN_DB_PROFILE=sqlite is disabled; shujuan now requires PostgreSQL.",
            safe_next_action="Use SHUJUAN_DB_PROFILE=postgres or run `python -m shujuan init --postgres-dev`.",
        )
    if profile and profile not in {"postgres", "postgresql", "product"}:
        _raise_runtime_error(
            "postgres_runtime_config_invalid",
            f"unsupported SHUJUAN_DB_PROFILE: {profile_raw}",
            safe_next_action="Use SHUJUAN_DB_PROFILE=postgres, postgresql, or product.",
        )
    url = os.environ.get("SHUJUAN_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        source = "SHUJUAN_DATABASE_URL" if os.environ.get("SHUJUAN_DATABASE_URL") else "DATABASE_URL"
        lowered = url.lower()
        if lowered.startswith(("postgresql://", "postgres://")):
            return DatabaseConfig("postgres", url, profile or "postgres", True, source)
        if lowered.startswith("sqlite:///"):
            _raise_runtime_error(
                "migration_runtime_ddl_hazard",
                "SQLite database URLs are disabled; set SHUJUAN_DATABASE_URL to postgresql://...",
                safe_next_action="Set SHUJUAN_DATABASE_URL to a postgresql:// URL or run `python -m shujuan init --postgres-dev`.",
            )
        _raise_runtime_error(
            "postgres_runtime_config_invalid",
            f"unsupported shujuan database URL scheme: {url}",
            safe_next_action="Set SHUJUAN_DATABASE_URL to a postgresql:// URL.",
        )
    if profile:
        postgres_dev_url = postgres_dev_database_url(repo_root)
        if postgres_dev_url:
            return DatabaseConfig("postgres", postgres_dev_url, profile, True, "postgres-dev")
        _raise_runtime_error(
            "postgres_runtime_unavailable",
            f"SHUJUAN_DB_PROFILE={profile} requires SHUJUAN_DATABASE_URL or project-owned postgres-dev config; refusing SQLite fallback.",
            safe_next_action="Set SHUJUAN_DATABASE_URL or run `python -m shujuan init --postgres-dev`.",
        )
    postgres_dev_url = postgres_dev_database_url(repo_root)
    if postgres_dev_url:
        return DatabaseConfig("postgres", postgres_dev_url, "postgres-dev", True, "postgres-dev")
    _raise_runtime_error(
        "postgres_runtime_unavailable",
        POSTGRES_REQUIRED_MESSAGE,
        safe_next_action="Run `python -m shujuan init --postgres-dev`, or set SHUJUAN_DATABASE_URL to a postgresql:// URL.",
    )


class EmptyCursor:
    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []


class PostgresConnection:
    backend = "postgres"

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.runtime_schema_validated = False

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> Any:
        sql = sqlite_sql_to_postgres(sql)
        if not sql.strip():
            return EmptyCursor()
        param_tuple = tuple(params or ())
        if not param_tuple:
            return self.raw.execute(sql)
        return self.raw.execute(sql, param_tuple)

    def executescript(self, script: str) -> None:
        for statement in split_sql_script(script):
            self.execute(statement)

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()


def is_postgres_connection(conn: Any) -> bool:
    return isinstance(conn, PostgresConnection)


def split_sql_script(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    dollar_quote: str | None = None
    index = 0
    while index < len(script):
        if not in_single and not in_double:
            if dollar_quote:
                if script.startswith(dollar_quote, index):
                    current.append(dollar_quote)
                    index += len(dollar_quote)
                    dollar_quote = None
                    continue
                current.append(script[index])
                index += 1
                continue
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", script[index:])
            if match:
                dollar_quote = match.group(0)
                current.append(dollar_quote)
                index += len(dollar_quote)
                continue
        char = script[index]
        current.append(char)
        if char == "'" and not in_double:
            if index + 1 < len(script) and script[index + 1] == "'":
                current.append(script[index + 1])
                index += 2
                continue
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == ";" and not in_single and not in_double and not dollar_quote:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        index += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def qmark_to_psycopg(sql: str) -> str:
    result: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'" and not in_double:
            result.append(char)
            if index + 1 < len(sql) and sql[index + 1] == "'":
                result.append(sql[index + 1])
                index += 2
                continue
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
            result.append(char)
        elif char == "?" and not in_single and not in_double:
            result.append("%s")
        else:
            result.append(char)
        index += 1
    return "".join(result)


def postgres_schema_sql() -> str:
    statements: list[str] = []
    for statement in split_sql_script(SCHEMA_SQL):
        stripped = statement.strip()
        if not stripped:
            continue
        if re.search(r"(?is)^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+endpoints\b", stripped):
            lines = [
                line
                for line in stripped.splitlines()
                if not line.strip().startswith("FOREIGN KEY (current_body_id)")
            ]
            stripped = re.sub(r",\s*\n\);?\s*$", "\n);", "\n".join(lines))
        statements.append(stripped.rstrip(";") + ";")
    statements.append(POSTGRES_ENDPOINT_CURRENT_BODY_FK_SQL)
    return "\n\n".join(statements)


POSTGRES_ENDPOINT_CURRENT_BODY_FK_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'endpoints_current_body_id_fkey'
      AND conrelid = 'endpoints'::regclass
  ) THEN
    ALTER TABLE endpoints
      ADD CONSTRAINT endpoints_current_body_id_fkey
      FOREIGN KEY (current_body_id) REFERENCES endpoint_bodies(id);
  END IF;
END $$;
""".strip()


def schema_objects_sql(schema_sql: str, *, tables: set[str], indexes: set[str]) -> str:
    objects: list[str] = []
    for statement in split_sql_script(schema_sql):
        stripped = statement.strip()
        table_match = re.match(r"(?is)^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][\w]*)\b", stripped)
        index_match = re.match(r"(?is)^CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][\w]*)\b", stripped)
        if (table_match and table_match.group(1) in tables) or (index_match and index_match.group(1) in indexes):
            objects.append(stripped.rstrip(";") + ";")
    return "\n\n".join(objects)


RUNTIME_SEMANTIC_SQL = schema_objects_sql(
    SCHEMA_SQL,
    tables={"semantic_items", "semantic_lifecycle_events"},
    indexes={"idx_semantic_items_state", "idx_semantic_lifecycle_item"},
)


RUNTIME_INTERACTION_SQL = schema_objects_sql(
    SCHEMA_SQL,
    tables={
        "interaction_events",
        "discussion_segments",
        "discussion_messages",
        "discussion_lifecycle_events",
        "projection_snapshots",
        "evidence_records",
    },
    indexes={
        "idx_interaction_events_endpoint",
        "idx_discussion_segments_endpoint",
        "idx_discussion_messages_segment",
        "idx_discussion_messages_session",
        "idx_discussion_lifecycle_segment",
        "idx_projection_snapshots_endpoint",
        "idx_evidence_records_node",
    },
)


RUNTIME_PROVIDER_SQL = schema_objects_sql(
    SCHEMA_SQL,
    tables={"provider_runs", "provider_artifacts", "provider_entity_map", "provider_facts"},
    indexes={"idx_provider_facts_run", "idx_provider_facts_external", "idx_provider_entity_map_external"},
)


POSTGRES_DDL_LOCK_ID = 77427001
POSTGRES_DDL_LOCK_TIMEOUT_SECONDS = 5.0
POSTGRES_DDL_LOCK_RETRY_SECONDS = 0.2
POSTGRES_DDL_LOCK_TIMEOUT_MS = 5000
POSTGRES_DDL_STATEMENT_TIMEOUT_MS = 60000


RUNTIME_REQUIRED_TABLES = {
    "semantic_items",
    "semantic_lifecycle_events",
    "interaction_events",
    "discussion_segments",
    "discussion_messages",
    "discussion_lifecycle_events",
    "projection_snapshots",
    "evidence_records",
    "provider_runs",
    "provider_artifacts",
    "provider_entity_map",
    "provider_facts",
}


RUNTIME_DISCUSSION_MESSAGE_REQUIRED_COLUMNS = {
    "session_id",
    "agent_name",
    "model_name",
    "source_message_id",
    "source_node_id",
}


POSTGRES_RUNTIME_REQUIRED_FUNCTIONS = {
    "shujuan_validate_semantic_item",
    "shujuan_validate_check_closure",
    "shujuan_validate_task_closure",
}


POSTGRES_RUNTIME_REQUIRED_TRIGGERS = {
    "shujuan_semantic_item_guard",
    "shujuan_check_closure_guard",
    "shujuan_task_closure_guard",
}


POSTGRES_RUNTIME_CONSTRAINT_SQL = [
    """
    CREATE OR REPLACE FUNCTION shujuan_validate_semantic_item()
    RETURNS trigger AS $$
    BEGIN
      IF NEW.current_state NOT IN ('resolved', 'deferred', 'product_backlog', 'backlog', 'invalidated', 'superseded') THEN
        IF NEW.source_node_id IS NULL THEN
          RAISE EXCEPTION 'active semantic item % requires source_node_id', NEW.node_id;
        END IF;
        IF NEW.item_type IN ('change_set', 'test_result', 'artifact', 'user_confirmation') THEN
          RETURN NEW;
        END IF;
        IF NEW.scope_node_id IS NULL
           AND NOT EXISTS (
             SELECT 1 FROM edges e
             WHERE (
               e.from_node_id = NEW.node_id
               AND e.type IN ('APPLIES_TO', 'IMPLEMENTS')
             ) OR (
               e.to_node_id = NEW.node_id
               AND e.type = 'VALIDATED_BY'
             )
           ) THEN
          RAISE EXCEPTION 'active semantic item % requires scope_node_id or active linkage edge', NEW.node_id;
        END IF;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    "DROP TRIGGER IF EXISTS shujuan_semantic_item_guard ON semantic_items;",
    """
    CREATE CONSTRAINT TRIGGER shujuan_semantic_item_guard
    AFTER INSERT OR UPDATE OF current_state, source_node_id, scope_node_id ON semantic_items
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION shujuan_validate_semantic_item();
    """,
    """
    CREATE OR REPLACE FUNCTION shujuan_validate_check_closure()
    RETURNS trigger AS $$
    DECLARE
      evidence_type TEXT;
      expected TEXT;
      allowed TEXT[];
    BEGIN
      IF NEW.closed_by_node_id IS NULL THEN
        RETURN NEW;
      END IF;
      SELECT type INTO evidence_type FROM nodes WHERE id = NEW.closed_by_node_id;
      IF evidence_type IS NULL OR evidence_type <> ALL(ARRAY['change_set','test_result','artifact','user_confirmation']) THEN
        RAISE EXCEPTION 'acceptance check % requires evidence node closure', NEW.id;
      END IF;
      expected := lower(replace(coalesce(NEW.expected_evidence_type, ''), '-', '_'));
      IF expected = '' THEN
        RETURN NEW;
      ELSIF expected IN ('diff', 'change_set') THEN
        allowed := ARRAY['change_set'];
      ELSIF expected IN ('test', 'test_result') THEN
        allowed := ARRAY['test_result'];
      ELSIF expected IN ('artifact', 'file') THEN
        allowed := ARRAY['artifact'];
      ELSIF expected = 'doc_update' THEN
        allowed := ARRAY['artifact','change_set'];
      ELSIF expected IN ('user_confirmation', 'confirmation') THEN
        allowed := ARRAY['user_confirmation'];
      ELSE
        allowed := ARRAY[expected];
      END IF;
      IF NOT evidence_type = ANY(allowed) AND NOT EXISTS (
        SELECT 1
        FROM nodes override_node
        WHERE override_node.type = 'audit_finding'
          AND override_node.props::jsonb @> jsonb_build_object(
            'kind', 'evidence_type_override',
            'check_id', NEW.id,
            'evidence_node_id', NEW.closed_by_node_id
          )
      ) THEN
        RAISE EXCEPTION 'acceptance check % expected %, got %', NEW.id, NEW.expected_evidence_type, evidence_type;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    "DROP TRIGGER IF EXISTS shujuan_check_closure_guard ON acceptance_checks;",
    """
    CREATE CONSTRAINT TRIGGER shujuan_check_closure_guard
    AFTER INSERT OR UPDATE OF closed_by_node_id, expected_evidence_type ON acceptance_checks
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION shujuan_validate_check_closure();
    """,
    """
    CREATE OR REPLACE FUNCTION shujuan_validate_task_closure()
    RETURNS trigger AS $$
    BEGIN
      IF NEW.closed_by_node_id IS NOT NULL
         AND EXISTS (
           SELECT 1 FROM acceptance_checks ac
           WHERE ac.task_id = NEW.id
             AND ac.closed_by_node_id IS NULL
         ) THEN
        RAISE EXCEPTION 'task % cannot close while acceptance checks remain open', NEW.id;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    "DROP TRIGGER IF EXISTS shujuan_task_closure_guard ON tasks;",
    """
    CREATE CONSTRAINT TRIGGER shujuan_task_closure_guard
    AFTER INSERT OR UPDATE OF closed_by_node_id ON tasks
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION shujuan_validate_task_closure();
    """,
]


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (TypeError, KeyError):
        return row[index]


def configure_postgres_ddl_timeouts(conn: PostgresConnection) -> None:
    if not is_postgres_connection(conn):
        raise SystemExit(SQLITE_DISABLED_MESSAGE)
    conn.raw.execute(f"SET lock_timeout = '{POSTGRES_DDL_LOCK_TIMEOUT_MS}ms'")
    conn.raw.execute(f"SET statement_timeout = '{POSTGRES_DDL_STATEMENT_TIMEOUT_MS}ms'")


def acquire_postgres_ddl_lock(
    conn: PostgresConnection,
    *,
    purpose: str,
    timeout_seconds: float = POSTGRES_DDL_LOCK_TIMEOUT_SECONDS,
    retry_seconds: float = POSTGRES_DDL_LOCK_RETRY_SECONDS,
) -> dict[str, Any]:
    if not is_postgres_connection(conn):
        raise SystemExit(SQLITE_DISABLED_MESSAGE)
    started = time.monotonic()
    deadline = started + max(timeout_seconds, 0.0)
    attempts = 0
    while True:
        attempts += 1
        row = conn.raw.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (POSTGRES_DDL_LOCK_ID,)).fetchone()
        if bool(_row_value(row, "acquired")):
            configure_postgres_ddl_timeouts(conn)
            return {
                "lock_id": POSTGRES_DDL_LOCK_ID,
                "purpose": purpose,
                "attempts": attempts,
                "waited_seconds": round(time.monotonic() - started, 3),
                "lock_timeout_ms": POSTGRES_DDL_LOCK_TIMEOUT_MS,
                "statement_timeout_ms": POSTGRES_DDL_STATEMENT_TIMEOUT_MS,
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            waited = round(time.monotonic() - started, 3)
            raise SystemExit(
                "could not acquire shujuan PostgreSQL DDL lock "
                f"{POSTGRES_DDL_LOCK_ID} for {purpose} after {attempts} attempts over {waited}s. "
                "Another init/migrate process may be running; retry after it finishes. "
                "No schema DDL was reported as successful."
            )
        time.sleep(min(retry_seconds, remaining))


def release_postgres_ddl_lock(conn: PostgresConnection, *, purpose: str) -> None:
    if not is_postgres_connection(conn):
        raise SystemExit(SQLITE_DISABLED_MESSAGE)
    row = conn.raw.execute("SELECT pg_advisory_unlock(%s) AS released", (POSTGRES_DDL_LOCK_ID,)).fetchone()
    if not bool(_row_value(row, "released")):
        raise SystemExit(
            "failed to release shujuan PostgreSQL DDL lock "
            f"{POSTGRES_DDL_LOCK_ID} for {purpose}. "
            "Treat the migration/bootstrap result as failed and rerun diagnostics."
        )


def apply_postgres_runtime_schema_ddl(conn: PostgresConnection) -> None:
    if not is_postgres_connection(conn):
        raise SystemExit(SQLITE_DISABLED_MESSAGE)
    conn.executescript(RUNTIME_PROVIDER_SQL)
    for statement in POSTGRES_RUNTIME_CONSTRAINT_SQL:
        conn.raw.execute(statement)


def inspect_runtime_schema(conn: PostgresConnection) -> dict[str, Any]:
    if not is_postgres_connection(conn):
        raise SystemExit(SQLITE_DISABLED_MESSAGE)
    tables = {
        str(row["table_name"])
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        ).fetchall()
    }
    columns: set[str] = set()
    if "discussion_messages" in tables:
        columns = {
            str(row["column_name"])
            for row in conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'discussion_messages'
                """
            ).fetchall()
        }
    functions = {
        str(row["proname"])
        for row in conn.execute(
            """
            SELECT p.proname
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = current_schema()
            """
        ).fetchall()
    }
    triggers: set[str] = set()
    for trigger in sorted(POSTGRES_RUNTIME_REQUIRED_TRIGGERS):
        row = conn.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname = ?", (trigger,)).fetchone()
        if row:
            triggers.add(str(row["tgname"]))
    missing_tables = sorted(RUNTIME_REQUIRED_TABLES - tables)
    missing_columns = sorted(RUNTIME_DISCUSSION_MESSAGE_REQUIRED_COLUMNS - columns) if "discussion_messages" in tables else sorted(RUNTIME_DISCUSSION_MESSAGE_REQUIRED_COLUMNS)
    missing_functions = sorted(POSTGRES_RUNTIME_REQUIRED_FUNCTIONS - functions)
    missing_triggers = sorted(POSTGRES_RUNTIME_REQUIRED_TRIGGERS - triggers)
    return {
        "ok": not (missing_tables or missing_columns or missing_functions or missing_triggers),
        "missing_tables": missing_tables,
        "missing_discussion_message_columns": missing_columns,
        "missing_functions": missing_functions,
        "missing_triggers": missing_triggers,
    }


def runtime_schema_diagnostic(purpose: str, status: dict[str, Any]) -> str:
    parts = []
    if status.get("missing_tables"):
        parts.append("missing tables: " + ", ".join(status["missing_tables"]))
    if status.get("missing_discussion_message_columns"):
        parts.append("missing discussion_messages columns: " + ", ".join(status["missing_discussion_message_columns"]))
    if status.get("missing_functions"):
        parts.append("missing PostgreSQL functions: " + ", ".join(status["missing_functions"]))
    if status.get("missing_triggers"):
        parts.append("missing PostgreSQL triggers: " + ", ".join(status["missing_triggers"]))
    detail = "; ".join(parts) or "unknown runtime schema gap"
    return (
        f"shujuan runtime schema is incomplete for {purpose}: {detail}. "
        "Run `python -m shujuan migrate apply` to install tracked runtime DDL, "
        "or `python -m shujuan migrate status` for migration/repair diagnostics, "
        "or use `python -m shujuan init --postgres-dev` for a new project database. "
        "Runtime connections no longer run schema DDL automatically."
    )


def assert_runtime_schema_ready(conn: PostgresConnection, *, purpose: str = "runtime") -> None:
    if not is_postgres_connection(conn):
        raise SystemExit(SQLITE_DISABLED_MESSAGE)
    if conn.runtime_schema_validated:
        return
    status = inspect_runtime_schema(conn)
    if not status["ok"]:
        raise SystemExit(runtime_schema_diagnostic(purpose, status))
    conn.runtime_schema_validated = True


def sqlite_sql_to_postgres(sql: str) -> str:
    converted = sql
    if re.search(r"\browid\b", converted):
        raise RuntimeError("PostgreSQL backend does not rewrite SQLite rowid; use explicit stable ordering columns")
    return qmark_to_psycopg(converted)


def postgres_connection_error_message(exc: BaseException, config: DatabaseConfig) -> str:
    if config.source == "postgres-dev":
        hint = (
            "Project-owned postgres-dev is configured but unreachable. "
            "Run `python -m shujuan postgres-dev start`, or check SHUJUAN_DATABASE_URL if an environment override is intended."
        )
    else:
        hint = (
            f"PostgreSQL is configured from {config.source} but is unreachable. "
            "Check SHUJUAN_DATABASE_URL, or run `python -m shujuan postgres-dev start` if you intended the project-owned postgres-dev database."
        )
    return f"could not connect to PostgreSQL: {exc}\n{hint}"


def _connect_postgres(config: DatabaseConfig) -> PostgresConnection:
    if not config.url:
        _raise_runtime_error(
            "postgres_runtime_unavailable",
            POSTGRES_REQUIRED_MESSAGE,
            safe_next_action="Run `python -m shujuan init --postgres-dev`, or set SHUJUAN_DATABASE_URL to a postgresql:// URL.",
        )
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise StructuredRuntimeError(
            "postgres_driver_missing",
            "PostgreSQL backend requires the `psycopg[binary]` package; no SQLite fallback was used.",
            read_only=True,
            safe_next_action="Install `psycopg[binary]`, then rerun the command.",
        ) from exc
    try:
        raw = psycopg.connect(config.url, row_factory=dict_row, connect_timeout=2)
    except psycopg.OperationalError as exc:
        code = "postgres_runtime_stale_handle" if config.source == "postgres-dev" else "postgres_runtime_unavailable"
        safe_next_action = (
            "Run `python -m shujuan postgres-dev start`, then rerun the command."
            if config.source == "postgres-dev"
            else "Check SHUJUAN_DATABASE_URL, or run `python -m shujuan postgres-dev start` if the project-owned runtime was intended."
        )
        raise StructuredRuntimeError(
            code,
            postgres_connection_error_message(exc, config),
            read_only=True,
            safe_next_action=safe_next_action,
        ) from exc
    return PostgresConnection(raw)


def open_db_raw(
    repo_root: Path,
    *,
    allow_missing: bool = False,
    allow_filesystem_writes: bool = True,
) -> PostgresConnection | None:
    config = resolve_database_config(repo_root)
    if config.backend == "postgres":
        assert config.url is not None
        if allow_filesystem_writes:
            ensure_layout(repo_root)
        elif not (repo_root / ".shujuan").exists():
            _raise_runtime_error(
                "postgres_runtime_layout_missing",
                "read-only shujuan diagnostics cannot create missing .shujuan layout metadata. Run `python -m shujuan init --postgres-dev` for a new project, `python -m shujuan migrate status` for an initialized project, or rerun the read-only command after restoring the project .shujuan directory.",
                safe_next_action="Restore the project .shujuan directory or run `python -m shujuan init --postgres-dev`.",
            )
        return _connect_postgres(config)
    _raise_runtime_error(
        "migration_runtime_ddl_hazard",
        SQLITE_DISABLED_MESSAGE,
        safe_next_action="Use project-owned PostgreSQL via `python -m shujuan init --postgres-dev` or SHUJUAN_DATABASE_URL=postgresql://...",
    )


def inspect_schema(conn: PostgresConnection) -> dict[str, Any]:
    if not is_postgres_connection(conn):
        raise SystemExit(SQLITE_DISABLED_MESSAGE)
    tables = {
        str(row["table_name"])
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        ).fetchall()
    }
    backend = "postgres"
    versions: list[str] = []
    if "project_meta" in tables:
        versions = [
            str(row["schema_version"])
            for row in conn.execute("SELECT DISTINCT schema_version FROM project_meta ORDER BY schema_version").fetchall()
        ]
    if "project_meta" not in tables:
        state = "bootstrap_conflict"
    elif any(version != SCHEMA_VERSION for version in versions):
        state = "needs_migration"
    else:
        state = "current"
    return {
        "state": state,
        "backend": backend,
        "schema_version": SCHEMA_VERSION,
        "project_meta_versions": versions,
        "has_project_meta": "project_meta" in tables,
        "has_migration_ledger": "applied_migrations" in tables,
        "tables": sorted(tables),
    }


def connect_runtime(repo_root: Path, *, read_only_filesystem: bool = False) -> PostgresConnection:
    conn = open_db_raw(repo_root, allow_filesystem_writes=False) if read_only_filesystem else open_db_raw(repo_root)
    assert conn is not None
    if read_only_filesystem:
        conn.execute("SET TRANSACTION READ ONLY")
    state = inspect_schema(conn)
    if state["state"] == "bootstrap_conflict":
        _raise_runtime_error(
            "postgres_runtime_schema_incomplete",
            "shujuan database exists but base metadata is missing. Run `shujuan migrate status` for diagnostics.",
            safe_next_action="Run `python -m shujuan migrate status` and repair the database metadata before retrying.",
        )
    if not state["has_migration_ledger"]:
        _raise_runtime_error(
            "postgres_runtime_schema_incomplete",
            "shujuan database has no migration ledger. Run `shujuan migrate status` for diagnostics.",
            safe_next_action="Run `python -m shujuan migrate status` and repair the migration ledger before retrying.",
        )
    mismatched = sorted({version for version in state["project_meta_versions"] if version != SCHEMA_VERSION})
    if mismatched:
        _raise_runtime_error(
            "postgres_runtime_schema_mismatch",
            "shujuan schema_version mismatch: "
            f"database has {', '.join(mismatched)}, code expects {SCHEMA_VERSION}. "
            "Run explicit migrations instead of relying on connect() to rewrite schema metadata.",
            safe_next_action="Run the explicit migration flow, then rerun the command.",
        )
    assert_runtime_schema_ready(conn, purpose="runtime connection")
    if not read_only_filesystem:
        write_schema_version_file(repo_root)
    return conn


def connect(repo_root: Path) -> PostgresConnection:
    return connect_runtime(repo_root)


def connect_read_only(repo_root: Path) -> PostgresConnection:
    return connect_runtime(repo_root, read_only_filesystem=True)


def ensure_layout(repo_root: Path) -> Path:
    shujuan_dir = repo_root / ".shujuan"
    for child in ("exports", "patches", "artifacts", "logs", "migrations"):
        (shujuan_dir / child).mkdir(parents=True, exist_ok=True)
    return shujuan_dir


def write_schema_version_file(repo_root: Path) -> None:
    shujuan_dir = ensure_layout(repo_root)
    version_path = shujuan_dir / "schema_version.json"
    version_path.write_text(
        json_dumps({"schema_version": SCHEMA_VERSION, "updated_at": now_iso()}),
        encoding="utf-8",
    )


def init_schema(repo_root: Path) -> PostgresConnection:
    config = resolve_database_config(repo_root)
    if config.backend == "postgres":
        assert config.url is not None
        ensure_layout(repo_root)
        conn = _connect_postgres(config)
        purpose = "bootstrap schema initialization"
        acquire_postgres_ddl_lock(conn, purpose=purpose)
        try:
            conn.executescript(postgres_schema_sql())
            apply_postgres_runtime_schema_ddl(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_postgres_ddl_lock(conn, purpose=purpose)
        assert_runtime_schema_ready(conn, purpose=purpose)
        write_schema_version_file(repo_root)
        return conn
    raise SystemExit(SQLITE_DISABLED_MESSAGE)


def create_node(
    conn: PostgresConnection,
    node_type: str,
    label: str | None = None,
    summary: str | None = None,
    props: dict[str, Any] | None = None,
) -> str:
    node_id = new_id("node")
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO nodes
          (id, type, label, summary, created_at, updated_at, valid_from, props)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node_id,
            node_type,
            label,
            summary,
            timestamp,
            timestamp,
            timestamp,
            json_dumps(props or {}),
        ),
    )
    return node_id


def create_edge(
    conn: PostgresConnection,
    from_node_id: str,
    edge_type: str,
    to_node_id: str,
    *,
    reason: str | None = None,
    confidence: float | None = None,
    evidence_node_id: str | None = None,
    created_by: str = "script",
    props: dict[str, Any] | None = None,
) -> str:
    edge_id = new_id("edge")
    conn.execute(
        """
        INSERT INTO edges
          (id, from_node_id, type, to_node_id, reason, confidence,
           evidence_node_id, created_by, created_at, props)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            edge_id,
            from_node_id,
            edge_type,
            to_node_id,
            reason,
            confidence,
            evidence_node_id,
            created_by,
            now_iso(),
            json_dumps(props or {}),
        ),
    )
    return edge_id


def ensure_project_meta(
    conn: PostgresConnection,
    *,
    name: str,
    repo_root: Path,
    default_branch: str | None,
) -> str:
    existing = conn.execute("SELECT id FROM project_meta LIMIT 1").fetchone()
    if existing:
        return str(existing["id"])
    project_id = new_id("project")
    conn.execute(
        """
        INSERT INTO project_meta
          (id, name, repo_root, default_branch, schema_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (project_id, name, str(repo_root), default_branch, SCHEMA_VERSION, now_iso()),
    )
    return project_id
