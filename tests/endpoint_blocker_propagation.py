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


def assert_inherited(status: dict, finding_node_id: str, target_node_id: str) -> None:
    inherited = status.get("inherited_active_blockers") or []
    if finding_node_id not in {item["id"] for item in inherited}:
        raise AssertionError(f"child endpoint did not expose inherited blocker: {status}")
    blocker = next(item for item in inherited if item["id"] == finding_node_id)
    if target_node_id not in set(blocker.get("inherited_target_node_ids") or []):
        raise AssertionError(f"inherited blocker did not cite child target node {target_node_id}: {blocker}")
    recent = status.get("recent_audit_findings") or []
    recent_blocker = next((item for item in recent if item["id"] == finding_node_id), None)
    if not recent_blocker or recent_blocker.get("inherited_active_blocker") is not True:
        raise AssertionError(f"recent audit findings did not mark inherited blocker: {recent}")


def assert_not_inherited(status: dict, finding_node_id: str) -> None:
    inherited_ids = {item["id"] for item in status.get("inherited_active_blockers") or []}
    recent_ids = {item["id"] for item in status.get("recent_audit_findings") or []}
    if finding_node_id in inherited_ids or finding_node_id in recent_ids:
        raise AssertionError(f"inactive finding still blocks child endpoint: {status}")


