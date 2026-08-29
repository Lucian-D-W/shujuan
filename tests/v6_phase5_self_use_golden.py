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


ENDPOINT = "v6-phase5-self-use"
PHASE_LABELS = [f"Phase {index}" for index in range(6)]
REPEATABLE_ENTRYPOINTS = {
    "activation_brief": "python tests\\v6_phase2_activation_brief.py",
    "delegate_capsule": "python tests\\v6_phase3_delegate_capsule.py",
    "closeout_dry_run": "python tests\\v6_phase4_closeout_gates.py",
    "postgres_runtime": "python tests\\postgres_runtime_ddl_repair.py",
    "broader_smoke": "python tests\\agcp11_cli_workflows.py",
    "governance_v4": "python tests\\v4_interaction_trust_layer.py",
    "legacy_smoke": "python tests\\smoke_shujuan.py",
}


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
        raise AssertionError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def run(repo: Path, *args: str) -> dict:
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


def check_closed_by(repo: Path, check_id: str) -> str | None:
    conn = connect(repo)
    try:
        row = conn.execute("SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?", (check_id,)).fetchone()
        return row["closed_by_node_id"] if row else None
    finally:
        conn.close()


def active_open_check_ids(repo: Path, endpoint: str) -> set[str]:
    report = run(repo, "report", "endpoint", endpoint, "--active-only")
    return {str(item["id"]) for item in report["active_obligations"].get("open_checks") or []}


def bootstrap_self_use_fixture(repo: Path) -> dict:
    init = run(
        repo,
        "init",
        "--name",
        "v6-phase5-self-use",
        "--postgres-dev",
        "--postgres-dev-port",
        str(free_port()),
    )
    if init["database"]["backend"] != "postgres":
        raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")
    if (repo / ".shujuan" / "shujuan.db").exists():
        raise AssertionError("init --postgres-dev created a SQLite runtime/write fallback")
    pg_status = run(repo, "postgres-dev", "status")
    if pg_status.get("state") != "ready" or not pg_status.get("running") or not pg_status.get("ready"):
        raise AssertionError(f"postgres-dev status did not report a ready PostgreSQL service: {pg_status}")
    migrate_status = run(repo, "migrate", "status")
    if migrate_status.get("backend") != "postgres" or migrate_status.get("schema_state") != "current":
        raise AssertionError(f"migrate status did not report current PostgreSQL schema: {migrate_status}")

    (repo / "plan.md").write_text(
        "# V6 Phase 5 Self-use\n\nPhase 0 -> Phase 5 ordering must be visible before closeout.\n",
        encoding="utf-8",
    )
    doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
    source_node = doc["document_node_id"]
    run(
        repo,
        "center",
        "update",
        "--body",
        "Phase 5 self-use fixture center: PostgreSQL-only, activation-first, worker material-only.",
        "--from-node",
        source_node,
    )
    scope = run(
        repo,
        "scope",
        "create",
        "--body",
        "V6 Phase 5 self-use regression closes only fixture-scoped checks with test_result evidence.",
        "--non-downgrade-rules",
        "Phase order visible; no SQLite runtime/write fallback; worker capsule cannot close current governance.",
        "--source-node",
        source_node,
    )
    run(repo, "endpoint", "create", ENDPOINT, "--description", "V6 Phase 5 self-use endpoint.", "--root-node", scope["node_id"])
    tasks = []
    checks = []
    for index, phase in enumerate(PHASE_LABELS):
        task = run(
            repo,
            "task",
            "add",
            "--contract",
            scope["contract_id"],
            "--body",
            f"{phase} prerequisite step for the V6 self-use golden chain.",
            "--from-node",
            source_node,
        )
        check = run(
            repo,
            "acceptance",
            "add",
            "--task",
            task["task_id"],
            "--body",
            f"{phase} check must close before downstream closeout is ready.",
            "--expected-evidence-type",
            "test_result",
            "--from-node",
            source_node,
        )
        tasks.append(task)
        checks.append(check)

    intake = run(
        repo,
        "work",
        "intake",
        "--endpoint",
        ENDPOINT,
        "--source-node",
        source_node,
        "--source-locator",
        "plan.md#V6 Phase 5 Self-use",
        "--promise-id",
        "SP-V6-PHASE5",
        "--text",
        "Phase 5 closeout requires a repeatable self-use golden chain.",
        "--predicate",
        "HP-V6-PHASE5::Phase 5 golden chain closes only after prerequisites and test_result evidence.",
        "--required-term",
        "HP-V6-PHASE5::Phase 5",
        "--mode",
        "standard",
    )
    run(
        repo,
        "work",
        "split",
        "--endpoint",
        ENDPOINT,
        "--name",
        "Phase 5 golden chain",
        "--chain-id",
        "WC-V6-PHASE5",
        "--task",
        tasks[-1]["task_id"],
        "--check",
        checks[-1]["acceptance_check_id"],
        "--predicate",
        intake["hard_predicates"][0]["id"],
        "--mode",
        "standard",
    )
    return {
        "source_node": source_node,
        "scope_node": scope["node_id"],
        "tasks": tasks,
        "checks": checks,
        "phase5_check_id": checks[-1]["acceptance_check_id"],
    }


