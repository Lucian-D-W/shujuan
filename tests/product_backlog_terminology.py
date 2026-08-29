from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.store import connect


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
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
        raise AssertionError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def run(repo: Path, *args: str) -> dict[str, Any]:
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


def assert_no_ambiguous_backlog_label(value: Any, label: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "backlog":
                raise AssertionError(f"{label} exposed ambiguous backlog key")
            assert_no_ambiguous_backlog_label(child, label)
    elif isinstance(value, list):
        for child in value:
            assert_no_ambiguous_backlog_label(child, label)
    elif value == "backlog":
        raise AssertionError(f"{label} exposed ambiguous backlog value")


def find_inactive_item(report: dict[str, Any], node_id: str) -> dict[str, Any]:
    for item in report["status"]["semantic_projection"]["inactive"]:
        if item["node_id"] == node_id:
            return item
    raise AssertionError(f"inactive product_backlog item {node_id} missing from report: {report}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-product-backlog-") as temp:
        repo = Path(temp)
        try:
            run(
                repo,
                "init",
                "--name",
                "product-backlog-terminology",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            (repo / "plan.md").write_text(
                "# Product Backlog Terminology\n\n"
                "HP-PRODUCT-BACKLOG-TERM requires lifecycle, report, and workbench surfaces to use product_backlog.\n",
                encoding="utf-8",
            )
            doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            scope = run(repo, "scope", "create", "--body", "Terminology consistency scope.", "--source-node", doc["document_node_id"])
            endpoint = run(repo, "endpoint", "create", "product-term", "--root-node", scope["node_id"])
            imported = run(
                repo,
                "audit",
                "import-agent-output",
                "--endpoint",
                "product-term",
                "--source-node",
                doc["document_node_id"],
                "--classification",
                "product_backlog",
                "--body",
                "Future product-grade idea from subagent.",
            )
            note_id = imported["work_note"]["node_id"]
            artifact_id = imported["artifact_node_id"]
            if imported["classification"] != "product_backlog" or imported["work_note"]["semantic_item_id"] is None:
                raise AssertionError(f"CLI import did not preserve product_backlog classification: {imported}")
            if not imported["source_edges"]:
                raise AssertionError(f"CLI import did not keep source-backed artifact linkage: {imported}")

            conn = connect(repo)
            stored = conn.execute("SELECT current_state, source_node_id FROM semantic_items WHERE node_id = ?", (note_id,)).fetchone()
            if not stored or stored["current_state"] != "product_backlog" or stored["source_node_id"] != artifact_id:
                raise AssertionError(f"new product_backlog write was not source-backed/canonical: {stored}")
            conn.execute("UPDATE semantic_items SET current_state = 'backlog' WHERE node_id = ?", (note_id,))
            conn.execute("UPDATE semantic_lifecycle_events SET to_state = 'backlog' WHERE node_id = ?", (note_id,))
            conn.commit()
            conn.close()

            lifecycle = run(repo, "report", "lifecycle", "--item", note_id)
            if lifecycle["current_state"] != "product_backlog":
                raise AssertionError(f"lifecycle report did not canonicalize legacy state: {lifecycle}")
            if "product_backlog" not in lifecycle["partitions"] or "backlog" in lifecycle["partitions"]:
                raise AssertionError(f"lifecycle partitions did not use product_backlog: {lifecycle['partitions'].keys()}")
            if lifecycle["partitions"]["history"][0]["to_state"] != "product_backlog":
                raise AssertionError(f"lifecycle history did not canonicalize legacy state: {lifecycle}")
            assert_no_ambiguous_backlog_label(lifecycle, "report lifecycle JSON")

            lifecycle_markdown = run_cli(repo, "report", "lifecycle", "--item", note_id, "--markdown").stdout
            if "## backlog" in lifecycle_markdown or "state=backlog" in lifecycle_markdown:
                raise AssertionError(f"lifecycle markdown exposed ambiguous backlog label:\n{lifecycle_markdown}")
            if "product_backlog" not in lifecycle_markdown:
                raise AssertionError(f"lifecycle markdown omitted product_backlog:\n{lifecycle_markdown}")

            report = run(repo, "report", "endpoint", "product-term", "--full")
            inactive_item = find_inactive_item(report, note_id)
            if inactive_item["current_state"] != "product_backlog" or inactive_item["source_node_id"] != artifact_id:
                raise AssertionError(f"endpoint report did not expose source-backed product_backlog state: {inactive_item}")
            assert_no_ambiguous_backlog_label(report, "endpoint report JSON")

            context = run(repo, "context", "load", "--task", "active work only", "--endpoint", "product-term")
            context_ids = {item["id"] for item in context["semantic_context"]}
            if note_id in context_ids:
                raise AssertionError(f"product_backlog item appeared as active context: {context}")

            projection = run(repo, "graph", "projection", "--endpoint", "product-term", "--view", "audit", "--include-history")
            assert_no_ambiguous_backlog_label(projection, "projection JSON")
            projection_items = [
                item for item in projection["views"]["audit"]["items"]
                if item.get("node_id") == note_id
            ]
            if not projection_items or projection_items[0]["raw"]["current_state"] != "product_backlog":
                raise AssertionError(f"projection did not expose product_backlog history item: {projection}")
            if projection_items[0]["raw"]["source_node_id"] != artifact_id:
                raise AssertionError(f"projection did not preserve source-backed mapping: {projection_items[0]}")

            workbench_path = repo / "workbench.json"
            export = run(
                repo,
                "workbench",
                "export",
                "--endpoint",
                "product-term",
                "--format",
                "json",
                "--view",
                "audit",
                "--include-history",
                "--path",
                str(workbench_path),
            )
            if not workbench_path.exists():
                raise AssertionError(f"workbench export did not create JSON payload: {export}")
            workbench_payload = json.loads(workbench_path.read_text(encoding="utf-8"))
            assert_no_ambiguous_backlog_label(workbench_payload, "workbench JSON")
            workbench_items = [
                item for item in workbench_payload["views"]["audit"]["items"]
                if item.get("node_id") == note_id
            ]
            if not workbench_items or workbench_items[0]["raw"]["current_state"] != "product_backlog":
                raise AssertionError(f"workbench did not expose product_backlog history item: {workbench_payload}")
            if workbench_items[0]["raw"]["source_node_id"] != artifact_id:
                raise AssertionError(f"workbench did not preserve source-backed mapping: {workbench_items[0]}")

            print(json.dumps({"ok": True, "node_id": note_id, "endpoint_node_id": endpoint["node_id"]}, indent=2, sort_keys=True))
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
