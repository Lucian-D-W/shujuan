from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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


def main() -> int:
    doc_path = ROOT / "docs" / "sqlite_legacy_residual_classification_2026-05-21.md"
    if doc_path.exists():
        doc = doc_path.read_text(encoding="utf-8")
        for phrase in (
            "Runtime/write fallback to SQLite remains disabled.",
            "`sqlite3` type annotations and adapter compatibility",
            "Command paths that silently initialize `.shujuan/shujuan.db`",
        ):
            if phrase not in doc:
                raise AssertionError(f"SQLite residual classification doc missing: {phrase}")
        doc_status = "checked"
    else:
        doc_status = "skipped_docs_omitted_runtime_package"
    with tempfile.TemporaryDirectory(prefix="shujuan-sqlite-residual-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        sqlite_url = run_cli(repo, "--database-url", "sqlite:///tmp/shujuan.db", "migrate", "status", expect_ok=False)
        if "SQLite database URLs are disabled" not in sqlite_url.stderr:
            raise AssertionError(f"sqlite URL did not fail closed: {sqlite_url.stderr}")
        sqlite_profile = run_cli(repo, "--db-profile", "sqlite", "migrate", "status", expect_ok=False)
        if "--db-profile sqlite is disabled" not in sqlite_profile.stderr:
            raise AssertionError(f"sqlite profile did not fail closed: {sqlite_profile.stderr}")
        cutover = run_cli(repo, "postgres-dev", "cutover", expect_ok=False)
        if "cutover from SQLite is disabled" not in cutover.stderr:
            raise AssertionError(f"legacy cutover did not fail closed: {cutover.stderr}")
        if (repo / ".shujuan" / "shujuan.db").exists():
            raise AssertionError("disabled SQLite cutover created a runtime SQLite DB")
    print(json.dumps({"ok": True, "sqlite_legacy_residual_classification": "passed", "doc_assertion": doc_status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
