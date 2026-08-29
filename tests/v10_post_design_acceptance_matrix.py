from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


COMMANDS = [
    [sys.executable, "tests/v10_route_relation_regressions.py"],
    [sys.executable, "tests/v10_task_chain_source_coverage.py"],
    [sys.executable, "tests/plan_to_db_task_chain_hygiene.py"],
    [sys.executable, "tests/v7_p0_10_route_guidance.py"],
    [sys.executable, "tests/v10_codex_surfaces.py"],
    [sys.executable, "tests/packaging_install.py"],
    [sys.executable, "tests/v7_p0_09_ownership_manifest.py"],
    [sys.executable, "-m", "compileall", "-q", "shujuan"],
]


def main() -> int:
    results = []
    for command in COMMANDS:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        results.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-1000:],
                "stderr": completed.stderr[-1000:],
            }
        )
        if completed.returncode:
            print(json.dumps({"ok": False, "failed": command, "results": results}, ensure_ascii=False))
            return completed.returncode
    print(json.dumps({"ok": True, "v10_post_design_acceptance_matrix": "passed", "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