def assert_activation_and_role_surfaces(repo: Path, fixture: dict) -> None:
    phase5_task = fixture["tasks"][-1]
    phase5_check = fixture["checks"][-1]
    brief = run(
        repo,
        "endpoint",
        "brief",
        ENDPOINT,
        "--role",
        "worker_agent",
        "--mode",
        "standard",
        "--task",
        phase5_task["task_id"],
        "--check",
        phase5_check["acceptance_check_id"],
        "--work-chain",
        "WC-V6-PHASE5",
    )
    activation = brief["activation"]
    if brief["activation_schema"] != "activation.v6" or activation["mode_capsule"]["mode"] != "standard":
        raise AssertionError(f"activation brief did not expose V6 standard mode: {brief}")
    role = activation["role_capsule"]
    if role["role"] != "worker_agent" or role["current_project_governance_write_authorized"] or role["can_close_checks_or_tasks"]:
        raise AssertionError(f"worker role capsule leaked governance authority: {role}")
    if "current_project_governance_write" not in role["forbidden_actions"]:
        raise AssertionError(f"worker role capsule missed governance-write prohibition: {role}")
    proof = activation["proof_capsule"]
    if "HP-V6-PHASE5" not in {item["id"] for item in proof["hard_predicates"]}:
        raise AssertionError(f"activation proof capsule missed Phase 5 hard predicate: {proof}")

    capsule = run(
        repo,
        "delegate",
        "capsule",
        "--role",
        "worker",
        "--endpoint",
        ENDPOINT,
        "--task",
        phase5_task["task_id"],
        "--check",
        phase5_check["acceptance_check_id"],
        "--hard-predicate",
        "Worker capsule must remain material-only.",
    )
    surface = capsule["capsule"]
    if surface["role_authority"]["db_write_authority"] or surface["role_authority"]["closeout_authority"]:
        raise AssertionError(f"delegate capsule leaked closure/write authority: {capsule}")
    if surface["governance_write_boundary"]["current_project_governance_write_allowed"]:
        raise AssertionError(f"delegate capsule allowed current-project governance writes: {capsule}")


def assert_prerequisites_gate_phase5(repo: Path, fixture: dict) -> None:
    all_check_ids = [check["acceptance_check_id"] for check in fixture["checks"]]
    before = active_open_check_ids(repo, ENDPOINT)
    if not set(all_check_ids) <= before:
        raise AssertionError(f"active surface did not show all phase prerequisites: {before}")
    dry_run = run(
        repo,
        "work",
        "close",
        "--dry-run",
        "--mode",
        "full",
        "--endpoint",
        ENDPOINT,
        "--check",
        fixture["phase5_check_id"],
        "--close-check",
        "--task",
        fixture["tasks"][-1]["task_id"],
        "--close-task",
    )
    matrix = dry_run["gate_matrix"]
    if "active_blockers_present" not in matrix["stop_reasons"] or not matrix["active_blockers"]:
        raise AssertionError(f"Phase 5 dry-run looked closure-ready while prerequisites were open: {matrix}")

    for index, check in enumerate(fixture["checks"][:-1]):
        evidence = run(
            repo,
            "evidence",
            "test-result",
            "--check",
            check["acceptance_check_id"],
            "--close-check",
            "--close-task",
            "--from-node",
            fixture["source_node"],
            "--",
            sys.executable,
            "-c",
            f"print('phase {index} prerequisite closed')",
        )
        if check_closed_by(repo, check["acceptance_check_id"]) != evidence["node_id"]:
            raise AssertionError(f"Phase {index} check did not close with test_result evidence: {evidence}")

    after_prereqs = active_open_check_ids(repo, ENDPOINT)
    if after_prereqs != {fixture["phase5_check_id"]}:
        raise AssertionError(f"active surface did not move forward to Phase 5 only: {after_prereqs}")


