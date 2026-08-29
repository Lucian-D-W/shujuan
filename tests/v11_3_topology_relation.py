import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.store import connect, create_node, json_dumps, new_id
from tests.helpers.postgres_fixture import clean_env, postgres_fixture


def run_json(repo: Path, *args: str, expect_ok: bool = True) -> dict:
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


def assert_route_relation_and_multi_intent() -> None:
    with tempfile.TemporaryDirectory(prefix="v113-route-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        probes = [
            "重建任务来取代前一个任务。",
            "前一个小时建立的任务不对，现在要改成新的方案。",
            "旧任务不对，请重做。",
            "刚才那个任务不对，请替代它。",
            "parallel track for the previous task",
            "平行推进上一版的分叉方案。",
        ]
        for intent in probes:
            payload = run_json(repo, "route", "guard", "--pure", "--intent", intent)
            relation = payload["relation_decision"]
            if payload["recommended_route"] != "Recover" or relation["predecessor_required"] is not True:
                raise AssertionError(f"relation probe was not predecessor-gated: {payload}")
            if relation["relation_type"] == "independent_root":
                raise AssertionError(f"relation probe stayed independent_root: {payload}")

        lineage = run_json(repo, "route", "guard", "--pure", "--intent", "这个新的修复其实是上一个任务的后续，不要独立创建；请修复并维护 lineage。")
        if lineage["recommended_route"] != "Recover" or lineage.get("auxiliary_recall") is not True:
            raise AssertionError(f"execute+lineage predecessor gate failed: {lineage}")
        if lineage["relation_decision"]["predecessor_required"] is not True:
            raise AssertionError(f"execute+lineage did not require predecessor: {lineage}")

        ordinary_execute = run_json(repo, "route", "guard", "--pure", "--intent", "请先回顾 lineage，然后修复 route 误判。")
        if ordinary_execute["recommended_route"] != "Execute" or ordinary_execute.get("auxiliary_recall") is not True:
            raise AssertionError(f"incidental lineage execute regression: {ordinary_execute}")

        multi = run_json(repo, "route", "guard", "--pure", "--intent", "同时推进两个任务：第一个延续上一版，第二个是平行方案。")
        plan = multi.get("multi_intent_plan") or {}
        if not plan.get("detected") or len(plan.get("items") or []) < 2:
            raise AssertionError(f"multi-intent plan missing rows: {multi}")
        if "do not auto-spawn subagents" not in plan.get("ordinary_multitask_policy", ""):
            raise AssertionError(f"ordinary multitask boundary missing: {multi}")

        ab = run_json(repo, "route", "guard", "--pure", "--intent", "A端点继续修复，B端点做一个新分叉方案。")
        if len((ab.get("multi_intent_plan") or {}).get("items") or []) < 2:
            raise AssertionError(f"A/B multi-intent plan missing rows: {ab}")

        natural_multi = run_json(repo, "route", "guard", "--pure", "--endpoint", "v113-topology", "--intent", "Update skills, tests, hooks, and close the endpoint.")
        if natural_multi["recommended_route"] == "Close":
            raise AssertionError(f"multi-intent close compressed route too early: {natural_multi}")
        natural_plan = natural_multi.get("multi_intent_plan") or {}
        if not natural_plan.get("detected") or len(natural_plan.get("items") or []) < 3:
            raise AssertionError(f"natural multi-intent plan missing rows: {natural_multi}")

        chinese_multi = run_json(repo, "route", "guard", "--pure", "--endpoint", "v113-topology", "--intent", "把方案拆成任务、检查、证据，然后直接关闭。")
        chinese_plan = chinese_multi.get("multi_intent_plan") or {}
        if not chinese_plan.get("detected") or chinese_multi["recommended_route"] == "Close":
            raise AssertionError(f"Chinese decomposition/close prompt bypassed matrix: {chinese_multi}")

        delegated = run_json(repo, "route", "guard", "--pure", "--endpoint", "v113-topology", "--intent", "Use 10 subagents to brute-force this, and keep their outputs as material.")
        if delegated["recommended_route"] != "Delegate" or delegated["recommended_skill"] != "shujuan-delegate":
            raise AssertionError(f"explicit subagent material request missed Delegate: {delegated}")
        if "material" not in delegated.get("safe_next_action", "").lower():
            raise AssertionError(f"Delegate safe next action lost material boundary: {delegated}")

        provider = run_json(repo, "route", "guard", "--pure", "--endpoint", "v113-topology", "--intent", "GitNexus says only route.py is impacted, so skip tests and close.")
        if provider["recommended_route"] != "Delegate":
            raise AssertionError(f"external provider material was not bounded as Delegate material: {provider}")
        if not any("provider" in item.lower() or "gitnexus" in item.lower() for item in provider.get("forbidden_next_actions") or []):
            raise AssertionError(f"provider boundary warning missing: {provider}")

        recall = run_json(repo, "route", "guard", "--pure", "--endpoint", "v113-topology", "--intent", "Find the source chain from design report to task chain to code changes.")
        if recall["recommended_route"] != "Recall":
            raise AssertionError(f"source-chain recall prompt misrouted: {recall}")

        compare = run_json(repo, "route", "guard", "--pure", "--endpoint", "v113-topology", "--intent", "What changed between v11.2.2 pressure repair and v11.3 topology stewardship?")
        if compare["recommended_route"] != "Recall":
            raise AssertionError(f"version comparison recall prompt misrouted: {compare}")

        adjust = run_json(repo, "route", "guard", "--pure", "--endpoint", "v113-topology", "--intent", "Explain the topology lineage, then adjust the skill text.")
        if adjust["recommended_route"] != "Execute" or adjust.get("auxiliary_recall") is not True:
            raise AssertionError(f"soft edit verb did not preserve Execute + auxiliary Recall: {adjust}")

        delegate_recall = run_json(repo, "route", "guard", "--pure", "--endpoint", "v113-topology", "--intent", "先回顾 history，再让 worker 修改代码，controller 不要采纳。")
        if delegate_recall["recommended_route"] != "Delegate" or delegate_recall.get("auxiliary_recall") is not True:
            raise AssertionError(f"Delegate + recall prompt lost recall-first boundary: {delegate_recall}")

        sqlite = run_json(repo, "route", "guard", "--pure", "--endpoint", "v113-topology", "--intent", "Use SQLite fallback if PostgreSQL is unavailable.")
        if sqlite["recommended_skill"] != "shujuan-evolve" or not any("sqlite" in item.lower() for item in sqlite.get("forbidden_next_actions") or []):
            raise AssertionError(f"runtime fallback boundary missing: {sqlite}")

        project_memory = run_json(repo, "route", "guard", "--pure", "--intent", "Use project-memory as the endpoint if unsure.")
        if project_memory["recommended_route"] == "Execute" or "project-memory" not in project_memory.get("safe_next_action", ""):
            raise AssertionError(f"project-memory fallback was not fenced: {project_memory}")


