from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import clean_env, postgres_fixture


REQUIRED_SKILLS = {
    "shujuan-harness",
    "shujuan-recall",
    "shujuan-capture",
    "shujuan-execute",
    "shujuan-delegate",
    "shujuan-close",
    "shujuan-evolve",
}


def _run_json(repo: Path, *args: str, expect_ok: bool = True) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_env({"PYTHONPATH": str(ROOT)}),
    )
    if expect_ok and completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return json.loads(completed.stdout)


def _assert_codex_surface() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    kernel = agents.split("<!-- gitnexus:start -->", 1)[0]
    if len(kernel.encode("utf-8")) > 8192 or len(kernel.splitlines()) > 120:
        raise AssertionError("AGENTS.md exceeds v11 contracted size")
    first = agents.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
    for fragment in ("Sovereignty", "Relation", "Authority", "Source coverage", "Method Map", "PostgreSQL", "material"):
        if fragment not in first:
            raise AssertionError(f"AGENTS first surface missed {fragment}")
    descriptions = []
    for skill in REQUIRED_SKILLS:
        body = (ROOT / ".agents" / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        asset = (ROOT / "shujuan" / "assets" / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        if body != asset:
            raise AssertionError(f"skill asset drifted for {skill}")
        if "Trigger" not in body or "Do not trigger" not in body or "Completion" not in body:
            raise AssertionError(f"skill missing method contract prose: {skill}")
        descriptions.append(next(line for line in body.splitlines() if line.startswith("description:")))
    if len("\n".join(descriptions).encode("utf-8")) > 2500:
        raise AssertionError("method skill descriptions exceed v11 budget")
    core = (ROOT / ".agents" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")
    if "compatibility shim" not in core or "ordinary v11 work" not in core:
        raise AssertionError("core skill is not a v11 compatibility shim")
    expected_templates = {
        "attention_contract": (
            ROOT / ".agents" / "skills" / "shujuan-harness" / "templates" / "attention-contract.md",
            ROOT / "shujuan" / "assets" / "skills" / "shujuan-harness" / "templates" / "attention-contract.md",
            ("Primary method", "Write posture", "Allowed transition"),
        ),
        "claim_ledger": (
            ROOT / ".agents" / "skills" / "shujuan-recall" / "templates" / "claim-ledger.md",
            ROOT / "shujuan" / "assets" / "skills" / "shujuan-recall" / "templates" / "claim-ledger.md",
            ("Claim type", "Anchor candidates", "Unsupported claims"),
        ),
        "recall_frontier": (
            ROOT / ".agents" / "skills" / "shujuan-recall" / "templates" / "recall-frontier.md",
            ROOT / "shujuan" / "assets" / "skills" / "shujuan-recall" / "templates" / "recall-frontier.md",
            ("Candidate surface", "Information scent", "Evidence strength"),
        ),
        "recall_stop_decision": (
            ROOT / ".agents" / "skills" / "shujuan-recall" / "templates" / "recall-stop-decision.md",
            ROOT / "shujuan" / "assets" / "skills" / "shujuan-recall" / "templates" / "recall-stop-decision.md",
            ("Coverage", "Unsearched frontier", "Stop reason"),
        ),
    }
    for name, (repo_path, asset_path, fragments) in expected_templates.items():
        if not repo_path.exists() or not asset_path.exists():
            raise AssertionError(f"C23 template missing for {name}")
        repo_text = repo_path.read_text(encoding="utf-8")
        if asset_path.read_text(encoding="utf-8") != repo_text:
            raise AssertionError(f"C23 template mirror drift for {name}")
        for fragment in fragments:
            if fragment not in repo_text:
                raise AssertionError(f"C23 template {name} missed fragment {fragment}")


def _assert_route_matrix() -> None:
    with tempfile.TemporaryDirectory(prefix="v11-route-method-") as temp:
        repo = Path(temp)
        cases = json.loads((ROOT / "tests" / "fixtures" / "v11_route_method_matrix.json").read_text(encoding="utf-8"))
        if len(cases) != 84:
            raise AssertionError(f"route fixture matrix must contain exactly 84 cases, found {len(cases)}")
        expected_counts = {
            "positive:harness": 6,
            "positive:recall": 6,
            "positive:capture": 12,
            "positive:execute": 6,
            "positive:delegate": 6,
            "positive:close": 12,
            "positive:evolve": 6,
            "overlap_negation_collision": 18,
            "no_governance_directive": 6,
            "no_governance_meta": 6,
        }
        counts = Counter(case.get("kind") for case in cases)
        if counts != expected_counts:
            raise AssertionError(f"route fixture kind counts mismatch: expected={expected_counts} actual={dict(counts)}")
        for case in cases:
            for required in ("kind", "intent", "route", "skill"):
                if required not in case:
                    raise AssertionError(f"route fixture case missing {required}: {case}")
            args = ["route", "guard", "--intent", case["intent"]]
            if "endpoint" in case:
                args.extend(["--endpoint", case["endpoint"]])
            for key in ("task_id", "check_id", "expected_evidence_type", "current_matching_evidence_ref"):
                if key in case:
                    args.extend([f"--{key.replace('_', '-')}", case[key]])
            payload = _run_json(repo, *args, expect_ok=case.get("requires_close_inputs") is not True)
            if payload["recommended_route"] != case["route"]:
                raise AssertionError(f"route mismatch for {case['intent']!r}: {payload}")
            if payload.get("recommended_skill") != case["skill"]:
                raise AssertionError(f"skill mismatch for {case['intent']!r}: {payload}")
            for field in ("asks_recall", "asks_review", "asks_close", "negates_close", "asks_execute"):
                if field not in payload.get("intent_facts", {}):
                    raise AssertionError(f"missing intent fact {field}: {payload}")
            if payload["recommended_route"] == "No Governance":
                with tempfile.TemporaryDirectory(prefix="v11-no-gov-isolated-") as isolated:
                    isolated_repo = Path(isolated)
                    isolated_payload = _run_json(isolated_repo, "route", "guard", "--intent", case["intent"])
                    if isolated_payload["recommended_route"] != "No Governance" or (isolated_repo / ".shujuan").exists():
                        raise AssertionError(f"No Governance route created side effects: {isolated_payload}")


def _assert_role_policy() -> None:
    fixture_pair = postgres_fixture("v11-role-")
    if fixture_pair is None:
        return
    temp, fixture = fixture_pair
    try:
        repo = fixture.repo
        source = repo / "source.md"
        source.write_text("# role\n", encoding="utf-8")
        doc = fixture.run_json("doc", "import", "source.md", "--source-type", "plan")
        scope = fixture.run_json("scope", "create", "--body", "role scope", "--source-node", doc["document_node_id"])
        fixture.run_json("endpoint", "create", "v11-role", "--root-node", scope["node_id"])
        controller = fixture.run_json("endpoint", "brief", "v11-role", "--role", "controller")
        capsule = controller["activation"]["role_capsule"]
        if capsule["role"] != "controller_agent" or capsule["current_project_governance_write_authorized"] is not True:
            raise AssertionError(f"controller alias did not normalize: {controller}")
        invalid = fixture.run_json("endpoint", "brief", "v11-role", "--role", "owner", expect_ok=False)
        if invalid["error"]["code"] != "invalid_role":
            raise AssertionError(f"unknown role did not fail closed: {invalid}")
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


def _assert_install_registry_and_hooks() -> None:
    with tempfile.TemporaryDirectory(prefix="v11-install-") as temp:
        repo = Path(temp)
        payload = _run_json(repo, "init", "--install-skills", expect_ok=False)
        installed = payload.get("installed_assets") or []
        if not installed:
            raise AssertionError(f"partial init did not expose installed assets: {payload}")
        for skill in REQUIRED_SKILLS:
            if not (repo / ".agents" / "skills" / skill / "SKILL.md").exists():
                raise AssertionError(f"init did not install {skill}")
        for profile in ("shujuan-controller.toml", "shujuan-worker.toml", "shujuan-reviewer.toml", "shujuan-researcher.toml", "shujuan-writer.toml"):
            if not (repo / ".codex" / "agents" / profile).exists():
                raise AssertionError(f"init did not install role profile {profile}")
        doctor = _run_json(repo, "install-layout", "doctor")
        present = {item["name"]: item for item in doctor["skills"]}
        if not REQUIRED_SKILLS.issubset(present):
            raise AssertionError(f"doctor missed required skills: {doctor}")
        if not all(item["present"] and item["metadata_ok"] for item in present.values()):
            raise AssertionError(f"doctor did not report skill metadata ok: {doctor}")
        registry_roles = doctor["skill_registry"].get("role_profiles") or []
        if not registry_roles or not all(isinstance(item, dict) for item in registry_roles):
            raise AssertionError(f"role registry is not metadata-rich: {doctor['skill_registry']}")
        for item in doctor["agents"]:
            for field in ("role", "version", "compatibility", "required", "metadata_ok", "sha256"):
                if field not in item:
                    raise AssertionError(f"doctor role profile missed {field}: {item}")
            if item["required"] and (not item["present"] or not item["metadata_ok"]):
                raise AssertionError(f"doctor role profile is not install-ready: {item}")
        alias = subprocess.run(
            [sys.executable, "-m", "shujuan", "--repo", str(repo), "init", "--install-skill"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            env=clean_env({"PYTHONPATH": str(ROOT)}),
        )
        try:
            alias_payload = json.loads(alias.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"install alias did not return JSON: {alias.stdout}") from exc
        if not alias_payload.get("deprecations") or alias_payload["deprecations"][0].get("option") != "--install-skill":
            raise AssertionError(f"install alias did not report deprecation: {alias_payload}")
        if (repo / ".shujuan").exists():
            shutil.rmtree(repo / ".shujuan")
        hook = subprocess.run(
            [sys.executable, str(ROOT / "shujuan" / "assets" / "hooks" / "shujuan-method-hint.py")],
            input=json.dumps({"prompt": "why did No Governance become an exit?"}),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=repo,
            env=clean_env({"PYTHONPATH": str(ROOT)}),
        )
        if hook.returncode:
            raise AssertionError(f"method hint hook failed\nSTDOUT:\n{hook.stdout}\nSTDERR:\n{hook.stderr}")
        hint = json.loads(hook.stdout)
        hook_output = hint.get("hookSpecificOutput") or {}
        if hook_output.get("hookEventName") != "UserPromptSubmit" or "shujuan-recall" not in hook_output.get("additionalContext", ""):
            raise AssertionError(f"method hint hook missed recall skill: {hint}")
        if (repo / ".shujuan").exists():
            raise AssertionError("method hint hook created .shujuan in isolated repo for Recall/meta prompt")
        pure = _run_json(repo, "route", "guard", "--pure", "--intent", "why did No Governance become an exit?")
        if not pure.get("pure") or pure.get("runtime_access") != "skipped_pure" or pure.get("filesystem_writes") != 0 or pure.get("db_writes") != 0:
            raise AssertionError(f"pure route guard did not expose no-side-effect contract: {pure}")
        if (repo / ".shujuan").exists():
            raise AssertionError("pure route guard created .shujuan in isolated repo for Recall/meta prompt")


def _assert_recall_benchmarks() -> None:
    benches = json.loads((ROOT / "tests" / "fixtures" / "v11_recall_benchmarks.json").read_text(encoding="utf-8"))
    if len(benches) != 12:
        raise AssertionError("v11 recall benchmark set must contain exactly 12 prompts")
    kinds = {item["kind"] for item in benches}
    for kind in {"historical", "code_why", "lineage", "contradiction", "current_vs_history"}:
        if kind not in kinds:
            raise AssertionError(f"recall benchmark missing kind {kind}")
    required_fields = {
        "anchors",
        "report_evidence_labels",
        "stop_frontier",
        "candidate_reduction",
        "db_writes_allowed",
    }
    for item in benches:
        missing = sorted(required_fields - set(item))
        if missing:
            raise AssertionError(f"recall benchmark {item.get('id')} missed evidence fields {missing}")
        if not item["anchors"] or not item["report_evidence_labels"] or not item["stop_frontier"]:
            raise AssertionError(f"recall benchmark {item.get('id')} has empty evidence contract fields")
        reduction = item["candidate_reduction"]
        if not isinstance(reduction, dict) or reduction.get("predicate") != "targeted_less_than_naive" or not reduction.get("naive_surface"):
            raise AssertionError(f"recall benchmark {item.get('id')} has weak candidate reduction predicate: {reduction}")
        if item["db_writes_allowed"] is not False:
            raise AssertionError(f"recall benchmark {item.get('id')} permits DB writes")


def _assert_delegate_role_aliases() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        repo = Path(tempdir)
        payload = _run_json(
            repo,
            "delegate",
            "verify",
            "--role",
            "worker_agent",
            "--claims-closeout",
            "--allow-fail",
        )
    if payload.get("role") != "worker":
        raise AssertionError(f"delegate CLI did not normalize worker_agent role alias: {payload}")
    if payload.get("usable") is not False:
        raise AssertionError(f"delegate verify should still reject delegated closeout claims: {payload}")


def _assert_release_manifest_completeness() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/verify_release_manifest.py", "."],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise AssertionError(f"release manifest verifier failed\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    payload = json.loads(completed.stdout)
    if payload.get("missing_required_surface_count") != 0 or payload.get("required_surface_count", 0) < 40:
        raise AssertionError(f"release manifest does not prove v11 delivery surface coverage: {payload}")


def main() -> int:
    _assert_codex_surface()
    _assert_route_matrix()
    _assert_role_policy()
    _assert_install_registry_and_hooks()
    _assert_recall_benchmarks()
    _assert_delegate_role_aliases()
    _assert_release_manifest_completeness()
    print(json.dumps({"ok": True, "v11_method_plane": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
