from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import clean_env


def _run(repo: Path, *args: str, expect_ok: bool = True) -> tuple[int, str, str]:
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_env(),
    )
    if expect_ok and completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed.returncode, completed.stdout, completed.stderr


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="v10-structured-") as temp:
        repo = Path(temp)

        _code, stdout, stderr = _run(repo, "route", "guard", "--mode", "nonsense", "--intent", "hi", expect_ok=False)
        payload = json.loads(stdout)
        if payload["error"]["code"] != "invalid_mode":
            raise AssertionError(f"invalid mode was not normalized: {payload}")
        if stderr.strip():
            raise AssertionError(f"invalid mode leaked stderr text: {stderr}")

        _code, stdout, _stderr = _run(repo, "workflow", "begin", "--content", "hello", expect_ok=False)
        payload = json.loads(stdout)
        if payload["error"]["code"] != "missing_endpoint":
            raise AssertionError(f"workflow begin missing endpoint was not structured: {payload}")

        intent_file = repo / "intent.txt"
        intent_file.write_text("route", encoding="utf-8")
        _code, stdout, _stderr = _run(repo, "route", "guard", "--intent", "route", "--intent-file", str(intent_file), expect_ok=False)
        payload = json.loads(stdout)
        if payload["error"]["code"] != "mutually_exclusive_input":
            raise AssertionError(f"intent input conflict was not structured: {payload}")

        missing_artifact = repo / "missing.json"
        _code, stdout, _stderr = _run(repo, "plan-to-db", "import-task-chain", "--artifact", str(missing_artifact), "--endpoint", "ep", "--dry-run", expect_ok=False)
        payload = json.loads(stdout)
        if payload["error"]["code"] != "invalid_plan_to_db_artifact":
            raise AssertionError(f"missing import artifact was not structured: {payload}")

        _code, stdout, stderr = _run(repo, "workflow", "begin", "--endpoint", "ep", "--content", "hello", expect_ok=False)
        payload = json.loads(stdout)
        if payload["error"]["code"] != "postgres_runtime_unavailable":
            raise AssertionError(f"missing DB runtime was not structured: {payload}")
        if payload.get("read_only") is not True or "safe_next_action" not in payload:
            raise AssertionError(f"missing DB runtime payload was incomplete: {payload}")
        if stderr.strip():
            raise AssertionError(f"missing DB runtime leaked stderr text: {stderr}")

        env = clean_env()
        env["SHUJUAN_DATABASE_URL"] = "postgresql://bad:bad@127.0.0.1:1/bad"  # pragma: allowlist secret
        completed = subprocess.run(
            [sys.executable, "-m", "shujuan", "--repo", str(repo), "workflow", "begin", "--endpoint", "ep", "--content", "hello"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        if completed.returncode == 0:
            raise AssertionError("bad DB runtime unexpectedly passed")
        payload = json.loads(completed.stdout)
        if payload["error"]["code"] != "postgres_runtime_unavailable":
            raise AssertionError(f"bad DB runtime was not structured: {payload}")
        if payload.get("read_only") is not True or "safe_next_action" not in payload:
            raise AssertionError(f"bad DB runtime payload was incomplete: {payload}")
        if completed.stderr.strip():
            raise AssertionError(f"bad DB runtime leaked stderr text: {completed.stderr}")

        print(json.dumps({"ok": True, "v10_structured_errors": "passed"}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
