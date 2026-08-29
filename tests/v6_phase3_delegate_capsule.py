from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


REQUIRED_RETURN_FIELDS = {
    "changed_files",
    "owned_hunks_or_paths",
    "pre_existing_dirty_paths",
    "inspected_only_paths",
    "fixture_writes",
    "tests",
    "blocked_checks",
    "unresolved_risks",
    "assumptions",
    "provider_outputs",
    "tests_run",
    "check_status",
    "identity_boundary",
    "no_closure_attestation",
}


FORBIDDEN_WORKER_ACTIONS = {
    "current_project_governance_write",
    "endpoint refresh",
    "exec stop",
    "check/task closure",
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
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def run_json(repo: Path, *args: str) -> dict[str, object]:
    return json.loads(run_cli(repo, *args).stdout)


def assert_common_delegate_contract(payload: dict[str, object]) -> None:
    if not payload.get("ok") or not payload.get("usable"):
        raise AssertionError(f"delegate payload was not usable: {payload}")
    if payload.get("db_writes") != 0 or payload.get("capture_claim"):
        raise AssertionError(f"delegate payload claimed governance side effects: {payload}")
    if payload.get("controller_only_closeout") is not True:
        raise AssertionError(f"delegate payload lost controller-only closeout: {payload}")


def assert_return_requirements(surface: dict[str, object]) -> None:
    return_requirements = surface["return_requirements"]
    missing = REQUIRED_RETURN_FIELDS - set(return_requirements["required_fields"])
    if missing:
        raise AssertionError(f"return requirements missed required fields {missing}: {surface}")
    for field in REQUIRED_RETURN_FIELDS:
        if field not in return_requirements:
            raise AssertionError(f"return requirements omitted field {field}: {surface}")
    return_capsule = surface["return_capsule"]
    for field in REQUIRED_RETURN_FIELDS:
        if field not in return_capsule:
            raise AssertionError(f"return capsule omitted field {field}: {surface}")
    if "did not close checks/tasks" not in return_capsule["no_closure_attestation"]:
        raise AssertionError(f"return capsule no-closure attestation is weak: {surface}")
    if return_capsule["check_status"]["closed_by_delegate"]:
        raise AssertionError(f"return capsule leaked closure status: {surface}")


def assert_worker_authority(surface: dict[str, object]) -> None:
    forbidden = set(surface["forbidden_actions"])
    if not FORBIDDEN_WORKER_ACTIONS <= forbidden:
        raise AssertionError(f"worker forbidden actions missed current-project write/closeout boundaries: {surface}")
    role_authority = surface["role_authority"]
    if role_authority["db_write_authority"] or role_authority["closeout_authority"]:
        raise AssertionError(f"worker authority leaked governance or closeout power: {surface}")
    boundary = surface["governance_write_boundary"]
    if boundary["current_project_governance_write_allowed"] or not boundary["current_project_governance_write_prohibited"]:
        raise AssertionError(f"worker current-project governance write boundary was wrong: {surface}")
    if not boundary["isolated_fixture_writes_are_material_only"]:
        raise AssertionError(f"fixture writes were not classified as material only: {surface}")


def assert_provider_material(surface: dict[str, object]) -> None:
    provider = surface["provider_impact_classification"]
    if not provider["material_only"] or not provider["cannot_close_checks"]:
        raise AssertionError(f"provider output was not material-only: {surface}")
    if provider["output_classification"] not in {"provider_fact", "provider_hypothesis"}:
        raise AssertionError(f"provider output classification was invalid: {surface}")
    guidance = surface["provider_guidance"]
    if not guidance["material_only"] or not guidance["cannot_close_checks"] or not guidance["cannot_close_tasks"]:
        raise AssertionError(f"provider guidance leaked closure authority: {surface}")
    output_contract = guidance["output_contract"]
    for key in ("seed", "question", "boundary", "output_classification"):
        if key not in output_contract:
            raise AssertionError(f"provider output contract missed {key}: {surface}")


def assert_worker_packet(repo: Path) -> None:
    payload = run_json(
        repo,
        "delegate",
        "packet",
        "--role",
        "worker",
        "--packet-kind",
        "delegation",
        "--endpoint",
        "shujuan-v6-activation-consolidation-2026-05-21",
        "--task",
        "task_a88626385c134941",
        "--check",
        "check_ba3bbed5ec5b4409",
        "--goal",
        "Connect delegate capsule to activation.",
        "--hard-predicate",
        "Return Capsule fields must be complete.",
        "--pre-existing-dirty-path",
        "README.md",
        "--owned-hunk-or-path",
        "shujuan/commands/delegate_handlers.py::delegate_role_packet",
        "--inspected-only-path",
        "shujuan/commands/delegate.py",
        "--fixture-write",
        "isolated temp repo .shujuan fixture writes",
        "--blocked-check",
        "check_ba3bbed5ec5b4409",
        "--unresolved-risk",
        "controller must import and verify material before closure",
        "--assumption",
        "no runtime DB read requested for this packet",
        "--known-red",
        "pre-existing dirty tree remains outside worker ownership",
        "--provider-seed",
        "cmd_delegate_capsule",
        "--provider-question",
        "Does the capsule surface material-only provider output?",
        "--provider-boundary",
        "codegraph/GitNexus/provider output is provider material only.",
        "--provider-output-classification",
        "provider_fact",
        "--provider-output",
        "gitnexus impact preflight: LOW",
    )
    assert_common_delegate_contract(payload)
    packet = payload["packet"]["role_packet"]
    assert_return_requirements(packet)
    assert_worker_authority(packet)
    assert_provider_material(packet)
    if packet["fact_source"]["kind"] != "source_labeled_cli_args" or packet["db_backed"]:
        raise AssertionError(f"packet did not source-label non-DB facts: {packet}")
    if packet["return_capsule"]["fixture_writes"] != ["isolated temp repo .shujuan fixture writes"]:
        raise AssertionError(f"packet did not report isolated fixture writes separately: {packet}")


def assert_worker_capsule(repo: Path) -> None:
    payload = run_json(
        repo,
        "delegate",
        "capsule",
        "--role",
        "worker",
        "--endpoint",
        "shujuan-v6-activation-consolidation-2026-05-21",
        "--task",
        "task_a88626385c134941",
        "--check",
        "check_ba3bbed5ec5b4409",
        "--hard-predicate",
        "Worker capsule must forbid current project governance writes.",
        "--owned-hunk-or-path",
        "shujuan/commands/delegate_handlers.py::cmd_delegate_capsule",
        "--inspected-only-path",
        "tests/v5_dccp_delegate_skeleton_regressions.py",
        "--fixture-write",
        "temp fixture governance write report only",
        "--blocked-check",
        "check_bfbb01738da6451c",
        "--unresolved-risk",
        "controller closeout still required",
        "--assumption",
        "capsule is source-labeled, not DB-backed",
        "--known-red",
        "legacy v5 diagnostics must remain compatible",
        "--provider-seed",
        "delegate capsule",
        "--provider-question",
        "Classify provider material boundary.",
        "--provider-output-classification",
        "provider_hypothesis",
        "--provider-output",
        "provider output not used",
    )
    assert_common_delegate_contract(payload)
    capsule = payload["capsule"]
    if not capsule["read_only"] or capsule["live_db_read"] or capsule["db_backed"]:
        raise AssertionError(f"capsule did not stay read-only/source-labeled: {capsule}")
    assert_return_requirements(capsule)
    assert_worker_authority(capsule)
    assert_provider_material(capsule)
    if capsule["safe_verification"]["blocked_checks"] != ["check_bfbb01738da6451c", "check_ba3bbed5ec5b4409"]:
        raise AssertionError(f"capsule did not surface blocked checks: {capsule}")
    if "legacy v5 diagnostics must remain compatible" not in capsule["known_reds"]:
        raise AssertionError(f"capsule did not surface known reds: {capsule}")


def assert_provider_import_classification(repo: Path) -> None:
    payload = run_json(
        repo,
        "delegate",
        "import",
        "--role",
        "provider",
        "--import-kind",
        "provider_fact",
        "--classification",
        "provider-hypothesis",
        "--artifact",
        "provider.json",
    )
    assert_common_delegate_contract(payload)
    row = payload["classification_row"]
    if not row["provider_hypothesis"] or row["closure_material"] or row["closes_check"] or row["closes_task"]:
        raise AssertionError(f"provider import classification leaked closure authority: {payload}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-v6-phase3-delegate-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        assert_worker_packet(repo)
        assert_worker_capsule(repo)
        assert_provider_import_classification(repo)
        if (repo / ".shujuan").exists():
            raise AssertionError("delegate diagnostics created .shujuan governance artifacts")
    print(json.dumps({"ok": True, "v6_phase3_delegate_capsule": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