def close_phase5_and_verify(repo: Path, fixture: dict) -> None:
    matrix_path = repo / "phase5_predicate_matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "predicate_coverage_matrix": [
                    {
                        "check_id": fixture["phase5_check_id"],
                        "predicate_id": "HP-V6-PHASE5",
                        "assertion": "Phase 5 golden chain closes only after prerequisites and test_result evidence.",
                        "result": "pass",
                        "not_covered": False,
                        "reason": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    evidence = run(
        repo,
        "evidence",
        "test-result",
        "--predicate-coverage-matrix",
        "phase5_predicate_matrix.json",
        "--check",
        fixture["phase5_check_id"],
        "--close-check",
        "--close-task",
        "--from-node",
        fixture["source_node"],
        "--",
        sys.executable,
        "-c",
        "print('phase 5 golden verification passed')",
    )
    if check_closed_by(repo, fixture["phase5_check_id"]) != evidence["node_id"]:
        raise AssertionError(f"Phase 5 check did not close with test_result evidence: {evidence}")
    run(repo, "endpoint", "refresh", ENDPOINT)
    doctor = run(repo, "endpoint", "doctor", ENDPOINT, "--strict-closeout")
    if not doctor["ok"]:
        raise AssertionError(f"fixture endpoint was not strict-closeout ready after scoped closure: {doctor}")


def assert_repeatable_entrypoints(repo: Path) -> None:
    required_keys = {
        "activation_brief",
        "delegate_capsule",
        "closeout_dry_run",
        "postgres_runtime",
        "broader_smoke",
        "governance_v4",
    }
    if not required_keys <= set(REPEATABLE_ENTRYPOINTS):
        raise AssertionError(f"repeatable entrypoint names are incomplete: {REPEATABLE_ENTRYPOINTS}")
    active_brief = run(repo, "endpoint", "brief", ENDPOINT, "--role", "worker_agent", "--mode", "standard")
    delegate = run(repo, "delegate", "capsule", "--role", "worker", "--endpoint", ENDPOINT)
    closeout = run(repo, "work", "close", "--dry-run", "--mode", "full", "--endpoint", ENDPOINT)
    pg_status = run(repo, "postgres-dev", "status")
    migrate_status = run(repo, "migrate", "status")
    if active_brief["activation_schema"] != "activation.v6":
        raise AssertionError(f"activation brief entrypoint failed: {active_brief}")
    if delegate["controller_only_closeout"] is not True:
        raise AssertionError(f"delegate capsule entrypoint failed: {delegate}")
    if closeout["gate_matrix"]["version"] != "activation.v6.closeout_gate_matrix":
        raise AssertionError(f"closeout dry-run entrypoint failed: {closeout}")
    if pg_status.get("state") != "ready" or migrate_status.get("backend") != "postgres":
        raise AssertionError(f"PostgreSQL runtime entrypoint failed: {pg_status} {migrate_status}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-v6-phase5-golden-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        try:
            fixture = bootstrap_self_use_fixture(repo)
            postgres_started = True
            assert_activation_and_role_surfaces(repo, fixture)
            assert_prerequisites_gate_phase5(repo, fixture)
            close_phase5_and_verify(repo, fixture)
            assert_repeatable_entrypoints(repo)
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")
    print(
        json.dumps(
            {
                "ok": True,
                "v6_phase5_self_use_golden": "passed",
                "repeatable_entrypoints": REPEATABLE_ENTRYPOINTS,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
