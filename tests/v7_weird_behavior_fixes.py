from __future__ import annotations

import json
import os
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
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def run_json(repo: Path, *args: str, expect_ok: bool = True) -> dict[str, object]:
    return json.loads(run_cli(repo, *args, expect_ok=expect_ok).stdout)


def assert_controller_packet_is_material_only(repo: Path) -> None:
    payload = run_json(
        repo,
        "delegate",
        "packet",
        "--endpoint",
        "weird",
        "--role",
        "controller",
        "--goal",
        "controller claim; close checks",
    )
    if payload["usable"] or payload["diagnostics"]["usable"] or payload["usable_as_delegation_packet"]:
        raise AssertionError(f"controller role packet remained safely usable: {payload}")
    packet = payload["packet"]["role_packet"]
    if packet["requested_role"] != "controller" or packet["actual_authority"] != "delegate_packet_material_only":
        raise AssertionError(f"controller role packet did not separate requested role from actual authority: {packet}")
    if packet["db_write_authority"] or packet["closeout_authority"]:
        raise AssertionError(f"controller role packet leaked authority: {packet}")
    if packet["role_authority"]["can_write_governance_db"] or packet["role_authority"]["can_close_checks"]:
        raise AssertionError(f"role authority leaked write/close permissions: {packet}")
    if not packet["authority_assertion_is_self_reported"]:
        raise AssertionError(f"controller self-report marker missing: {packet}")
    requested = packet["requested_role_policy"]
    if not requested["db_write_authority"] or not requested["closeout_authority"]:
        raise AssertionError(f"requested role policy no longer describes controller role: {packet}")
    effects_role = payload["command_effects"]["role"]
    if effects_role["actual_authority"] != "delegate_packet_material_only":
        raise AssertionError(f"command effects did not expose material-only authority: {payload}")


def assert_brief_markdown_hides_internal_schema(repo: Path) -> None:
    source = run_json(repo, "doc", "import", "AGENTS.md", "--source-type", "plan")
    scope = run_json(repo, "scope", "create", "--body", "brief schema smoke", "--source-node", source["document_node_id"])
    run_json(repo, "endpoint", "create", "weird", "--root-node", scope["node_id"])
    run_json(repo, "endpoint", "refresh", "weird")
    raw = run_json(repo, "endpoint", "brief", "weird")
    if raw["activation_schema"] != "activation.v6" or raw["activation"]["activation_schema"] != "activation.v6":
        raise AssertionError(f"JSON schema compatibility marker changed: {raw}")
    markdown = run_cli(repo, "endpoint", "brief", "weird", "--markdown").stdout
    if "activation.v6" in markdown:
        raise AssertionError(f"markdown leaked internal activation schema id:\n{markdown}")
    if "Activation schema: available in JSON output" not in markdown:
        raise AssertionError(f"markdown did not explain where schema moved:\n{markdown}")

def assert_mode_conflict_fails(repo: Path) -> None:
    for intent in ("继续接手这个 endpoint", "do not record this"):
        routed = run_json(repo, "mode", "suggest", "--intent", intent)
        if routed["suggested_mode"] != "no_governance" or routed["contract"]["db_writes"]:
            raise AssertionError(f"recover/no-record intent no longer routes no-governance: {routed}")
    conflict = run_json(repo, "mode", "suggest", "--intent", "v7 p1", "--mode", "std", "--no-governance", expect_ok=False)
    codes = {item["code"] for item in conflict.get("errors") or []}
    if conflict["ok"] or conflict["usable"] or conflict["suggested_mode"] is not None:
        raise AssertionError(f"mode conflict remained usable: {conflict}")
    if "mode_flag_conflict_explicit_mode_overrode_no_governance" not in codes:
        raise AssertionError(f"mode conflict did not keep the expected diagnostic code: {conflict}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-v7-weird-fixes-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        assert_controller_packet_is_material_only(repo)
        assert_mode_conflict_fails(repo)
    from helpers.postgres_fixture import postgres_fixture

    fixture_pair = postgres_fixture("shujuan-v7-weird-brief-")
    if fixture_pair is not None:
        temp, fixture = fixture_pair
        with temp:
            try:
                assert_brief_markdown_hides_internal_schema(fixture.repo)
                fixture_writes = fixture.writes
            finally:
                fixture.stop()
    else:
        fixture_writes = ["skipped: native PostgreSQL fixture unavailable"]
    print(json.dumps({"ok": True, "v7_weird_behavior_fixes": "passed", "fixture_writes": fixture_writes}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