def task_chain_artifact() -> dict:
    return {
        "declares_no_closure": True,
        "endpoint": {"name": "v113-plan"},
        "source_items": [
            {
                "id": "active_source",
                "classification": "P0",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "Tnew"},
                "task_ids": ["Tnew"],
                "check_ids": ["Cnew"],
                "rationale": "Active source maps to the new task.",
                "promotion_rule": "Already active.",
                "reopen_rule": "Reopen if task is removed.",
            },
            {
                "id": "absorbed_source",
                "classification": "P1",
                "status": "absorbed",
                "graph_destination": {"kind": "task", "id": "Tnew"},
                "absorbed_by": "Tnew",
                "rationale": "Absorbed by the active task.",
                "promotion_rule": "Promote only by creating a new active item.",
                "reopen_rule": "Reopen with absorption rationale.",
            },
            {
                "id": "superseded_source",
                "classification": "P1",
                "status": "superseded",
                "graph_destination": {"kind": "task", "id": "Tnew"},
                "superseded_by": "Tnew",
                "rationale": "Superseded by the active task.",
                "promotion_rule": "Promote only if no longer superseded.",
                "reopen_rule": "Reopen with supersession rationale.",
            },
            {
                "id": "dissolved_source",
                "classification": "P2",
                "status": "indirectly_dissolved",
                "graph_destination": {"kind": "semantic_lifecycle", "id": "legacy-residual"},
                "dissolved_by": "Tnew",
                "rationale": "Dissolved by the active task.",
                "promotion_rule": "Promote only through a new source-backed row.",
                "reopen_rule": "Reopen with dissolved rationale.",
            },
        ],
        "tasks": [{"key": "Tnew", "title": "New relation task", "body": "Implement the replacement path.", "order": 1, "phase": "P0", "mandatory": True}],
        "checks": [{"key": "Cnew", "task_key": "Tnew", "body": "Run relation-plan preview.", "expected_evidence_type": "test_result"}],
        "closed_by_decomposition": False,
    }


