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


def run_fails(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_cli(repo, *args, expect_ok=False)


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


def doctor_codes(payload: dict) -> set[str]:
    return {
        item["code"]
        for bucket in payload["severity_buckets"].values()
        for item in bucket
    }


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-control-gates-") as temp:
        repo = Path(temp)
        try:
            init = run(
                repo,
                "init",
                "--name",
                "control-gates",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

            (repo / "plan.md").write_text(
                "# Control Gates\n\n## Acceptance\n\nG0-G7 gate failures must surface as warnings, blockers, or hard failures.\n",
                encoding="utf-8",
            )
            doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            source_node = doc["document_node_id"]
            contract = run(repo, "scope", "create", "--body", "Control gate focused contract.", "--source-node", source_node)
            task = run(
                repo,
                "task",
                "add",
                "--contract",
                contract["contract_id"],
                "--body",
                "Control gate focused task.",
                "--from-node",
                source_node,
            )
            check_a = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task["task_id"],
                "--body",
                "Control gate check A.",
                "--expected-evidence-type",
                "test_result",
                "--from-node",
                source_node,
            )
            check_b = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task["task_id"],
                "--body",
                "Control gate check B.",
                "--expected-evidence-type",
                "test_result",
                "--from-node",
                source_node,
            )
            endpoint = "control-gates-endpoint"
            run(repo, "endpoint", "create", endpoint, "--description", "Control gates endpoint.", "--root-node", contract["node_id"])

            missing_source = run_fails(repo, "scope", "create", "--body", "G0 missing source promise input")
            if "--source-node" not in missing_source.stderr:
                raise AssertionError(f"G0 missing source was not a hard failure: {missing_source.stderr}")
            g0_audit = run(
                repo,
                "audit",
                "record",
                "--endpoint",
                endpoint,
                "--source-node",
                source_node,
                "--body",
                "G0 intake fidelity failure: named requirement missing from source promise ledger.",
                "--finding",
                "G0 intake fidelity failure must remain an active blocker until resolved/deferred.",
            )
            g1_audit = run(
                repo,
                "audit",
                "record",
                "--endpoint",
                endpoint,
                "--source-node",
                source_node,
                "--body",
                "G1 decomposition failure: hard predicate lacks task slice and acceptance check mapping.",
                "--finding",
                "G1 decomposition failure must remain an active blocker until mapped.",
                "--task",
                task["task_id"],
            )
            status = run(repo, "endpoint", "status", endpoint)
            active_finding_ids = {item["id"] for item in status.get("recent_audit_findings") or []}
            if not {g0_audit["audit_finding_node_ids"][0], g1_audit["audit_finding_node_ids"][0]} <= active_finding_ids:
                raise AssertionError(f"G0/G1 audit blockers were not surfaced: {status}")

            run(repo, "workflow", "begin", "--endpoint", endpoint, "--session-id", "gate-session", "--content", "Implement G6 evidence closeout with hard predicates.")
            full_start = run_fails(
                repo,
                "work",
                "start",
                "--mode",
                "full",
                "--endpoint",
                endpoint,
                "--task",
                task["task_id"],
                "--session-id",
                "gate-session",
                "--content",
                "Full P0 work without attention packet must fail.",
            )
            if "attention packet" not in full_start.stderr.lower():
                raise AssertionError(f"G2 full start did not fail on missing attention packet: {full_start.stderr}")
            standard_start = run(
                repo,
                "work",
                "start",
                "--mode",
                "standard",
                "--endpoint",
                endpoint,
                "--task",
                task["task_id"],
                "--session-id",
                "gate-session",
                "--content",
                "Standard work without attention packet should warn.",
            )
            standard_warning_codes = {item["code"] for item in standard_start["preflight"].get("warnings") or []}
            if "attention_packet_missing" not in standard_warning_codes:
                raise AssertionError(f"G2 standard start did not surface attention warning: {standard_start}")

            broad_evidence = run_fails(
                repo,
                "evidence",
                "test-result",
                "--check",
                check_a["acceptance_check_id"],
                "--check",
                check_b["acceptance_check_id"],
                "--close-check",
                "--from-node",
                source_node,
                "--",
                sys.executable,
                "-c",
                "print('broad evidence without predicate matrix')",
            )
            if "predicate_coverage_matrix" not in broad_evidence.stderr:
                raise AssertionError(f"G3 broad evidence did not require predicate matrix: {broad_evidence.stderr}")

            g4_audit = run(
                repo,
                "audit",
                "record",
                "--endpoint",
                endpoint,
                "--source-node",
                source_node,
                "--body",
                "G4 review failure: worker prose is not independent read-only review.",
                "--finding",
                "G4 independent review failure must block until source-bound reviewer output exists.",
                "--check",
                check_a["acceptance_check_id"],
            )
            g4_status = run(repo, "endpoint", "status", endpoint)
            if g4_audit["audit_finding_node_ids"][0] not in {item["id"] for item in g4_status.get("recent_audit_findings") or []}:
                raise AssertionError(f"G4 review blocker was not surfaced: {g4_status}")

            child_task = run(
                repo,
                "task",
                "add",
                "--contract",
                contract["contract_id"],
                "--parent",
                task["task_id"],
                "--body",
                "Child chain task for propagation gate.",
                "--from-node",
                source_node,
            )
            child_check = run(
                repo,
                "acceptance",
                "add",
                "--task",
                child_task["task_id"],
                "--body",
                "Child chain check targeted by umbrella finding.",
                "--expected-evidence-type",
                "artifact",
                "--from-node",
                source_node,
            )
            child_endpoint = "control-gates-child"
            run(repo, "endpoint", "create", child_endpoint, "--description", "Child task endpoint.", "--root-node", child_task["node_id"])
            run(repo, "endpoint", "link-child", "--parent", endpoint, "--child", child_endpoint)
            g5_audit = run(
                repo,
                "audit",
                "record",
                "--endpoint",
                endpoint,
                "--source-node",
                source_node,
                "--body",
                "G5 propagation failure targets child scope.",
                "--finding",
                "G5 propagation failure must block child endpoint.",
                "--check",
                child_check["acceptance_check_id"],
            )
            child_status = run(repo, "endpoint", "status", child_endpoint)
            if g5_audit["audit_finding_node_ids"][0] not in {item["id"] for item in child_status.get("inherited_active_blockers") or []}:
                raise AssertionError(f"G5 inherited blocker was not surfaced: {child_status}")

            empty_contract = run(repo, "scope", "create", "--body", "G6 empty closeout contract.", "--source-node", source_node)
            empty_endpoint = "g6-empty-endpoint"
            run(repo, "endpoint", "create", empty_endpoint, "--description", "No active obligations, but not projection-refreshed.", "--root-node", empty_contract["node_id"])
            empty_report = run(repo, "report", "endpoint", empty_endpoint, "--active-only")
            if empty_report["next_valid_entry_point"]["active_obligation_count"] != 0:
                raise AssertionError(f"G6 fixture unexpectedly had active obligations: {empty_report}")
            empty_doctor = run_fails(repo, "endpoint", "doctor", empty_endpoint, "--strict-closeout")
            if "closeout_reality_no_evidence" not in doctor_codes(json.loads(empty_doctor.stdout)):
                raise AssertionError(f"G6 strict doctor looked only at active count: {empty_doctor.stdout}")

            light_suggestion = run(repo, "mode", "suggest", "--mode", "light", "--intent", "P0 G6 evidence closeout with named technology")
            if "mode_friction_high_risk_light" not in {item["code"] for item in light_suggestion.get("warnings") or []}:
                raise AssertionError(f"G7 mode suggest did not warn on high-risk Light: {light_suggestion}")
            light_start = run_fails(
                repo,
                "work",
                "start",
                "--mode",
                "light",
                "--endpoint",
                endpoint,
                "--task",
                task["task_id"],
                "--session-id",
                "gate-session",
                "--content",
                "P0 evidence closeout should not silently downgrade to Light.",
                "--force",
            )
            if "high-risk work" not in light_start.stderr.lower():
                raise AssertionError(f"G7 light start did not hard fail: {light_start.stderr}")
            low_risk_full = run(repo, "mode", "suggest", "--mode", "full", "--intent", "small wording typo")
            if "mode_friction_low_risk_full" not in {item["code"] for item in low_risk_full.get("warnings") or []}:
                raise AssertionError(f"G7 low-risk Full warning missing: {low_risk_full}")

            strict_doctor = run_fails(repo, "endpoint", "doctor", endpoint, "--strict-closeout")
            strict_codes = doctor_codes(json.loads(strict_doctor.stdout))
            if not {"active_audit_findings", "open_obligations"} <= strict_codes:
                raise AssertionError(f"control endpoint strict doctor did not surface gate blockers: {strict_doctor.stdout}")

            print(
                json.dumps(
                    {
                        "ok": True,
                        "results": {
                            "G0_intake_failure_surfaced": True,
                            "G1_decomposition_failure_surfaced": True,
                            "G2_attention_packet_full_failure_standard_warning": True,
                            "G3_predicate_matrix_required": True,
                            "G4_independent_review_missing_blocks": True,
                            "G5_child_inherits_parent_blocker": True,
                            "G6_strict_closeout_not_active_count_only": True,
                            "G7_mode_friction_blocks_high_risk_light_warns_low_risk_full": True,
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")


if __name__ == "__main__":
    raise SystemExit(main())
