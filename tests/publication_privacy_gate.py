from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_public_repository import audit_repository


def git(repo: Path, *args: str) -> None:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr}")


def commit(repo: Path, message: str, *, email: str = "tester@example.invalid") -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=Publication Tester", "-c", f"user.email={email}", "commit", "-m", message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-publication-gate-") as raw_temp:
        repo = Path(raw_temp)
        git(repo, "init", "-b", "main")
        (repo / "safe.txt").write_text(
            "public content\n"
            "docs/task-chain-review-material-with-a-long-safe-slug.md\n"
            "postgresql://{user}:{password}@127.0.0.1/example\n",
            encoding="utf-8",
        )
        commit(repo, "safe root")

        safe = audit_repository(repo, ref="main", require_no_remotes=True, require_clean=True)
        if not safe["ok"] or safe["findings"] or not safe["matched_values_redacted"]:
            raise AssertionError(f"safe repository failed publication gate: {safe}")

        token = "gh" + "p_" + ("A" * 36)
        (repo / "leak.txt").write_text(token + "\n", encoding="utf-8")
        commit(repo, "add leak fixture")
        leaked = audit_repository(repo, ref="main", require_no_remotes=True, require_clean=True)
        if leaked["ok"] or not any(item["rule"] == "github_token" for item in leaked["findings"]):
            raise AssertionError(f"strong token was not detected: {leaked}")
        if token in json.dumps(leaked):
            raise AssertionError("publication gate exposed a matched secret value")

        (repo / "leak.txt").unlink()
        (repo / ".env").write_text("SAFE_PLACEHOLDER=example\n", encoding="utf-8")
        commit(repo, "replace with forbidden path fixture")
        forbidden = audit_repository(repo, ref="main", require_no_remotes=True, require_clean=True)
        if not any(item["rule"] == "github_token" and item["path"] == "leak.txt" for item in forbidden["findings"]):
            raise AssertionError(f"deleted historical secret was not detected: {forbidden}")
        if not any(item["rule"] == "credential_file_path" for item in forbidden["findings"]):
            raise AssertionError(f"forbidden credential path was not detected: {forbidden}")

        git(repo, "remote", "add", "origin", "https://example.invalid/repository.git")
        remote = audit_repository(repo, ref="main", require_no_remotes=True)
        if not any(item["rule"] == "remote_present" for item in remote["findings"]):
            raise AssertionError(f"unexpected remote was not detected: {remote}")

        duplicate_repo = Path(raw_temp) / "duplicate-path-repo"
        duplicate_repo.mkdir()
        git(duplicate_repo, "init", "-b", "main")
        shared = "same public bytes\n"
        (duplicate_repo / "safe.txt").write_text(shared, encoding="utf-8")
        commit(duplicate_repo, "safe shared blob")
        (duplicate_repo / ".env").write_text(shared, encoding="utf-8")
        commit(duplicate_repo, "reuse shared blob under forbidden path")
        (duplicate_repo / ".env").unlink()
        commit(duplicate_repo, "remove forbidden historical path")
        duplicate = audit_repository(duplicate_repo, ref="main", require_no_remotes=True, require_clean=True)
        if not any(
            item["rule"] == "credential_file_path" and item["path"] == ".env"
            for item in duplicate["findings"]
        ):
            raise AssertionError(f"historical duplicate blob path was not detected: {duplicate}")

    print(json.dumps({"ok": True, "publication_privacy_gate": "passed", "matched_values_redacted": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
