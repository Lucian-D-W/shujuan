from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.store import open_db_raw


GOVERNANCE_TABLES = [
    "nodes",
    "edges",
    "source_documents",
    "document_sections",
    "scope_contracts",
    "endpoints",
    "endpoint_bodies",
    "terms",
    "semantic_items",
    "semantic_lifecycle_events",
    "tasks",
    "acceptance_checks",
    "evidence_records",
    "agent_runs",
    "change_sets",
]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(ROOT), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    return completed


def db_counts() -> dict[str, int]:
    conn = open_db_raw(ROOT)
    if conn is None:
        raise AssertionError("current repo shujuan database is not configured")
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        counts: dict[str, int] = {}
        for table in GOVERNANCE_TABLES:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            counts[table] = int(row["count"])
        return counts
    finally:
        conn.close()


def main() -> int:
    before = db_counts()
    payload = json.loads(run_cli("report", "v6-phase0").stdout)
    after = db_counts()

    if before != after:
        changed = {key: (before[key], after[key]) for key in before if before[key] != after[key]}
        raise AssertionError(f"read-only V6 Phase 0 verifier changed DB counts: {changed}")
    if not payload.get("ok") or not payload.get("read_only") or payload.get("db_writes") != 0:
        raise AssertionError(f"V6 Phase 0 verifier did not report a clean read-only pass: {payload}")
    failed = [item for item in payload.get("assertions", []) if not item.get("passed")]
    if failed:
        raise AssertionError(f"V6 Phase 0 verifier failed assertions: {failed}")

    assertion_names = {item["name"] for item in payload["assertions"]}
    required_assertions = {
        "v6_source_document_imported",
        "source_backed_scope_contract",
        "endpoint_root_binding",
        "v6_term_nodes_source_backed",
        "old_endpoint_relationship_notes_and_links",
        "p1_defer_record_queryable",
        "p2_product_backlog_record_queryable",
        "center_stage_and_non_goals_source_backed",
    }
    missing = sorted(required_assertions - assertion_names)
    if missing:
        raise AssertionError(f"V6 Phase 0 verifier omitted assertions: {missing}")

    markdown = run_cli("report", "v6-phase0", "--markdown").stdout
    if "# V6 Phase 0 Verification" not in markdown or "P2 product_backlog" not in markdown:
        raise AssertionError(f"markdown artifact surface is missing expected sections:\n{markdown}")

    print(json.dumps({"ok": True, "v6_phase0_activation_consolidation": "passed", "db_writes": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
