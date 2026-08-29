from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.cli import ensure_agents_md, ensure_shujuan_skill

SKILL_DIR = ROOT / ".agents" / "skills" / "shujuan-core"
SKILL = SKILL_DIR / "SKILL.md"
AGENTS = ROOT / "AGENTS.md"
README = ROOT / "README.md"


REQUIRED_SKILL_FILES = [
    "SKILL.md",
    "references/activation-first.md",
    "references/evidence-closeout.md",
    "references/delegation.md",
    "references/modes-and-terms.md",
    "references/postgres-runtime.md",
    "references/plan-to-db-task-chain-hygiene.md",
    "templates/delegate-return.md",
    "templates/reviewer-return.md",
    "templates/closeout-handoff.md",
]


REQUIRED_COMMANDS = [
    "python -m shujuan workflow begin --session-id <session_id> --endpoint \"<endpoint>\" --content \"<current user request>\"",
    "python -m shujuan report endpoint <endpoint> --active-only --markdown",
    "python -m shujuan endpoint doctor <endpoint> --strict-closeout --read-only --allow-fail",
    "python -m shujuan endpoint doctor <endpoint> --strict-closeout --allow-fail",
    "python -m shujuan endpoint refresh <endpoint>",
    "python -m shujuan evidence verify --endpoint <endpoint>",
    "python -m shujuan exec start --endpoint <endpoint> --task-node <task_node_id> --summary \"<summary>\"",
    "python -m shujuan exec stop --endpoint <endpoint> --summary \"<summary>\" --task <task_id> --check <check_id>",
    "python -m shujuan evidence test-result --check <check_id> --close-check -- <test command>",
    "python -m shujuan evidence artifact --path <file> --check <check_id> --close-check",
    "python -m shujuan evidence user-confirmation --body \"<confirmation>\" --check <check_id> --close-check",
    "python -m shujuan task add --body \"<task body>\" --from-node <source_node_id>",
    "python -m shujuan acceptance add --task <task_id> --body \"<check body>\" --expected-evidence-type <change_set|test_result|artifact|user_confirmation> --from-node <source_node_id>",
    "python -m shujuan scope change --body \"<why scope changed>\" --source-node <source_node_id> --applies-to <target_node_id>",
    "python -m shujuan task defer --task <task_id> --body \"<why deferred>\" --source-node <source_node_id>",
    "python -m shujuan unresolved add --body \"<question>\" --source-node <source_node_id> --applies-to <target_node_id>",
    "python -m shujuan assumption add --body \"<assumption>\" --source-node <source_node_id> --applies-to <target_node_id>",
    "python -m shujuan delegate packet --endpoint <endpoint> --task <task_id> --check <check_id> --role worker --body \"<delegation body>\"",
    "python -m shujuan delegate review --endpoint <endpoint> --task <task_id> --check <check_id> --result accept --summary \"<review summary>\"",
    "python -m shujuan delegate import --endpoint <endpoint> --task <task_id> --check <check_id> --import-kind summary --artifact <handoff.md>",
    "python -m shujuan audit import-agent-output --endpoint <endpoint> --source-node <source_node_id> --path <handoff.md>",
    "python -m shujuan postgres-dev status",
    "python -m shujuan postgres-dev start",
    "python -m shujuan init --postgres-dev --name \"<project>\"",
]


HARD_PHRASES = [
    "Activation-First Entry",
    "Use this file as the always-on shujuan policy surface.",
    "Shared Route Grammar",
    "does not close checks/tasks",
    "SQLite and contracted legacy tables are not write fallbacks",
    "controller evidence route",
    "controller adoption",
    "gitnexus-impact-analysis",
    "provider_fact",
    "provider_hypothesis",
    "deferred",
    "product_backlog",
    "PostgreSQL success",
    "DB readiness gate",
]

SKILL_FORBIDDEN_FRAGMENTS = [
    "```bash",
    "python -m shujuan ",
    "## Default Operating Core",
    "## Default Routes",
    "## Current Terms",
    "## DCCP Role Boundary",
]


FORBIDDEN_ACTIVE_GUIDANCE = [
    "Treat SQLite/cutover wording as legacy guidance only",
    "SQLite dev-only",
    "SQLite remains only as an explicit",
    "SQLite runtime/write fallback is available",
    "SQLite runtime/write fallback is recommended",
    "provider-following subagent",
]


def read_surface(root: Path = ROOT) -> dict[str, str]:
    files = {
        "AGENTS.md": root / "AGENTS.md",
    }
    if (root / "README.md").exists():
        files["README.md"] = root / "README.md"
    for rel in REQUIRED_SKILL_FILES:
        files[rel] = root / ".agents" / "skills" / "shujuan-core" / rel
    missing = [name for name, path in files.items() if not path.exists()]
    if missing:
        raise AssertionError(f"activation modularization files are missing: {missing}")
    return {name: path.read_text(encoding="utf-8") for name, path in files.items()}


