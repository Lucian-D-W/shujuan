from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote


PostgresDevHandler = Callable[[argparse.Namespace], int]
POSTGRES_DEV_HANDLER_KEYS = ("init", "start", "stop", "status", "url", "cutover")
POSTGRES_DEV_DEPENDENCY_KEYS = (
    "ensure_layout",
    "json_dumps",
    "now_iso",
    "print_json",
    "print_text",
)

POSTGRES_DEV_DIR = Path(".shujuan") / "postgres-dev"
POSTGRES_DEV_CONFIG = POSTGRES_DEV_DIR / "config.json"
POSTGRES_DEV_CREDENTIALS = POSTGRES_DEV_DIR / "credentials.json"
DEFAULT_POSTGRES_DEV_PORT = 55432
DEFAULT_POSTGRES_DEV_PORT_WINDOW = 1000
DEFAULT_POSTGRES_DEV_USER = "shujuan_dev"
POSTGRES_DEV_DATABASE_PREFIX = "shujuan"

ensure_layout: Callable[[Path], Path] | None = None
json_dumps: Callable[[Any], str] | None = None
now_iso: Callable[[], str] | None = None
print_json: Callable[[Any], None] | None = None
print_text: Callable[..., None] | None = None


def _validate_handlers(handlers: Mapping[str, PostgresDevHandler]) -> None:
    missing = [key for key in POSTGRES_DEV_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"postgres-dev command boundary is missing: {', '.join(missing)}")


def _postgres_dev_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in POSTGRES_DEV_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"postgres-dev handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in POSTGRES_DEV_DEPENDENCY_KEYS}


def _require_dependency(name: str) -> Any:
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"postgres-dev command dependency is not configured: {name}")
    return value


def build_postgres_dev_handlers(deps: Mapping[str, Any]) -> dict[str, PostgresDevHandler]:
    """Build postgres-dev handlers from cli.py-owned output/layout helpers."""
    globals().update(_postgres_dev_dependencies(deps))
    return {
        "init": cmd_postgres_dev_init,
        "start": cmd_postgres_dev_start,
        "stop": cmd_postgres_dev_stop,
        "status": cmd_postgres_dev_status,
        "url": cmd_postgres_dev_url,
        "cutover": cmd_postgres_dev_cutover,
    }


def postgres_identifier_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    return slug or "project"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def default_postgres_dev_database(repo: Path) -> str:
    digest = _sha256_text(str(repo.resolve()).lower())[:8]
    max_slug = 63 - len(POSTGRES_DEV_DATABASE_PREFIX) - len(digest) - 2
    slug = postgres_identifier_slug(repo.name)[:max_slug].rstrip("_") or "project"
    return f"{POSTGRES_DEV_DATABASE_PREFIX}_{slug}_{digest}"


def default_postgres_dev_port(repo: Path) -> int:
    digest = _sha256_text(str(repo.resolve()).lower())
    offset = int(digest[:8], 16) % DEFAULT_POSTGRES_DEV_PORT_WINDOW
    return DEFAULT_POSTGRES_DEV_PORT + offset


def postgres_dev_root(repo: Path) -> Path:
    return repo / POSTGRES_DEV_DIR


def postgres_dev_config_path(repo: Path) -> Path:
    return repo / POSTGRES_DEV_CONFIG


def postgres_dev_credentials_path(repo: Path) -> Path:
    return repo / POSTGRES_DEV_CREDENTIALS


def postgres_dev_data_dir(repo: Path) -> Path:
    return postgres_dev_root(repo) / "data"


def postgres_dev_log_dir(repo: Path) -> Path:
    return postgres_dev_root(repo) / "logs"


def common_postgres_bin_dirs() -> list[Path]:
    candidates: list[Path] = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            candidates.extend(sorted(Path(base).glob("PostgreSQL/*/bin"), reverse=True))
    candidates.append(Path(r"C:\Program Files\PostgreSQL\17\bin"))
    return candidates


def port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def choose_postgres_dev_port(requested: int, *, explicit: bool) -> int:
    if explicit:
        if not port_is_available(requested):
            raise SystemExit(f"requested postgres-dev port is already in use: {requested}")
        return requested
    port = requested
    while port < requested + 100:
        if port_is_available(port):
            return port
        port += 1
    raise SystemExit(f"no available postgres-dev port found in range {requested}-{requested + 99}")


