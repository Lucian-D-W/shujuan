from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import clean_env, postgres_fixture


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_json_repo(repo: Path, *args: str, expect_ok: bool = True) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_env(),
    )
    if expect_ok and completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return json.loads(completed.stdout)


def _route_hard_stop_assertions(fixture) -> None:
    wrapper = fixture.run_json(
        "route",
        "guard",
        "--endpoint",
        "v9-repair",
        "--intent",
        "use a wrapper subprocess loop to create many tasks and checks from this long plan",
    )
    if wrapper["recommended_route"] == "Execute" or wrapper["exit_brake"]["stop_writes"] is not True:
        raise AssertionError(f"wrapper-loop route guard stayed write-allowed: {wrapper}")
    no_gov_trace = fixture.repo / ".shujuan" / "trace" / "workflow_trace.jsonl"
    before = no_gov_trace.read_text(encoding="utf-8") if no_gov_trace.exists() else ""
    no_gov = fixture.run_json(
        "route",
        "guard",
        "--intent",
        "请不要使用shujuan，直接回答",
    )
    after = no_gov_trace.read_text(encoding="utf-8") if no_gov_trace.exists() else ""
    if no_gov["recommended_route"] != "No Governance" or no_gov["exit_brake"]["stop_writes"] is not True:
        raise AssertionError(f"Chinese no-governance wording was not recognized: {no_gov}")
    if after != before:
        raise AssertionError("No Governance route guard wrote trace without explicit --trace")
    close_complete = fixture.run_json(
        "route",
        "guard",
        "--endpoint",
        "v9-repair",
        "--intent",
        "关闭这个检查",
        "--task-id",
        "task_fake",
        "--check-id",
        "check_fake",
        "--expected-evidence-type",
        "artifact",
        "--current-matching-evidence-ref",
        "node_fake",
    )
    if close_complete["recommended_route"] != "Close" or close_complete["first_surface"]["kind"] != "closeout_inputs":
        raise AssertionError(f"complete closeout inputs did not route to Close: {close_complete}")


def _task_chain_payload() -> dict:
    return {
        "declares_no_closure": True,
        "closed_by_decomposition": False,
        "endpoint": {"name": "v9-repair"},
        "tasks": [
            {"key": "R01", "title": "Repair route guard", "body": "Repair route guard.", "phase": "P0", "order": 10, "mandatory": True}
        ],
        "checks": [
            {"key": "RC01", "task_key": "R01", "body": "Run a focused regression.", "expected_evidence_type": "test_result"}
        ],
        "source_items": [
            {
                "id": "SRC01",
                "classification": "P0",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "R01"},
                "task_ids": ["R01"],
                "check_ids": ["RC01"],
                "rationale": "Keep the repair source visible.",
                "promotion_rule": "Already active.",
                "reopen_rule": "Reopen by restoring R01/RC01.",
            }
        ],
    }


