from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..schema_roles import (
    CONTRACTION_CANDIDATE_TABLES,
    CONTRACTION_MIGRATION_FILENAME,
    PREDICATE_TABLE_REPLACEMENT_CARRIERS,
    schema_role_by_table,
)


MigrateHandler = Callable[[argparse.Namespace], int]
MIGRATE_HANDLER_KEYS = ("status", "apply", "repair_ledger")
MIGRATE_DEPENDENCY_KEYS = (
    "SCHEMA_VERSION",
    "acquire_postgres_ddl_lock",
    "ensure_layout",
    "inspect_runtime_schema",
    "inspect_schema",
    "new_id",
    "now_iso",
    "open_db_raw",
    "print_json",
    "release_postgres_ddl_lock",
    "relpath",
    "resolve_database_config",
    "sha256_text",
    "sql_literal",
    "table_exists",
    "write_schema_version_file",
)

SCHEMA_VERSION: str | None = None
acquire_postgres_ddl_lock: Callable[..., Any] | None = None
ensure_layout: Callable[[Path], Path] | None = None
inspect_runtime_schema: Callable[[sqlite3.Connection], dict[str, Any]] | None = None
inspect_schema: Callable[[sqlite3.Connection], dict[str, Any]] | None = None
new_id: Callable[[str], str] | None = None
now_iso: Callable[[], str] | None = None
open_db_raw: Callable[..., sqlite3.Connection | None] | None = None
print_json: Callable[[Any], None] | None = None
release_postgres_ddl_lock: Callable[..., Any] | None = None
relpath: Callable[[Path, Path], str] | None = None
resolve_database_config: Callable[[Path], Any] | None = None
sha256_text: Callable[[str], str] | None = None
sql_literal: Callable[[str], str] | None = None
table_exists: Callable[[sqlite3.Connection, str], bool] | None = None
write_schema_version_file: Callable[[Path], None] | None = None


def _validate_handlers(handlers: Mapping[str, MigrateHandler]) -> None:
    missing = [key for key in MIGRATE_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"migrate command boundary is missing: {', '.join(missing)}")


def _migrate_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in MIGRATE_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"migrate handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in MIGRATE_DEPENDENCY_KEYS}


def _require_dependency(name: str) -> Any:
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"migrate command dependency is not configured: {name}")
    return value


def build_migrate_handlers(deps: Mapping[str, Any]) -> dict[str, MigrateHandler]:
    """Build migrate handlers from cli.py-owned runtime helpers without importing cli.py."""
    globals().update(_migrate_dependencies(deps))
    return {
        "status": cmd_migrate_status,
        "apply": cmd_migrate_apply,
        "repair_ledger": cmd_migrate_repair_ledger,
    }


def migration_dir(repo: Path) -> Path:
    return repo / "migrations" / "shujuan"


def legacy_runtime_migration_dir(repo: Path) -> Path:
    ensure_layout_fn = _require_dependency("ensure_layout")
    return ensure_layout_fn(repo) / "migrations"


def migration_files(repo: Path) -> list[Path]:
    return sorted(path for path in migration_dir(repo).glob("*.sql") if path.is_file())


