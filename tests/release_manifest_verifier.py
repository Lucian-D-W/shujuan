from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import v11_2_replay_evidence as replay_verifier
from scripts import verify_release_manifest as manifest_verifier


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_release(root: Path, *, file_sha: str | None = None, include_file: bool = True, sha_override: str | None = None) -> Path:
    release_root = root / "release"
    release_root.mkdir(parents=True)
    target = release_root / "artifact.txt"
    data = b"release artifact\n"
    if include_file:
        target.write_bytes(data)
    manifest = {
        "files": [
            {
                "path": "artifact.txt",
                "sha256": file_sha or _sha256_bytes(data),
            }
        ]
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (release_root / "MANIFEST.json").write_bytes(manifest_bytes)
    manifest_digest = _sha256_bytes(manifest_bytes)
    (release_root / "MANIFEST.sha256").write_text(sha_override or f"{manifest_digest}  MANIFEST.json", encoding="utf-8")
    return release_root


def _write_manifest(root: Path, files: dict[str, bytes], *, schema: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for relative_path, data in files.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        entries.append({"path": relative_path, "sha256": _sha256_bytes(data)})
    manifest = {"files": entries}
    if schema:
        manifest["schema"] = schema
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (root / "MANIFEST.json").write_bytes(manifest_bytes)
    manifest_digest = _sha256_bytes(manifest_bytes)
    (root / "MANIFEST.sha256").write_text(f"{manifest_digest}  MANIFEST.json", encoding="utf-8")
    return root


def _run(root: Path, *, expect_ok: bool) -> dict:
    completed = subprocess.run(
        [sys.executable, "scripts/verify_release_manifest.py", str(root)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if expect_ok and completed.returncode != 0:
        raise AssertionError(f"verifier failed unexpectedly\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"verifier passed unexpectedly\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    return json.loads(completed.stdout)


def _zip_release(root: Path) -> Path:
    zip_path = root.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(root.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(root).as_posix())
    return zip_path


def _assert_replay_command_validation() -> None:
    with tempfile.TemporaryDirectory(prefix="replay-verify-") as temp:
        coverage = Path(temp) / "coverage.json"
        bad_payload = {
            "checks": [
                {
                    "check_key": "C_BAD_COMMAND",
                    "phase": "P0",
                    "evidence_status": "observed_pass",
                    "replay": {"command": "python NO_SUCH_SCRIPT.py", "references": []},
                }
            ]
        }
        coverage.write_text(json.dumps(bad_payload), encoding="utf-8")
        bad = replay_verifier.verify_coverage(coverage, strict_p0=True)
        if bad["ok"] or not any(item.get("token") == "NO_SUCH_SCRIPT.py" for item in bad["command_failures"]):
            raise AssertionError(f"missing replay command script was not rejected: {bad}")

        good_payload = {
            "checks": [
                {
                    "check_key": "C_GOOD_COMMAND",
                    "phase": "P0",
                    "evidence_status": "observed_pass",
                    "replay": {
                        "command": f'"{sys.executable}" scripts/v11_2_replay_evidence.py',
                        "references": ["scripts/v11_2_replay_evidence.py"],
                    },
                }
            ]
        }
        coverage.write_text(json.dumps(good_payload), encoding="utf-8")
        good = replay_verifier.verify_coverage(coverage, strict_p0=True)
        if not good["ok"] or good["validated_command_count"] != 1:
            raise AssertionError(f"valid replay command was rejected: {good}")

        outside = Path(temp) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        outside_payload = {
            "checks": [
                {
                    "check_key": "C_OUTSIDE_REFERENCE",
                    "phase": "P0",
                    "evidence_status": "observed_pass",
                    "replay": {
                        "command": f'"{sys.executable}" scripts/v11_2_replay_evidence.py',
                        "references": [str(outside)],
                    },
                }
            ]
        }
        coverage.write_text(json.dumps(outside_payload), encoding="utf-8")
        outside_result = replay_verifier.verify_coverage(coverage, strict_p0=True)
        if outside_result["ok"] or not any(item.get("reason") == "replay reference is outside the repository" for item in outside_result["missing_references"]):
            raise AssertionError(f"outside replay reference was not rejected: {outside_result}")

        non_string_payload = {
            "checks": [
                {
                    "check_key": "C_NON_STRING_REFERENCE",
                    "phase": "P0",
                    "evidence_status": "observed_pass",
                    "replay": {
                        "command": f'"{sys.executable}" scripts/v11_2_replay_evidence.py',
                        "references": [123],
                    },
                }
            ]
        }
        coverage.write_text(json.dumps(non_string_payload), encoding="utf-8")
        non_string_result = replay_verifier.verify_coverage(coverage, strict_p0=True)
        if non_string_result["ok"] or not any(item.get("reason") == "replay reference must be a string" for item in non_string_result["missing_references"]):
            raise AssertionError(f"non-string replay reference was not rejected: {non_string_result}")

        slash_c_failures = replay_verifier._validate_replay_command(
            "C_WINDOWS_SWITCH",
            f'"{sys.executable}" /C scripts/v11_2_replay_evidence.py',
        )
        if any(item.get("token") == "/C" for item in slash_c_failures):
            raise AssertionError(f"Windows /C switch was treated as a file path: {slash_c_failures}")


def _assert_v11_required_surfaces_tracking() -> None:
    original_required = manifest_verifier.REQUIRED_V11_RELEASE_SURFACES
    manifest_verifier.REQUIRED_V11_RELEASE_SURFACES = ("tracked.txt", "untracked.txt")
    try:
        with tempfile.TemporaryDirectory(prefix="manifest-source-") as temp:
            source_root = _write_manifest(
                Path(temp) / "source",
                {"tracked.txt": b"tracked\n", "untracked.txt": b"packaged\n"},
                schema="shujuan.release_manifest.v11",
            )
            source_payload = manifest_verifier.verify_release_manifest(source_root)
            if not source_payload["ok"] or source_payload["required_surface_tracking_checked"]:
                raise AssertionError(f"non-git source package was incorrectly subject to tracking: {source_payload}")

            source_zip = _zip_release(source_root)
            zip_payload = manifest_verifier.verify_release_manifest_target(source_zip)
            if not zip_payload["ok"] or zip_payload["required_surface_tracking_checked"]:
                raise AssertionError(f"source zip was incorrectly subject to tracking: {zip_payload}")

        if shutil.which("git") is None:
            return

        with tempfile.TemporaryDirectory(prefix="manifest-git-") as temp:
            repo = _write_manifest(
                Path(temp) / "repo",
                {"tracked.txt": b"tracked\n", "untracked.txt": b"local only\n"},
                schema="shujuan.release_manifest.v11",
            )
            subprocess.run(["git", "init"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            subprocess.run(
                ["git", "add", "tracked.txt", "MANIFEST.json", "MANIFEST.sha256"],
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            payload = manifest_verifier.verify_release_manifest(repo)
            if payload["ok"] or payload["untracked_required_surfaces"] != ["untracked.txt"]:
                raise AssertionError(f"untracked required surface was not reported: {payload}")
            if not payload["required_surface_tracking_checked"]:
                raise AssertionError(f"git worktree tracking check did not run: {payload}")
    finally:
        manifest_verifier.REQUIRED_V11_RELEASE_SURFACES = original_required


def main() -> int:
    _assert_replay_command_validation()
    _assert_v11_required_surfaces_tracking()

    with tempfile.TemporaryDirectory(prefix="manifest-verify-") as temp:
        root = Path(temp)

        good_root = _write_release(root / "good")
        good = _run(good_root, expect_ok=True)
        if not good["ok"] or good["missing_count"] != 0 or good["bad_hash_count"] != 0 or not good["manifest_sha256"]["matches"]:
            raise AssertionError(f"good manifest did not pass cleanly: {good}")

        good_zip = _run(_zip_release(good_root), expect_ok=True)
        if not good_zip["ok"] or good_zip["target_kind"] != "zip":
            raise AssertionError(f"good release zip did not pass cleanly: {good_zip}")

        bad_hash_root = _write_release(root / "bad-hash", file_sha="0" * 64)
        bad_hash = _run(bad_hash_root, expect_ok=False)
        if bad_hash["bad_hash_count"] != 1 or bad_hash["missing_count"] != 0:
            raise AssertionError(f"bad hash case did not report exactly one mismatch: {bad_hash}")

        missing_root = _write_release(root / "missing", include_file=False)
        missing = _run(missing_root, expect_ok=False)
        if missing["missing_count"] != 1 or missing["bad_hash_count"] != 0:
            raise AssertionError(f"missing file case did not report exactly one missing file: {missing}")

        manifest_sha_root = _write_release(root / "bad-sha", sha_override="f" * 64)
        manifest_sha = _run(manifest_sha_root, expect_ok=False)
        if manifest_sha["manifest_sha256"]["matches"] is not False:
            raise AssertionError(f"MANIFEST.sha256 mismatch was not detected: {manifest_sha}")

    print(json.dumps({"ok": True, "release_manifest_verifier": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
