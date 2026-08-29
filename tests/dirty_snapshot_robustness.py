from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan import cli


def git(repo: Path, *args: str) -> None:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise AssertionError(f"git {' '.join(args)} failed\n{completed.stderr}")


def assert_snapshot_row(row: dict[str, object], *, reason: str, classification: str, requires_hash: bool = True) -> None:
    if row.get("skipped_text_reason") != reason or row.get("classification") != classification:
        raise AssertionError(f"wrong snapshot classification for {row}")
    if "warnings" not in row or not isinstance(row["warnings"], list):
        raise AssertionError(f"snapshot row omitted warnings field: {row}")
    if requires_hash and not row.get("file_hash_after") and not row.get("file_hash_before"):
        raise AssertionError(f"snapshot row omitted file hash: {row}")


def assert_function_level_unreadable_capture(repo: Path, before_state: dict[str, object]) -> dict[str, object]:
    unreadable = repo / "unreadable.txt"
    unreadable.write_text("content is hashable but text capture fails\n", encoding="utf-8")
    original_read_text = Path.read_text

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "unreadable.txt":
            raise OSError("simulated unreadable text capture")
        return original_read_text(self, *args, **kwargs)

    try:
        Path.read_text = flaky_read_text  # type: ignore[method-assign]
        state = cli.build_snapshot_state(repo)
        patch = cli.build_worktree_patch(repo)
        _changed_files, _delta_patch, evidence = cli.compute_snapshot_delta(before_state, state)
    finally:
        Path.read_text = original_read_text  # type: ignore[method-assign]

    unreadable_state = state["files"].get("unreadable.txt")
    if not unreadable_state:
        raise AssertionError(f"unreadable path missing from snapshot state: {state}")
    if unreadable_state.get("classification") != "unreadable" or unreadable_state.get("skipped_text_reason") != "unreadable":
        raise AssertionError(f"unreadable path was not classified as skipped unreadable text: {unreadable_state}")
    if not unreadable_state.get("sha256") or not unreadable_state.get("warnings"):
        raise AssertionError(f"unreadable path missed hash/warnings: {unreadable_state}")
    if "unreadable.txt" in patch:
        raise AssertionError(f"unreadable untracked file leaked into text patch: {patch}")
    unreadable_rows = [row for row in evidence["files"] if row["path_new"] == "unreadable.txt"]
    if not unreadable_rows:
        raise AssertionError(f"unreadable path missing from delta evidence: {evidence}")
    assert_snapshot_row(unreadable_rows[0], reason="unreadable", classification="unreadable")
    if not unreadable_rows[0]["warnings"]:
        raise AssertionError(f"unreadable delta row missed warnings: {unreadable_rows[0]}")
    return unreadable_rows[0]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-dirty-snapshot-") as temp:
        repo = Path(temp)
        git(repo, "init")
        (repo / ".gitignore").write_text(".shujuan/\n", encoding="utf-8")
        (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        git(repo, "add", ".gitignore", "seed.txt")
        git(repo, "-c", "user.name=Snapshot", "-c", "user.email=snapshot@example.invalid", "commit", "-m", "seed")

        before_state = cli.build_snapshot_state(repo)
        (repo / "large.txt").write_text("x" * (cli.MAX_SNAPSHOT_TEXT_CAPTURE_BYTES + 128), encoding="utf-8")
        (repo / "binary.bin").write_bytes(b"\x00\x01binary evidence\xff")
        (repo / "non_utf8.txt").write_bytes(b"\xff\xfe\xfd")
        (repo / "scratch.tmp").write_text("temporary text\n", encoding="utf-8")

        after_state = cli.build_snapshot_state(repo)
        _changed_files, _patch, fingerprint_evidence = cli.compute_snapshot_delta(before_state, after_state)
        evidence = {str(row["path_new"] or row["path_old"]): row for row in fingerprint_evidence["files"]}
        assert_snapshot_row(evidence["large.txt"], reason="large_file", classification="large_file")
        assert_snapshot_row(evidence["binary.bin"], reason="binary", classification="binary")
        if evidence["binary.bin"].get("is_binary") is not True:
            raise AssertionError(f"binary snapshot row lost binary flag: {evidence['binary.bin']}")
        assert_snapshot_row(evidence["non_utf8.txt"], reason="non_utf8", classification="non_utf8")
        assert_snapshot_row(evidence["scratch.tmp"], reason="temporary_path", classification="temporary_path")
        unreadable_evidence = assert_function_level_unreadable_capture(repo, before_state)

        print(
            json.dumps(
                {
                    "ok": True,
                    "check_id": "check_7bc64ccc27004692",
                    "covered_paths": sorted(evidence),
                    "unreadable_simulation": unreadable_evidence,
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