def assert_locally_clean(status: dict) -> None:
    if status.get("current_tasks") or status.get("open_checks") or status.get("unresolved"):
        raise AssertionError(f"child endpoint is not locally clean: {status}")
    direct_findings = [
        item
        for item in status.get("recent_audit_findings") or []
        if not item.get("inherited_active_blocker")
    ]
    if direct_findings:
        raise AssertionError(f"child endpoint has direct audit findings: {direct_findings}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-endpoint-propagation-") as temp:
        repo = Path(temp)
        try:
            init = run(
                repo,
                "init",
                "--name",
                "endpoint-blocker-propagation",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

            (repo / "plan.md").write_text(
                "# Endpoint Propagation\n\n## Acceptance\n\nUmbrella findings must block child chain endpoints when they target child scope.\n",
                encoding="utf-8",
            )
            doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            source_node = doc["document_node_id"]
            contract = run(repo, "scope", "create", "--body", "Endpoint propagation focused contract.", "--source-node", source_node)
            root_task = run(
                repo,
                "task",
                "add",
                "--contract",
                contract["contract_id"],
                "--body",
                "Projection workbench chain root task.",
                "--from-node",
                source_node,
            )
            descendant_task = run(
                repo,
                "task",
                "add",
                "--contract",
                contract["contract_id"],
                "--parent",
                root_task["task_id"],
                "--body",
                "Projection workbench child task targeted by umbrella finding.",
                "--from-node",
                source_node,
            )
            descendant_check = run(
                repo,
                "acceptance",
                "add",
                "--task",
                descendant_task["task_id"],
                "--body",
                "Projection workbench child check belongs to child endpoint scope.",
                "--expected-evidence-type",
                "artifact",
                "--from-node",
                source_node,
            )
            proof_path = repo / "workbench-proof.txt"
            proof_path.write_text("projection workbench child local closure proof\n", encoding="utf-8")
            run(
                repo,
                "evidence",
                "artifact",
                "--path",
                "workbench-proof.txt",
                "--from-node",
                source_node,
                "--check",
                descendant_check["acceptance_check_id"],
                "--close-check",
                "--close-task",
            )
            umbrella = "shujuan-v4-interaction-trust-layer-2026-05-19"
            child = "shujuan-v4-chain-projection-workbench-2026-05-19"
            run(repo, "endpoint", "create", umbrella, "--description", "Umbrella endpoint.", "--root-node", contract["node_id"])
            run(repo, "endpoint", "create", child, "--description", "Projection workbench child endpoint.", "--root-node", descendant_task["node_id"])
            run(repo, "endpoint", "link-child", "--parent", umbrella, "--child", child)

            clean_child_status = run(repo, "endpoint", "status", child)
            assert_locally_clean(clean_child_status)
            clean_child_doctor = run(repo, "endpoint", "doctor", child, "--strict-closeout")
            if not clean_child_doctor["ok"]:
                raise AssertionError(f"locally clean child endpoint failed before inherited blocker: {clean_child_doctor}")

            audit = run(
                repo,
                "audit",
                "record",
                "--endpoint",
                umbrella,
                "--source-node",
                source_node,
                "--body",
                "Umbrella workbench finding targets the projection-workbench child check.",
                "--finding",
                "Projection workbench child endpoint cannot appear clean while this umbrella workbench finding targets its child check.",
                "--check",
                descendant_check["acceptance_check_id"],
            )
            finding_node_id = audit["audit_finding_node_ids"][0]

            umbrella_report = run(repo, "report", "endpoint", umbrella, "--active-only")
            umbrella_findings = umbrella_report["active_obligations"].get("audit_findings") or []
            if finding_node_id not in {item["id"] for item in umbrella_findings}:
                raise AssertionError(f"umbrella active-only report did not expose finding: {umbrella_report}")
            child_chain_blockers = umbrella_report["active_obligations"].get("child_chain_blockers") or []
            if child not in {item["endpoint"] for item in child_chain_blockers}:
                raise AssertionError(f"umbrella active-only report did not expose blocked child endpoint: {umbrella_report}")
            umbrella_doctor = run_fails(repo, "endpoint", "doctor", umbrella, "--strict-closeout")
            umbrella_doctor_payload = json.loads(umbrella_doctor.stdout)
            umbrella_codes = doctor_codes(umbrella_doctor_payload)
            if not {"active_audit_findings", "active_child_chain_obligations"} <= umbrella_codes:
                raise AssertionError(f"umbrella strict doctor did not surface finding and child blocker: {umbrella_doctor_payload}")

            child_status = run(repo, "endpoint", "status", child)
            if child_status.get("scope_kind") != "task":
                raise AssertionError(f"task-root child endpoint did not report task scope: {child_status.get('scope_kind')}")
            if descendant_task["task_id"] not in {item["id"] for item in child_status.get("tasks") or []}:
                raise AssertionError(f"task-root scope did not include descendant task: {child_status}")
            if descendant_check["acceptance_check_id"] not in {item["id"] for item in child_status.get("closed_checks") or []}:
                raise AssertionError(f"task-root scope did not include descendant check: {child_status}")
            assert_locally_clean(child_status)
            assert_inherited(child_status, finding_node_id, descendant_check["node_id"])

            child_report = run(repo, "report", "endpoint", child, "--active-only")
            report_blockers = child_report["active_obligations"].get("inherited_active_blockers") or []
            if finding_node_id not in {item["id"] for item in report_blockers}:
                raise AssertionError(f"active-only report did not expose inherited blocker: {child_report}")
            child_brief = run(repo, "endpoint", "brief", child)
            brief_blockers = child_brief["active_obligations"].get("inherited_active_blockers") or []
            if finding_node_id not in {item["id"] for item in brief_blockers}:
                raise AssertionError(f"endpoint brief did not expose inherited blocker: {child_brief}")

            doctor = run_fails(repo, "endpoint", "doctor", child, "--strict-closeout")
            doctor_payload = json.loads(doctor.stdout)
            codes = doctor_codes(doctor_payload)
            if "inherited_active_blockers" not in codes or doctor_payload["ok"]:
                raise AssertionError(f"strict doctor did not block inherited finding: {doctor_payload}")

            unsourced_resolution = run_fails(
                repo,
                "semantic",
                "set-state",
                "--node",
                finding_node_id,
                "--state",
                "resolved",
                "--reason",
                "Unsourced lifecycle changes must not clear inherited blockers.",
            )
            if "--source-node" not in unsourced_resolution.stderr:
                raise AssertionError(f"unsourced lifecycle change did not require source evidence: {unsourced_resolution.stderr}")
            assert_inherited(run(repo, "endpoint", "status", child), finding_node_id, descendant_check["node_id"])

            run(
                repo,
                "semantic",
                "set-state",
                "--node",
                finding_node_id,
                "--state",
                "deferred",
                "--source-node",
                source_node,
                "--reason",
                "Focused test defers the umbrella finding.",
            )
            deferred_status = run(repo, "endpoint", "status", child)
            assert_not_inherited(deferred_status, finding_node_id)

            run(
                repo,
                "semantic",
                "set-state",
                "--node",
                finding_node_id,
                "--state",
                "reopened",
                "--source-node",
                source_node,
                "--reason",
                "Focused test reopens the umbrella finding.",
            )
            reopened_status = run(repo, "endpoint", "status", child)
            assert_inherited(reopened_status, finding_node_id, descendant_check["node_id"])

            run(
                repo,
                "semantic",
                "set-state",
                "--node",
                finding_node_id,
                "--state",
                "resolved",
                "--source-node",
                source_node,
                "--reason",
                "Focused test resolves the umbrella finding.",
            )
            resolved_status = run(repo, "endpoint", "status", child)
            assert_not_inherited(resolved_status, finding_node_id)
            resolved_doctor = run(repo, "endpoint", "doctor", child, "--strict-closeout")
            if not resolved_doctor["ok"]:
                raise AssertionError(f"child strict doctor did not clear after source-backed resolution: {resolved_doctor}")

            print(
                json.dumps(
                    {
                        "ok": True,
                        "results": {
                            "projection_workbench_child_was_locally_clean_before_inheritance": True,
                            "umbrella_report_surfaces_workbench_finding": True,
                            "umbrella_doctor_blocks_on_finding_and_child_chain": True,
                            "task_root_scope_includes_closed_descendant_check": True,
                            "child_status_inherited_blocker": True,
                            "child_report_inherited_blocker": True,
                            "child_brief_inherited_blocker": True,
                            "strict_doctor_blocks_inherited_finding": True,
                            "unsourced_lifecycle_change_does_not_clear_blocker": True,
                            "deferred_finding_no_longer_blocks": True,
                            "reopened_finding_blocks_again": True,
                            "resolved_finding_no_longer_blocks": True,
                            "child_strict_doctor_clean_after_source_backed_resolution": True,
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
