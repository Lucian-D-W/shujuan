from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import clean_env, postgres_fixture


def _run_json(repo: Path, *args: str, env: dict[str, str] | None = None, expect_ok: bool = True) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env or clean_env(),
    )
    if expect_ok and completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return json.loads(completed.stdout)


def _bad_db_no_governance_assertion() -> None:
    with tempfile.TemporaryDirectory(prefix="v10-no-gov-") as temp:
        repo = Path(temp)
        env = clean_env()
        env["SHUJUAN_DATABASE_URL"] = "postgresql://bad:bad@127.0.0.1:1/bad"
        payload = _run_json(repo, "route", "guard", "--intent", "do not use shujuan; answer directly", env=env)
        if payload["recommended_route"] != "No Governance" or payload["relation_decision"]["relation_type"] != "no_governance_exit":
            raise AssertionError(f"no-governance route was not preserved: {payload}")
        if (repo / ".shujuan").exists():
            raise AssertionError("no-governance route created .shujuan under a bad DB runtime")


def _assert_no_governance_without_side_effects(intent: str) -> None:
    with tempfile.TemporaryDirectory(prefix="v10-no-gov-local-") as temp:
        repo = Path(temp)
        payload = _run_json(repo, "route", "guard", "--intent", intent)
        if payload["recommended_route"] != "No Governance" or payload["relation_decision"]["relation_type"] != "no_governance_exit":
            raise AssertionError(f"expected No Governance for {intent!r}: {payload}")
        if (repo / ".shujuan").exists():
            raise AssertionError(f"No Governance intent created .shujuan for {intent!r}")


