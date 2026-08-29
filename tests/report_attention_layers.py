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


def run_cli(repo: Path, *args: str) -> dict[str, object]:
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
    if completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    return json.loads(completed.stdout)


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


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-report-layers-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        try:
            init = run_cli(
                repo,
                "init",
                "--name",
                "report-layers",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

            (repo / "plan.md").write_text("# Report Plan\n\nActive-only report scope.\n", encoding="utf-8")
            doc = run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan")
            scope = run_cli(repo, "scope", "create", "--body", "Report attention scope.", "--source-node", doc["document_node_id"])
            task = run_cli(repo, "task", "add", "--body", "Open active task.", "--contract", scope["contract_id"], "--from-node", doc["document_node_id"])
            run_cli(repo, "acceptance", "add", "--task", task["task_id"], "--body", "Open active check.", "--expected-evidence-type", "test_result", "--from-node", doc["document_node_id"])
            endpoint = run_cli(repo, "endpoint", "create", "attention", "--root-node", scope["node_id"])
            timestamp = "2026-05-18T00:00:00+00:00"
            conn = connect(repo)
            try:
                node_rows = [
                    ("node_scoped_code_file", "file", "scoped_attention.py", "Scoped endpoint code object.", timestamp, timestamp, timestamp, "{}"),
                    ("node_global_code_file", "file", "global_attention_noise.py", "Global code object should be fallback only.", timestamp, timestamp, timestamp, "{}"),
                    ("node_scoped_change", "change_set", "scoped change", "Scoped change set.", timestamp, timestamp, timestamp, "{}"),
                    ("node_scoped_run", "agent_run", "scoped run", "Scoped run.", timestamp, timestamp, timestamp, "{}"),
                ]
                for row in node_rows:
                    conn.execute(
                        "INSERT INTO nodes (id, type, label, summary, created_at, updated_at, valid_from, props) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        row,
                    )
                conn.execute(
                    "INSERT INTO agent_runs (id, node_id, started_at, metadata) VALUES (?, ?, ?, ?)",
                    ("run_scoped", "node_scoped_run", timestamp, "{}"),
                )
                conn.execute(
                    "INSERT INTO change_sets (id, node_id, run_id, patch_hash, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                    ("change_scoped", "node_scoped_change", "run_scoped", "hash", timestamp, "{}"),
                )
                code_rows = [
                    ("code_scoped", "node_scoped_code_file", "scoped_attention.py", "hash_scoped", "commit"),
                    ("code_global", "node_global_code_file", "global_attention_noise.py", "hash_global", "commit"),
                ]
                for row in code_rows:
                    conn.execute(
                        "INSERT INTO code_objects (id, node_id, type, path, language, content_hash, last_seen_commit) VALUES (?, ?, 'file', ?, 'python', ?, ?)",
                        row,
                    )
                conn.execute(
                    "INSERT INTO change_code_links (id, change_set_id, code_object_id, relation_type, confidence) VALUES (?, ?, ?, ?, ?)",
                    ("link_scoped_code", "change_scoped", "code_scoped", "modifies", 1.0),
                )
                conn.execute(
                    "INSERT INTO edges (id, from_node_id, type, to_node_id, reason, confidence, created_by, created_at, props) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("edge_scoped_change_task", "node_scoped_change", "IMPLEMENTS", task["node_id"], "Scoped context ranking fixture.", 1.0, "test", timestamp, "{}"),
                )
                conn.commit()
            finally:
                conn.close()
            active = run_cli(
                repo,
                "jot",
                "add",
                "--endpoint",
                "attention",
                "--kind",
                "needs_user_decision",
                "--body",
                "Active decision stays in active-only.",
                "--source-node",
                doc["document_node_id"],
            )
            resolved = run_cli(
                repo,
                "unresolved",
                "add",
                "--body",
                "Resolved history must not distract active workbench.",
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
                "attention",
            )
            run_cli(repo, "endpoint", "refresh", "attention")
            active_report = run_cli(repo, "report", "endpoint", "attention", "--active-only")
            active_blob = json.dumps(active_report, sort_keys=True)
            if active["node_id"] not in active_blob or resolved["node_id"] in active_blob:
                raise AssertionError(f"active-only endpoint report mixed active and resolved history: {active_report}")
            full_report = run_cli(repo, "report", "endpoint", "attention", "--full")
            inactive_ids = {item["node_id"] for item in full_report["status"]["semantic_projection"]["inactive"]}
            if resolved["node_id"] not in inactive_ids:
                raise AssertionError(f"full endpoint report did not preserve resolved history: {full_report}")
            overview = run_cli(repo, "report", "project", "--overview")
            full_project = run_cli(repo, "report", "project", "--full")
            if "entry_policy" not in overview or "risks_and_notes" in overview:
                raise AssertionError(f"project overview did not stay overview-only: {overview}")
            if "risks_and_notes" not in full_project:
                raise AssertionError(f"project full report did not include full attention layer: {full_project}")
            lifecycle = run_cli(repo, "report", "lifecycle", "--item", resolved["node_id"])
            if lifecycle["current_state"] != "resolved" or not lifecycle["partitions"]["history"]:
                raise AssertionError(f"lifecycle report did not include state partitions/history: {lifecycle}")
            context = run_cli(repo, "context", "load", "--task", "resume from active-only report", "--endpoint", "attention")
            if context["ranked_context"][0]["kind"] != "endpoint_active_report":
                raise AssertionError(f"context default did not prioritize endpoint active-only report: {context['ranked_context'][:3]}")
            scoped_context = run_cli(repo, "context", "load", "--task", "scoped_attention", "--endpoint", "attention")
            scoped_paths = {item.get("path") for item in scoped_context["ranked_context"] if item.get("kind") == "code_file"}
            if "scoped_attention.py" not in scoped_paths or "global_attention_noise.py" in scoped_paths:
                raise AssertionError(f"context load did not prefer endpoint-scoped code candidates before global fallback: {scoped_context['ranked_context']}")
            scoped_items = [item for item in scoped_context["ranked_context"] if item.get("path") == "scoped_attention.py"]
            if not scoped_items or scoped_items[0].get("context_scope") != "endpoint_change_set":
                raise AssertionError(f"scoped code candidate did not carry scoped provenance: {scoped_context['ranked_context']}")
            context_blob = json.dumps(context["active_endpoint_report"], sort_keys=True)
            if active["node_id"] not in context_blob or resolved["node_id"] in context_blob:
                raise AssertionError(f"context active endpoint report leaked resolved history: {context['active_endpoint_report']}")
            print(json.dumps({"ok": True, "active_node": active["node_id"], "resolved_node": resolved["node_id"]}, indent=2, sort_keys=True))
            return 0
        finally:
            if postgres_started or (repo / ".shujuan" / "postgres-dev" / "config.json").exists():
                run_cli(repo, "postgres-dev", "stop")


if __name__ == "__main__":
    raise SystemExit(main())
