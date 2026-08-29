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


def run_text(repo: Path, *args: str) -> str:
    return run_cli_completed(repo, *args).stdout


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


def discussion_message_rows(repo: Path, segment_id: str) -> list[dict[str, object]]:
    conn = connect(repo)
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM discussion_messages
            WHERE segment_id = ?
            ORDER BY turn_index ASC, id ASC
            """,
            (segment_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def assert_report_modes(repo: Path, resolved_node_id: str) -> None:
    active_report = run_cli(repo, "report", "endpoint", "visibility", "--active-only")
    active_blob = json.dumps(active_report, sort_keys=True)
    if active_report["mode"] != "active_only":
        raise AssertionError(f"active-only report used wrong mode: {active_report}")
    if resolved_node_id in active_blob:
        raise AssertionError(f"active-only report leaked inactive historical item: {active_report}")
    if "historical_details" in active_report:
        raise AssertionError(f"active-only report included full historical details: {active_report}")
    if "closed_checks" in active_report["closure_state"] or "evidence" in active_report["closure_state"]:
        raise AssertionError(f"active-only closure state still carried historical lists: {active_report['closure_state']}")

    full_report = run_cli(repo, "report", "endpoint", "visibility", "--full")
    inactive_ids = {item["node_id"] for item in full_report["historical_details"]["inactive_semantic_items"]}
    if full_report["mode"] != "full" or resolved_node_id not in inactive_ids:
        raise AssertionError(f"full report did not expose inactive historical details: {full_report}")

    active_markdown = run_text(repo, "report", "endpoint", "visibility", "--active-only", "--markdown")
    full_markdown = run_text(repo, "report", "endpoint", "visibility", "--full", "--markdown")
    if "# Endpoint Active Report" not in active_markdown or resolved_node_id in active_markdown:
        raise AssertionError(f"active markdown did not stay focused on active obligations:\n{active_markdown}")
    if "# Endpoint Full Report" not in full_markdown or resolved_node_id not in full_markdown:
        raise AssertionError(f"full markdown did not include historical details:\n{full_markdown}")


def assert_discussion_provenance(repo: Path) -> None:
    transcript = repo / "transcript.jsonl"
    transcript.write_text(
        '{"actor":"user","content":"user provenance request"}\n'
        '{"actor":"assistant","content":"assistant provenance answer"}\n',
        encoding="utf-8",
    )
    imported = run_cli(
        repo,
        "session",
        "import",
        "--transcript",
        "transcript.jsonl",
        "--endpoint",
        "visibility",
        "--capture-discussion",
        "--session-id",
        "provenance-session",
        "--agent-name",
        "CodexAgent",
        "--model-name",
        "GPT-Provenance",
    )
    capture = imported["discussion_capture"]
    rows = discussion_message_rows(repo, str(capture["segment_id"]))
    if len(rows) != 2:
        raise AssertionError(f"discussion capture did not create two message rows: {rows}")
    source_message_ids = {message["message_id"] for message in imported["messages"]}
    for row in rows:
        for key, expected in {
            "session_id": "provenance-session",
            "agent_name": "CodexAgent",
            "model_name": "GPT-Provenance",
        }.items():
            if row.get(key) != expected:
                raise AssertionError(f"discussion message row missed first-class {key}: {row}")
        if row.get("source_message_id") not in source_message_ids or not row.get("source_node_id"):
            raise AssertionError(f"discussion message source provenance stayed metadata-only: {row}")

    detail = run_cli(repo, "graph", "detail", "--node", str(capture["segment_node_id"]))
    detail_messages = detail["discussion"]["messages"]
    if not detail_messages or any(message.get("session_id") != "provenance-session" for message in detail_messages):
        raise AssertionError(f"graph detail did not expose message provenance top-level: {detail}")
    if any(not message.get("source_message_id") or not message.get("source_node_id") for message in detail_messages):
        raise AssertionError(f"graph detail message provenance required metadata parsing: {detail_messages}")

    full_report = run_cli(repo, "report", "endpoint", "visibility", "--full")
    recent = full_report["historical_details"]["recent_discussions"]
    if not recent:
        raise AssertionError(f"full report missed recent discussion segment: {full_report}")
    segment = recent[0]
    for key, expected in {
        "session_id": "provenance-session",
        "agent_name": "CodexAgent",
        "model_name": "GPT-Provenance",
    }.items():
        if segment.get(key) != expected:
            raise AssertionError(f"full report discussion segment missed first-class {key}: {segment}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    with tempfile.TemporaryDirectory(prefix="shujuan-vf-report-discussion-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        try:
            init = run_cli(
                repo,
                "init",
                "--name",
                "vf-report-discussion",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

            (repo / "plan.md").write_text("# Visibility Plan\n\nReport and discussion visibility fixture.\n", encoding="utf-8")
            doc = run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan")
            scope = run_cli(repo, "scope", "create", "--body", "Visibility scope.", "--source-node", doc["document_node_id"])
            task = run_cli(repo, "task", "add", "--contract", scope["contract_id"], "--body", "Active visibility task.", "--from-node", doc["document_node_id"])
            run_cli(
                repo,
                "acceptance",
                "add",
                "--task",
                task["task_id"],
                "--body",
                "Active visibility check.",
                "--expected-evidence-type",
                "test_result",
                "--from-node",
                doc["document_node_id"],
            )
            endpoint = run_cli(repo, "endpoint", "create", "visibility", "--root-node", scope["node_id"])
            resolved = run_cli(
                repo,
                "unresolved",
                "add",
                "--body",
                "Resolved historical note belongs only in full report.",
                "--source-node",
                doc["document_node_id"],
                "--applies-to",
                endpoint["node_id"],
            )
            run_cli(
                repo,
                "semantic",
                "set-state",
                "--node",
                resolved["node_id"],
                "--state",
                "resolved",
                "--source-node",
                doc["document_node_id"],
                "--endpoint",
                "visibility",
            )
            run_cli(repo, "endpoint", "refresh", "visibility")

            assert_report_modes(repo, str(resolved["node_id"]))
            assert_discussion_provenance(repo)
        finally:
            if (repo / ".shujuan" / "postgres-dev" / "config.json").exists():
                run_cli_completed(repo, "postgres-dev", "stop", expect_ok=True)

    print(json.dumps({"ok": True}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
