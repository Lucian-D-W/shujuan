from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


REQUIRED_LANES = {
    "worker_owned",
    "pre_existing_dirty",
    "provider_runtime",
    "observed_only",
    "not_owned",
    "deleted_obsolete",
    "fallback",
    "out_of_scope",
}


REQUIRED_FIELDS = {
    "lane",
    "path",
    "hunk_id",
    "hunk_header",
    "range",
    "hash",
    "claimed_owner",
    "pre_existing_dirty",
    "source",
    "reason",
    "promotion_or_reopen_rule",
}


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


def run_json(repo: Path, *args: str) -> dict[str, object]:
    return json.loads(run_cli(repo, *args).stdout)


def assert_schema(schema: dict[str, object]) -> None:
    if set(schema.get("required_lanes", [])) != REQUIRED_LANES:
        raise AssertionError(f"ownership schema lost required lanes: {schema}")
    if not REQUIRED_FIELDS <= set(schema.get("required_fields", [])):
        raise AssertionError(f"ownership schema lost required fields: {schema}")
    boundary = schema.get("material_boundary")
    if not isinstance(boundary, dict) or not boundary.get("material_only"):
        raise AssertionError(f"ownership schema lost material-only boundary: {schema}")
    if boundary.get("manifest_is_closure_evidence") is not False:
        raise AssertionError(f"ownership schema allowed manifest as closure evidence: {schema}")
    fallback = " ".join(schema.get("allowed_path_level_fallback", []))
    if "pre-existing dirty hunks" not in fallback:
        raise AssertionError(f"ownership schema fallback rule does not protect dirty hunks: {schema}")
    rules = schema.get("promotion_reopen_rules")
    if not isinstance(rules, dict) or set(rules) != REQUIRED_LANES:
        raise AssertionError(f"ownership schema lost promotion/reopen rules: {schema}")


def assert_manifest(surface: dict[str, object]) -> None:
    if set(surface.get("ownership_lanes", [])) != REQUIRED_LANES:
        raise AssertionError(f"surface lost ownership_lanes: {surface}")
    if surface.get("ownership_manifest_material_only") is not True:
        raise AssertionError(f"surface did not mark ownership manifest material-only: {surface}")
    if surface.get("manifest_is_closure_evidence") is not False:
        raise AssertionError(f"surface allowed manifest as closure evidence: {surface}")
    assert_schema(surface["ownership_manifest_schema"])
    manifest = surface.get("ownership_manifest")
    if not isinstance(manifest, dict) or set(manifest) != REQUIRED_LANES:
        raise AssertionError(f"surface ownership manifest collapsed lanes: {surface}")


def assert_packet_and_capsule_preserve_schema(repo: Path) -> None:
    common_args = [
        "--role",
        "worker",
        "--endpoint",
        "shujuan-v7-friction-control-2026-05-22",
        "--task",
        "task_f7e836b42b904a2d",
        "--check",
        "check_8d3302539674430d",
        "--pre-existing-dirty-path",
        "dirty.py",
        "--owned-hunk-or-path",
        "worker.py@@def worker",
        "--inspected-only-path",
        "docs/context.md",
        "--provider-runtime-path",
        ".claude/skills/gitnexus/runtime.json",
        "--observed-only-path",
        "README.md",
        "--not-owned-path",
        "other-worker.py",
        "--deleted-obsolete-path",
        "old-route.py",
        "--fallback-path",
        "mixed-file.py",
        "--out-of-scope-path",
        ".shujuan/state.json",
        "--provider-output",
        "gitnexus impact: LOW",
    ]
    packet = run_json(repo, "delegate", "packet", "--packet-kind", "delegation", "--goal", "Patch ownership schema.", *common_args)
    role_packet = packet["packet"]["role_packet"]
    assert_manifest(role_packet)
    assert_manifest(role_packet["return_requirements"])
    required_fields = set(role_packet["return_requirements"]["required_fields"])
    for field in ("ownership_manifest", "ownership_manifest_schema", "ownership_lanes", "ownership_manifest_material_only", "manifest_is_closure_evidence"):
        if field not in required_fields:
            raise AssertionError(f"return requirements omitted ownership field {field}: {packet}")
    manifest = role_packet["return_requirements"]["ownership_manifest"]
    if manifest["worker_owned"] != ["worker.py@@def worker"]:
        raise AssertionError(f"worker_owned lane did not preserve owned hunk: {packet}")
    if manifest["pre_existing_dirty"] != ["dirty.py"]:
        raise AssertionError(f"pre_existing_dirty lane did not preserve dirty path: {packet}")
    if manifest["provider_runtime"] != [".claude/skills/gitnexus/runtime.json"]:
        raise AssertionError(f"provider_runtime lane did not preserve provider path: {packet}")
    if set(manifest["observed_only"]) != {"docs/context.md", "README.md"}:
        raise AssertionError(f"observed_only lane did not include inspected/observed paths: {packet}")
    if packet.get("db_writes") != 0 or packet.get("capture_claim"):
        raise AssertionError(f"delegate packet claimed governance writes: {packet}")

    capsule = run_json(repo, "delegate", "capsule", *common_args)
    assert_manifest(capsule["capsule"]["return_requirements"])
    if capsule["capsule"]["return_requirements"]["manifest_is_closure_evidence"] is not False:
        raise AssertionError(f"capsule return requirements leaked closure evidence: {capsule}")


