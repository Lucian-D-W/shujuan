from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import clean_env


def main() -> int:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    skill = (ROOT / ".agents" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")
    ref = (ROOT / ".agents" / "skills" / "shujuan-core" / "references" / "v10-ontology-relation-gate.md").read_text(encoding="utf-8")
    asset_agents = (ROOT / "shujuan" / "assets" / "AGENTS.md").read_text(encoding="utf-8")
    asset_skill = (ROOT / "shujuan" / "assets" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")

    first_screen = agents[:2500]
    for fragment in ("First Route", "Intent Priority", "Topology Operating Card", "Four Gates"):
        if fragment not in first_screen:
            raise AssertionError(f"AGENTS first screen missed fragment: {fragment}")
    for fragment in ("compatibility shim", "ordinary v11 work", "controller adoption"):
        if fragment not in skill:
            raise AssertionError(f"skill activation card missed fragment: {fragment}")
    if "Decision types stay small and operational" not in ref:
        raise AssertionError("v10 relation reference is missing the operational decision taxonomy")
    if asset_agents != agents or asset_skill != skill:
        raise AssertionError("packaged AGENTS/SKILL assets drifted from repo source")

    temp = Path(tempfile.mkdtemp(prefix="v10-package-assets-"))
    target = temp / "target"
    repo = temp / "repo"
    repo.mkdir()
    try:
        install = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(ROOT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if install.returncode:
            raise AssertionError(f"pip install --target failed\nSTDOUT:\n{install.stdout}\nSTDERR:\n{install.stderr}")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(target)
        install_assets = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "from shujuan.cli import ensure_agents_md, ensure_shujuan_skill; "
                    f"repo=Path({str(repo)!r}); "
                    "ensure_agents_md(repo); "
                    "ensure_shujuan_skill(repo)"
                ),
            ],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        if install_assets.returncode:
            raise AssertionError(f"installed asset generation failed\nSTDOUT:\n{install_assets.stdout}\nSTDERR:\n{install_assets.stderr}")
        installed_agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
        installed_skill = (repo / ".agents" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")
        for fragment in ("First Route", "Relation", "Source coverage"):
            if fragment not in installed_agents:
                raise AssertionError(f"installed AGENTS missed fragment: {fragment}")
        for fragment in ("compatibility shim", "ordinary v11 work"):
            if fragment not in installed_skill:
                raise AssertionError(f"installed skill missed fragment: {fragment}")

        init_repo = temp / "init-partial"
        init_repo.mkdir()
        init_result = subprocess.run(
            [sys.executable, "-m", "shujuan", "--repo", str(init_repo), "init"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_env(),
        )
        if init_result.returncode == 0:
            raise AssertionError("plain init unexpectedly passed without PostgreSQL runtime")
        payload = json.loads(init_result.stdout)
        if payload.get("partial_init") is not True:
            raise AssertionError(f"init did not expose partial_init after asset installation: {payload}")
        installed_assets = payload.get("installed_assets") or []
        if not installed_assets:
            raise AssertionError(f"init partial payload did not include installed assets: {payload}")
        if not (init_repo / "AGENTS.md").exists():
            raise AssertionError("partial init did not install AGENTS.md before DB failure")
        for skill_name in ("shujuan-harness", "shujuan-recall", "shujuan-capture", "shujuan-execute", "shujuan-delegate", "shujuan-close", "shujuan-evolve", "shujuan-core"):
            if not (init_repo / ".agents" / "skills" / skill_name / "SKILL.md").exists():
                raise AssertionError(f"partial init did not install {skill_name} before DB failure")
        if init_result.stderr.strip():
            raise AssertionError(f"partial init leaked stderr text: {init_result.stderr}")
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    print(json.dumps({"ok": True, "v10_codex_surfaces": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
