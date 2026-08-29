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
    test_bin = repo / ".test-bin"
    if test_bin.exists():
        env["PATH"] = str(test_bin) + os.pathsep + env.get("PATH", "")
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


def git(repo: Path, *args: str) -> None:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise AssertionError(f"git {' '.join(args)} failed\n{completed.stderr}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def provider_rows(repo: Path) -> dict[str, int]:
    conn = connect(repo)
    try:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
            for table in ("provider_runs", "provider_artifacts", "provider_facts")
        }
    finally:
        conn.close()


def write_fake_provider(repo: Path) -> None:
    (repo / ".gitnexus").mkdir(parents=True, exist_ok=True)
    bin_dir = repo / ".test-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    provider = bin_dir / "fake_gitnexus.py"
    provider.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import sys",
                "import time",
                "",
                "Path('provider-invoked.txt').write_text(' '.join(sys.argv[1:]), encoding='utf-8')",
                "time.sleep(2)",
                "print('Changes: 1 file; Risk level: low')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        (bin_dir / "gitnexus.cmd").write_text(f'@echo off\r\n"{sys.executable}" "%~dp0fake_gitnexus.py" %*\r\n', encoding="utf-8")
    else:
        launcher = bin_dir / "gitnexus"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/fake_gitnexus.py" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)


