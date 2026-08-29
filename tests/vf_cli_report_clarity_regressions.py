from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.cli import project_report_payload, render_project_report_markdown


def run_cli(repo: Path, *args: str, env_extra: dict[str, str] | None = None, expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
        env.pop(key, None)
    if env_extra:
        env.update(env_extra)
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


def assert_project_report_hash_fields_are_explicit() -> None:
    output = render_project_report_markdown(
        {
            "schema": {"backend": "postgres", "state": "current", "project_meta_versions": ["0.3.0"]},
            "center": {"body": "center"},
            "endpoints": [
                {
                    "name": "audit",
                    "root_node_id": "node_root",
                    "body_props": json.dumps({"source_kind": "projection"}),
                    "projection_hash": "current-hash",
                    "stored_projection_hash": "stored-hash",
                    "projection_hash_missing": False,
                    "projection_hash_mismatch": True,
                }
            ],
            "current_tasks": [],
            "open_checks": [],
            "acceptance_checks": [],
            "evidence": [],
            "risks_and_notes": [],
            "terms": [],
        }
    )
    required_fragments = [
        "projection_hash=current-hash",
        "stored_projection_hash=stored-hash",
        "projection_hash_missing=no",
        "projection_hash_mismatch=yes",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in output]
    if missing:
        raise AssertionError(f"project report omitted explicit hash state fields {missing}:\n{output}")
    if "stale_hash=" in output:
        raise AssertionError(f"project report still uses ambiguous stale_hash field:\n{output}")


def assert_project_report_hash_uses_chain_projection() -> None:
    names: list[tuple[str, bool]] = []

    class FakeConn:
        def execute(self, query: str, params: object = ()) -> "FakeCursor":
            if "FROM center_bodies" in query:
                return FakeCursor([{"body": "center"}])
            if "FROM endpoints" in query:
                return FakeCursor(
                    [
                        {
                            "name": "umbrella",
                            "node_id": "node_endpoint",
                            "root_node_id": "node_root",
                            "current_body_id": "endpoint_body",
                            "body_props": "{}",
                            "created_at": "2026-05-20T00:00:00+00:00",
                        }
                    ]
                )
            return FakeCursor([])

    class FakeCursor:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self.rows = rows

        def fetchone(self) -> dict[str, object] | None:
            return self.rows[0] if self.rows else None

        def fetchall(self) -> list[dict[str, object]]:
            return self.rows

    def fake_status(_conn: object, endpoint: str, *, include_chain: bool = True) -> dict[str, object]:
        names.append((endpoint, include_chain))
        return {"projection": {"projection_hash": "hash", "stored_projection_hash": "hash"}}

    original = project_report_payload.__globals__["endpoint_status_payload"]
    original_inspect = project_report_payload.__globals__["inspect_schema"]
    project_report_payload.__globals__["endpoint_status_payload"] = fake_status
    project_report_payload.__globals__["inspect_schema"] = lambda _conn: {"backend": "postgres", "state": "current"}
    try:
        project_report_payload(FakeConn())  # type: ignore[arg-type]
    finally:
        project_report_payload.__globals__["endpoint_status_payload"] = original
        project_report_payload.__globals__["inspect_schema"] = original_inspect
    if names != [("umbrella", True)]:
        raise AssertionError(f"project report did not use chain-aware endpoint projection hash: {names}")


def assert_unreachable_postgres_error_has_operator_hint() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-vf-cli-report-") as temp:
        repo = Path(temp)
        result = run_cli(
            repo,
            "migrate",
            "status",
            env_extra={"SHUJUAN_DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:1/shujuan?connect_timeout=1"},  # pragma: allowlist secret
            expect_ok=False,
        )
    diagnostic = f"{result.stdout}\n{result.stderr}"
    if "could not connect to PostgreSQL" in diagnostic:
        expected = ["python -m shujuan postgres-dev start", "SHUJUAN_DATABASE_URL"]
    elif "SQLite is disabled" in diagnostic:
        expected = ["python -m shujuan init --postgres-dev", "postgresql://"]
    else:
        raise AssertionError(f"PostgreSQL/runtime error omitted a recognizable operator hint:\n{diagnostic}")
    missing = [fragment for fragment in expected if fragment not in diagnostic]
    if missing:
        raise AssertionError(f"PostgreSQL/runtime error omitted clear hints {missing}:\n{diagnostic}")


def main() -> int:
    assert_project_report_hash_fields_are_explicit()
    assert_project_report_hash_uses_chain_projection()
    assert_unreachable_postgres_error_has_operator_hint()
    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
