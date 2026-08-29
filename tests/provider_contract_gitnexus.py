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
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def as_json(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(completed.stdout)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-provider-contract-") as temp:
        repo = Path(temp)
        postgres_started = False
        try:
            init_payload = as_json(run_cli(repo, "init", "--name", "provider-contract", "--postgres-dev", "--postgres-dev-port", str(free_port())))
            postgres_started = True
            if init_payload["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init_payload}")
            (repo / "plan.md").write_text("# Provider Plan\n\nProvider import scope.\n", encoding="utf-8")
            doc = as_json(run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan"))
            scope = as_json(run_cli(repo, "scope", "create", "--body", "Provider contract scope.", "--source-node", doc["document_node_id"]))
            task = as_json(run_cli(repo, "task", "add", "--body", "Provider mapped task.", "--contract", scope["contract_id"], "--from-node", doc["document_node_id"]))
            check = as_json(
                run_cli(
                    repo,
                    "acceptance",
                    "add",
                    "--task",
                    task["task_id"],
                    "--body",
                    "Provider facts cannot close this check.",
                    "--expected-evidence-type",
                    "test_result",
                    "--from-node",
                    doc["document_node_id"],
                )
            )
            as_json(run_cli(repo, "endpoint", "create", "provider", "--root-node", scope["node_id"]))
            payload = {
                "contract_version": "shujuan.impact_provider.v1",
                "provider": "gitnexus",
                "status": "executed",
                "command": ["gitnexus", "detect-changes", "--scope", "all", "--repo", "."],
                "entity_map": [{"external_id": "gitnexus:task", "node_id": task["node_id"], "confidence": 0.91}],
                "facts": [
                    {
                        "external_id": "gitnexus:task",
                        "fact_type": "impact",
                        "summary": "Mapped provider fact with structured provenance.",
                        "confidence": 0.8,
                        "provenance": {"index_path": ".gitnexus"},
                    },
                    {
                        "external_id": "gitnexus:unknown",
                        "fact_type": "impact",
                        "summary": "Unmapped provider fact must remain hypothesis.",
                        "confidence": 0.2,
                    },
                ],
                "warnings": [
                    {"summary": "Provider warning stays non-active by default.", "classification": "provider_hypothesis"},
                    {"summary": "Provider warning explicitly actionable.", "classification": "actionable"},
                ],
            }
            json_path = repo / "provider.json"
            json_path.write_text(json.dumps(payload), encoding="utf-8")
            imported = as_json(run_cli(repo, "provider", "import-json", "--endpoint", "provider", "--source-node", doc["document_node_id"], "--path", "provider.json"))
            if not imported["artifact"].get("sha256") or len(imported["facts"]) != 2 or len(imported["entity_maps"]) != 1:
                raise AssertionError(f"provider import lost artifact hashing, facts, or entity map: {imported}")
            unmapped = next(item for item in imported["facts"] if item["mapped_node_id"] is None)
            if unmapped["classification"] != "provider_hypothesis":
                raise AssertionError(f"unmapped provider fact did not stay provider_hypothesis: {imported}")
            close_attempt = run_cli(
                repo,
                "acceptance",
                "close",
                "--check",
                check["acceptance_check_id"],
                "--evidence-node",
                imported["facts"][0]["node_id"],
                expect_ok=False,
            )
            if "requires evidence node type" not in close_attempt.stderr:
                raise AssertionError(f"provider_fact was not clearly rejected as closure evidence: {close_attempt.stderr}")
            status = as_json(run_cli(repo, "endpoint", "status", "provider"))
            active_audit_ids = {item["id"] for item in status["recent_audit_findings"]}
            if imported["warnings"][1]["node_id"] not in active_audit_ids or imported["warnings"][0]["node_id"] in active_audit_ids:
                raise AssertionError(f"provider warnings did not respect actionable classification: {status}")
            stdout_only = repo / "stdout-only.json"
            stdout_only.write_text(json.dumps({"stdout": "format drift"}), encoding="utf-8")
            missing_structured = run_cli(repo, "provider", "import-json", "--path", "stdout-only.json", expect_ok=False)
            if "stdout text is not accepted" not in missing_structured.stderr:
                raise AssertionError(f"stdout-only provider shape was not rejected: {missing_structured.stderr}")
            not_json = repo / "not-json.txt"
            not_json.write_text("plain stdout text", encoding="utf-8")
            bad_json = run_cli(repo, "provider", "import-json", "--path", "not-json.txt", expect_ok=False)
            if "structured JSON could not be parsed" not in bad_json.stderr:
                raise AssertionError(f"non-JSON provider output was not rejected: {bad_json.stderr}")
            conn = connect(repo)
            try:
                counts = {}
                for table in ("provider_runs", "provider_artifacts", "provider_facts", "provider_entity_map"):
                    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
                    counts[table] = int(row["count"] if isinstance(row, dict) else row[0])
            finally:
                conn.close()
            if counts != {"provider_runs": 1, "provider_artifacts": 1, "provider_facts": 2, "provider_entity_map": 1}:
                raise AssertionError(f"provider structured rows not persisted correctly: {counts}")
            print(json.dumps({"ok": True, "provider_rows": counts}, indent=2, sort_keys=True))
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