def setup_repo(repo: Path) -> tuple[dict[str, object], dict[str, object]]:
    git(repo, "init")
    (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (repo / "plan.md").write_text("# Provider Boundary\n\nStop must capture changes without provider blocking.\n", encoding="utf-8")
    git(repo, "add", "app.py", "plan.md")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "seed")
    init_payload = as_json(
        run_cli(repo, "init", "--name", "provider-boundary", "--postgres-dev", "--postgres-dev-port", str(free_port()))
    )
    if init_payload["database"]["backend"] != "postgres":
        raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init_payload}")
    doc = as_json(run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan"))
    scope = as_json(run_cli(repo, "scope", "create", "--body", "Provider boundary scope.", "--source-node", str(doc["document_node_id"])))
    task = as_json(
        run_cli(
            repo,
            "task",
            "add",
            "--body",
            "Repair exec stop provider boundary.",
            "--contract",
            str(scope["contract_id"]),
            "--from-node",
            str(doc["document_node_id"]),
        )
    )
    check = as_json(
        run_cli(
            repo,
            "acceptance",
            "add",
            "--task",
            str(task["task_id"]),
            "--body",
            "Provider facts cannot directly close this check.",
            "--expected-evidence-type",
            "test_result",
            "--from-node",
            str(doc["document_node_id"]),
        )
    )
    as_json(run_cli(repo, "endpoint", "create", "provider-boundary", "--root-node", str(scope["node_id"])))
    return task, check


def start_run(repo: Path, task: dict[str, object], summary: str) -> None:
    as_json(run_cli(repo, "context", "load", "--task", "Repair exec stop provider boundary.", "--endpoint", "provider-boundary"))
    as_json(
        run_cli(
            repo,
            "exec",
            "start",
            "--endpoint",
            "provider-boundary",
            "--summary",
            summary,
            "--task-node",
            str(task["node_id"]),
            "--allow-preflight-warning",
            "--allow-reason",
            "isolated regression fixture does not import a conversation prompt",
        )
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-exec-stop-provider-") as temp:
        repo = Path(temp)
        postgres_started = False
        try:
            task, check = setup_repo(repo)
            postgres_started = True
            write_fake_provider(repo)

            start_run(repo, task, "default provider boundary run")
            (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
            stopped = as_json(
                run_cli(
                    repo,
                    "exec",
                    "stop",
                    "--endpoint",
                    "provider-boundary",
                    "--summary",
                    "Default stop skips provider.",
                )
            )
            impact = stopped["change_set"]["impact"]
            if impact["status"] != "skipped" or impact.get("reason") != "explicit_opt_in_required":
                raise AssertionError(f"default exec stop did not report explicit provider skip: {impact}")
            if impact.get("default_source") != "GitNexus direct CLI and global gitnexus-* skills":
                raise AssertionError(f"default exec stop did not lead with GitNexus source: {impact}")
            if impact.get("entrypoint_used") != "default_skipped_no_impact":
                raise AssertionError(f"default exec stop did not label skipped entrypoint clearly: {impact}")
            provider_detail = impact.get("provider_detail") or {}
            if provider_detail.get("name") != "gitnexus" or provider_detail.get("invoked"):
                raise AssertionError(f"default skipped impact misreported provider boundary: {impact}")
            closure_boundary = impact.get("closure_evidence_boundary") or {}
            if not closure_boundary.get("material_only") or not closure_boundary.get("cannot_close_checks"):
                raise AssertionError(f"default skipped impact omitted material-only closure boundary: {impact}")
            if closure_boundary.get("output_classification") != "provider_fact or provider_hypothesis":
                raise AssertionError(f"default skipped impact did not classify graph/provider material correctly: {impact}")
            if (repo / "provider-invoked.txt").exists():
                raise AssertionError("default exec stop invoked the optional GitNexus provider")
            if provider_rows(repo) != {"provider_runs": 0, "provider_artifacts": 0, "provider_facts": 0}:
                raise AssertionError(f"default exec stop wrote provider rows: {provider_rows(repo)}")
            changed_paths = {item["path_new"] or item["path_old"] for item in stopped["change_set"]["files"]}
            if "app.py" not in changed_paths:
                raise AssertionError(f"default exec stop failed to capture change_set files: {stopped['change_set']}")
            if stopped["endpoint_closeout"]["endpoint"] != "provider-boundary":
                raise AssertionError(f"default exec stop lost endpoint closeout: {stopped}")

            start_run(repo, task, "explicit provider boundary run")
            (repo / "slow.py").write_text("SLOW = True\n", encoding="utf-8")
            opt_in = as_json(
                run_cli(
                    repo,
                    "exec",
                    "stop",
                    "--endpoint",
                    "provider-boundary",
                    "--summary",
                    "Opt-in provider remains bounded material.",
                    "--impact",
                    "--impact-timeout",
                    "1",
                )
            )
            opt_in_impact = opt_in["change_set"]["impact"]
            if opt_in_impact["status"] != "failed":
                raise AssertionError(f"bounded opt-in provider timeout was not recorded as failed material: {opt_in_impact}")
            if opt_in_impact.get("default_source") != "GitNexus direct CLI and global gitnexus-* skills":
                raise AssertionError(f"opt-in provider material lost GitNexus source boundary: {opt_in_impact}")
            if opt_in_impact.get("entrypoint_used") != "gitnexus_cli_opt_in":
                raise AssertionError(f"opt-in provider material did not label GitNexus entrypoint: {opt_in_impact}")
            if not (opt_in_impact.get("provider_detail") or {}).get("invoked"):
                raise AssertionError(f"opt-in provider material did not mark GitNexus invocation: {opt_in_impact}")
            if not (opt_in_impact.get("closure_evidence_boundary") or {}).get("cannot_close_checks"):
                raise AssertionError(f"opt-in provider material omitted closure evidence boundary: {opt_in_impact}")
            if (opt_in_impact.get("closure_evidence_boundary") or {}).get("output_classification") != "provider_fact or provider_hypothesis":
                raise AssertionError(f"opt-in provider material did not classify provider output correctly: {opt_in_impact}")
            if not any("slow.py" in report["changed_files"] and report["exit_code"] == 124 for report in opt_in_impact["reports"]):
                raise AssertionError(f"provider timeout report was not bounded and traceable: {opt_in_impact}")
            if (
                not opt_in_impact["provider_material_nodes"]
                or {item["type"] for item in opt_in_impact["provider_material_nodes"]} != {"provider_fact"}
            ):
                raise AssertionError(f"optional provider output was not classified as provider facts: {opt_in_impact}")

            rows = provider_rows(repo)
            if rows["provider_runs"] != 1 or rows["provider_artifacts"] != 1 or rows["provider_facts"] < 1:
                raise AssertionError(f"opt-in provider material was not persisted in provider tables: {rows}")
            conn = connect(repo)
            try:
                fact = conn.execute(
                    """
                    SELECT n.id AS node_id, n.type, pf.classification
                    FROM provider_facts pf
                    JOIN nodes n ON n.id = pf.node_id
                    LIMIT 1
                    """
                ).fetchone()
                artifact_types = {
                    row["type"]
                    for row in conn.execute(
                        """
                        SELECT n.type
                        FROM provider_artifacts pa
                        JOIN nodes n ON n.id = pa.node_id
                        """
                    ).fetchall()
                }
            finally:
                conn.close()
            if fact is None or fact["type"] != "provider_fact" or fact["classification"] != "provider_hypothesis":
                raise AssertionError(f"provider fact row was not material-only hypothesis: {dict(fact) if fact else None}")
            if artifact_types != {"provider_artifact"}:
                raise AssertionError(f"provider artifacts used closure-capable node types: {artifact_types}")
            close_attempt = run_cli(
                repo,
                "acceptance",
                "close",
                "--check",
                str(check["acceptance_check_id"]),
                "--evidence-node",
                str(fact["node_id"]),
                expect_ok=False,
            )
            if "requires evidence node type" not in close_attempt.stderr:
                raise AssertionError(f"provider_fact was not rejected as closure evidence: {close_attempt.stderr}")
            print(json.dumps({"ok": True, "default_rows": {"provider_runs": 0}, "opt_in_rows": rows}, indent=2, sort_keys=True))
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