def _plan_to_db_assertions(fixture) -> None:
    repo = fixture.repo
    good = _write_json(repo / "good_chain.json", _task_chain_payload())
    preview = fixture.run_json("plan-to-db", "import-task-chain", "--artifact", str(good), "--endpoint", "v9-repair", "--dry-run")
    if preview["counts"]["source_items"] != 1 or preview["source_items"]["ids"] != ["SRC01"]:
        raise AssertionError(f"preview did not expose explicit source_items: {preview}")
    if preview["counts"]["edges"] != 5:
        raise AssertionError(f"preview edge count did not match apply semantics: {preview}")
    edge_sources = preview.get("edge_plan", {}).get("by_source", {})
    expected_edge_sources = {
        "source_item_derived_from_scope_source": 1,
        "contract_decomposes_to_task": 1,
        "task_derived_from_source": 1,
        "parent_task_decomposes_to_child_task": 0,
        "task_decomposes_to_check": 1,
        "check_derived_from_source": 1,
    }
    if edge_sources != expected_edge_sources:
        raise AssertionError(f"preview edge plan was not explicit or accurate: {preview}")

    missing_no_closure = dict(_task_chain_payload())
    missing_no_closure.pop("declares_no_closure", None)
    bad_no_closure = _write_json(repo / "bad_no_closure.json", missing_no_closure)
    failed_preview = fixture.run_json(
        "plan-to-db",
        "import-task-chain",
        "--artifact",
        str(bad_no_closure),
        "--endpoint",
        "v9-repair",
        "--dry-run",
        expect_ok=False,
    )
    codes = {item["code"] for item in failed_preview["violations"]}
    if "missing_declares_no_closure" not in codes:
        raise AssertionError(f"preview did not fail on declares_no_closure hygiene: {failed_preview}")

    weak_source_shape = dict(_task_chain_payload())
    weak_source_shape["source_items"] = [{"id": "SRC01"}]
    bad_source = _write_json(repo / "bad_source_shape.json", weak_source_shape)
    source_shape_error = fixture.run_json(
        "plan-to-db",
        "import-task-chain",
        "--artifact",
        str(bad_source),
        "--endpoint",
        "v9-repair",
        "--dry-run",
        expect_ok=False,
    )
    source_shape_codes = {item["code"] for item in source_shape_error["violations"]}
    if "missing_output_shape_field" not in source_shape_codes:
        raise AssertionError(f"import-task-chain did not reuse verify-artifact source_items hygiene: {source_shape_error}")

    malformed = _write_json(repo / "malformed.json", {"endpoint": {"name": "v9-repair"}, "tasks": "nope"})
    dry_run_error = fixture.run_json(
        "plan-to-db",
        "import-task-chain",
        "--artifact",
        str(malformed),
        "--endpoint",
        "v9-repair",
        "--dry-run",
        expect_ok=False,
    )
    apply_error = fixture.run_json(
        "plan-to-db",
        "import-task-chain",
        "--artifact",
        str(malformed),
        "--endpoint",
        "v9-repair",
        "--apply",
        expect_ok=False,
    )
    for payload in (dry_run_error, apply_error):
        if payload["error"]["code"] != "invalid_task_chain_artifact" or "violations" not in payload:
            raise AssertionError(f"malformed import-task-chain was not structured: {payload}")


