from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..commands.postgres_dev import DEFAULT_POSTGRES_DEV_USER
from ..services.errors import StructuredRuntimeError

InitHandler = Callable[[argparse.Namespace], int]
INIT_DEPENDENCY_KEYS = (
    "SCHEMA_VERSION",
    "connect",
    "create_node",
    "current_branch",
    "ensure_agents_md",
    "ensure_project_meta",
    "ensure_shujuan_migrations",
    "ensure_codex_hooks",
    "ensure_shujuan_skill",
    "init_schema",
    "initialize_postgres_dev",
    "new_id",
    "now_iso",
    "print_json",
    "resolve_database_config",
)


def _init_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in INIT_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"init handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in INIT_DEPENDENCY_KEYS}


def build_init_handlers(deps: Mapping[str, Any]) -> dict[str, InitHandler]:
    globals().update(_init_dependencies(deps))
    return {"init": cmd_init}


def cmd_init(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    alias_value = getattr(args, "install_skill_alias", None)
    install_skills = bool(getattr(args, "install_skills", True) if alias_value is None else alias_value)
    deprecations = []
    if alias_value is not None:
        deprecations.append(
            {
                "option": "--install-skill",
                "use": "--install-skills",
                "message": "--install-skill is a v11.0 compatibility alias and will be removed after the compatibility window.",
            }
        )
    agents_md = None if args.no_agents_md else ensure_agents_md(repo, skill_expected=install_skills)
    skill = ensure_shujuan_skill(repo) if install_skills else None
    codex_hooks = ensure_codex_hooks(repo) if install_skills else None
    migrations = ensure_shujuan_migrations(repo)
    installed_assets = [
        item
        for item in (agents_md, skill, codex_hooks, migrations)
        if item is not None
    ]
    postgres_dev = None
    if args.postgres_dev:
        postgres_dev = initialize_postgres_dev(
            repo,
            pg_bin_arg=args.postgres_dev_pg_bin,
            port_arg=args.postgres_dev_port,
            user=args.postgres_dev_user,
            database=args.postgres_dev_database,
            reuse_existing=True,
            stop_after_init=False,
        )
        os.environ["SHUJUAN_DATABASE_URL"] = postgres_dev["database_url"]
    try:
        config = resolve_database_config(repo)
        conn = init_schema(repo)
    except StructuredRuntimeError as exc:
        payload = exc.payload()
        payload.update(
            {
                "partial_init": True,
                "installed_assets": installed_assets,
                "deprecations": deprecations,
                "postgres_dev": {key: value for key, value in (postgres_dev or {}).items() if key != "database_url"} if postgres_dev else None,
            }
        )
        print_json(payload)
        return 1
    project_id = ensure_project_meta(
        conn,
        name=args.name or repo.name,
        repo_root=repo,
        default_branch=current_branch(repo),
    )
    current_center = conn.execute("SELECT id FROM center_bodies WHERE is_current = 1").fetchone()
    center_id = None
    if not current_center:
        node_id = create_node(conn, "center_body", "Project center", "Initial shujuan center body")
        center_id = new_id("center")
        body = args.center_body or (
            "shujuan project center\n\n"
            "- Canonical memory lives in the project-owned PostgreSQL database.\n"
            "- Center and endpoint bodies activate current project context.\n"
            "- Source documents, diff records, tasks, checks, and evidence stay traceable."
        )
        conn.execute(
            """
            INSERT INTO center_bodies
              (id, node_id, body, version, is_current, created_at)
            VALUES (?, ?, ?, 1, 1, ?)
            """,
            (center_id, node_id, body, now_iso()),
        )
    else:
        center_id = str(current_center["id"])
    conn.commit()
    print_json(
        {
            "ok": True,
            "project_id": project_id,
            "schema_version": SCHEMA_VERSION,
            "database": {
                "backend": config.backend,
                "url": config.url,
                "profile": config.profile,
                "explicit": config.explicit,
                "warning": None,
            },
            "schema_version_path": str(repo / ".shujuan" / "schema_version.json"),
            "center_body_id": center_id,
            "agents_md": agents_md,
            "skill": skill,
            "codex_hooks": codex_hooks,
            "migrations": migrations,
            "installed_assets": installed_assets,
            "deprecations": deprecations,
            "postgres_dev": {key: value for key, value in (postgres_dev or {}).items() if key != "database_url"} if postgres_dev else None,
        }
    )
    return 0


def register_init(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, InitHandler],
) -> None:
    init = subparsers.add_parser(
        "init",
        help="Initialize shujuan; use --postgres-dev for the normal project-owned PostgreSQL runtime path.",
        description=(
            "Initialize shujuan metadata, canonical AGENTS.md, v11 method skills, role profiles, "
            "and optionally create/reuse the project-owned PostgreSQL dev database with --postgres-dev."
        ),
    )
    init.add_argument("--name")
    init.add_argument("--center-body")
    init.add_argument("--no-agents-md", action="store_true", help="Do not create or inject the default shujuan AGENTS.md instructions.")
    init.add_argument("--install-skills", dest="install_skills", action=argparse.BooleanOptionalAction, default=True, help="Install required v11 method skills and role profiles (default: true).")
    init.add_argument("--install-skill", dest="install_skill_alias", action=argparse.BooleanOptionalAction, default=None, help="Deprecated compatibility alias for --install-skills.")
    init.add_argument("--postgres-dev", action="store_true", help="Create/reuse a project-owned native PostgreSQL dev database and initialize shujuan into it.")
    init.add_argument("--postgres-dev-pg-bin", help="Native PostgreSQL bin directory for --postgres-dev.")
    init.add_argument("--postgres-dev-port", type=int, help="Port for the project-owned PostgreSQL dev cluster.")
    init.add_argument("--postgres-dev-user", default=DEFAULT_POSTGRES_DEV_USER, help="User for the project-owned PostgreSQL dev cluster.")
    init.add_argument("--postgres-dev-database", help="Database name for the project. Defaults to a stable repo-derived name.")
    init.set_defaults(func=handlers["init"])


__all__ = ["build_init_handlers", "register_init"]