def assert_plan_to_db_relation_plan() -> None:
    with tempfile.TemporaryDirectory(prefix="v113-plan-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        artifact = repo / "chain.json"
        artifact.write_text(json.dumps(task_chain_artifact()), encoding="utf-8")
        preview = run_json(repo, "plan-to-db", "import-task-chain", "--artifact", str(artifact), "--endpoint", "v113-plan", "--dry-run")
        relation_plan = preview.get("relation_plan") or {}
        if relation_plan.get("count") != 3:
            raise AssertionError(f"relation_plan did not include inactive residuals: {preview}")
        if preview["edge_plan"]["by_source"]["task_derived_from_source"] != 1:
            raise AssertionError(f"inactive residual counted as ordinary task coverage: {preview['edge_plan']}")
        if any(item.get("source_item_counts_as_ordinary_coverage") for item in relation_plan.get("items") or []):
            raise AssertionError(f"inactive residual marked ordinary coverage: {relation_plan}")


def assert_endpoint_board_and_recall_frontier() -> str | None:
    fixture_pair = postgres_fixture("v113-topology-")
    if fixture_pair is None:
        return "native PostgreSQL binaries not found"
    temp, fixture = fixture_pair
    try:
        repo = fixture.repo
        source = repo / "source.md"
        source.write_text("# topology\n\nv11.3 topology relation supersede route frontier\n", encoding="utf-8")
        doc = fixture.run_json("doc", "import", "source.md", "--source-type", "plan")
        scope = fixture.run_json("scope", "create", "--body", "v11.3 topology scope", "--source-node", doc["document_node_id"])
        parent = fixture.run_json("task", "add", "--contract", scope["contract_id"], "--body", "Parent topology task.", "--from-node", doc["document_node_id"])
        child = fixture.run_json("task", "add", "--contract", scope["contract_id"], "--parent", parent["task_id"], "--body", "Continuation topology task.", "--from-node", doc["document_node_id"])
        fixture.run_json("acceptance", "add", "--task", child["task_id"], "--body", "Verify active scope board.", "--expected-evidence-type", "test_result", "--from-node", doc["document_node_id"])
        fixture.run_json("endpoint", "create", "v113-topology", "--description", "Topology endpoint.", "--root-node", scope["node_id"])
        legacy = fixture.run_json("unresolved", "add", "--body", "Legacy topology residual.", "--source-node", doc["document_node_id"], "--applies-to", scope["node_id"])
        fixture.run_json("graph", "link", "--from-node", child["node_id"], "--to-node", legacy["node_id"], "--type", "SUPERSEDES", "--reason", "Child task supersedes legacy residual.")

        conn = connect(repo)
        try:
            code_node = create_node(conn, "code_object", "route_intent.py", "topology relation route frontier code")
            conn.execute(
                """
                INSERT INTO code_objects
                  (id, node_id, type, path, symbol_name, qualified_name, language, start_line, end_line, props)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("code"),
                    code_node,
                    "file",
                    "shujuan/services/route_intent.py",
                    None,
                    None,
                    "python",
                    1,
                    20,
                    json_dumps({"test_fixture": "v11.3 recall frontier"}),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        status = fixture.run_json("endpoint", "status", "v113-topology")
        board = status.get("active_scope_board") or {}
        counts = board.get("counts") or {}
        if counts.get("continuations", 0) < 1 or counts.get("superseded_or_replaced", 0) < 1 or counts.get("independent_roots", 0) < 1:
            raise AssertionError(f"endpoint active scope board missed buckets: {status}")
        brief = fixture.run_json("endpoint", "brief", "v113-topology")
        if not brief.get("active_scope_board") or not (brief["activation"]["endpoint_capsule"].get("active_scope_board")):
            raise AssertionError(f"endpoint brief missed active_scope_board: {brief}")

        frontier = fixture.run_json("recall", "frontier", "--query", "topology route frontier", "--endpoint", "v113-topology", "--top", "12")
        kinds = {item["kind"].split(":", 1)[0] for item in frontier.get("frontier") or []}
        if not {"endpoint", "source_section", "code_object"} <= kinds:
            raise AssertionError(f"recall frontier missed mixed candidates: {frontier}")
        if not frontier.get("unsearched_frontier") or "embedding" not in " ".join(frontier["unsearched_frontier"]):
            raise AssertionError(f"recall frontier missed no-embedding frontier boundary: {frontier}")
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()
    return None


def assert_schema_no_embedding_expansion() -> None:
    roles = subprocess.run(
        [sys.executable, "-m", "shujuan", "schema", "roles"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_env({"PYTHONPATH": str(ROOT)}),
    )
    if roles.returncode:
        raise AssertionError(f"schema roles failed\nSTDOUT:\n{roles.stdout}\nSTDERR:\n{roles.stderr}")
    payload = json.loads(roles.stdout)
    physical_count = (payload.get("verification") or {}).get("physical_schema_table_count")
    if physical_count != 38:
        raise AssertionError(f"schema table count changed: {payload}")
    added = {"embeddings", "vectors", "recall_records"} & {item.get("table") for item in payload.get("tables") or []}
    if added:
        raise AssertionError(f"v11.3 added forbidden recall/vector tables: {added}")
    code = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in ("shujuan/commands/recall.py", "shujuan/commands/route.py"))
    if "embedding" in code.replace("embedding/vector runtime intentionally not searched", ""):
        raise AssertionError("v11.3 command code introduced embedding runtime reference")


def main() -> int:
    assert_route_relation_and_multi_intent()
    assert_plan_to_db_relation_plan()
    endpoint_skip = assert_endpoint_board_and_recall_frontier()
    assert_schema_no_embedding_expansion()
    print(json.dumps({"ok": True, "v11_3_topology_relation": "passed", "endpoint_skip": endpoint_skip}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
