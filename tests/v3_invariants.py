from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


V3_INVARIANT_COMMANDS = [
    {
        "name": "v3_lifecycle_evidence_subagent_provider_contract",
        "covers": [
            "semantic lifecycle",
            "evidence lifecycle",
            "subagent handoff classification",
            "provider contract/default-off",
            "report active filtering",
        ],
        "command": [sys.executable, "tests/governance_invariants.py"],
    },
    {
        "name": "v3_structured_provider_contract_gitnexus_boundary",
        "covers": [
            "provider contract/default-off",
            "provider structured import",
            "provider facts cannot close checks",
        ],
        "command": [sys.executable, "tests/provider_contract_gitnexus.py"],
    },
    {
        "name": "v3_report_attention_layers",
        "covers": [
            "active-only endpoint report",
            "report attention layers",
            "lifecycle item history",
        ],
        "command": [sys.executable, "tests/report_attention_layers.py"],
    },
    {
        "name": "v3_postgres_live_cutover_new_project_readiness",
        "covers": [
            "PostgreSQL live/project-owned backend",
            "PostgreSQL cutover",
            "workflow begin/session/run chain",
            "new-project readiness gate",
            "active-only endpoint report",
        ],
        "command": [sys.executable, "tests/postgres_backend.py"],
    },
    {
        "name": "v3_postgres_write_constraints",
        "covers": [
            "PostgreSQL live/project-owned backend",
            "unified write API and DB constraints",
        ],
        "command": [sys.executable, "tests/postgres_constraints.py"],
    },
]


def run_named_invariant(item: dict[str, object]) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        item["command"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return {
        "name": item["name"],
        "covers": item["covers"],
        "command": item["command"],
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def main() -> int:
    results = [run_named_invariant(item) for item in V3_INVARIANT_COMMANDS]
    required_coverages = {
        "semantic lifecycle",
        "PostgreSQL live/project-owned backend",
        "evidence lifecycle",
        "subagent handoff classification",
        "provider contract/default-off",
        "provider structured import",
        "active-only endpoint report",
        "report attention layers",
        "lifecycle item history",
        "new-project readiness gate",
        "unified write API and DB constraints",
    }
    covered = {coverage for result in results for coverage in result["covers"]}
    missing = sorted(required_coverages - covered)
    ok = all(result["ok"] for result in results) and not missing
    print(json.dumps({"ok": ok, "missing_coverage": missing, "results": results}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