def assert_ownership_output_distinguishes_lanes(repo: Path) -> None:
    ownership = run_json(
        repo,
        "delegate",
        "ownership",
        "--endpoint",
        "shujuan-v7-friction-control-2026-05-22",
        "--task",
        "task_f7e836b42b904a2d",
        "--check",
        "check_8d3302539674430d",
        "--lane",
        "lane_v7_p0_09",
        "--pre-existing-dirty-path",
        "dirty.py",
        "--assigned-path",
        "worker.py",
        "--claimed-path",
        "worker.py",
        "--provider-runtime-path",
        ".claude/skills/gitnexus/runtime.json",
        "--observed-only-path",
        "docs/context.md",
        "--not-owned-path",
        "other-worker.py",
        "--deleted-obsolete-path",
        "old-route.py",
        "--fallback-path",
        "mixed-file.py",
        "--out-of-scope-path",
        ".shujuan/state.json",
        "--after-snapshot-path",
        "worker.py",
        "--after-snapshot-path",
        "dirty.py",
        "--after-snapshot-path",
        ".claude/skills/gitnexus/runtime.json",
        "--after-snapshot-path",
        "docs/context.md",
        "--after-snapshot-path",
        "other-worker.py",
        "--after-snapshot-path",
        "mixed-file.py",
        "--after-snapshot-path",
        ".shujuan/state.json",
        "--after-snapshot-path",
        "unexpected.py",
    )
    body = ownership["ownership"]
    assert_manifest(body)
    classes = body["controller_path_classes"]
    if not REQUIRED_LANES <= set(classes):
        raise AssertionError(f"controller_path_classes lost required lanes: {ownership}")
    if classes["worker_owned"] != ["worker.py"]:
        raise AssertionError(f"worker_owned class wrong: {ownership}")
    if classes["pre_existing_dirty"] != ["dirty.py"] or classes["pre_existing"] != ["dirty.py"]:
        raise AssertionError(f"pre-existing dirty compatibility class wrong: {ownership}")
    if classes["provider_runtime"] != [".claude/skills/gitnexus/runtime.json"]:
        raise AssertionError(f"provider_runtime class wrong: {ownership}")
    if classes["observed_only"] != ["docs/context.md"]:
        raise AssertionError(f"observed_only class wrong: {ownership}")
    if classes["not_owned"] != ["other-worker.py"]:
        raise AssertionError(f"not_owned class wrong: {ownership}")
    if classes["deleted_obsolete"] != ["old-route.py"]:
        raise AssertionError(f"deleted_obsolete class wrong: {ownership}")
    if classes["fallback"] != ["mixed-file.py"]:
        raise AssertionError(f"fallback class wrong: {ownership}")
    if classes["out_of_scope"] != [".shujuan/state.json"]:
        raise AssertionError(f"out_of_scope class wrong: {ownership}")
    if body["unassigned_paths"] != ["unexpected.py"]:
        raise AssertionError(f"explicit lanes should not leak into unassigned paths: {ownership}")


def assert_template_and_artifact_document_lanes() -> None:
    paths = [
        ROOT / ".agents" / "skills" / "shujuan-core" / "templates" / "delegate-return.md",
        ROOT / "shujuan" / "cli.py",
        ROOT / "docs" / "v7_p0_09_ownership_manifest_schema_2026-05-22.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for lane in REQUIRED_LANES:
            if f"`{lane}`" not in text and f'"{lane}"' not in text:
                raise AssertionError(f"{path} omitted required lane {lane}")
        if path.name in {"delegate-return.md", "cli.py"}:
            lowered = text.lower()
            if "controller adoption" not in lowered or "closure evidence" not in lowered:
                raise AssertionError(f"{path} did not document controller adoption ownership boundary")
        elif "material only" not in text or "manifest_is_closure_evidence=false" not in text:
            raise AssertionError(f"{path} did not document material-only ownership boundary")
    artifact = (ROOT / "docs" / "v7_p0_09_ownership_manifest_schema_2026-05-22.md").read_text(encoding="utf-8")
    for phrase in (
        "Path-level fallback",
        "pre-existing dirty hunks",
        "Promotion And Reopen Rules",
        "Dirty worktree review",
    ):
        if phrase not in artifact:
            raise AssertionError(f"schema artifact omitted required rule phrase: {phrase}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-v7-p0-09-ownership-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        assert_packet_and_capsule_preserve_schema(repo)
        assert_ownership_output_distinguishes_lanes(repo)
        if (repo / ".shujuan").exists():
            raise AssertionError("delegate ownership diagnostics created current-project governance artifacts")
    assert_template_and_artifact_document_lanes()
    print(json.dumps({"ok": True, "v7_p0_09_ownership_manifest": "passed", "lanes": sorted(REQUIRED_LANES)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