def _structured_error_and_review_state_assertions(fixture) -> None:
    missing_endpoint = fixture.run_json("workflow", "begin", "--content", "hello", expect_ok=False)
    if missing_endpoint["error"]["code"] != "missing_endpoint" or missing_endpoint.get("read_only") is not True:
        raise AssertionError(f"workflow begin missing endpoint was not structured: {missing_endpoint}")

    intent_file = fixture.repo / "intent.txt"
    intent_file.write_text("inspect route", encoding="utf-8")
    input_conflict = fixture.run_json(
        "route",
        "guard",
        "--intent",
        "inspect route",
        "--intent-file",
        str(intent_file),
        expect_ok=False,
    )
    if input_conflict["error"]["code"] != "mutually_exclusive_input" or input_conflict.get("input_label") != "intent":
        raise AssertionError(f"read_arg_or_stdin conflict was not structured: {input_conflict}")

    invalid_args = fixture.run_json("endpoint", "doctor", "v9-repair", "--not-a-real-flag", expect_ok=False)
    if invalid_args["error"]["code"] != "invalid_cli_arguments" or invalid_args.get("read_only") is not True:
        raise AssertionError(f"argparse failure was not structured: {invalid_args}")

    packet = fixture.run_json(
        "review",
        "packet",
        "--endpoint",
        "v9-repair",
        "--role",
        "reviewer_agent",
        "--question",
        "Confirm no packet-only review can close the endpoint.",
        "--save-artifact",
        "review_packet.md",
    )
    if packet["material_only"] is not True or packet["reviewer_executed"] is not False:
        raise AssertionError(f"review packet did not record packet-only state: {packet}")

    status = fixture.run_json("endpoint", "status", "v9-repair")
    review_state = status.get("review_state") or {}
    if review_state.get("state_kind") != "review_material_waiting_for_reviewer":
        raise AssertionError(f"endpoint status did not surface packet-only review state: {status}")
    if not status.get("review_material"):
        raise AssertionError(f"endpoint status missed active review material: {status}")

    report = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(fixture.repo), "report", "endpoint", "v9-repair", "--active-only", "--markdown"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_env(),
    )
    if report.returncode:
        raise AssertionError(f"endpoint report failed\nSTDOUT:\n{report.stdout}\nSTDERR:\n{report.stderr}")
    if "Review material:" not in report.stdout or "reviewer_executed=false" not in report.stdout:
        raise AssertionError(f"endpoint report did not expose review material:\n{report.stdout}")

    doctor = fixture.run_json("endpoint", "doctor", "v9-repair", "--strict-closeout", "--read-only", "--allow-fail")
    p0_codes = {item["code"] for item in doctor.get("severity_buckets", {}).get("P0", [])}
    if "review_material_not_executed" not in p0_codes:
        raise AssertionError(f"strict doctor did not gate packet-only review material: {doctor}")

    return_artifact = fixture.repo / "review_return.md"
    return_artifact.write_text("reviewer result: accept material for controller adoption", encoding="utf-8")
    returned = fixture.run_json("review", "record-return", "--endpoint", "v9-repair", "--return-artifact", str(return_artifact))
    if returned["reviewer_executed"] is not True:
        raise AssertionError(f"review return was not recorded: {returned}")
    adopted = fixture.run_json("review", "adopt", "--endpoint", "v9-repair", "--decision", "accept")
    if adopted["controller_adopted"] is not True:
        raise AssertionError(f"review adoption was not recorded: {adopted}")
    final_status = fixture.run_json("endpoint", "status", "v9-repair")
    if final_status.get("review_material"):
        raise AssertionError(f"adopted review material stayed active: {final_status}")


def _install_layout_assertions(fixture) -> None:
    current = fixture.run_json("install-layout", "doctor")
    if current["postgres_ready"] is not True or current["postgres_runtime"]["runtime_status_kind"] != "postgres_runtime_ready":
        raise AssertionError(f"current repo doctor should report the live runtime: {current}")

    shadow_dir = Path(tempfile.mkdtemp(prefix="v9-shadow-layout-"))
    empty_dir = Path(tempfile.mkdtemp(prefix="v9-empty-layout-"))
    try:
        shutil.copytree(fixture.repo / ".shujuan", shadow_dir / ".shujuan")
        shutil.copytree(ROOT / ".agents", shadow_dir / ".agents")
        fixture.stop()
        shadow = _run_json_repo(shadow_dir, "install-layout", "doctor")
        empty = _run_json_repo(empty_dir, "install-layout", "doctor")
        if shadow["postgres_ready"] is not False or empty["postgres_ready"] is not False:
            raise AssertionError(f"doctor should not trust config/data-dir existence after the runtime stops: shadow={shadow}, empty={empty}")
    finally:
        shutil.rmtree(shadow_dir, ignore_errors=True)
        shutil.rmtree(empty_dir, ignore_errors=True)


def _artifact_index_assertions(fixture) -> None:
    repo = fixture.repo
    (repo / "task_chain.json").write_text("{}", encoding="utf-8")
    (repo / "mapping.json").write_text("{}", encoding="utf-8")
    good = fixture.run_json(
        "artifact",
        "index",
        "refresh",
        "--endpoint",
        "v9-repair",
        "--current",
        "task_chain.json",
        "--mapping",
        "mapping.json",
    )
    if not good["authoritative"] or not good["db_mapping"]:
        raise AssertionError(f"artifact index refresh missed expected buckets: {good}")
    verified = fixture.run_json("artifact", "index", "verify", "--endpoint", "v9-repair")
    if not verified["ok"]:
        raise AssertionError(f"artifact index rejected existing artifacts: {verified}")
    fixture.run_json(
        "artifact",
        "index",
        "refresh",
        "--endpoint",
        "v9-missing-artifact",
        "--current",
        "missing/task_chain.json",
        "--mapping",
        "missing/map.json",
    )
    missing = fixture.run_json("artifact", "index", "verify", "--endpoint", "v9-missing-artifact", "--allow-fail")
    codes = {item["code"] for item in missing["violations"]}
    if missing["ok"] or "missing_artifact_file" not in codes:
        raise AssertionError(f"artifact index allowed missing canonical/current artifacts: {missing}")


