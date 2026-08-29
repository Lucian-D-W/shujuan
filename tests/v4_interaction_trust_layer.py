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


def run_cli(repo: Path, *args: str) -> dict[str, object]:
    return json.loads(run_cli_completed(repo, *args).stdout)


def run_cli_completed(repo: Path, *args: str, expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
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


def run_cli_fails(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_cli_completed(repo, *args, expect_ok=False)


def db_counts(repo: Path) -> dict[str, int]:
    conn = connect(repo)
    try:
        tables = [
            "tasks",
            "acceptance_checks",
            "agent_runs",
            "change_sets",
            "interaction_events",
            "discussion_segments",
            "discussion_messages",
        ]
        counts: dict[str, int] = {}
        for table in tables:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            counts[table] = int(row["count"] if isinstance(row, dict) else row[0])
        return counts
    finally:
        conn.close()


def assert_no_delta(before: dict[str, int], after: dict[str, int], keys: set[str]) -> None:
    changed = {key: (before[key], after[key]) for key in keys if before[key] != after[key]}
    if changed:
        raise AssertionError(f"unexpected DB count changes: {changed}")


def assert_v4_docs_frozen() -> None:
    required_terms = [
        "interaction_event",
        "discussion_segment",
        "mode_router",
        "No Governance",
        "projection payload",
        "read-only workbench",
        "hidden_source_count",
        "detail_ref",
    ]
    modes_terms = (ROOT / ".agents" / "skills" / "shujuan-core" / "references" / "modes-and-terms.md").read_text(encoding="utf-8")
    for term in required_terms:
        if term not in modes_terms:
            raise AssertionError(f"v4 term missing from the maintained compatibility reference: {term}")


def assert_projection_view(payload: dict[str, object], view: str) -> None:
    view_payload = payload["views"][view]
    if view_payload["broken_visible_chain_count"]:
        raise AssertionError(f"projection view has broken visible chains: {view_payload}")
    for item in view_payload["items"]:
        if "hidden_source_count" not in item or not item.get("detail_ref"):
            raise AssertionError(f"projection item omitted traceability fields: {item}")
        if "visual" not in item or "state" not in item["visual"]:
            raise AssertionError(f"projection item omitted visual state metadata: {item}")
        if "visible_edges" not in item:
            raise AssertionError(f"projection item omitted edge style/confidence metadata: {item}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    assert_v4_docs_frozen()
    if not (ROOT / "docs" / "shujuan_v4_interaction_trust_layer.md").exists():
        print(
            json.dumps(
                {
                    "ok": True,
                    "v4_interaction_trust_layer": "legacy_runtime_suite_skipped",
                    "reason": "v4 frozen design fixture was intentionally removed from the canonical v11 public tree",
                    "compatibility_terms_verified": True,
                }
            )
        )
        return 0
    with tempfile.TemporaryDirectory(prefix="shujuan-v4-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        init_payload = run_cli(repo, "init", "--name", "v4", "--postgres-dev", "--postgres-dev-port", str(free_port()))
        if init_payload["database"]["backend"] != "postgres":
            raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init_payload}")
        generated_modes_terms = (repo / ".agents" / "skills" / "shujuan-core" / "references" / "modes-and-terms.md").read_text(encoding="utf-8")
        if "discussion_segment" not in generated_modes_terms or "mode_router" not in generated_modes_terms:
            raise AssertionError("init templates did not preserve v4 terms in the maintained compatibility reference")

        (repo / "plan.md").write_text("# v4 plan\n\nInteraction trust layer scope.\n", encoding="utf-8")
        doc = run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan")
        scope = run_cli(repo, "scope", "create", "--body", "v4 scope", "--source-node", doc["document_node_id"])
        task = run_cli(repo, "task", "add", "--body", "Implement v4 behavior", "--contract", scope["contract_id"], "--from-node", doc["document_node_id"])
        check = run_cli(
            repo,
            "acceptance",
            "add",
            "--task",
            task["task_id"],
            "--body",
            "v4 behavior is repeatably verified",
            "--expected-evidence-type",
            "test_result",
            "--from-node",
            doc["document_node_id"],
        )
        run_cli(repo, "endpoint", "create", "trust", "--root-node", scope["node_id"])

        before_capture = db_counts(repo)
        capture = run_cli(
            repo,
            "discuss",
            "capture",
            "--endpoint",
            "trust",
            "--session-id",
            "session_v4",
            "--content",
            "Capture this discussion as source material only.",
        )
        after_capture = db_counts(repo)
        assert_no_delta(before_capture, after_capture, {"tasks", "acceptance_checks", "agent_runs", "change_sets"})
        if not capture["receipt"] or capture["receipt"]["creates_run"] or capture["receipt"]["creates_task"]:
            raise AssertionError(f"discussion capture receipt claimed governance work: {capture}")
        if after_capture["interaction_events"] != before_capture["interaction_events"] + 1:
            raise AssertionError(f"interaction event was not captured: {before_capture} -> {after_capture}")
        status = run_cli(repo, "discuss", "status", "--endpoint", "trust")
        if status["unreviewed_count"] != 1:
            raise AssertionError(f"discussion status did not expose unreviewed count: {status}")
        endpoint_report = run_cli(repo, "report", "endpoint", "trust", "--active-only")
        if endpoint_report["direction"]["discussion_brief"]["unreviewed_count"] != 1:
            raise AssertionError(f"endpoint brief missed discussion count: {endpoint_report}")
        before_review = db_counts(repo)
        reviewed = run_cli(repo, "discuss", "review", "--endpoint", "trust", "--segment", capture["segment_id"], "--source-node", doc["document_node_id"])
        after_review = db_counts(repo)
        assert_no_delta(before_review, after_review, {"tasks", "acceptance_checks", "agent_runs", "change_sets"})
        if reviewed["status"] != "reviewed":
            raise AssertionError(f"discussion review did not transition status: {reviewed}")
        extracted = run_cli(
            repo,
            "discuss",
            "extract",
            "--endpoint",
            "trust",
            "--segment",
            capture["segment_id"],
            "--type",
            "decision",
            "--label",
            "Captured discussion remains source-first",
            "--summary",
            "Extraction creates only the requested semantic decision.",
        )
        after_extract = db_counts(repo)
        assert_no_delta(after_review, after_extract, {"tasks", "acceptance_checks", "agent_runs", "change_sets"})
        if extracted["structured"]["created"]:
            raise AssertionError(f"decision extraction unexpectedly created task/check rows: {extracted}")
        consumed = run_cli(repo, "discuss", "consume", "--endpoint", "trust", "--segment", capture["segment_id"], "--by-node", task["node_id"])
        if consumed["status"] != "consumed":
            raise AssertionError(f"discussion consume did not transition status: {consumed}")
        replacement = run_cli(
            repo,
            "discuss",
            "replace",
            "--endpoint",
            "trust",
            "--segment",
            capture["segment_id"],
            "--replacement-content",
            "Replacement discussion segment with corrected wording.",
        )
        if replacement["status"] != "superseded" or not replacement["replacement_segment_id"]:
            raise AssertionError(f"discussion replacement did not supersede old segment: {replacement}")
        discussion_detail = run_cli(repo, "graph", "detail", "--node", capture["segment_node_id"])
        lifecycle_types = {event["event_type"] for event in discussion_detail["discussion"]["lifecycle_events"]}
        if not {"captured", "reviewed", "extracted", "consumed", "superseded"} <= lifecycle_types:
            raise AssertionError(f"discussion detail missed lifecycle/provenance: {discussion_detail}")
        discussion_status = run_cli(repo, "discuss", "status", "--endpoint", "trust")
        if discussion_status["status_counts"].get("superseded", 0) < 1:
            raise AssertionError(f"discussion status did not expose transition counts: {discussion_status}")

        (repo / "manual_transcript.jsonl").write_text(
            '{"actor":"user","content":"manual adapter user turn"}\n'
            '{"actor":"assistant","content":"manual adapter assistant turn"}\n',
            encoding="utf-8",
        )
        before_adapter = db_counts(repo)
        adapter_import = run_cli(
            repo,
            "adapter",
            "manual",
            "import",
            "--transcript",
            "manual_transcript.jsonl",
            "--endpoint",
            "trust",
            "--capture-discussion",
            "--session-id",
            "manual_session",
        )
        after_adapter = db_counts(repo)
        assert_no_delta(before_adapter, after_adapter, {"tasks", "acceptance_checks", "agent_runs", "change_sets"})
        if not adapter_import["discussion_capture"] or adapter_import["discussion_capture"]["receipt"]["message_count"] != 2:
            raise AssertionError(f"manual adapter did not create discussion receipt: {adapter_import}")
        adapter_detail = run_cli(repo, "graph", "detail", "--node", adapter_import["discussion_capture"]["segment_node_id"])
        if len(adapter_detail["discussion"]["messages"]) != 2 or not adapter_detail["discussion"]["messages"][0]["content_hash"]:
            raise AssertionError(f"adapter detail missed messages/hashes/source refs: {adapter_detail}")

        suggest = run_cli(repo, "mode", "suggest", "--no-governance", "--intent", "answer a quick status question")
        if suggest["suggested_mode"] != "no_governance" or suggest["contract"]["capture_claim"]:
            raise AssertionError(f"No Governance suggestion was wrong: {suggest}")
        no_governance_examples = [
            "write a separate report without shujuan",
            "单独写一份报告，不走 shujuan",
            "单独写一份报告，不走流程",
            "这次独立整理，不纳入流程",
        ]
        for example in no_governance_examples:
            before_suggest = db_counts(repo)
            routed = run_cli(repo, "mode", "suggest", "--intent", example)
            after_suggest = db_counts(repo)
            assert_no_delta(before_suggest, after_suggest, set(before_suggest))
            if routed["suggested_mode"] != "no_governance" or routed["contract"]["capture_claim"]:
                raise AssertionError(f"explicit no-governance intent routed incorrectly for {example!r}: {routed}")
        before_no = db_counts(repo)
        no_work = run_cli(repo, "work", "start", "--mode", "no-governance", "--content", "answer without recording")
        after_no = db_counts(repo)
        assert_no_delta(before_no, after_no, set(before_no))
        if no_work["capture_claim"] or no_work["current_handle"] is not None:
            raise AssertionError(f"No Governance created a capture/run claim: {no_work}")

        before_explore = db_counts(repo)
        explore = run_cli(repo, "work", "start", "--mode", "explore", "--endpoint", "trust", "--content", "Explore this idea without execution.")
        after_explore = db_counts(repo)
        assert_no_delta(before_explore, after_explore, {"agent_runs", "change_sets", "tasks", "acceptance_checks"})
        if explore["receipt"]["creates_run"] or explore["receipt"]["creates_change_set"]:
            raise AssertionError(f"Explore receipt created execution claims: {explore}")

        invalid_check = run_cli(
            repo,
            "acceptance",
            "add",
            "--task",
            task["task_id"],
            "--body",
            "Invalidated evidence must not close this current check.",
            "--expected-evidence-type",
            "test_result",
            "--from-node",
            doc["document_node_id"],
        )
        invalid_evidence = run_cli(
            repo,
            "evidence",
            "test-result",
            "--from-node",
            doc["document_node_id"],
            "--",
            sys.executable,
            "-c",
            "print('valid before invalidation')",
        )
        evidence_detail = run_cli(repo, "graph", "detail", "--node", invalid_evidence["node_id"])
        record_types = {record["record_type"] for record in evidence_detail["evidence_records"]}
        if not {"stdout", "stderr", "command"} <= record_types:
            raise AssertionError(f"test_result evidence records missed refs/hashes/predicates: {evidence_detail}")
        run_cli(repo, "evidence", "set-state", "--node", invalid_evidence["node_id"], "--state", "invalidated", "--source-node", doc["document_node_id"])
        invalid_close = run_cli_fails(
            repo,
            "acceptance",
            "close",
            "--check",
            invalid_check["acceptance_check_id"],
            "--evidence-node",
            invalid_evidence["node_id"],
        )
        if "only current valid evidence can close" not in invalid_close.stderr:
            raise AssertionError(f"invalidated evidence was not clearly rejected: {invalid_close.stderr}")

        provider_payload = {
            "contract_version": "shujuan.impact_provider.v1",
            "provider": "gitnexus",
            "status": "executed",
            "facts": [
                {"external_id": "gitnexus:unmapped", "fact_type": "impact", "summary": "Unmapped provider output defaults to hypothesis."},
                {"external_id": "gitnexus:mapped", "mapped_node_id": task["node_id"], "fact_type": "impact", "summary": "Mapped provider output still is not closure evidence by default."},
            ],
            "warnings": [{"summary": "Default provider warning remains non-active hypothesis."}],
        }
        (repo / "provider.json").write_text(json.dumps(provider_payload), encoding="utf-8")
        provider_import = run_cli(
            repo,
            "provider",
            "import-json",
            "--endpoint",
            "trust",
            "--source-node",
            doc["document_node_id"],
            "--path",
            "provider.json",
        )
        if {fact["classification"] for fact in provider_import["facts"]} != {"provider_hypothesis"}:
            raise AssertionError(f"provider facts did not default to provider_hypothesis: {provider_import}")
        provider_close = run_cli_fails(
            repo,
            "acceptance",
            "close",
            "--check",
            invalid_check["acceptance_check_id"],
            "--evidence-node",
            provider_import["facts"][0]["node_id"],
        )
        if "requires evidence node type" not in provider_close.stderr:
            raise AssertionError(f"provider output was not rejected as closure evidence: {provider_close.stderr}")
        provider_status = run_cli(repo, "endpoint", "status", "trust")
        if provider_import["warnings"][0]["node_id"] in {item["id"] for item in provider_status["recent_audit_findings"]}:
            raise AssertionError(f"default provider warning became active audit finding: {provider_status}")

        chain_scope = run_cli(repo, "scope", "create", "--body", "discussion lifecycle chain scope", "--source-node", doc["document_node_id"])
        chain_task = run_cli(repo, "task", "add", "--body", "Open child chain task", "--contract", chain_scope["contract_id"], "--from-node", doc["document_node_id"])
        chain_check = run_cli(
            repo,
            "acceptance",
            "add",
            "--task",
            chain_task["task_id"],
            "--body",
            "Open child chain check blocks umbrella closeout.",
            "--expected-evidence-type",
            "test_result",
            "--from-node",
            doc["document_node_id"],
        )
        run_cli(repo, "endpoint", "create", "chain-discussion", "--root-node", chain_scope["node_id"])
        link_child = run_cli(repo, "endpoint", "link-child", "--parent", "trust", "--child", "chain-discussion")
        if link_child["relationship"] != "CHAIN_CHILD":
            raise AssertionError(f"child chain link failed: {link_child}")
        run_cli(repo, "alias", "set", "--kind", "endpoint", "--name", "umbrella", "--target", "trust")
        brief = run_cli(repo, "endpoint", "brief", "@alias.umbrella")
        if brief["chain_brief"]["active_child_count"] != 1 or not brief["active_obligations"]["child_chain_blockers"]:
            raise AssertionError(f"endpoint brief did not expose child chain blocker: {brief}")
        child_doctor = run_cli_completed(repo, "endpoint", "doctor", "trust", "--strict-closeout", "--allow-fail")
        doctor_payload = json.loads(child_doctor.stdout)
        if not any(item["code"] == "active_child_chain_obligations" for item in doctor_payload["severity_buckets"]["P0"]):
            raise AssertionError(f"strict doctor did not block umbrella with active child chain: {doctor_payload}")
        chain_projection = run_cli(repo, "graph", "projection", "--endpoint", "trust", "--view", "attention")
        if not any(item["kind"] == "child_chain" and item["raw"]["endpoint"] == "chain-discussion" for item in chain_projection["views"]["attention"]["items"]):
            raise AssertionError(f"attention projection missed child chain status: {chain_projection}")
        if chain_check["acceptance_check_id"] not in json.dumps(brief, sort_keys=True):
            raise AssertionError("child chain open check was not traceable from umbrella brief")

        other_scope = run_cli(repo, "scope", "create", "--body", "unrelated scope", "--source-node", doc["document_node_id"])
        other_task = run_cli(
            repo,
            "task",
            "add",
            "--body",
            "Unrelated endpoint task must not pollute trust stop-check.",
            "--contract",
            other_scope["contract_id"],
            "--from-node",
            doc["document_node_id"],
        )
        other_check = run_cli(
            repo,
            "acceptance",
            "add",
            "--task",
            other_task["task_id"],
            "--body",
            "Unrelated endpoint check must stay out of trust stop-check.",
            "--expected-evidence-type",
            "test_result",
            "--from-node",
            doc["document_node_id"],
        )
        run_cli(repo, "endpoint", "create", "other", "--root-node", other_scope["node_id"])
        run_cli(repo, "workflow", "begin", "--session-id", "session_stop_scope", "--endpoint", "trust", "--content", "Stop-check should stay scoped.")
        stop_scope_start = run_cli(repo, "exec", "start", "--endpoint", "trust", "--task-node", task["node_id"], "--session-id", "session_stop_scope", "--summary", "Scoped stop-check run")
        (repo / "scoped_stop.txt").write_text("endpoint scoped stop check\n", encoding="utf-8")
        scoped_stop = run_cli(repo, "exec", "stop", "--endpoint", "trust", "--summary", "Scoped stop-check stop")
        stop_check = scoped_stop["stop_check"]
        if stop_check["endpoint"] != "trust" or stop_check["scope_mode"] != "endpoint_scope":
            raise AssertionError(f"exec stop did not report endpoint-scoped stop-check: {stop_check}")
        stop_blob = json.dumps(stop_check, sort_keys=True)
        closeout_blob = json.dumps(scoped_stop["endpoint_closeout"], sort_keys=True)
        if other_task["task_id"] in stop_blob or other_check["acceptance_check_id"] in stop_blob or other_task["task_id"] in closeout_blob:
            raise AssertionError(f"exec stop leaked unrelated endpoint obligations: {scoped_stop}")
        if task["task_id"] not in stop_blob or check["acceptance_check_id"] not in stop_blob:
            raise AssertionError(f"exec stop omitted scoped trust obligations: {stop_check}")
        if stop_scope_start["run_id"] != scoped_stop["run_id"]:
            raise AssertionError(f"exec stop closed a different run: {stop_scope_start}, {scoped_stop}")

        run_cli(repo, "endpoint", "create", "rootless", "--rootless", "--reason", "rootless stop-check fixture")
        rootless_start = run_cli(
            repo,
            "exec",
            "start",
            "--endpoint",
            "rootless",
            "--summary",
            "Rootless scoped stop-check run",
            "--allow-preflight-warning",
            "--allow-reason",
            "Rootless endpoint fixture checks stop-check scope behavior.",
        )
        rootless_stop = run_cli(repo, "exec", "stop", "--endpoint", "rootless", "--summary", "Rootless stop")
        rootless_check = rootless_stop["stop_check"]
        if rootless_check["scope_mode"] != "endpoint_without_root" or not rootless_check["warnings"] or not rootless_check["must_not_claim_complete"]:
            raise AssertionError(f"rootless endpoint stop-check was not explicit: {rootless_check}")
        rootless_blob = json.dumps(rootless_check, sort_keys=True)
        if task["task_id"] in rootless_blob or other_task["task_id"] in rootless_blob:
            raise AssertionError(f"rootless stop-check leaked unrelated obligations: {rootless_check}")
        if rootless_start["run_id"] != rootless_stop["run_id"]:
            raise AssertionError(f"rootless stop closed a different run: {rootless_start}, {rootless_stop}")

        run_cli(repo, "workflow", "begin", "--session-id", "session_v4", "--endpoint", "trust", "--content", "Start light implementation.")
        light = run_cli(repo, "work", "start", "--mode", "light", "--endpoint", "trust", "--task", task["task_id"], "--session-id", "session_v4")
        if not light["contract"]["creates_run"] or not light["run_id"]:
            raise AssertionError(f"Light work did not start an execution run: {light}")
        close_dry = run_cli(repo, "work", "close", "--mode", "light", "--endpoint", "trust", "--dry-run", "--check", check["acceptance_check_id"])
        if not close_dry["dry_run"] or not close_dry["would_create_change_set"]:
            raise AssertionError(f"close dry-run did not report pending change-set behavior: {close_dry}")
        current = run_cli(repo, "work", "current")
        if current["active_run"]["run_id"] != light["run_id"]:
            raise AssertionError(f"current handle did not expose active run: {current}")
        current_brief = run_cli(repo, "endpoint", "brief", "@current.endpoint")
        if current_brief["endpoint"] != "trust":
            raise AssertionError(f"@current.endpoint did not resolve from current handle: {current_brief}")
        default_dry = run_cli(repo, "fix", "close", "--mode", "full", "--endpoint", "trust")
        if not default_dry["dry_run"] or not default_dry["default_dry_run"] or "endpoint doctor --strict-closeout" not in default_dry["full_closeout_requirements"]:
            raise AssertionError(f"fix close did not default to dry-run/full closeout requirements: {default_dry}")
        missing_alias = run_cli_fails(repo, "endpoint", "brief", "@alias.missing")
        if "alias not found" not in missing_alias.stderr:
            raise AssertionError(f"missing alias did not fail safely: {missing_alias.stderr}")

        projection = run_cli(repo, "graph", "projection", "--endpoint", "trust", "--view", "all", "--include-consumed", "--include-history", "--save-snapshot")
        if not projection.get("projection_metadata") or not projection.get("generated_at") or not projection.get("snapshot"):
            raise AssertionError(f"projection omitted metadata/snapshot: {projection}")
        for view in ("attention", "execution", "discussions", "audit", "full"):
            assert_projection_view(projection, view)
        if not projection["views"]["discussions"]["items"]:
            raise AssertionError(f"projection did not expose discussions: {projection}")
        if not (repo / projection["snapshot"]["payload_ref"]).exists():
            raise AssertionError(f"projection snapshot file was not written: {projection['snapshot']}")

        workbench = run_cli(repo, "workbench", "export", "--endpoint", "trust", "--path", "workbench.html", "--include-consumed", "--include-history")
        html = (repo / workbench["path"]).read_text(encoding="utf-8")
        if "Read-only projection export" not in html or "hidden_source_count" not in html or "no write/action endpoints" not in html:
            raise AssertionError("workbench export did not render the read-only projection")
        workbench_json = run_cli(repo, "workbench", "export", "--endpoint", "@alias.umbrella", "--path", "workbench.json", "--format", "json", "--view", "all")
        json_payload = json.loads((repo / workbench_json["path"]).read_text(encoding="utf-8"))
        if not json_payload["read_only"] or workbench_json["db_write_path"] is not False:
            raise AssertionError(f"workbench JSON export lost readonly metadata: {workbench_json}, {json_payload}")
        handoff = run_cli(
            repo,
            "audit",
            "import-agent-output",
            "--endpoint",
            "trust",
            "--source-node",
            doc["document_node_id"],
            "--classification",
            "summary",
            "--body",
            "Subagent handoff summary: changed files, tests, provider outputs, unresolved risks.",
        )
        if not handoff["artifact_node_id"] or handoff["classification"] != "summary":
            raise AssertionError(f"multi-subagent handoff artifact was not imported as summary: {handoff}")

        print(json.dumps({"ok": True, "endpoint": "trust", "run_id": light["run_id"], "segment_id": capture["segment_id"]}, indent=2, sort_keys=True))
        run_cli_completed(repo, "postgres-dev", "stop", expect_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