def applied_migrations(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    table_exists_fn = _require_dependency("table_exists")
    if not table_exists_fn(conn, "applied_migrations"):
        return {}
    return {
        str(row["filename"]): row
        for row in conn.execute(
            "SELECT id, filename, checksum, applied_at FROM applied_migrations ORDER BY filename"
        ).fetchall()
    }


def migration_status_kind(
    schema_state: str,
    pending_count: int,
    *,
    runtime_schema: dict[str, Any] | None,
    drift_count: int = 0,
) -> str:
    if drift_count:
        return "postgres_migration_drift"
    if schema_state == "current" and runtime_schema and runtime_schema.get("ok") is True and pending_count == 0:
        return "postgres_runtime_schema_current"
    if schema_state == "current" and runtime_schema and runtime_schema.get("ok") is not True:
        return "postgres_runtime_schema_incomplete"
    if pending_count:
        return "postgres_migrations_pending"
    if schema_state == "db_missing":
        return "postgres_database_missing"
    if schema_state == "bootstrap_conflict":
        return "postgres_bootstrap_conflict"
    if schema_state == "needs_migration":
        return "postgres_schema_needs_migration"
    return f"postgres_schema_{schema_state}"


def migration_status(repo: Path, conn: sqlite3.Connection) -> dict[str, Any]:
    inspect_schema_fn = _require_dependency("inspect_schema")
    inspect_runtime_schema_fn = _require_dependency("inspect_runtime_schema")
    resolve_database_config_fn = _require_dependency("resolve_database_config")
    relpath_fn = _require_dependency("relpath")
    sha256_text_fn = _require_dependency("sha256_text")
    schema_version = _require_dependency("SCHEMA_VERSION")

    schema = inspect_schema_fn(conn)
    runtime_schema = inspect_runtime_schema_fn(conn) if schema["state"] == "current" else None
    config = resolve_database_config_fn(repo)
    files = migration_files(repo)
    applied = applied_migrations(conn)
    migrations = []
    for path in files:
        filename = path.name
        checksum = sha256_text_fn(path.read_text(encoding="utf-8"))
        row = applied.get(filename)
        migrations.append(
            {
                "filename": filename,
                "checksum": checksum,
                "status": "applied" if row else "pending",
                "applied_at": row["applied_at"] if row else None,
                "checksum_matches": (row["checksum"] == checksum) if row else None,
            }
        )
    file_names = {path.name for path in files}
    for filename, row in applied.items():
        if filename in file_names:
            continue
        migrations.append(
            {
                "filename": filename,
                "checksum": None,
                "status": "missing_file",
                "applied_at": row["applied_at"],
                "checksum_matches": False,
                "recorded_checksum": row["checksum"],
            }
        )
    pending = [item for item in migrations if item["status"] == "pending"]
    applied_items = [item for item in migrations if item["status"] == "applied"]
    missing_files = [item for item in migrations if item["status"] == "missing_file"]
    checksum_mismatches = [
        item
        for item in migrations
        if item.get("checksum_matches") is False
    ]
    drift_count = len(checksum_mismatches)
    status_kind = migration_status_kind(schema["state"], len(pending), runtime_schema=runtime_schema, drift_count=drift_count)
    drift_blocks_apply = bool(checksum_mismatches)
    return {
        "ok": not drift_blocks_apply,
        "backend": schema["backend"],
        "database_warning": None,
        "schema_state": schema["state"],
        "status_kind": status_kind,
        "runtime_status_kind": "postgres_runtime_ready" if status_kind == "postgres_runtime_schema_current" else "postgres_runtime_not_ready",
        "migration_status_kind": "drift" if drift_blocks_apply else "pending" if pending else "applied",
        "writability_status_kind": "not_checked_by_migrate_status",
        "next_schema_check_command": "python -m shujuan migrate status",
        "next_migration_command": (
            "resolve migration drift before running python -m shujuan migrate apply"
            if drift_blocks_apply
            else "python -m shujuan migrate apply"
            if pending
            else "python -m shujuan migrate status"
        ),
        "schema_version": schema_version,
        "project_meta_versions": schema["project_meta_versions"],
        "has_migration_ledger": schema["has_migration_ledger"],
        "runtime_schema": runtime_schema,
        "migration_policy": "tracked_repo_sql",
        "schema_version_ref": relpath_fn(repo / ".shujuan" / "schema_version.json", repo),
        "migrations_dir": relpath_fn(migration_dir(repo), repo),
        "legacy_runtime_migrations_dir": relpath_fn(legacy_runtime_migration_dir(repo), repo),
        "migrations": migrations,
        "pending": pending,
        "applied": applied_items,
        "missing_files": missing_files,
        "checksum_mismatches": checksum_mismatches,
        "migration_drift": {
            "present": drift_blocks_apply,
            "count": drift_count,
            "blocks_apply": drift_blocks_apply,
            "filenames": [item["filename"] for item in checksum_mismatches],
        },
    }


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _contraction_replacement_path(table: str) -> str:
    carriers = PREDICATE_TABLE_REPLACEMENT_CARRIERS.get(table)
    if carriers:
        return " + ".join(carriers)
    role = schema_role_by_table().get(table) or {}
    return str(role.get("replacement_path") or "derive from current endpoint/task/check/evidence/semantic facts")


def contraction_migration_preflight(conn: sqlite3.Connection, filename: str) -> dict[str, Any]:
    table_exists_fn = _require_dependency("table_exists")
    if filename != CONTRACTION_MIGRATION_FILENAME:
        return {
            "ok": True,
            "required": False,
            "migration": filename,
            "blocked_by": None,
            "tables": [],
        }
    tables: list[dict[str, Any]] = []
    for table in CONTRACTION_CANDIDATE_TABLES:
        if not table_exists_fn(conn, table):
            continue
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {_quote_ident(table)}").fetchone()
        row_count = int(row["count"]) if row else 0
        if row_count:
            tables.append(
                {
                    "table": table,
                    "row_count": row_count,
                    "replacement_path": _contraction_replacement_path(table),
                }
            )
    return {
        "ok": not tables,
        "required": True,
        "migration": filename,
        "blocked_by": "non_empty_contraction_candidate_tables" if tables else None,
        "tables": tables,
        "next_action": (
            "export/review rows or create an explicit user-approved migration; normal migrate apply will not drop non-empty contracted tables"
            if tables
            else "all contraction candidate tables are absent or empty; normal migrate apply may continue"
        ),
        "silent_archive": False,
        "new_db_tables": False,
    }


def cmd_migrate_status(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    open_db_raw_fn = _require_dependency("open_db_raw")
    print_json_fn = _require_dependency("print_json")
    resolve_database_config_fn = _require_dependency("resolve_database_config")
    relpath_fn = _require_dependency("relpath")
    schema_version = _require_dependency("SCHEMA_VERSION")

    conn = open_db_raw_fn(repo, allow_missing=True)
    if conn is None:
        config = resolve_database_config_fn(repo)
        payload = {
                "ok": True,
                "backend": config.backend,
                "database_warning": None,
                "schema_state": "db_missing",
                "status_kind": "postgres_database_missing",
                "runtime_status_kind": "postgres_runtime_not_available",
                "migration_status_kind": "unknown_without_database",
                "writability_status_kind": "not_checked_database_missing",
                "next_schema_check_command": "python -m shujuan init --postgres-dev",
                "next_migration_command": "python -m shujuan init --postgres-dev",
                "schema_version": schema_version,
                "project_meta_versions": [],
                "has_migration_ledger": False,
                "runtime_schema": None,
                "migration_policy": "tracked_repo_sql",
                "schema_version_ref": relpath_fn(repo / ".shujuan" / "schema_version.json", repo),
                "migrations_dir": relpath_fn(migration_dir(repo), repo),
                "legacy_runtime_migrations_dir": relpath_fn(legacy_runtime_migration_dir(repo), repo),
                "migrations": [],
                "pending": [],
                "applied": [],
            }
        print_json_fn(payload)
        return 0
    status = migration_status(repo, conn)
    print_json_fn(status)
    return 0


def cmd_migrate_apply(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    open_db_raw_fn = _require_dependency("open_db_raw")
    table_exists_fn = _require_dependency("table_exists")
    acquire_postgres_ddl_lock_fn = _require_dependency("acquire_postgres_ddl_lock")
    release_postgres_ddl_lock_fn = _require_dependency("release_postgres_ddl_lock")
    new_id_fn = _require_dependency("new_id")
    now_iso_fn = _require_dependency("now_iso")
    sql_literal_fn = _require_dependency("sql_literal")
    sha256_text_fn = _require_dependency("sha256_text")
    write_schema_version_file_fn = _require_dependency("write_schema_version_file")
    print_json_fn = _require_dependency("print_json")
    schema_version = _require_dependency("SCHEMA_VERSION")

    conn = open_db_raw_fn(repo)
    assert conn is not None
    if not table_exists_fn(conn, "project_meta"):
        raise SystemExit("database is missing project_meta; run init for a new database or restore base metadata before applying migrations")
    if not table_exists_fn(conn, "applied_migrations"):
        raise SystemExit("database is missing applied_migrations; run migrate status for diagnostics before applying migrations")
    status = migration_status(repo, conn)
    mismatched = [
        item
        for item in status["applied"]
        if item.get("checksum_matches") is False
    ]
    if mismatched:
        names = ", ".join(item["filename"] for item in mismatched)
        raise SystemExit(f"migration checksum mismatch; refusing to apply pending migrations: {names}")
    applied = []
    preflights = []
    lock_info = None
    try:
        for item in status["pending"]:
            filename = item["filename"]
            path = migration_dir(repo) / filename
            sql = path.read_text(encoding="utf-8")
            checksum = sha256_text_fn(sql)
            preflight = contraction_migration_preflight(conn, filename)
            if preflight["required"]:
                preflights.append(preflight)
            if not preflight["ok"]:
                print_json_fn(
                    {
                        "ok": False,
                        "dry_run": bool(args.dry_run),
                        "migration": filename,
                        "blocked_by": preflight["blocked_by"],
                        "tables": preflight["tables"],
                        "next_action": preflight["next_action"],
                        "preflight": preflight,
                        "applied": applied,
                        "ddl_lock": None,
                    }
                )
                return 1
            if args.dry_run:
                applied.append({"filename": filename, "checksum": checksum, "dry_run": True})
                continue
            if lock_info is None:
                lock_info = acquire_postgres_ddl_lock_fn(conn, purpose="tracked migration apply")
            migration_id = new_id_fn("migration")
            applied_at = now_iso_fn()
            script = (
                "BEGIN;\n"
                f"{sql.rstrip()}\n"
                "INSERT INTO applied_migrations (id, filename, checksum, applied_at)\n"
                f"VALUES ({sql_literal_fn(migration_id)}, {sql_literal_fn(filename)}, {sql_literal_fn(checksum)}, {sql_literal_fn(applied_at)});\n"
                "COMMIT;"
            )
            try:
                conn.executescript(script)
            except Exception:
                conn.rollback()
                raise
            applied.append({"filename": filename, "checksum": checksum, "dry_run": False})
        if not args.dry_run:
            conn.execute("UPDATE project_meta SET schema_version = ?", (schema_version,))
            conn.commit()
            write_schema_version_file_fn(repo)
    except Exception:
        conn.rollback()
        raise
    finally:
        if lock_info is not None:
            release_postgres_ddl_lock_fn(conn, purpose="tracked migration apply")
    print_json_fn({"ok": True, "applied": applied, "ddl_lock": lock_info, "contraction_preflights": preflights, "status": migration_status(repo, conn)})
    return 0


def cmd_migrate_repair_ledger(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    open_db_raw_fn = _require_dependency("open_db_raw")
    print_json_fn = _require_dependency("print_json")
    sha256_text_fn = _require_dependency("sha256_text")

    if not args.reason.strip():
        raise SystemExit("migrate repair-ledger requires a non-empty --reason.")
    conn = open_db_raw_fn(repo)
    assert conn is not None
    status = migration_status(repo, conn)
    applied_rows = applied_migrations(conn)
    migrations = {item["filename"]: item for item in status.get("migrations") or []}
    item = migrations.get(args.filename)
    if not item:
        raise SystemExit(f"migration file is not tracked: {args.filename}")
    if item.get("status") != "applied":
        raise SystemExit(f"migration ledger repair only applies to already-applied migrations: {args.filename}")
    current_checksum = item.get("checksum")
    if not current_checksum:
        path = migration_dir(repo) / args.filename
        current_checksum = sha256_text_fn(path.read_text(encoding="utf-8"))
    runtime_ok = bool((status.get("runtime_schema") or {}).get("ok"))
    schema_current = status.get("schema_state") == "current"
    eligible = bool(runtime_ok and schema_current)
    no_op = item.get("checksum_matches") is True
    payload = {
        "ok": bool(eligible),
        "dry_run": not args.apply,
        "filename": args.filename,
        "reason": args.reason,
        "repair_scope": "migration_ledger_only",
        "physical_schema_changes": False,
        "drop_archive_shrink": False,
        "source_of_truth_branch": "ledger_repair_only" if eligible else "unresolved_needs_user_decision",
        "before": {
            "recorded_checksum": applied_rows[args.filename]["checksum"],
            "current_file_checksum": current_checksum,
            "checksum_matches": item.get("checksum_matches"),
            "status_kind": status.get("status_kind"),
        },
        "applied": False,
        "no_op": no_op,
    }
    if not eligible:
        payload["ok"] = False
        payload["blocker"] = "live schema is not current/runtime-ok; use a forward-only repair migration or record unresolved source-of-truth material"
        print_json_fn(payload)
        return 1
    if args.apply and not no_op:
        conn.execute(
            "UPDATE applied_migrations SET checksum = ? WHERE filename = ?",
            (current_checksum, args.filename),
        )
        conn.commit()
        payload["applied"] = True
        payload["after"] = migration_status(repo, conn)
    else:
        payload["after"] = status
    print_json_fn(payload)
    return 0


def register_migrate(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, MigrateHandler],
) -> None:
    """Register the migrate command family while cli.py keeps global flags and dispatch."""
    _validate_handlers(handlers)

    migrate = subparsers.add_parser("migrate")
    migrate_sub = migrate.add_subparsers(dest="migrate_command", required=True)
    migrate_status = migrate_sub.add_parser("status")
    migrate_status.set_defaults(func=handlers["status"])
    migrate_apply = migrate_sub.add_parser("apply")
    migrate_apply.add_argument("--dry-run", action="store_true")
    migrate_apply.set_defaults(func=handlers["apply"])
    migrate_repair = migrate_sub.add_parser("repair-ledger")
    migrate_repair.add_argument("--filename", required=True)
    migrate_repair.add_argument("--reason", required=True)
    migrate_repair.add_argument("--apply", action="store_true")
    migrate_repair.set_defaults(func=handlers["repair_ledger"])


__all__ = [
    "MIGRATE_HANDLER_KEYS",
    "applied_migrations",
    "build_migrate_handlers",
    "cmd_migrate_apply",
    "cmd_migrate_repair_ledger",
    "cmd_migrate_status",
    "contraction_migration_preflight",
    "legacy_runtime_migration_dir",
    "migration_dir",
    "migration_files",
    "migration_status",
    "register_migrate",
]
