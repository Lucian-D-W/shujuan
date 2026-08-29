from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
import json

from ..commands.postgres_dev import (
    discover_postgres_bin,
    pg_ctl_status,
    postgres_dev_config_path,
    postgres_dev_lifecycle_payload,
    read_postgres_dev_config,
    wait_for_pg_detail,
)
from ..schema_roles import verify_schema_roles
from ..services.skill_registry import registry_payload, role_profile_specs, role_target_dir, skill_specs, skill_target_dir


InstallLayoutHandler = Callable[[argparse.Namespace], int]
INSTALL_LAYOUT_HANDLER_KEYS = ("doctor",)
INSTALL_LAYOUT_DEPENDENCY_KEYS = (
    "connect",
    "print_json",
    "append_trace_event",
    "inspect_schema",
)


def _configure(deps: Mapping[str, Any]) -> None:
    missing = [key for key in INSTALL_LAYOUT_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"install-layout command boundary is missing: {', '.join(missing)}")
    globals().update({key: deps[key] for key in INSTALL_LAYOUT_DEPENDENCY_KEYS})


def _official_hook_schema_ok(config: Any) -> bool:
    if not isinstance(config, dict):
        return False
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event, groups in hooks.items():
        if not isinstance(event, str) or not event:
            return False
        if not isinstance(groups, list) or not groups:
            return False
        for group in groups:
            if not isinstance(group, dict):
                return False
            if event == "PreToolUse" and not isinstance(group.get("matcher"), str):
                return False
            handlers = group.get("hooks")
            if not isinstance(handlers, list) or not handlers:
                return False
            for handler in handlers:
                if not isinstance(handler, dict):
                    return False
                if handler.get("type") != "command" or not isinstance(handler.get("command"), str):
                    return False
    return True


