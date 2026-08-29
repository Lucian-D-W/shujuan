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


ENDPOINT = "shujuan-v6-activation-consolidation-2026-05-21"
PREDECESSORS = {
    "shujuan-v1-v5-design-current-state-audit-2026-05-21",
    "shujuan-v5-dccp-delegated-collaboration-2026-05-20",
}


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
        raise AssertionError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def run_cli(repo: Path, *args: str) -> dict[str, object]:
    return json.loads(run_cli_completed(repo, *args).stdout)


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


def assert_no_historical_dump(brief: dict[str, object]) -> None:
    blob = json.dumps(brief, sort_keys=True)
    if '"historical_details"' in blob:
        raise AssertionError(f"activation brief dumped full historical details: {brief}")
    if '"current_body"' in blob or '"current_body_props"' in blob:
        raise AssertionError(f"activation brief dumped full endpoint body internals: {brief}")
    proof = brief["activation"]["proof_capsule"]
    hidden = proof["closed_history_summary"]
    if hidden["closed_check_count"] < 1 or hidden["evidence_count"] < 1 or hidden["hidden_source_count"] < 2:
        raise AssertionError(f"closed history was not hidden behind detail refs: {hidden}")
    if "report endpoint" not in hidden["detail_ref"] or "--full" not in hidden["detail_ref"]:
        raise AssertionError(f"closed history detail ref is not actionable: {hidden}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-v6-phase2-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        try:
            init = run_cli(
                repo,
                "init",
                "--name",
                "v6-phase2",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            if init["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")

            (repo / "plan.md").write_text(
                "# V6 Phase 2\n\nActivation Brief must preserve center, endpoint, role, mode, proof, action, and lineage anchors.\n",
                encoding="utf-8",
            )
            doc = run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan")
            source_node = doc["document_node_id"]
            run_cli(
                repo,
                "center",
                "update",
                "--body",
                "shujuan current center: V6 activation consolidation with PostgreSQL-only governance and DCCP role boundaries.",
                "--from-node",
                source_node,
            )
            scope = run_cli(
                repo,
                "scope",
                "create",
                "--body",
                "V6 current-stage activation boundary: endpoint brief is an activation surface, not a status dump.",
                "--non-downgrade-rules",
                "PostgreSQL-only runtime; controller owns closure; workers return material only; P2 product backlog is non-blocking.",
                "--source-node",
                source_node,
            )
            for term, definition in {
                "Activation Surface": "A concise entry payload for center, endpoint, role, mode, proof, and action.",
                "Center Capsule": "Minimal project identity and boundary context, not a long report.",
                "Proof Capsule": "Hard predicates, forbidden substitutes, work chains, links, and projection metadata.",
            }.items():
                run_cli(repo, "term", "define", term, "--definition", definition, "--scope-node", scope["node_id"], "--from-node", source_node)

            task = run_cli(
                repo,
                "task",
                "add",
                "--contract",
                scope["contract_id"],
                "--body",
                "Phase 2 upgrade endpoint brief into Activation Brief.",
                "--from-node",
                source_node,
            )
            check = run_cli(
                repo,
                "acceptance",
                "add",
                "--task",
                task["task_id"],
                "--body",
                "Activation brief exposes center identity, endpoint line, obligations, role authority, mode contract, proof capsule, and next safe action.",
                "--expected-evidence-type",
                "test_result",
                "--from-node",
                source_node,
            )
            closed_check = run_cli(
                repo,
                "acceptance",
                "add",
                "--task",
                task["task_id"],
                "--body",
                "Closed history exists but remains hidden behind detail refs.",
                "--expected-evidence-type",
                "artifact",
                "--from-node",
                source_node,
            )
            (repo / "closed-proof.txt").write_text("closed history proof\n", encoding="utf-8")
            run_cli(
                repo,
                "evidence",
                "artifact",
                "--path",
                "closed-proof.txt",
                "--from-node",
                source_node,
                "--check",
                closed_check["acceptance_check_id"],
                "--close-check",
            )
            run_cli(repo, "endpoint", "create", ENDPOINT, "--description", "V6 activation consolidation endpoint.", "--root-node", scope["node_id"])
            intake = run_cli(
                repo,
                "work",
                "intake",
                "--endpoint",
                ENDPOINT,
                "--source-node",
                source_node,
                "--promise-id",
                "SP-V6-PHASE2",
                "--text",
                "Endpoint brief must be an Activation Brief with hard predicates and forbidden substitutes.",
                "--predicate",
                "HP-V6-BRIEF::Activation Brief exposes role/mode/proof/action capsules from DB projections.",
                "--required-term",
                "HP-V6-BRIEF::Activation Brief",
                "--forbidden-substitute",
                "HP-V6-BRIEF::MVP downgrade::The brief must not collapse into a minimal status dump.",
                "--mode",
                "standard",
            )
            run_cli(
                repo,
                "work",
                "split",
                "--endpoint",
                ENDPOINT,
                "--name",
                "Phase 2 activation brief",
                "--chain-id",
                "WC-V6-PHASE2",
                "--task",
                task["task_id"],
                "--check",
                check["acceptance_check_id"],
                "--predicate",
                intake["hard_predicates"][0]["id"],
                "--mode",
                "standard",
            )
            run_cli(repo, "endpoint", "refresh", ENDPOINT)

            brief = run_cli(
                repo,
                "endpoint",
                "brief",
                ENDPOINT,
                "--role",
                "worker_agent",
                "--mode",
                "standard",
                "--task",
                task["task_id"],
                "--check",
                check["acceptance_check_id"],
                "--work-chain",
                "WC-V6-PHASE2",
            )
            activation = brief["activation"]
            if brief["activation_schema"] != "activation.v6" or activation["activation_schema"] != "activation.v6":
                raise AssertionError(f"activation schema marker missing: {brief}")
            for key in ("center_capsule", "endpoint_capsule", "role_capsule", "mode_capsule", "proof_capsule", "action_capsule"):
                if key not in activation:
                    raise AssertionError(f"activation brief omitted {key}: {activation}")
            center = activation["center_capsule"]
            if center["project_identity"] != "shujuan" or "PostgreSQL" not in json.dumps(center):
                raise AssertionError(f"center capsule lost identity/runtime boundary: {center}")
            if len(json.dumps(center)) > 5000 or "Closed checks:" in json.dumps(center):
                raise AssertionError(f"center capsule became a long status dump: {center}")
            if not {"Activation Surface", "Center Capsule", "Proof Capsule"} <= {item["term"] for item in center["term_anchors"]}:
                raise AssertionError(f"center capsule missed term anchors: {center}")

            endpoint = activation["endpoint_capsule"]
            if ENDPOINT not in endpoint["line"] or endpoint["active_obligation_count"] < 1:
                raise AssertionError(f"endpoint capsule lost endpoint line or obligations: {endpoint}")
            if not endpoint["projection"]["projection_hash"] or endpoint["projection"]["stored_projection_hash"] != endpoint["projection"]["projection_hash"]:
                raise AssertionError(f"projection hash metadata missing or stale: {endpoint['projection']}")
            if check["acceptance_check_id"] not in json.dumps(endpoint["active_obligations"], sort_keys=True):
                raise AssertionError(f"active obligations did not cite open check: {endpoint['active_obligations']}")

            role = activation["role_capsule"]
            if role["role"] != "worker_agent" or role["current_project_governance_write_authorized"] or role["can_close_checks_or_tasks"]:
                raise AssertionError(f"worker role authority was wrong: {role}")
            if "current_project_governance_write" not in role["forbidden_actions"]:
                raise AssertionError(f"worker forbidden actions missing governance write boundary: {role}")

            mode = activation["mode_capsule"]
            if mode["mode"] != "standard" or not mode["contract"]["creates_run"] or mode["contract"]["creates_change_set"]:
                raise AssertionError(f"mode contract was wrong: {mode}")

            proof = activation["proof_capsule"]
            if {item["id"] for item in proof["hard_predicates"]} != {"HP-V6-BRIEF"}:
                raise AssertionError(f"hard predicate not surfaced: {proof}")
            if proof["forbidden_substitutes"][0]["substitute_text"] != "MVP downgrade":
                raise AssertionError(f"forbidden substitute not surfaced: {proof}")
            if proof["work_chains"][0]["id"] != "WC-V6-PHASE2" or proof["task_predicate_links"][0]["check_id"] != check["acceptance_check_id"]:
                raise AssertionError(f"work chain or predicate links not surfaced: {proof}")
            assert_no_historical_dump(brief)

            action = activation["action_capsule"]
            if "work focus" not in json.dumps(action) or action["db_writes"] != 0 or not action["read_only"]:
                raise AssertionError(f"action capsule did not expose next safe read-only commands: {action}")
            lineage = activation["lineage_anchors"]
            if {item["endpoint"] for item in lineage} != PREDECESSORS:
                raise AssertionError(f"lineage anchors missing predecessors: {lineage}")
            if not all(item["relation"] in {"directly_addressed", "indirectly_dissolved", "deferred", "out_of_scope"} for item in lineage):
                raise AssertionError(f"lineage anchor relation classification invalid: {lineage}")

            focus = run_cli(repo, "work", "focus", "--endpoint", ENDPOINT, "--work-chain", "WC-V6-PHASE2")
            if focus["hard_predicates"] != proof["hard_predicates"] or focus["task_predicate_links"] != proof["task_predicate_links"]:
                raise AssertionError(f"endpoint brief proof capsule diverged from work focus: {focus} vs {proof}")
            markdown = run_cli_completed(
                repo,
                "endpoint",
                "brief",
                ENDPOINT,
                "--role",
                "worker_agent",
                "--mode",
                "standard",
                "--work-chain",
                "WC-V6-PHASE2",
                "--markdown",
            ).stdout
            if "# Activation Brief" not in markdown or "Lineage Anchors" not in markdown or "Proof Capsule" not in markdown:
                raise AssertionError(f"activation markdown missing expected sections:\n{markdown}")
            active_report = run_cli_completed(repo, "report", "endpoint", ENDPOINT, "--active-only", "--markdown").stdout
            if "# Endpoint Active Report" not in active_report or "Active Obligations" not in active_report:
                raise AssertionError(f"active-only report backing ledger no longer works:\n{active_report}")

            print(
                json.dumps(
                    {
                        "ok": True,
                        "v6_phase2_activation_brief": "passed",
                        "endpoint": ENDPOINT,
                        "hard_predicate": "HP-V6-BRIEF",
                        "work_chain": "WC-V6-PHASE2",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        finally:
            if postgres_started:
                run_cli_completed(repo, "postgres-dev", "stop", expect_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
