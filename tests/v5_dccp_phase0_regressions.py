from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.cli import ensure_agents_md, ensure_shujuan_skill


ROLE_TERMS = [
    "controller_agent",
    "worker_agent",
    "reviewer_agent",
    "researcher_agent",
    "writer_agent",
    "writing_no_governance",
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
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def assert_role_surface(text: str, label: str) -> None:
    missing = [term for term in ROLE_TERMS if term not in text]
    if missing:
        raise AssertionError(f"{label} missing DCCP role terms: {missing}")
    required_fragments = [
        "controller",
        "check/task closure",
        "governance authority stays with the controller",
        "read-only review",
        "writing_no_governance",
    ]
    lowered = text.lower()
    missing_fragments = [fragment for fragment in required_fragments if fragment.lower() not in lowered]
    if missing_fragments:
        raise AssertionError(f"{label} missing role boundary wording: {missing_fragments}")


def assert_activation_card_role_boundary(text: str, label: str) -> None:
    required = [
        "`AGENTS.md` is the canonical repo policy",
        "## Activation",
        "## Five Routes",
        "## Minimal Hard Boundaries",
        "worker output is material until controller adoption",
        "evidence import, check/task closure, and final closeout",
    ]
    missing = [fragment for fragment in required if fragment not in text]
    if missing:
        raise AssertionError(f"{label} missing activation-card role boundary: {missing}")
    if "## DCCP Role Boundary" in text or "python -m shujuan " in text:
        raise AssertionError(f"{label} should not duplicate role cards or command maps")


def assert_live_role_surfaces() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    skill = (ROOT / ".agents" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")
    assert_role_surface(agents, "repo AGENTS.md")
    assert_activation_card_role_boundary(skill, "repo shujuan-core skill")
    if "## Authority" not in skill[:900] or "## Activation" not in skill[:900]:
        raise AssertionError("controller skill does not start with authority and activation")


def assert_template_install_surfaces() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-phase0-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        agents_result = ensure_agents_md(repo)
        skill_result = ensure_shujuan_skill(repo)
        if agents_result["action"] != "created" or skill_result["action"] != "created":
            raise AssertionError(f"template install did not create expected surfaces: {agents_result}, {skill_result}")
        generated_agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
        generated_skill = (repo / ".agents" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")
        assert_role_surface(generated_agents, "generated AGENTS.md")
        assert_activation_card_role_boundary(generated_skill, "generated shujuan-core skill")
        if "## Authority" not in generated_skill[:900] or "## Activation" not in generated_skill[:900]:
            raise AssertionError("generated shujuan-core skill does not start with authority and activation")
        if "project-owned PostgreSQL" not in generated_agents or "init --postgres-dev" not in generated_agents:
            raise AssertionError("generated templates do not point to the normal project-owned PostgreSQL path")
        (repo / "AGENTS.md").write_text(
            "# shujuan Repository Instructions\n\n<!-- shujuan-agent-instructions:v1 -->\n\nOld managed block.\n",
            encoding="utf-8",
        )
        old_skill_path = repo / ".agents" / "skills" / "shujuan-core" / "SKILL.md"
        old_skill_path.write_text(
            "---\nname: shujuan-core\n---\n\n# Shujuan Core SOP\n\n- `generated endpoint body`: old term.\n",
            encoding="utf-8",
        )
        agents_update = ensure_agents_md(repo)
        skill_update = ensure_shujuan_skill(repo)
        if agents_update["action"] != "updated" or skill_update["action"] != "updated":
            raise AssertionError(f"stale managed templates were not updated: {agents_update}, {skill_update}")
        assert_role_surface((repo / "AGENTS.md").read_text(encoding="utf-8"), "updated AGENTS.md")
        assert_activation_card_role_boundary(old_skill_path.read_text(encoding="utf-8"), "updated shujuan-core skill")


def assert_normal_help_is_postgres_first() -> None:
    repo = ROOT
    top_help = run_cli(repo, "--help").stdout
    if "init --postgres-dev" not in top_help or "project-owned PostgreSQL" not in top_help:
        raise AssertionError(f"top-level help does not surface PostgreSQL init path:\n{top_help}")
    init_help = run_cli(repo, "init", "--help").stdout
    if "canonical AGENTS.md" not in init_help or "--postgres-dev" not in init_help:
        raise AssertionError(f"init help does not surface canonical templates and PostgreSQL:\n{init_help}")
    postgres_help = run_cli(repo, "postgres-dev", "--help").stdout
    if "cutover" in postgres_help:
        raise AssertionError(f"legacy cutover leaked into normal postgres-dev help:\n{postgres_help}")
    cutover = run_cli(repo, "postgres-dev", "cutover", expect_ok=False)
    if "cutover from SQLite is disabled" not in cutover.stderr:
        raise AssertionError(f"legacy cutover did not remain fail-closed:\n{cutover.stderr}")


def assert_writer_no_governance_paths_do_not_connect() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-writer-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        suggest = json.loads(run_cli(repo, "mode", "suggest", "--intent", "writer packet draft outside shujuan").stdout)
        if suggest["suggested_mode"] != "no_governance" or suggest["contract"]["db_writes"]:
            raise AssertionError(f"writer packet did not route to no_governance: {suggest}")
        work = json.loads(
            run_cli(repo, "work", "start", "--mode", "no-governance", "--content", "writer packet draft only").stdout
        )
        if work["db_writes"] != 0 or work["capture_claim"] or work["current_handle"] is not None:
            raise AssertionError(f"writer no-governance path created governance state: {work}")
        if (repo / ".shujuan").exists():
            raise AssertionError("no-governance writer path created .shujuan artifacts")


def main() -> int:
    assert_live_role_surfaces()
    assert_template_install_surfaces()
    assert_normal_help_is_postgres_first()
    assert_writer_no_governance_paths_do_not_connect()
    print(json.dumps({"ok": True, "v5_dccp_phase0": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