def assert_activation_skill_shape(surfaces: dict[str, str]) -> None:
    skill = surfaces["SKILL.md"]
    if "# Shujuan Core Compatibility Shim" in skill:
        required_shim_phrases = [
            "Explicit v10 compatibility shim",
            "For ordinary v11 work, select exactly one method Skill",
            "does not grant authority",
            "PostgreSQL remains the runtime/write path",
            "SQLite and contracted legacy tables are not write fallbacks",
        ]
        missing = [phrase for phrase in required_shim_phrases if phrase not in skill]
        if missing:
            raise AssertionError(f"v11 compatibility shim lost its authority/runtime boundary: {missing}")
        if len(skill.splitlines()) > 20:
            raise AssertionError("v11 compatibility shim is no longer concise")
        for fragment in SKILL_FORBIDDEN_FRAGMENTS:
            if fragment in skill:
                raise AssertionError(f"compatibility shim duplicates detailed guidance: {fragment}")
        return
    if "## Authority" not in skill[:900] or "## Activation" not in skill[:900]:
        raise AssertionError("SKILL.md must open as a concise authority + activation entry")
    if len(skill.splitlines()) > 60:
        raise AssertionError("SKILL.md is no longer the short activation entry")
    for fragment in SKILL_FORBIDDEN_FRAGMENTS:
        if fragment in skill:
            raise AssertionError(f"SKILL.md still duplicates detailed guidance: {fragment}")
    for rel in REQUIRED_SKILL_FILES:
        if rel == "SKILL.md":
            continue
        route = f"`{rel}`"
        if route not in skill:
            raise AssertionError(f"SKILL.md does not route to {rel}")


def assert_commands_are_clear(surfaces: dict[str, str]) -> None:
    combined = "\n".join(surfaces.values())
    missing = [command for command in REQUIRED_COMMANDS if command not in combined]
    if missing:
        raise AssertionError(f"activation guidance is missing exact runnable command examples: {missing}")
    detail_surfaces = "\n".join(text for name, text in surfaces.items() if name != "SKILL.md")
    lost = [command for command in REQUIRED_COMMANDS if command not in detail_surfaces]
    if lost:
        raise AssertionError(f"commands removed from SKILL.md were not preserved in routed/canonical surfaces: {lost}")

    stale_patterns = [
        r"python -m shujuan endpoint doctor <endpoint>\s*(?:\r?\n|$)(?!.*--strict-closeout)",
        r"python -m shujuan endpoint doctor <endpoint>\s+--allow-fail(?!\s+--strict-closeout)",
        r"python -m shujuan report endpoint <endpoint>\s+--markdown(?!\s+--active-only)",
    ]
    for pattern in stale_patterns:
        if re.search(pattern, combined):
            raise AssertionError(f"stale command example matched: {pattern}")


def assert_hard_invariants(surfaces: dict[str, str]) -> None:
    combined = "\n".join(surfaces.values())
    missing = [phrase for phrase in HARD_PHRASES if phrase not in combined]
    if missing:
        raise AssertionError(f"activation guidance is missing hard phrases: {missing}")
    forbidden = [phrase for phrase in FORBIDDEN_ACTIVE_GUIDANCE if phrase in combined]
    if forbidden:
        raise AssertionError(f"active guidance still contains stale fallback/provider wording: {forbidden}")


def assert_installed_templates_align() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v6-phase1-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        agents_result = ensure_agents_md(repo)
        skill_result = ensure_shujuan_skill(repo)
        if agents_result["action"] != "created" or skill_result["action"] != "created":
            raise AssertionError(f"template install did not create expected surfaces: {agents_result}, {skill_result}")
        installed = read_surface(repo)
        assert_activation_skill_shape(installed)
        assert_commands_are_clear(installed)
        assert_hard_invariants(installed)
        core_install = next(
            (item for item in skill_result.get("skills", []) if item.get("name") == "shujuan-core"),
            None,
        )
        if not core_install or core_install.get("compatibility") != "compatibility_shim":
            raise AssertionError(f"ensure_shujuan_skill did not report the v10 compatibility shim: {skill_result}")
        for rel in REQUIRED_SKILL_FILES:
            if rel == "SKILL.md":
                continue
            if rel not in core_install["references"] + core_install["templates"]:
                raise AssertionError(f"ensure_shujuan_skill did not report installed routed file: {rel}")


def main() -> int:
    surfaces = read_surface()
    assert_activation_skill_shape(surfaces)
    assert_commands_are_clear(surfaces)
    assert_hard_invariants(surfaces)
    assert_installed_templates_align()
    print(json.dumps({"ok": True, "v6_phase1_activation_modularization": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
