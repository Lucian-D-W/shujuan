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

from shujuan.cli import props_dict, render_workbench_html
from shujuan.store import connect


def run_cli_completed(repo: Path, *args: str, expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
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


def run_cli(repo: Path, *args: str) -> dict[str, object]:
    return json.loads(run_cli_completed(repo, *args).stdout)


def run_cli_fails(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_cli_completed(repo, *args, expect_ok=False)


def run_git(repo: Path, *args: str) -> None:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise AssertionError(f"git failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")


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


def db_counts(repo: Path) -> dict[str, int]:
    conn = connect(repo)
    try:
        tables = ["conversation_sessions", "messages", "nodes", "activation_logs"]
        counts: dict[str, int] = {}
        for table in tables:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            counts[table] = int(row["count"])
        return counts
    finally:
        conn.close()


def lifecycle_events(repo: Path, segment_id: str) -> list[dict[str, object]]:
    conn = connect(repo)
    try:
        rows = conn.execute(
            """
            SELECT event_type, from_status, to_status
            FROM discussion_lifecycle_events
            WHERE segment_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (segment_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def assert_props_dict_preserves_native_dict() -> None:
    nested = {"props": {"kind": "native", "nested": {"ok": True}}}
    if props_dict(nested) != nested["props"]:
        raise AssertionError(f"props_dict dropped native props dict: {props_dict(nested)}")
    plain = {"kind": "plain", "nested": {"ok": True}}
    if props_dict(plain) != plain:
        raise AssertionError(f"props_dict dropped plain native dict: {props_dict(plain)}")


def assert_workbench_layout_is_used() -> None:
    html = render_workbench_html({"endpoint": "fixture", "views": {}, "detail_payloads": {}}, layout="force")
    if 'const requestedLayout = "force";' not in html:
        raise AssertionError("render_workbench_html did not embed the requested layout")
    if "layout: layoutOptions(requestedLayout, data, size)" not in html:
        raise AssertionError("render_workbench_html still bypasses the layout option")
    if "layout: {\n          type: 'grid'" in html:
        raise AssertionError("render_workbench_html still hardcodes the G6 grid layout inline")


def assert_workflow_begin_mode_contract(repo: Path) -> None:
    before = db_counts(repo)
    no_gov = run_cli(repo, "workflow", "begin", "--mode", "no-governance", "--content", "answer without capture")
    after = db_counts(repo)
    if before != after:
        raise AssertionError(f"workflow begin --mode no-governance wrote DB rows: before={before} after={after}")
    if no_gov["db_writes"] != 0 or no_gov["capture_claim"] or no_gov["context"] is not None:
        raise AssertionError(f"workflow begin no-governance returned an unsafe contract: {no_gov}")

    missing_endpoint = run_cli_fails(repo, "workflow", "begin", "--content", "standard work without endpoint")
    if "--endpoint" not in missing_endpoint.stderr or "no-governance" not in missing_endpoint.stderr:
        raise AssertionError(f"workflow begin without endpoint failed unclearly: {missing_endpoint.stderr}")


def assert_discuss_inbox_records_lifecycle(repo: Path) -> None:
    capture = run_cli(repo, "discuss", "capture", "--endpoint", "audit", "--content", "review this segment")
    before = lifecycle_events(repo, str(capture["segment_id"]))
    inbox = run_cli(repo, "discuss", "inbox", "--endpoint", "audit", "--mark-reviewed")
    after = lifecycle_events(repo, str(capture["segment_id"]))
    if len(after) != len(before) + 1:
        raise AssertionError(f"discuss inbox did not add a lifecycle event: before={before} after={after} inbox={inbox}")
    if after[-1] != {"event_type": "discussion_review", "from_status": "unreviewed", "to_status": "reviewed"}:
        raise AssertionError(f"discussion review lifecycle event had the wrong shape: {after[-1]}")
    if str(capture["segment_id"]) not in inbox["marked_reviewed"] or not inbox["lifecycle_events"]:
        raise AssertionError(f"discuss inbox did not report reviewed segment/lifecycle ids: {inbox}")


def assert_current_work_clears_only_matching_run(repo: Path) -> None:
    run_cli(repo, "workflow", "begin", "--session-id", "work-session-a", "--endpoint", "audit", "--content", "start work a")
    started_a = run_cli(
        repo,
        "work",
        "start",
        "--mode",
        "standard",
        "--endpoint",
        "audit",
        "--session-id",
        "work-session-a",
        "--allow-preflight-warning",
        "--allow-reason",
        "Regression fixture allows rootless endpoint preflight warnings.",
    )
    current_work = repo / ".shujuan" / "current_work.json"
    current_work.write_text(json.dumps({"run_id": "run_other", "endpoint": "audit"}), encoding="utf-8")
    (repo / "fixture.txt").write_text("changed a\n", encoding="utf-8")
    stopped_a = run_cli(repo, "exec", "stop", "--endpoint", "audit", "--run", str(started_a["run_id"]), "--summary", "stop a")
    if stopped_a["current_work_cleared"]:
        raise AssertionError(f"exec stop cleared a non-matching current_work handle: {stopped_a}")
    if not current_work.exists():
        raise AssertionError("exec stop removed current_work.json for a different run")

    run_cli(repo, "workflow", "begin", "--session-id", "work-session-b", "--endpoint", "audit", "--content", "start work b")
    started_b = run_cli(
        repo,
        "work",
        "start",
        "--mode",
        "standard",
        "--endpoint",
        "audit",
        "--session-id",
        "work-session-b",
        "--allow-preflight-warning",
        "--allow-reason",
        "Regression fixture allows rootless endpoint preflight warnings.",
    )
    if json.loads(current_work.read_text(encoding="utf-8"))["run_id"] != started_b["run_id"]:
        raise AssertionError("work start did not write current_work for the active run")
    (repo / "fixture.txt").write_text("changed b\n", encoding="utf-8")
    stopped_b = run_cli(repo, "exec", "stop", "--endpoint", "audit", "--run", str(started_b["run_id"]), "--summary", "stop b")
    if not stopped_b["current_work_cleared"] or current_work.exists():
        raise AssertionError(f"exec stop did not clear matching current_work handle: {stopped_b}")


def main() -> int:
    assert_props_dict_preserves_native_dict()
    assert_workbench_layout_is_used()
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    with tempfile.TemporaryDirectory(prefix="shujuan-vf-first-batch-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        run_git(repo, "init")
        run_git(repo, "config", "user.email", "test@example.invalid")
        run_git(repo, "config", "user.name", "Test User")
        (repo / "fixture.txt").write_text("base\n", encoding="utf-8")
        run_git(repo, "add", "fixture.txt")
        run_git(repo, "commit", "-m", "base fixture")
        run_cli(repo, "init", "--name", "vf-first-batch", "--postgres-dev", "--postgres-dev-port", str(free_port()))
        run_cli(repo, "endpoint", "create", "audit", "--rootless", "--reason", "Regression fixture endpoint.")
        assert_workflow_begin_mode_contract(repo)
        assert_discuss_inbox_records_lifecycle(repo)
        assert_current_work_clears_only_matching_run(repo)
    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