def _installed_package_assets_assertions() -> None:
    temp = Path(tempfile.mkdtemp(prefix="v9-package-assets-"))
    target = temp / "target"
    repo = temp / "repo"
    repo.mkdir()
    try:
        install = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(ROOT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if install.returncode:
            raise AssertionError(f"pip install --target failed\nSTDOUT:\n{install.stdout}\nSTDERR:\n{install.stderr}")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(target)
        install_assets = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "from shujuan.cli import ensure_agents_md, ensure_shujuan_skill; "
                    f"repo=Path({str(repo)!r}); "
                    "ensure_agents_md(repo); "
                    "ensure_shujuan_skill(repo)"
                ),
            ],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        if install_assets.returncode:
            raise AssertionError(
                f"installed shujuan asset install failed\nSTDOUT:\n{install_assets.stdout}\nSTDERR:\n{install_assets.stderr}"
            )
        installed_skill = (repo / ".agents" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")
        installed_agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
        package_skill = (ROOT / "shujuan" / "assets" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")
        for fragment in ("First 90 seconds", "No Governance", "Batch boundary", "Exit brake"):
            if fragment not in installed_skill:
                raise AssertionError(f"installed package skill missed current activation fragment: {fragment}")
        for fragment in ("Use `No Governance` only as the explicit no-write/no-capture mode and exit", "Batch boundary", "State-change boundary"):
            if fragment not in installed_agents:
                raise AssertionError(f"installed AGENTS.md missed current policy fragment: {fragment}")
        if installed_skill != package_skill:
            raise AssertionError("installed package skill does not match shujuan/assets skill source")
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _doc_consistency_assertions() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    skill = (ROOT / ".agents" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")
    activation = (ROOT / ".agents" / "skills" / "shujuan-core" / "references" / "activation-first.md").read_text(encoding="utf-8")
    modes = (ROOT / ".agents" / "skills" / "shujuan-core" / "references" / "modes-and-terms.md").read_text(encoding="utf-8")
    if "Choose exactly one entry route first: `Recover`, `Recall`, `Execute`, `Close`, or `Delegate`" not in agents:
        raise AssertionError("AGENTS.md drifted from the five-route baseline")
    required = "not a sixth default route"
    if required not in skill or required not in activation or required not in modes:
        raise AssertionError("No Governance wording is still inconsistent across skill references")


def main() -> int:
    fixture_pair = postgres_fixture("v9-repair-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    temp, fixture = fixture_pair
    try:
        source = fixture.repo / "source.md"
        source.write_text("# v9 repair\n\nSource plan.\n", encoding="utf-8")
        doc = fixture.run_json("doc", "import", "source.md", "--source-type", "plan")
        scope = fixture.run_json("scope", "create", "--body", "v9 repair scope", "--source-node", doc["document_node_id"])
        fixture.run_json("endpoint", "create", "v9-repair", "--root-node", scope["node_id"])
        _route_hard_stop_assertions(fixture)
        _plan_to_db_assertions(fixture)
        _structured_error_and_review_state_assertions(fixture)
        _artifact_index_assertions(fixture)
        _install_layout_assertions(fixture)
        _doc_consistency_assertions()
        _installed_package_assets_assertions()
        print(json.dumps({"ok": True, "v9_simulation_repair_regressions": "passed"}))
        return 0
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