def discover_postgres_bin(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get("SHUJUAN_POSTGRES_BIN")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = path.resolve()
        if not (path / "initdb.exe").exists() and not (path / "initdb").exists():
            raise SystemExit(f"PostgreSQL bin directory does not contain initdb: {path}")
        return path
    initdb = shutil.which("initdb")
    if initdb:
        return Path(initdb).resolve().parent
    for path in common_postgres_bin_dirs():
        if (path / "initdb.exe").exists() or (path / "initdb").exists():
            return path
    raise SystemExit(
        "PostgreSQL binaries not found. Set SHUJUAN_POSTGRES_BIN or pass --pg-bin "
        "pointing at the native PostgreSQL bin directory."
    )


def postgres_exe(pg_bin: Path, name: str) -> str:
    exe = pg_bin / f"{name}.exe"
    if exe.exists():
        return str(exe)
    plain = pg_bin / name
    if plain.exists():
        return str(plain)
    raise SystemExit(f"PostgreSQL binary not found: {name} in {pg_bin}")


def run_postgres_tool(
    pg_bin: Path,
    name: str,
    args: list[str],
    *,
    env_extra: dict[str, str] | None = None,
    allow_fail: bool = False,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = str(pg_bin) + os.pathsep + env.get("PATH", "")
    if env_extra:
        env.update(env_extra)
    completed = subprocess.run(
        [postgres_exe(pg_bin, name), *args],
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
        env=env,
    )
    if completed.returncode and not allow_fail:
        raise SystemExit(
            f"{name} failed with exit code {completed.returncode}\n"
            f"STDOUT:\n{completed.stdout or ''}\nSTDERR:\n{completed.stderr or ''}"
        )
    return completed


def postgres_dev_url(config: dict[str, Any], credentials: dict[str, Any]) -> str:
    user = quote(str(config["user"]), safe="")
    password = quote(str(credentials["password"]), safe="")
    database = quote(str(config["database"]), safe="")
    return f"postgresql://{user}:{password}@127.0.0.1:{int(config['port'])}/{database}"


def read_postgres_dev_config(repo: Path) -> dict[str, Any]:
    path = postgres_dev_config_path(repo)
    if not path.exists():
        raise SystemExit("postgres-dev is not initialized. Run `python -m shujuan postgres-dev init` first.")
    return json.loads(path.read_text(encoding="utf-8"))


def read_postgres_dev_credentials(repo: Path) -> dict[str, Any]:
    path = postgres_dev_credentials_path(repo)
    if not path.exists():
        raise SystemExit("postgres-dev credentials are missing. Run `python -m shujuan postgres-dev init` again.")
    return json.loads(path.read_text(encoding="utf-8"))


def _windows_current_identity() -> str:
    completed = subprocess.run(
        ["whoami"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    identity = completed.stdout.strip()
    if completed.returncode or not identity:
        raise RuntimeError("could not resolve the current Windows identity for credential ACL hardening")
    return identity


def restrict_private_file_permissions(path: Path) -> None:
    """Limit a secret-bearing file to the owning runtime context."""
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    if os.name != "nt":
        return

    identity = _windows_current_identity()
    grant = subprocess.run(
        [
            "icacls",
            str(path),
            "/grant:r",
            f"{identity}:(F)",
            "*S-1-5-18:(F)",
            "*S-1-5-32-544:(F)",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    inheritance = subprocess.run(
        ["icacls", str(path), "/inheritance:r"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if grant.returncode or inheritance.returncode:
        raise RuntimeError("could not restrict the PostgreSQL credential file ACL")


def write_private_text(path: Path, contents: str) -> None:
    """Write secret-bearing text without leaving a broadly readable partial file."""
    temporary_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    previous_path: Path | None = None
    destination_moved = False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary_path, flags, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = None
            handle.write(contents)
        restrict_private_file_permissions(temporary_path)
        if path.exists():
            previous_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.previous")
            os.replace(path, previous_path)
            destination_moved = True
        os.replace(temporary_path, path)
        restrict_private_file_permissions(path)
        if previous_path is not None:
            previous_path.unlink()
            previous_path = None
    except BaseException:
        if destination_moved:
            path.unlink(missing_ok=True)
        if destination_moved and previous_path is not None and previous_path.exists():
            os.replace(previous_path, path)
            previous_path = None
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        if previous_path is not None and previous_path.exists():
            previous_path.unlink()


def write_postgres_dev_files(repo: Path, config: dict[str, Any], credentials: dict[str, Any]) -> None:
    json_dumps_fn = _require_dependency("json_dumps")
    root = postgres_dev_root(repo)
    root.mkdir(parents=True, exist_ok=True)
    postgres_dev_log_dir(repo).mkdir(parents=True, exist_ok=True)
    postgres_dev_config_path(repo).write_text(json_dumps_fn(config), encoding="utf-8")
    write_private_text(postgres_dev_credentials_path(repo), json_dumps_fn(credentials))


def pg_ctl_status(pg_bin: Path, data_dir: Path) -> dict[str, Any]:
    completed = run_postgres_tool(pg_bin, "pg_ctl", ["status", "-D", str(data_dir)], allow_fail=True)
    return {
        "running": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def pg_isready_status(pg_bin: Path, config: dict[str, Any]) -> dict[str, Any]:
    completed = run_postgres_tool(
        pg_bin,
        "pg_isready",
        ["-h", "127.0.0.1", "-p", str(config["port"]), "-d", "postgres"],
        allow_fail=True,
    )
    return {
        "ready": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def wait_for_pg_detail(pg_bin: Path, config: dict[str, Any], *, timeout_seconds: int = 20) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last = pg_isready_status(pg_bin, config)
    while time.time() < deadline:
        last = pg_isready_status(pg_bin, config)
        if last["ready"]:
            return last
        time.sleep(0.5)
    return last


def wait_for_pg(pg_bin: Path, config: dict[str, Any], *, timeout_seconds: int = 20) -> bool:
    return wait_for_pg_detail(pg_bin, config, timeout_seconds=timeout_seconds)["ready"]


def postgres_dev_lifecycle_payload(
    config: dict[str, Any],
    *,
    pg_ctl: dict[str, Any],
    readiness: dict[str, Any],
) -> dict[str, Any]:
    running = bool(pg_ctl["running"])
    ready = bool(readiness["ready"])
    warnings: list[str] = []
    if running and ready:
        state = "ready"
        status_kind = "postgres_dev_runtime_ready"
    elif running:
        state = "running_not_ready"
        status_kind = "postgres_dev_running_not_schema_ready"
        warnings.append("pg_ctl reports the cluster is running but pg_isready did not report ready")
    elif ready:
        state = "ready_without_pg_ctl"
        status_kind = "postgres_dev_port_ready_without_project_cluster"
        warnings.append("pg_isready reports a server on the configured port, but pg_ctl does not own the configured data directory")
    else:
        state = "stopped"
        status_kind = "postgres_dev_stopped"
    return {
        "running": running,
        "ready": ready,
        "state": state,
        "status_kind": status_kind,
        "runtime_status_kind": "postgres_runtime_ready" if running and ready else "postgres_runtime_not_ready",
        "schema_status_kind": "schema_not_checked_by_postgres_dev_status",
        "migration_status_kind": "schema_not_checked_by_postgres_dev_status",
        "writability_status_kind": "postgres_dev_local_writable_when_running" if running and ready else "not_checked_until_running",
        "next_schema_check_command": "python -m shujuan migrate status" if running and ready else "python -m shujuan postgres-dev start",
        "warnings": warnings,
        "pg_ctl": pg_ctl,
        "readiness": readiness,
        "data_dir": config["data_dir"],
        "log_dir": config["log_dir"],
        "pg_bin": config["pg_bin"],
        "host": config["host"],
        "port": config["port"],
        "user": config["user"],
        "database": config["database"],
        "url_redacted": f"postgresql://{config['user']}:***@127.0.0.1:{config['port']}/{config['database']}",
    }


def start_postgres_dev_cluster(repo: Path, config: dict[str, Any], *, wait: bool = True) -> dict[str, Any]:
    json_dumps_fn = _require_dependency("json_dumps")
    pg_bin = discover_postgres_bin(config.get("pg_bin"))
    data_dir = Path(config["data_dir"])
    log_dir = postgres_dev_log_dir(repo)
    log_dir.mkdir(parents=True, exist_ok=True)
    before = pg_ctl_status(pg_bin, data_dir)
    started = False
    if not before["running"]:
        run_postgres_tool(
            pg_bin,
            "pg_ctl",
            [
                "start",
                "-D",
                str(data_dir),
                "-l",
                str(log_dir / "postgres.log"),
                "-o",
                f"-p {int(config['port'])} -h 127.0.0.1",
                "-w",
            ],
            capture=False,
        )
        started = True
    readiness = wait_for_pg_detail(pg_bin, config) if wait else pg_isready_status(pg_bin, config)
    after = pg_ctl_status(pg_bin, data_dir)
    payload = postgres_dev_lifecycle_payload(config, pg_ctl=after, readiness=readiness)
    payload["started"] = started
    payload["previous_pg_ctl"] = before
    if wait and not payload["ready"]:
        raise SystemExit(
            json_dumps_fn(
                {
                    "ok": False,
                    "error": "postgres_dev_not_ready",
                    "message": f"postgres-dev did not become ready on port {config['port']}",
                    **payload,
                }
            )
        )
    if not payload["running"]:
        raise SystemExit(
            json_dumps_fn(
                {
                    "ok": False,
                    "error": "postgres_dev_not_running",
                    "message": "postgres-dev pg_ctl did not report the configured data directory as running",
                    **payload,
                }
            )
        )
    return payload


def stop_postgres_dev_cluster(repo: Path, config: dict[str, Any]) -> dict[str, Any]:
    pg_bin = discover_postgres_bin(config.get("pg_bin"))
    data_dir = Path(config["data_dir"])
    status = pg_ctl_status(pg_bin, data_dir)
    if not status["running"]:
        return {"running": False, "stopped": False, "data_dir": str(data_dir)}
    run_postgres_tool(pg_bin, "pg_ctl", ["stop", "-D", str(data_dir), "-m", "fast", "-w"])
    return {"running": False, "stopped": True, "data_dir": str(data_dir)}


def ensure_postgres_dev_database(repo: Path, config: dict[str, Any], credentials: dict[str, Any]) -> None:
    pg_bin = discover_postgres_bin(config.get("pg_bin"))
    env = {"PGPASSWORD": str(credentials["password"])}
    database_literal = str(config["database"]).replace("'", "''")
    exists = run_postgres_tool(
        pg_bin,
        "psql",
        [
            "-h",
            "127.0.0.1",
            "-p",
            str(config["port"]),
            "-U",
            str(config["user"]),
            "-d",
            "postgres",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname = '{database_literal}'",
        ],
        env_extra=env,
        allow_fail=False,
    )
    if exists.stdout.strip() == "1":
        return
    run_postgres_tool(
        pg_bin,
        "createdb",
        [
            "-h",
            "127.0.0.1",
            "-p",
            str(config["port"]),
            "-U",
            str(config["user"]),
            str(config["database"]),
        ],
        env_extra=env,
    )


def initialize_postgres_dev(
    repo: Path,
    *,
    pg_bin_arg: str | None = None,
    port_arg: int | None = None,
    user: str = DEFAULT_POSTGRES_DEV_USER,
    database: str | None = None,
    reuse_existing: bool = False,
    stop_after_init: bool = True,
) -> dict[str, Any]:
    ensure_layout_fn = _require_dependency("ensure_layout")
    now_iso_fn = _require_dependency("now_iso")
    repo = repo.resolve()
    database_name = database or default_postgres_dev_database(repo)
    data_dir = postgres_dev_data_dir(repo)
    has_existing_data = data_dir.exists() and any(data_dir.iterdir())
    if has_existing_data and not reuse_existing:
        raise SystemExit(f"postgres-dev data directory already exists: {data_dir}. Use --reuse-existing to keep it.")
    ensure_layout_fn(repo)
    postgres_dev_log_dir(repo).mkdir(parents=True, exist_ok=True)
    if has_existing_data and postgres_dev_config_path(repo).exists() and postgres_dev_credentials_path(repo).exists():
        config = read_postgres_dev_config(repo)
        credentials = read_postgres_dev_credentials(repo)
        pg_bin = discover_postgres_bin(pg_bin_arg or config.get("pg_bin"))
        if str(credentials.get("user") or config.get("user")) != user:
            raise SystemExit(
                "postgres-dev existing cluster user differs from requested user; "
                f"existing={credentials.get('user') or config.get('user')} requested={user}"
            )
        if port_arg is not None:
            config["port"] = choose_postgres_dev_port(port_arg, explicit=True)
        config.update(
            {
                "pg_bin": str(pg_bin),
                "data_dir": str(data_dir),
                "log_dir": str(postgres_dev_log_dir(repo)),
                "host": "127.0.0.1",
                "user": user,
                "database": database_name if database is not None else str(config.get("database") or database_name),
                "local_dev_only": True,
            }
        )
    else:
        pg_bin = discover_postgres_bin(pg_bin_arg)
        requested_port = port_arg or default_postgres_dev_port(repo)
        port = choose_postgres_dev_port(requested_port, explicit=port_arg is not None)
        password = secrets.token_urlsafe(24)
        credentials = {"user": user, "password": password, "created_at": now_iso_fn()}
        config = {
            "pg_bin": str(pg_bin),
            "data_dir": str(data_dir),
            "log_dir": str(postgres_dev_log_dir(repo)),
            "host": "127.0.0.1",
            "port": port,
            "user": user,
            "database": database_name,
            "local_dev_only": True,
        }
    if not has_existing_data:
        pwfile = postgres_dev_root(repo) / "pwfile.tmp"
        postgres_dev_root(repo).mkdir(parents=True, exist_ok=True)
        pwfile.write_text(str(credentials["password"]), encoding="utf-8")
        try:
            run_postgres_tool(
                pg_bin,
                "initdb",
                [
                    "-D",
                    str(data_dir),
                    "-U",
                    user,
                    "--pwfile",
                    str(pwfile),
                    "--auth-host=scram-sha-256",
                    "--auth-local=trust",
                    "-E",
                    "UTF8",
                ],
            )
        finally:
            if pwfile.exists():
                pwfile.unlink()
    write_postgres_dev_files(repo, config, credentials)
    start_postgres_dev_cluster(repo, config)
    ensure_postgres_dev_database(repo, config, credentials)
    stop_result = stop_postgres_dev_cluster(repo, config) if stop_after_init else {"stopped": False}
    return {
        "ok": True,
        "initialized": True,
        "reused_existing": has_existing_data,
        "stopped_after_init": stop_result["stopped"],
        "data_dir": str(data_dir),
        "log_dir": str(postgres_dev_log_dir(repo)),
        "pg_bin": str(pg_bin),
        "host": "127.0.0.1",
        "port": int(config["port"]),
        "user": user,
        "database": str(config["database"]),
        "database_url": postgres_dev_url(config, credentials),
        "url_command": "python -m shujuan postgres-dev url",
        "local_dev_only": True,
    }


def cmd_postgres_dev_init(args: argparse.Namespace) -> int:
    print_json_fn = _require_dependency("print_json")
    repo = args.repo.resolve()
    result = initialize_postgres_dev(
        repo,
        pg_bin_arg=args.pg_bin,
        port_arg=args.port,
        user=args.user,
        database=args.database,
        reuse_existing=args.reuse_existing,
        stop_after_init=True,
    )
    print_json_fn({key: value for key, value in result.items() if key != "database_url"})
    return 0


def cmd_postgres_dev_start(args: argparse.Namespace) -> int:
    print_json_fn = _require_dependency("print_json")
    repo = args.repo.resolve()
    config = read_postgres_dev_config(repo)
    result = start_postgres_dev_cluster(repo, config)
    print_json_fn({"ok": True, **result})
    return 0


def cmd_postgres_dev_stop(args: argparse.Namespace) -> int:
    print_json_fn = _require_dependency("print_json")
    repo = args.repo.resolve()
    config = read_postgres_dev_config(repo)
    result = stop_postgres_dev_cluster(repo, config)
    print_json_fn({"ok": True, **result})
    return 0


def cmd_postgres_dev_status(args: argparse.Namespace) -> int:
    print_json_fn = _require_dependency("print_json")
    repo = args.repo.resolve()
    config = read_postgres_dev_config(repo)
    pg_bin = discover_postgres_bin(config.get("pg_bin"))
    status = pg_ctl_status(pg_bin, Path(config["data_dir"]))
    readiness = wait_for_pg_detail(pg_bin, config, timeout_seconds=2)
    print_json_fn({"ok": True, **postgres_dev_lifecycle_payload(config, pg_ctl=status, readiness=readiness)})
    return 0


def cmd_postgres_dev_url(args: argparse.Namespace) -> int:
    print_json_fn = _require_dependency("print_json")
    print_text_fn = _require_dependency("print_text")
    repo = args.repo.resolve()
    config = read_postgres_dev_config(repo)
    credentials = read_postgres_dev_credentials(repo)
    url = postgres_dev_url(config, credentials)
    if args.env:
        print_text_fn(f"SHUJUAN_DATABASE_URL={url}")
    else:
        print_json_fn({"ok": True, "database_url": url, "local_dev_only": True})
    return 0


def cmd_postgres_dev_cutover(args: argparse.Namespace) -> int:
    raise SystemExit(
        "postgres-dev cutover from SQLite is disabled. Current shujuan execution has no SQLite runtime/write entry point; "
        "start project-owned PostgreSQL with `python -m shujuan init --postgres-dev` or restore from PostgreSQL evidence/backup artifacts."
    )


def register_postgres_dev(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, PostgresDevHandler],
) -> None:
    """Register postgres-dev commands while cli.py keeps global flags and dispatch."""
    _validate_handlers(handlers)

    postgres_dev = subparsers.add_parser(
        "postgres-dev",
        help="Manage the project-owned native PostgreSQL dev database.",
        description="Manage the normal local runtime path under .shujuan/postgres-dev/.",
    )
    postgres_dev_sub = postgres_dev.add_subparsers(
        dest="postgres_dev_command",
        required=True,
        metavar="{init,start,stop,status,url}",
    )
    postgres_dev_init = postgres_dev_sub.add_parser("init")
    postgres_dev_init.add_argument("--pg-bin", help="Native PostgreSQL bin directory. Defaults to SHUJUAN_POSTGRES_BIN, PATH, or common Windows install paths.")
    postgres_dev_init.add_argument("--port", type=int)
    postgres_dev_init.add_argument("--user", default=DEFAULT_POSTGRES_DEV_USER)
    postgres_dev_init.add_argument("--database", help="Database name. Defaults to a stable repo-derived project database name.")
    postgres_dev_init.add_argument("--reuse-existing", action="store_true", help="Reuse an existing .shujuan/postgres-dev/data directory.")
    postgres_dev_init.set_defaults(func=handlers["init"])
    postgres_dev_start = postgres_dev_sub.add_parser("start")
    postgres_dev_start.set_defaults(func=handlers["start"])
    postgres_dev_stop = postgres_dev_sub.add_parser("stop")
    postgres_dev_stop.set_defaults(func=handlers["stop"])
    postgres_dev_status = postgres_dev_sub.add_parser("status")
    postgres_dev_status.set_defaults(func=handlers["status"])
    postgres_dev_url_parser = postgres_dev_sub.add_parser("url")
    postgres_dev_url_parser.add_argument("--env", action="store_true", help="Print SHUJUAN_DATABASE_URL=... for shell/env use.")
    postgres_dev_url_parser.set_defaults(func=handlers["url"])
    postgres_dev_cutover = postgres_dev_sub.add_parser(
        "cutover",
        help=argparse.SUPPRESS,
        description="Legacy disabled SQLite cutover compatibility command; not part of the normal runtime path.",
    )
    postgres_dev_cutover.set_defaults(func=handlers["cutover"])
    postgres_dev_sub._choices_actions = [  # type: ignore[attr-defined]
        action for action in postgres_dev_sub._choices_actions if action.dest != "cutover"  # type: ignore[attr-defined]
    ]


__all__ = [
    "DEFAULT_POSTGRES_DEV_PORT",
    "DEFAULT_POSTGRES_DEV_PORT_WINDOW",
    "DEFAULT_POSTGRES_DEV_USER",
    "POSTGRES_DEV_CONFIG",
    "POSTGRES_DEV_CREDENTIALS",
    "POSTGRES_DEV_DATABASE_PREFIX",
    "POSTGRES_DEV_DIR",
    "POSTGRES_DEV_HANDLER_KEYS",
    "build_postgres_dev_handlers",
    "choose_postgres_dev_port",
    "cmd_postgres_dev_cutover",
    "cmd_postgres_dev_init",
    "cmd_postgres_dev_start",
    "cmd_postgres_dev_status",
    "cmd_postgres_dev_stop",
    "cmd_postgres_dev_url",
    "common_postgres_bin_dirs",
    "default_postgres_dev_database",
    "default_postgres_dev_port",
    "discover_postgres_bin",
    "ensure_postgres_dev_database",
    "initialize_postgres_dev",
    "pg_ctl_status",
    "pg_isready_status",
    "postgres_dev_lifecycle_payload",
    "postgres_dev_url",
    "postgres_exe",
    "read_postgres_dev_config",
    "read_postgres_dev_credentials",
    "register_postgres_dev",
    "run_postgres_tool",
    "start_postgres_dev_cluster",
    "stop_postgres_dev_cluster",
    "wait_for_pg",
    "wait_for_pg_detail",
]