def build_install_layout_handlers(deps: Mapping[str, Any]) -> dict[str, InstallLayoutHandler]:
    _configure(deps)

    def doctor(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        package_root = Path(__file__).resolve().parents[1]
        imported_package = package_root / "__init__.py"
        skill_status = []
        for spec in skill_specs():
            skill_path = skill_target_dir(repo, spec) / "SKILL.md"
            content = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
            metadata_ok = f"name: {spec.name}" in content
            skill_status.append(
                {
                    "name": spec.name,
                    "required": spec.required,
                    "version": spec.version,
                    "compatibility": spec.compatibility,
                    "path": str(skill_path),
                    "present": skill_path.exists(),
                    "metadata_ok": metadata_ok,
                    "sha256": __import__("hashlib").sha256(content.encode("utf-8")).hexdigest() if content else None,
                }
            )
        agents_dir = role_target_dir(repo)
        role_profiles = []
        for spec in role_profile_specs():
            path = agents_dir / spec.name
            content = path.read_text(encoding="utf-8") if path.exists() else ""
            metadata_ok = (
                f'name = "{Path(spec.name).stem}"' in content
                and f'version = "{spec.version}"' in content
                and f'compatibility = "{spec.compatibility}"' in content
                and "description" in content
                and "developer_instructions" in content
                if content
                else False
            )
            role_profiles.append(
                {
                    "name": spec.name,
                    "role": spec.role,
                    "required": spec.required,
                    "version": spec.version,
                    "compatibility": spec.compatibility,
                    "asset_path": spec.asset_path,
                    "path": str(path),
                    "present": path.exists(),
                    "metadata_ok": metadata_ok,
                    "sha256": __import__("hashlib").sha256(content.encode("utf-8")).hexdigest() if content else None,
                }
            )
        state_dir = repo / ".shujuan"
        agents_md_path = repo / "AGENTS.md"
        agents_md = agents_md_path.read_text(encoding="utf-8") if agents_md_path.exists() else ""
        agents_first = agents_md.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        agents_first_lower = agents_first.lower()
        hooks_config_path = repo / ".codex" / "hooks.json"
        hooks_config = None
        hooks_config_ok = False
        if hooks_config_path.exists():
            try:
                hooks_config = json.loads(hooks_config_path.read_text(encoding="utf-8"))
                hooks_config_ok = _official_hook_schema_ok(hooks_config)
            except json.JSONDecodeError:
                hooks_config = None
        hook_files = [
            repo / ".codex" / "hooks" / "shujuan-method-hint.py",
            repo / ".codex" / "hooks" / "shujuan-pretool-guard.py",
        ]
        evidence_pack = {
            "task_chain": repo / "docs" / "history" / "shujuan-v11.2-task-chain-2026-06-28.json",
            "import_mapping": repo / "docs" / "history" / "shujuan-v11.2-task-chain-import-mapping-2026-06-28.json",
            "provider_evidence": repo / "docs" / "history" / "shujuan-v11.2-native-provider-evidence-2026-06-28.md",
        }
        config_path = postgres_dev_config_path(repo)
        postgres_config = read_postgres_dev_config(repo) if config_path.exists() else None
        postgres_runtime = None
        postgres_ready = False
        postgres_warning = None
        if postgres_config:
            try:
                pg_bin = discover_postgres_bin(postgres_config.get("pg_bin"))
                data_dir = Path(str(postgres_config["data_dir"]))
                lifecycle = postgres_dev_lifecycle_payload(
                    postgres_config,
                    pg_ctl=pg_ctl_status(pg_bin, data_dir),
                    readiness=wait_for_pg_detail(pg_bin, postgres_config, timeout_seconds=2),
                )
                postgres_runtime = lifecycle
                postgres_ready = lifecycle["runtime_status_kind"] == "postgres_runtime_ready"
            except BaseException as exc:
                postgres_warning = str(exc)
        live_tables: list[str] = []
        if postgres_ready:
            try:
                conn = connect(repo)
                live_tables = sorted(inspect_schema(conn))
            except BaseException as exc:
                postgres_ready = False
                postgres_warning = f"connect_failed:{exc}"
                live_tables = []
        schema_verification = verify_schema_roles(live_tables=live_tables)
        shadowing = package_root.parent == repo and (repo / "shujuan").exists()
        payload = {
            "ok": True,
            "read_only": True,
            "repo": str(repo),
            "imported_package": str(imported_package),
            "root_package_shadowing": shadowing,
            "skill_registry": registry_payload(),
            "skills": skill_status,
            "agents": role_profiles,
            "v11_2_diagnostics": {
                "agents_md": {
                    "path": str(agents_md_path),
                    "present": agents_md_path.exists(),
                    "size_bytes": len(agents_md.encode("utf-8")) if agents_md else 0,
                    "line_count": len(agents_md.splitlines()) if agents_md else 0,
                    "first_4096_contains_four_gates": "## Four Gates" in agents_first,
                    "first_4096_contains_method_map": "## Method Map" in agents_first,
                    "first_4096_contains_route_guard_pure": "--pure" in agents_first,
                    "first_4096_contains_read_only_no_side_effect": "read-only commands must not create" in agents_first_lower,
                    "hooks_advisory": "hooks are advisory" in agents_md.lower(),
                },
                "skills": {
                    "required_count": len([item for item in skill_status if item["required"]]),
                    "present_required_count": len([item for item in skill_status if item["required"] and item["present"]]),
                    "metadata_ok": all(item["metadata_ok"] for item in skill_status if item["required"]),
                },
                "core_shim": {
                    "present": any(item["name"] == "shujuan-core" and item["present"] for item in skill_status),
                    "compatibility_shim": any(item["name"] == "shujuan-core" and item["compatibility"] == "compatibility_shim" for item in skill_status),
                },
                "route_guard": {
                    "pure_supported": True,
                    "trace_explicit_only": True,
                    "payload_write_fields": ["filesystem_writes", "db_writes", "trace_explicit", "trace_written"],
                },
                "role_profiles": {
                    "canonical_fields": ["name", "description", "developer_instructions"],
                    "required_count": len([item for item in role_profiles if item["required"]]),
                    "present_required_count": len([item for item in role_profiles if item["required"] and item["present"]]),
                    "metadata_ok": all(item["metadata_ok"] for item in role_profiles if item["required"]),
                },
                "hooks": {
                    "advisory": True,
                    "config_path": str(hooks_config_path),
                    "config_present": hooks_config_path.exists(),
                    "config_ok": hooks_config_ok,
                    "hook_files_present": all(path.exists() for path in hook_files),
                    "authoritative": False,
                },
                "evidence_pack": {
                    name: {"path": str(path), "present": path.exists()} for name, path in evidence_pack.items()
                },
            },
            "skill_present": all(item["present"] for item in skill_status if item["required"]),
            "skill_path": str(skill_target_dir(repo, skill_specs()[-1]) / "SKILL.md"),
            "state_dir_present": state_dir.exists(),
            "state_dir": str(state_dir),
            "state_dir_is_skill_dir": False,
            "postgres_ready": postgres_ready,
            "postgres_dev_config": str(config_path) if postgres_config else None,
            "postgres_runtime": postgres_runtime,
            "postgres_runtime_warning": postgres_warning,
            "next_ready_action": (
                "python -m shujuan postgres-dev start"
                if not postgres_config or not postgres_ready
                else "python -m shujuan migrate status"
            ),
            "schema_roles_verification": {
                "physical_schema_table_count": schema_verification["physical_schema_table_count"],
                "role_registry_count": schema_verification["role_registry_count"],
                "contracted_legacy_role_count": schema_verification["contracted_legacy_role_count"],
            },
            "repairs": [
                "Run python -m shujuan init --install-skills to restore required method skills." if any(not item["present"] for item in skill_status if item["required"]) else None,
                "Run python -m shujuan init --install-skills to restore role profiles." if any(not item["present"] for item in role_profiles if item["required"]) else None,
                "Start postgres-dev before writeful routes." if not postgres_ready else None,
            ],
        }
        payload["repairs"] = [item for item in payload["repairs"] if item]
        print_json(payload)
        return 0

    return {"doctor": doctor}


def register_install_layout(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, InstallLayoutHandler]) -> None:
    missing = [key for key in INSTALL_LAYOUT_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"install-layout command boundary is missing: {', '.join(missing)}")
    install_layout = subparsers.add_parser("install-layout")
    install_layout_sub = install_layout.add_subparsers(dest="install_layout_command", required=True)
    doctor = install_layout_sub.add_parser("doctor")
    doctor.set_defaults(func=handlers["doctor"])


__all__ = ["INSTALL_LAYOUT_HANDLER_KEYS", "build_install_layout_handlers", "register_install_layout"]