def _assert_no_governance_default_cwd_without_side_effects(intent: str) -> None:
    with tempfile.TemporaryDirectory(prefix="v10-no-gov-cwd-") as temp:
        repo = Path(temp)
        env = clean_env()
        env["PYTHONPATH"] = str(ROOT)
        completed = subprocess.run(
            [sys.executable, "-m", "shujuan", "route", "guard", "--intent", intent],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        if completed.returncode:
            raise AssertionError(f"default-cwd route guard failed for {intent!r}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
        payload = json.loads(completed.stdout)
        if payload["recommended_route"] != "No Governance" or payload["relation_decision"]["relation_type"] != "no_governance_exit":
            raise AssertionError(f"expected default-cwd No Governance for {intent!r}: {payload}")
        if (repo / ".shujuan").exists():
            raise AssertionError(f"default-cwd No Governance intent created .shujuan for {intent!r}")


def _assert_capture_gate_without_side_effects(args: list[str], intent: str) -> None:
    with tempfile.TemporaryDirectory(prefix="v10-capture-no-gov-") as temp:
        repo = Path(temp)
        payload = _run_json(repo, *args, "--content", intent)
        if payload.get("recommended_route") != "No Governance" or payload.get("mode") != "no_governance":
            raise AssertionError(f"expected capture command to exit No Governance for {intent!r}: {payload}")
        if payload.get("stop_writes") is not True or payload.get("db_writes") != 0:
            raise AssertionError(f"No Governance capture gate did not expose write stop for {intent!r}: {payload}")
        if (repo / ".shujuan").exists():
            raise AssertionError(f"No Governance capture command created .shujuan for {intent!r}")


def _assert_bad_db_command_no_governance_without_side_effects(args: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="v10-bad-db-no-gov-") as temp:
        repo = Path(temp)
        env = clean_env({"PYTHONPATH": str(ROOT), "SHUJUAN_DATABASE_URL": "postgresql://bad:bad@127.0.0.1:1/bad"})
        completed = subprocess.run(
            [sys.executable, "-m", "shujuan", *args],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        if completed.returncode:
            raise AssertionError(f"bad-DB No Governance command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
        payload = json.loads(completed.stdout)
        if payload.get("recommended_route") != "No Governance" or payload.get("recommended_mode") != "no_governance":
            raise AssertionError(f"bad-DB command did not exit No Governance for {' '.join(args)}: {payload}")
        if payload.get("db_writes") != 0 or payload.get("capture_claim") is not False or payload.get("stop_writes") is not True:
            raise AssertionError(f"bad-DB No Governance payload did not expose write stop for {' '.join(args)}: {payload}")
        if (repo / ".shujuan").exists():
            raise AssertionError(f"bad-DB No Governance command created .shujuan for {' '.join(args)}")


def _assert_route_matrix(fixture, cases: list[dict[str, object]]) -> None:
    for case in cases:
        payload = fixture.run_json("route", "guard", "--intent", str(case["intent"]))
        expected_route = case["route"]
        if payload["recommended_route"] != expected_route:
            raise AssertionError(f"route mismatch for {case['intent']!r}: {payload}")
        if payload["relation_decision"]["relation_type"] != case["relation_type"]:
            raise AssertionError(f"relation mismatch for {case['intent']!r}: {payload}")
        if payload["relation_decision"]["decision_type"] != case["decision_type"]:
            raise AssertionError(f"decision mismatch for {case['intent']!r}: {payload}")
        if payload["authority_posture"] != case["authority_posture"]:
            raise AssertionError(f"authority mismatch for {case['intent']!r}: {payload}")
        if payload["relation_decision"]["predecessor_required"] is not case["predecessor_required"]:
            raise AssertionError(f"predecessor requirement mismatch for {case['intent']!r}: {payload}")
        if payload["recommended_mode"] != case["mode"]:
            raise AssertionError(f"mode mismatch for {case['intent']!r}: {payload}")


def main() -> int:
    _bad_db_no_governance_assertion()
    for intent in (
        "请不要使用shujuan处理这个问题",
        "这次不要使用shujuan处理这个问题",
        "请不要记录，帮我看看是否有问题",
        "请不要记录这次验收，直接告诉我是否通过",
        "请验收这个修复，不要记录",
        "验收一下，不走流程",
        "继续处理，不要落库",
        "解释一下不要记录，并且这次不要记录",
        "这次不用治理，直接回答",
        "本次不做治理，只解释一下",
        "本次不要落库，只回答",
        "do not record this task, just answer",
    ):
        _assert_no_governance_without_side_effects(intent)
    _assert_no_governance_default_cwd_without_side_effects("do not use governance for this task")
    _assert_capture_gate_without_side_effects(["workflow", "begin", "--endpoint", "ghost"], "不要记录这次任务，直接回答")
    _assert_capture_gate_without_side_effects(["workflow", "begin", "--endpoint", "ghost"], "解释一下不要记录，并且这次不要记录")
    _assert_capture_gate_without_side_effects(["workflow", "begin", "--endpoint", "ghost"], "这次不用治理，直接回答")
    _assert_capture_gate_without_side_effects(["hook", "user-prompt"], "do not record this task, just answer")
    _assert_capture_gate_without_side_effects(["hook", "user-prompt"], "解释一下不要记录，并且这次不要记录")
    _assert_capture_gate_without_side_effects(["hook", "user-prompt"], "本次不做治理，只解释一下")
    _assert_bad_db_command_no_governance_without_side_effects(["workflow", "begin", "--endpoint", "ep", "--content", "请验收这个修复，不要记录"])
    _assert_bad_db_command_no_governance_without_side_effects(["workflow", "begin", "--endpoint", "ep", "--content", "继续处理，不要落库"])
    _assert_bad_db_command_no_governance_without_side_effects(["workflow", "begin", "--endpoint", "ep", "--content", "验收一下，不走流程"])
    _assert_bad_db_command_no_governance_without_side_effects(["hook", "user-prompt", "--content", "请不要记录这次任务，直接回答"])
    _assert_bad_db_command_no_governance_without_side_effects(["work", "start", "--endpoint", "ep", "--content", "请不要记录这次任务，直接回答"])
    _assert_bad_db_command_no_governance_without_side_effects(["exec", "start", "--endpoint", "ep", "--summary", "请不要记录这次任务，直接回答"])
    fixture_pair = postgres_fixture("v10-route-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    temp, fixture = fixture_pair
    try:
        source = fixture.repo / "source.md"
        source.write_text("# v10 route\n\nsource\n", encoding="utf-8")
        doc = fixture.run_json("doc", "import", "source.md", "--source-type", "plan")
        scope = fixture.run_json("scope", "create", "--body", "v10 route scope", "--source-node", doc["document_node_id"])
        fixture.run_json("endpoint", "create", "v10-route", "--root-node", scope["node_id"])

        execution_only_task = fixture.run_json(
            "task",
            "add",
            "--body",
            "Execution-only task must remain visible through the endpoint run chain.",
            "--from-node",
            doc["document_node_id"],
        )
        execution_only_check = fixture.run_json(
            "acceptance",
            "add",
            "--task",
            execution_only_task["task_id"],
            "--body",
            "Execution-only check is visible in endpoint status while open.",
            "--expected-evidence-type",
            "test_result",
            "--from-node",
            doc["document_node_id"],
        )
        fixture.run_json(
            "workflow",
            "begin",
            "--session-id",
            "v10-route-execution-only",
            "--endpoint",
            "v10-route",
            "--content",
            "Exercise endpoint projection for a run-linked task.",
        )
        fixture.run_json(
            "exec",
            "start",
            "--session-id",
            "v10-route-execution-only",
            "--endpoint",
            "v10-route",
            "--task-node",
            execution_only_task["task_id"],
            "--summary",
            "Start an endpoint-scoped execution-only task.",
        )
        run_status = fixture.run_json("endpoint", "status", "v10-route")
        if execution_only_task["task_id"] not in {item["id"] for item in run_status.get("current_tasks") or []}:
            raise AssertionError(f"endpoint status did not include run-linked task: {run_status}")
        if execution_only_check["acceptance_check_id"] not in {item["id"] for item in run_status.get("open_checks") or []}:
            raise AssertionError(f"endpoint status did not include run-linked check: {run_status}")

        trace_path = fixture.repo / ".shujuan" / "trace" / "workflow_trace.jsonl"
        before_trace = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
        no_gov = fixture.run_json(
            "route",
            "guard",
            "--endpoint",
            "v10-route",
            "--intent",
            "不要使用shujuan，也不要记录，直接告诉我这个是否通过验收",
        )
        after_trace = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
        if no_gov["recommended_route"] != "No Governance" or no_gov["relation_decision"]["decision_type"] != "no_write":
            raise AssertionError(f"close wording overrode No Governance: {no_gov}")
        if after_trace != before_trace:
            raise AssertionError("No Governance route wrote trace without explicit --trace")

        independent_review = fixture.run_json(
            "route",
            "guard",
            "--endpoint",
            "v10-route",
            "--intent",
            "单独检查这个修复是否通过",
        )
        if independent_review["recommended_route"] == "No Governance":
            raise AssertionError(f"independent review still routed to No Governance: {independent_review}")
        if independent_review["relation_decision"]["relation_type"] != "independent_review":
            raise AssertionError(f"independent review relation was not exposed: {independent_review}")
        if independent_review["authority_posture"] != "reviewer_material":
            raise AssertionError(f"independent review authority posture drifted: {independent_review}")
        if independent_review["relation_decision"]["predecessor_required"] is not True:
            raise AssertionError(f"independent review did not require a review target: {independent_review}")

        review_close_question = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "独立审查这个 PR 是否可以 close",
        )
        if review_close_question["recommended_route"] != "Delegate":
            raise AssertionError(f"review close question fell into closeout: {review_close_question}")
        if review_close_question["relation_decision"]["predecessor_required"] is not True or not review_close_question["relation_decision"]["predecessor_hint"]:
            raise AssertionError(f"review close question missed predecessor hinting: {review_close_question}")

        continuation = fixture.run_json(
            "route",
            "guard",
            "--endpoint",
            "v10-route",
            "--intent",
            "继续接手这个endpoint的工作",
        )
        if continuation["recommended_route"] != "Recover" or continuation["recommended_mode"] != "explore":
            raise AssertionError(f"continuation did not stay on Recover/explore: {continuation}")
        if continuation["relation_decision"]["relation_type"] != "continuation" or continuation["exit_brake"]["no_governance"]:
            raise AssertionError(f"continuation was confused with No Governance: {continuation}")

        successor = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "基于 v9 的问题推进 v10",
        )
        if successor["relation_decision"]["relation_type"] != "successor_scope":
            raise AssertionError(f"successor scope was not classified: {successor}")
        if successor["relation_decision"]["predecessor_required"] is not True or not successor["relation_decision"]["predecessor_hint"]:
            raise AssertionError(f"successor scope did not surface predecessor hints: {successor}")

        vector = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "implement retrieval based on vector search",
        )
        if vector["relation_decision"]["relation_type"] == "successor_scope":
            raise AssertionError(f"plain vector wording falsely matched successor scope: {vector}")

        no_gov_topic = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "总结一下为什么不要记录重复任务",
        )
        if no_gov_topic["recommended_route"] == "No Governance":
            raise AssertionError(f"topic discussion of no-record wording became No Governance: {no_gov_topic}")

        no_gov_meta = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "解释“不要使用 shujuan”这句话是什么意思",
        )
        if no_gov_meta["recommended_route"] == "No Governance":
            raise AssertionError(f"meta discussion of no-governance wording became No Governance: {no_gov_meta}")

        no_gov_definition = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "不要记录是什么意思",
        )
        if no_gov_definition["recommended_route"] == "No Governance":
            raise AssertionError(f"definition question of no-record wording became No Governance: {no_gov_definition}")

        no_gov_philosophy = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "解释一下不要记录的治理含义",
        )
        if no_gov_philosophy["recommended_route"] == "No Governance":
            raise AssertionError(f"philosophy question of no-record wording became No Governance: {no_gov_philosophy}")

        fixed_relation_fields = {
            "relation_type",
            "decision_type",
            "confidence",
            "evidence_phrases",
            "predecessor_required",
            "predecessor_hint",
            "authority_posture",
            "authority_hint",
            "route_hint",
            "mode_hint",
        }
        for intent in ("承接v9的mapping问题", "前一版遗留问题", "v10之后的下一步工作"):
            lineage = fixture.run_json("route", "guard", "--intent", intent)
            if lineage["recommended_route"] != "Recover" or lineage["recommended_mode"] != "explore":
                raise AssertionError(f"lineage intent did not enter Recover/explore for {intent!r}: {lineage}")
            if lineage["relation_decision"]["relation_type"] not in {"continuation", "successor_scope"}:
                raise AssertionError(f"lineage intent had wrong relation type for {intent!r}: {lineage}")
            if lineage["relation_decision"]["predecessor_required"] is not True:
                raise AssertionError(f"lineage intent did not require predecessor for {intent!r}: {lineage}")
            if not fixed_relation_fields.issubset(lineage["relation_decision"]):
                raise AssertionError(f"lineage relation_decision lost fixed fields for {intent!r}: {lineage}")

        for intent in ("follow-up leftover work", "remaining carry over task", "next step after v10"):
            lineage = fixture.run_json("route", "guard", "--intent", intent)
            if lineage["recommended_route"] != "Recover" or lineage["relation_decision"]["predecessor_required"] is not True:
                raise AssertionError(f"English lineage intent did not bind predecessor for {intent!r}: {lineage}")

        for intent, decision in (("把T1删除", "delete"), ("remove check C1", "delete"), ("defer T1 to backlog", "defer")):
            state_change = fixture.run_json("route", "guard", "--intent", intent)
            if state_change["recommended_route"] != "Recover":
                raise AssertionError(f"state-change ID intent did not enter Recover for {intent!r}: {state_change}")
            relation = state_change["relation_decision"]
            if relation["decision_type"] != decision or relation["predecessor_required"] is not True:
                raise AssertionError(f"state-change ID intent had wrong decision/predecessor for {intent!r}: {state_change}")
            if relation.get("target_binding_required") is not True or not relation.get("target_hint"):
                raise AssertionError(f"state-change ID intent did not expose target binding for {intent!r}: {state_change}")

        for importer_intent in ("把这个任务链导入数据库", "把这些条目拆成任务和检查", "把下面内容转成 task/check", "把下面内容转成任务和验收标准"):
            importer = fixture.run_json(
                "route",
                "guard",
                "--intent",
                importer_intent,
            )
            if importer["recommended_route"] != "Recover" or importer["recommended_mode"] != "explore":
                raise AssertionError(f"Chinese task-chain import did not enter read-only Recover/explore for {importer_intent!r}: {importer}")
            if importer["exit_brake"]["stop_writes"] is not True or "import-task-chain --dry-run" not in importer["safe_next_action"]:
                raise AssertionError(f"Chinese task-chain import missed formal dry-run gate for {importer_intent!r}: {importer}")

        acceptance_question = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "这个是否通过验收",
        )
        if acceptance_question["recommended_route"] != "Delegate" or acceptance_question["ok"] is not True:
            raise AssertionError(f"acceptance question did not stay in review material lane: {acceptance_question}")
        if acceptance_question["authority_posture"] == "controller_close":
            raise AssertionError(f"acceptance question still exposed closeout authority: {acceptance_question}")

        acceptance_request = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "请验收这个修复",
        )
        if acceptance_request["recommended_route"] != "Delegate" or acceptance_request["ok"] is not True:
            raise AssertionError(f"acceptance request did not stay out of closeout execution: {acceptance_request}")

        missing_closeout = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "执行验收",
            expect_ok=False,
        )
        if missing_closeout["error"]["code"] != "missing_closeout_inputs":
            raise AssertionError(f"execution closeout without inputs did not fail closed: {missing_closeout}")

        negated_review = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "单独写一份报告，不需要审查",
        )
        if negated_review["recommended_route"] == "Delegate":
            raise AssertionError(f"negated review wording still entered Delegate: {negated_review}")

        for negated_intent in (
            "不需要审查，继续处理这个任务 --endpoint ep",
            "无需 reviewer，继续处理这个任务",
            "不需要独立审查，继续处理",
            "skip review and continue this task",
            "skip the reviewer and continue",
        ):
            negated_continuation = fixture.run_json(
                "route",
                "guard",
                "--endpoint",
                "v10-route",
                "--intent",
                negated_intent,
            )
            if negated_continuation["recommended_route"] != "Recover" or negated_continuation["relation_decision"]["decision_type"] == "material_only":
                raise AssertionError(f"negated review continuation entered material_only for {negated_intent!r}: {negated_continuation}")

        reviewer_packet = fixture.run_json(
            "route",
            "guard",
            "--intent",
            "生成 reviewer packet",
        )
        if reviewer_packet["recommended_route"] != "Delegate":
            raise AssertionError(f"reviewer packet request did not enter Delegate: {reviewer_packet}")

        _assert_route_matrix(
            fixture,
            [
                {
                    "intent": "帮我看看这个能不能过验收",
                    "route": "Delegate",
                    "relation_type": "independent_review",
                    "decision_type": "material_only",
                    "authority_posture": "reviewer_material",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "这个修复能不能过验收",
                    "route": "Delegate",
                    "relation_type": "independent_review",
                    "decision_type": "material_only",
                    "authority_posture": "reviewer_material",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "验收一下这个修复",
                    "route": "Delegate",
                    "relation_type": "independent_review",
                    "decision_type": "material_only",
                    "authority_posture": "reviewer_material",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "看看这个是否可以验收",
                    "route": "Delegate",
                    "relation_type": "independent_review",
                    "decision_type": "material_only",
                    "authority_posture": "reviewer_material",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "这个修复通过了吗",
                    "route": "Delegate",
                    "relation_type": "independent_review",
                    "decision_type": "material_only",
                    "authority_posture": "reviewer_material",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "看一下是否通过",
                    "route": "Delegate",
                    "relation_type": "independent_review",
                    "decision_type": "material_only",
                    "authority_posture": "reviewer_material",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "review if this passes",
                    "route": "Delegate",
                    "relation_type": "independent_review",
                    "decision_type": "material_only",
                    "authority_posture": "reviewer_material",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "please review this fix",
                    "route": "Delegate",
                    "relation_type": "independent_review",
                    "decision_type": "material_only",
                    "authority_posture": "reviewer_material",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "separately check if this fix passes",
                    "route": "Delegate",
                    "relation_type": "independent_review",
                    "decision_type": "material_only",
                    "authority_posture": "reviewer_material",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "这个任务是上一版的后续",
                    "route": "Recover",
                    "relation_type": "continuation",
                    "decision_type": "update",
                    "authority_posture": "controller_recover",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "基于之前的报告做下一版",
                    "route": "Recover",
                    "relation_type": "successor_scope",
                    "decision_type": "update",
                    "authority_posture": "controller_recover",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "这不是新任务，是前序任务的延续",
                    "route": "Recover",
                    "relation_type": "continuation",
                    "decision_type": "update",
                    "authority_posture": "controller_recover",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "删除这个任务",
                    "route": "Recover",
                    "relation_type": "continuation",
                    "decision_type": "delete",
                    "authority_posture": "controller_recover",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "推迟这个检查",
                    "route": "Recover",
                    "relation_type": "continuation",
                    "decision_type": "defer",
                    "authority_posture": "controller_recover",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "提升这个问题优先级",
                    "route": "Recover",
                    "relation_type": "continuation",
                    "decision_type": "promote",
                    "authority_posture": "controller_recover",
                    "predecessor_required": True,
                    "mode": "explore",
                },
                {
                    "intent": "需要裁决这个问题",
                    "route": "Recover",
                    "relation_type": "continuation",
                    "decision_type": "escalate",
                    "authority_posture": "controller_recover",
                    "predecessor_required": True,
                    "mode": "explore",
                },
            ],
        )

        close_complete = fixture.run_json(
            "route",
            "guard",
            "--endpoint",
            "v10-route",
            "--intent",
            "close this check with evidence",
            "--task-id",
            "task_fake",
            "--check-id",
            "check_fake",
            "--expected-evidence-type",
            "artifact",
            "--current-matching-evidence-ref",
            "node_fake",
        )
        if close_complete["recommended_route"] != "Close":
            raise AssertionError(f"complete closeout inputs did not route to Close: {close_complete}")
        if "Run the Close chain" not in close_complete["safe_next_action"]:
            raise AssertionError(f"complete closeout hint stayed in missing-input mode: {close_complete}")

        print(json.dumps({"ok": True, "v10_route_relation_regressions": "passed"}))
        return 0
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
