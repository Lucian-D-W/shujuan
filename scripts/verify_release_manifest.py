from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any


REQUIRED_V11_RELEASE_SURFACES = (
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "docs/README.md",
    "docs/architecture.md",
    "docs/guides/windows-workbench.md",
    "scripts/verify_release_manifest.py",
    "scripts/update_release_manifest.py",
    "scripts/audit_public_repository.py",
    "scripts/v11_2_replay_evidence.py",
    ".codex/hooks/shujuan-method-hint.py",
    ".codex/hooks/shujuan-pretool-guard.py",
    ".codex/hooks.json",
    ".codex/agents/shujuan-controller.toml",
    ".codex/agents/shujuan-worker.toml",
    ".codex/agents/shujuan-reviewer.toml",
    ".codex/agents/shujuan-researcher.toml",
    ".codex/agents/shujuan-writer.toml",
    "shujuan/assets/AGENTS.md",
    "shujuan/assets/hooks.json",
    "shujuan/assets/hooks/shujuan-method-hint.py",
    "shujuan/assets/hooks/shujuan-pretool-guard.py",
    "shujuan/assets/agents/shujuan-controller.toml",
    "shujuan/assets/agents/shujuan-worker.toml",
    "shujuan/assets/agents/shujuan-reviewer.toml",
    "shujuan/assets/agents/shujuan-researcher.toml",
    "shujuan/assets/agents/shujuan-writer.toml",
    "shujuan/assets/skills/shujuan-harness/SKILL.md",
    "shujuan/assets/skills/shujuan-recall/SKILL.md",
    "shujuan/assets/skills/shujuan-capture/SKILL.md",
    "shujuan/assets/skills/shujuan-execute/SKILL.md",
    "shujuan/assets/skills/shujuan-delegate/SKILL.md",
    "shujuan/assets/skills/shujuan-close/SKILL.md",
    "shujuan/assets/skills/shujuan-evolve/SKILL.md",
    "shujuan/assets/skills/shujuan-core/SKILL.md",
    "shujuan/commands/route.py",
    "shujuan/commands/init.py",
    "shujuan/commands/install_layout.py",
    "shujuan/commands/delegate_handlers.py",
    "shujuan/services/method_policy.py",
    "shujuan/services/role_policy.py",
    "shujuan/services/route_intent.py",
    "shujuan/services/skill_registry.py",
    "tests/v11_method_plane.py",
    "tests/v11_2_verification_repair.py",
    "tests/postgres_dev_credentials_permissions.py",
    "tests/publication_privacy_gate.py",
    "tests/fixtures/v11_route_method_matrix.json",
    "tests/fixtures/v11_recall_benchmarks.json",
    "docs/history/shujuan-v11.2-verification-coverage-2026-06-28.json",
    "docs/history/shujuan-v11-method-plane-design-2026-06-25.md",
    "docs/history/shujuan-v11-task-chain-2026-06-26.json",
    "docs/history/shujuan-v11-migration-release-notes-2026-06-26.md",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_manifest_sha256(contents: str) -> str:
    return contents.strip().split()[0] if contents.strip() else ""


def _json_error(code: str, message: str, *, root: Path) -> dict[str, Any]:
    return {
        "ok": False,
        "root": str(root),
        "error": {
            "code": code,
            "message": message,
        },
    }


def _git_tracking_for_required_surfaces(root: Path, required_surfaces: list[str]) -> tuple[bool, list[str], str | None]:
    if not required_surfaces or not (root / ".git").exists():
        return False, [], None
    try:
        top_level = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return True, [], f"git tracking check could not start: {exc}"
    if top_level.returncode:
        return True, [], top_level.stderr.strip() or "git tracking check could not resolve repository root"
    if Path(top_level.stdout.strip()).resolve() != root.resolve():
        return False, [], None

    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", *required_surfaces],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if tracked.returncode:
        return True, [], tracked.stderr.strip() or "git tracking check failed"
    tracked_paths = {line.strip() for line in tracked.stdout.splitlines() if line.strip()}
    untracked = [path for path in required_surfaces if path not in tracked_paths]
    return True, untracked, None


def verify_release_manifest(root: Path) -> dict[str, Any]:
    manifest_path = root / "MANIFEST.json"
    manifest_sha_path = root / "MANIFEST.sha256"
    if not manifest_path.exists():
        return _json_error("missing_manifest_json", "MANIFEST.json is missing.", root=root)
    if not manifest_sha_path.exists():
        return _json_error("missing_manifest_sha256", "MANIFEST.sha256 is missing.", root=root)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _json_error("invalid_manifest_json", f"MANIFEST.json is not valid JSON: {exc}", root=root)

    files = manifest.get("files")
    if not isinstance(files, list):
        return _json_error("invalid_manifest_shape", "MANIFEST.json must contain a files[] list.", root=root)

    missing: list[str] = []
    bad_hash: list[dict[str, str]] = []
    listed_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            bad_hash.append({"path": "<invalid>", "expected": "", "actual": "invalid_manifest_entry"})
            continue
        relative_path = item.get("path")
        expected_hash = item.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
            bad_hash.append({"path": str(relative_path), "expected": str(expected_hash), "actual": "invalid_manifest_entry"})
            continue
        listed_paths.add(relative_path)
        target = root / relative_path
        if not target.exists():
            missing.append(relative_path)
            continue
        actual_hash = _sha256_bytes(target.read_bytes())
        if actual_hash != expected_hash:
            bad_hash.append({"path": relative_path, "expected": expected_hash, "actual": actual_hash})

    manifest_bytes = manifest_path.read_bytes()
    manifest_sha_actual = _sha256_bytes(manifest_bytes)
    manifest_sha_expected = _parse_manifest_sha256(manifest_sha_path.read_text(encoding="utf-8"))
    manifest_sha_matches = manifest_sha_expected == manifest_sha_actual
    required_surfaces = list(REQUIRED_V11_RELEASE_SURFACES) if manifest.get("schema") == "shujuan.release_manifest.v11" else []
    missing_required = [path for path in required_surfaces if path not in listed_paths]
    tracking_checked, untracked_required, tracking_error = _git_tracking_for_required_surfaces(root, required_surfaces)

    return {
        "ok": (
            not missing
            and not bad_hash
            and manifest_sha_matches
            and not missing_required
            and not untracked_required
            and tracking_error is None
        ),
        "root": str(root),
        "manifest_present": True,
        "sha_present": True,
        "file_count": len(files),
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "required_surface_count": len(required_surfaces),
        "missing_required_surface_count": len(missing_required),
        "required_surface_tracking_checked": tracking_checked,
        "untracked_required_surface_count": len(untracked_required),
        "missing": missing,
        "bad_hash": bad_hash,
        "missing_required_surfaces": missing_required,
        "untracked_required_surfaces": untracked_required,
        "required_surface_tracking_error": tracking_error,
        "manifest_sha256": {
            "expected": manifest_sha_expected,
            "actual": manifest_sha_actual,
            "matches": manifest_sha_matches,
        },
    }


def verify_release_manifest_target(target: Path) -> dict[str, Any]:
    if target.is_file() and target.suffix.lower() == ".zip":
        temp = Path(tempfile.mkdtemp(prefix="shujuan-release-manifest-"))
        try:
            with zipfile.ZipFile(target) as zf:
                zf.extractall(temp)
            manifest_root = temp
            if not (manifest_root / "MANIFEST.json").exists():
                children = list(temp.iterdir())
                top_level_dirs = [child for child in children if child.is_dir()]
                top_level_files = [child for child in children if child.is_file()]
                if len(top_level_dirs) == 1 and not top_level_files:
                    manifest_root = top_level_dirs[0]
            payload = verify_release_manifest(manifest_root)
            payload["target"] = str(target)
            payload["target_kind"] = "zip"
            return payload
        finally:
            shutil.rmtree(temp, ignore_errors=True)
    payload = verify_release_manifest(target)
    payload["target"] = str(target)
    payload["target_kind"] = "directory"
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a Shujuan release manifest from a directory or zip.")
    parser.add_argument("root", type=Path, help="Path to an unpacked release root or a release zip.")
    args = parser.parse_args()
    payload = verify_release_manifest_target(args.root.resolve())
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
