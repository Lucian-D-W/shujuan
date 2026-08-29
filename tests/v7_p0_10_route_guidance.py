from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.cli import ensure_agents_md, ensure_shujuan_skill


ROUTES = ["Recover", "Recall", "Execute", "Close", "Delegate"]
SURFACE_FILES = [
    "AGENTS.md",
    "README.md",
    ".agents/skills/shujuan-core/SKILL.md",
    ".agents/skills/shujuan-core/references/activation-first.md",
    ".agents/skills/shujuan-core/references/evidence-closeout.md",
    ".agents/skills/shujuan-core/references/delegation.md",
    ".agents/skills/shujuan-core/references/modes-and-terms.md",
    ".agents/skills/shujuan-core/references/plan-to-db-task-chain-hygiene.md",
    ".agents/skills/shujuan-core/templates/delegate-return.md",
    ".agents/skills/shujuan-core/templates/closeout-handoff.md",
]


def read_surfaces(root: Path) -> dict[str, str]:
    missing = [rel for rel in SURFACE_FILES if not (root / rel).exists()]
    if missing:
        raise AssertionError(f"route guidance surfaces missing: {missing}")
    return {rel: (root / rel).read_text(encoding="utf-8") for rel in SURFACE_FILES}


def assert_route_alignment(surfaces: dict[str, str], label: str) -> None:
    skill = surfaces[".agents/skills/shujuan-core/SKILL.md"]
    agents = surfaces["AGENTS.md"]
    readme = surfaces["README.md"]
    activation = surfaces[".agents/skills/shujuan-core/references/activation-first.md"]
    delegation = surfaces[".agents/skills/shujuan-core/references/delegation.md"]
    evidence = surfaces[".agents/skills/shujuan-core/references/evidence-closeout.md"]
    closeout_template = surfaces[".agents/skills/shujuan-core/templates/closeout-handoff.md"]
    delegate_template = surfaces[".agents/skills/shujuan-core/templates/delegate-return.md"]
    combined = "\n".join(surfaces.values())

    for route in ROUTES:
        if f"`{route}`" not in skill or f"`{route}`" not in agents or f"`{route}`" not in readme:
            raise AssertionError(f"{label} does not expose default route `{route}` across Skill, AGENTS, and README")

    if "## Five Routes" not in skill:
        raise AssertionError(f"{label} skill does not provide the activation route card")
    if "`Recall`: answer history" not in skill or "## Recall Route" not in activation:
        raise AssertionError(f"{label} surfaces do not expose Recall as a read-only lineage route")
    if "report endpoint <endpoint> --full --markdown" not in combined or "python -m shujuan why --path <path>" not in combined:
        raise AssertionError(f"{label} Recall route does not show full-history and code-reason read-only surfaces")
    if "separate observed facts" not in combined and "Distinguish observed facts" not in combined:
        raise AssertionError(f"{label} Recall route does not require fact/inference separation")
    if "Use the v3 terms" in combined or "V4 interaction anchors" in combined or "Canonical V3 Terms" in combined:
        raise AssertionError(f"{label} surfaces still expose historical version labels in the active entry text")
    if "python -m shujuan task add --body" in skill:
        raise AssertionError(f"{label} skill still presents task primitives in the normal front-door route")
    if "Open routed references only when the selected route needs detail" not in skill:
        raise AssertionError(f"{label} skill does not route detailed primitives to reference material")
    if "## Advanced Fallback" not in activation or "python -m shujuan task add --body" not in activation:
        raise AssertionError(f"{label} activation reference does not hold advanced fallback task primitives")

    recovery_command = "python -m shujuan endpoint doctor <endpoint> --strict-closeout --read-only --allow-fail"
    if recovery_command not in combined:
        raise AssertionError(f"{label} surfaces lost the read-only recovery doctor command")
    if "diagnostic only" not in combined or "does not refresh the current endpoint body" not in combined:
        raise AssertionError(f"{label} surfaces do not make read-only doctor recovery/diagnostic only")

    close_command = "python -m shujuan endpoint doctor <endpoint> --strict-closeout --allow-fail"
    if close_command not in combined:
        raise AssertionError(f"{label} surfaces lost the controller closeout doctor command")
    if "writeful controller" not in combined or "without `--read-only`" not in evidence + closeout_template + readme:
        raise AssertionError(f"{label} surfaces do not distinguish writeful controller closeout")

    if "Default route: `Delegate`" not in delegate_template:
        raise AssertionError(f"{label} delegate template does not identify the Delegate route")
    worker_model_phrase = "Code-modifying worker subagents default to `gpt-5.4 medium` unless the user explicitly specifies another model."
    if worker_model_phrase not in agents or worker_model_phrase not in skill or worker_model_phrase not in delegation:
        raise AssertionError(f"{label} surfaces do not expose the code-modifying worker default model")
    if "Worker model: code-modifying worker subagents default to `gpt-5.4 medium` unless the user explicitly specifies another model" not in delegate_template:
        raise AssertionError(f"{label} delegate template does not require the worker model field")

    stale_active_surface_phrases = [
        "Pick the action path: new-window recovery, execution, delegation/review, evidence closeout, or scope hygiene.",
        "before choosing execution, delegation/review, evidence closeout, or advanced fallback",
        "new-window recovery",
        "action paths",
        "`new-window recovery`",
        "`evidence closeout`",
    ]
    stale = [phrase for phrase in stale_active_surface_phrases if phrase in combined]
    if stale:
        raise AssertionError(f"{label} surfaces still privilege stale route wording: {stale}")


def assert_installed_route_alignment() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v7-p0-10-routes-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        agents_result = ensure_agents_md(repo)
        skill_result = ensure_shujuan_skill(repo)
        if agents_result["action"] != "created" or skill_result["action"] != "created":
            raise AssertionError(f"template install did not create expected surfaces: {agents_result}, {skill_result}")
        readme = repo / "README.md"
        readme.write_text((ROOT / "README.md").read_text(encoding="utf-8"), encoding="utf-8")
        assert_route_alignment(read_surfaces(repo), "installed templates")


def main() -> int:
    assert_route_alignment(read_surfaces(ROOT), "checked-in surfaces")
    assert_installed_route_alignment()
    print(json.dumps({"ok": True, "v7_p0_10_route_guidance": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
