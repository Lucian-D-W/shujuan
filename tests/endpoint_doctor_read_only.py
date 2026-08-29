from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.store import connect


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
        raise AssertionError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def run(repo: Path, *args: str) -> dict:
    return json.loads(run_cli(repo, *args).stdout)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def has_postgres_bins() -> bool:
    candidates = []
    env_bin = os.environ.get("SHUJUAN_POSTGRES_BIN")
    if env_bin:
        candidates.append(Path(env_bin))
    candidates.append(Path(r"C:\Program Files\PostgreSQL\17\bin"))
    return any((path / "initdb.exe").exists() or (path / "initdb").exists() for path in candidates)


def endpoint_body_state(repo: Path, endpoint: str) -> dict[str, object]:
    conn = connect(repo)
    try:
        row = conn.execute("SELECT id, current_body_id FROM endpoints WHERE name = ?", (endpoint,)).fetchone()
        if not row:
            raise AssertionError(f"endpoint missing: {endpoint}")
        body_count = conn.execute(
            "SELECT COUNT(*) AS count FROM endpoint_bodies WHERE endpoint_id = ?",
            (row["id"],),
        ).fetchone()
        return {
            "current_body_id": row["current_body_id"],
            "body_count": int(body_count["count"]),
        }
    finally:
        conn.close()


def doctor_codes(payload: dict) -> set[str]:
    return {
        item["code"]
        for bucket in payload["severity_buckets"].values()
        for item in bucket
    }


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    endpoint = "doctor-read-only"
    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-doctor-read-only-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        try:
            init = run(
                repo,
                "init",
                "--name",
                "doctor-read-only",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

            (repo / "plan.md").write_text(
                "# Doctor Read Only\n\nStrict read-only doctor must not refresh endpoint bodies.\n",
                encoding="utf-8",
            )
            doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            contract = run(repo, "scope", "create", "--body", "Doctor read-only contract.", "--source-node", doc["document_node_id"])
            run(repo, "endpoint", "create", endpoint, "--description", "Read-only doctor fixture.", "--root-node", contract["node_id"])

            before = endpoint_body_state(repo, endpoint)
            read_only = run(repo, "endpoint", "doctor", endpoint, "--strict-closeout", "--read-only", "--allow-fail")
            after_read_only = endpoint_body_state(repo, endpoint)
            if after_read_only != before:
                raise AssertionError(f"read-only doctor mutated endpoint body state: before={before}, after={after_read_only}")
            if not read_only.get("read_only") or read_only.get("refresh_policy") != "suppressed_by_read_only":
                raise AssertionError(f"read-only doctor did not report suppressed refresh policy: {read_only}")
            if read_only.get("endpoint_refresh"):
                raise AssertionError(f"read-only doctor unexpectedly returned endpoint_refresh: {read_only}")
            if "closeout_reality_no_evidence" not in doctor_codes(read_only):
                raise AssertionError(f"read-only strict diagnostics did not preserve closeout blockers: {read_only}")

            strict = run(repo, "endpoint", "doctor", endpoint, "--strict-closeout", "--allow-fail")
            after_strict = endpoint_body_state(repo, endpoint)
            if not strict.get("endpoint_refresh") or strict.get("refresh_policy") != "strict_closeout_refresh":
                raise AssertionError(f"strict doctor did not refresh explicitly: {strict}")
            if not after_strict["current_body_id"] or after_strict["current_body_id"] == before["current_body_id"]:
                raise AssertionError(f"strict doctor did not install a current endpoint body: before={before}, after={after_strict}")
            if after_strict["body_count"] <= before["body_count"]:
                raise AssertionError(f"strict doctor did not create an endpoint body row: before={before}, after={after_strict}")
            if strict["projection"]["source_kind"] != "projection" or strict["projection"].get("stale"):
                raise AssertionError(f"strict doctor did not diagnose against a refreshed projection: {strict['projection']}")

            print(json.dumps({"ok": True, "endpoint_doctor_read_only": "passed", "fixture_writes": "temporary postgres-dev repo only"}))
            return 0
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")


if __name__ == "__main__":
    raise SystemExit(main())
