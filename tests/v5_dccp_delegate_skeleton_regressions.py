from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.schema import SCHEMA_SQL, SCHEMA_VERSION


DCCP_TABLES = {"delegation_lanes", "delegation_packets", "worker_ownership_snapshots"}
REUSED_TABLES = {"review_results", "provider_facts", "evidence_records", "nodes", "acceptance_checks", "tasks"}


def run_cli(repo: Path, *args: str, expect_ok: bool = True, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
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


def run_raw_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise AssertionError(f"git {' '.join(args)} failed\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    return completed.stdout.strip()


def create_table_names(sql: str) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(r"(?is)\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][\w]*)\b", sql)
    }


def assert_schema_is_minimal() -> None:
    if SCHEMA_VERSION != "0.4.0":
        raise AssertionError(f"DCCP schema should bump to 0.4.0, got {SCHEMA_VERSION}")
    schema_tables = create_table_names(SCHEMA_SQL)
    missing = DCCP_TABLES - schema_tables
    if missing:
        raise AssertionError(f"canonical schema missing DCCP tables: {missing}")
    required_columns = [
        "lane_name TEXT NOT NULL",
        "lifecycle TEXT NOT NULL DEFAULT 'planned'",
        "authority_boundary TEXT NOT NULL",
        "forbidden_actions TEXT NOT NULL DEFAULT '[]'",
        "expected_return_fields TEXT NOT NULL DEFAULT '[]'",
        "pre_existing_dirty_paths TEXT NOT NULL DEFAULT '[]'",
        "worker_touched_paths TEXT NOT NULL DEFAULT '[]'",
    ]
    missing_columns = [column for column in required_columns if column not in SCHEMA_SQL]
    if missing_columns:
        raise AssertionError(f"canonical schema missing minimal DCCP columns: {missing_columns}")

    migration = (ROOT / "migrations" / "shujuan" / "002_v5_dccp_minimal_collaboration.sql").read_text(encoding="utf-8")
    migration_tables = create_table_names(migration)
    if migration_tables != DCCP_TABLES:
        raise AssertionError(f"DCCP migration created non-minimal table set: {migration_tables}")
    forbidden_new_tables = sorted(REUSED_TABLES & migration_tables)
    if forbidden_new_tables:
        raise AssertionError(f"DCCP migration recreated tables that should be reused: {forbidden_new_tables}")
    for index_name in [
        "idx_delegation_lanes_endpoint",
        "idx_delegation_lanes_task",
        "idx_delegation_packets_lane",
        "idx_worker_ownership_snapshots_lane",
    ]:
        if index_name not in migration:
            raise AssertionError(f"DCCP migration missing focused index: {index_name}")


def assert_delegate_payload(payload: dict[str, object], command: str) -> None:
    if not payload.get("ok") or not payload.get("usable"):
        raise AssertionError(f"delegate {command} was not usable: {payload}")
    if payload.get("db_writes") != 0 or payload.get("capture_claim"):
        raise AssertionError(f"delegate {command} claimed governance side effects: {payload}")
    if payload.get("schema_tables") != sorted(DCCP_TABLES):
        raise AssertionError(f"delegate {command} exposed wrong schema table set: {payload}")
    if payload.get("controller_only_closeout") is not True:
        raise AssertionError(f"delegate {command} did not preserve controller-only closeout: {payload}")
    mode = payload.get("collaboration_mode")
    if not isinstance(mode, dict) or "verification_policy" not in mode or "closeout_policy" not in mode:
        raise AssertionError(f"delegate {command} did not expose collaboration mode policy: {payload}")
    primitives = payload.get("existing_primitives")
    if not isinstance(primitives, dict) or "closeout" not in primitives or "return_import" not in primitives:
        raise AssertionError(f"delegate {command} did not route to existing primitives: {payload}")
    diagnostics = payload.get("diagnostics")
    required_diagnostics = {"usable", "raw_count", "visible_count", "filtered_count", "render_errors", "report_errors", "next_action"}
    if not isinstance(diagnostics, dict) or not required_diagnostics <= set(diagnostics):
        raise AssertionError(f"delegate {command} missed observable diagnostics: {payload}")


def assert_delegate_cli_no_db_writes() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-delegate-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        commands = [
            ("plan", "--endpoint", "dccp", "--task", "task_a", "--check", "check_a"),
            ("packet", "--role", "worker", "--packet-kind", "delegation", "--body", "Implement scoped patch."),
            ("import", "--role", "worker", "--import-kind", "artifact", "--artifact", "handoff.md"),
            (
                "ownership",
                "--role",
                "worker",
                "--assigned-path",
                "handoff.md",
                "--claimed-path",
                "handoff.md",
                "--after-snapshot-path",
                "handoff.md",
            ),
            ("review", "--result", "accept", "--summary", "Looks scoped.", "--covered-predicate", "predicate-a"),
            ("verify", "--role", "controller"),
            ("status", "--endpoint", "dccp"),
            ("controller", "status", "--endpoint", "dccp"),
            ("controller", "close", "--endpoint", "dccp"),
        ]
        for command in commands:
            completed = run_cli(repo, "delegate", *command)
            payload = json.loads(completed.stdout)
            assert_delegate_payload(payload, " ".join(command))
            if (repo / ".shujuan").exists():
                raise AssertionError(f"delegate {' '.join(command)} created .shujuan artifacts")

        worker_closeout = run_cli(repo, "delegate", "verify", "--role", "worker", "--claims-closeout", expect_ok=False)
        denied = json.loads(worker_closeout.stdout)
        if denied.get("usable") is not False or not denied.get("issues"):
            raise AssertionError(f"delegate verify did not reject worker closeout claim: {denied}")
        apply_close = run_cli(repo, "delegate", "controller", "close", "--endpoint", "dccp", "--apply", expect_ok=False)
        if "will not close checks/tasks" not in apply_close.stderr:
            raise AssertionError(f"delegate controller close --apply did not fail safe: {apply_close.stderr}")
        if (repo / ".shujuan").exists():
            raise AssertionError("failed delegate paths created .shujuan artifacts")


def assert_delegate_modes_plan_and_packet_behavior() -> None:
    expected_modes = {
        "solo-light",
        "delegated-light-fix",
        "delegated-standard-slice",
        "delegated-full-critical",
        "audit-only",
        "research-only",
        "writing-no-governance",
    }
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-delegate-modes-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        for mode in sorted(expected_modes):
            plan = json.loads(
                run_cli(repo, "delegate", "plan", "--collaboration-mode", mode, "--endpoint", "dccp").stdout
            )
            assert_delegate_payload(plan, f"plan {mode}")
            if plan["collaboration_mode"]["mode"] != mode:
                raise AssertionError(f"delegate plan returned wrong collaboration mode: {plan}")
            if not plan["plan"]["slices"] or not plan["plan"]["batches"]:
                raise AssertionError(f"delegate plan missed slices/batches for {mode}: {plan}")
            if "focused_verification" not in plan["plan"] or "return_classification" not in plan["plan"]:
                raise AssertionError(f"delegate plan missed verification/classification for {mode}: {plan}")
            suggestion = plan["plan"]["reviewer_recommendation"]
            if not isinstance(suggestion, dict) or "recommendation" not in suggestion:
                raise AssertionError(f"delegate plan missed review suggestion for {mode}: {plan}")
            if mode == "delegated-full-critical" and suggestion["required"] is not True:
                raise AssertionError(f"full critical mode did not require review: {plan}")
            if mode == "delegated-full-critical":
                owners_by_name = {item["name"]: item["owner_role"] for item in plan["plan"]["slices"]}
                if owners_by_name.get("worker_patch") != "worker" or owners_by_name.get("mandatory_review") != "reviewer":
                    raise AssertionError(f"full critical slices had misleading owners: {plan}")
            if mode == "writing-no-governance" and plan["collaboration_mode"]["closeout_policy"] != "no_governance_no_closeout":
                raise AssertionError(f"writing mode did not stay no-governance: {plan}")

        packet = json.loads(
            run_cli(
                repo,
                "delegate",
                "packet",
                "--collaboration-mode",
                "writing-no-governance",
                "--role",
                "writer",
                "--packet-kind",
                "writer",
                "--body",
                "Draft external prose only.",
            ).stdout
        )
        if packet["packet"]["closeout_policy"] != "no_governance_no_closeout":
            raise AssertionError(f"writer packet did not inherit writing-no-governance policy: {packet}")
        if packet["packet"]["agent_combination"] != ["writer"]:
            raise AssertionError(f"writer packet had wrong agent combination: {packet}")
        if (repo / ".shujuan").exists():
            raise AssertionError("delegate mode diagnostics created .shujuan artifacts")


def assert_delegate_status_lifecycle_validation() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-delegate-status-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        status = json.loads(
            run_cli(
                repo,
                "delegate",
                "status",
                "--collaboration-mode",
                "delegated-standard-slice",
                "--state",
                "returned",
                "--from-state",
                "returned",
                "--to-state",
                "imported",
            ).stdout
        )
        assert_delegate_payload(status, "status transition")
        if status["status"]["transition"]["valid"] is not True:
            raise AssertionError(f"valid returned->imported transition rejected: {status}")
        if "closed_by_controller" not in status["status"]["states"]:
            raise AssertionError(f"status did not expose closed_by_controller state: {status}")
        if not status["status"]["controller_closeout_gates"]:
            raise AssertionError(f"status did not expose controller closeout gates: {status}")

        invalid = json.loads(
            run_cli(
                repo,
                "delegate",
                "status",
                "--role",
                "worker",
                "--from-state",
                "returned",
                "--to-state",
                "closed_by_controller",
                expect_ok=False,
            ).stdout
        )
        if invalid.get("usable") is not False or invalid["issues"][0]["code"] != "invalid_delegate_transition":
            raise AssertionError(f"worker closeout transition was not rejected: {invalid}")
        skipped = json.loads(
            run_cli(
                repo,
                "delegate",
                "status",
                "--from-state",
                "open",
                "--to-state",
                "returned",
                expect_ok=False,
            ).stdout
        )
        if skipped.get("usable") is not False:
            raise AssertionError(f"skipped lifecycle transition was not rejected: {skipped}")
        if (repo / ".shujuan").exists():
            raise AssertionError("delegate status diagnostics created .shujuan artifacts")


def assert_phase1_role_packets_and_artifacts() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-packets-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        for role, packet_kind in [("worker", "delegation"), ("reviewer", "review"), ("writer", "writer")]:
            command = [
                "delegate",
                "packet",
                "--role",
                role,
                "--packet-kind",
                packet_kind,
                "--endpoint",
                "dccp",
                "--task",
                "task_a",
                "--check",
                "check_a",
                "--goal",
                f"{role} goal",
                "--claim",
                f"{role} claim",
                "--hard-predicate",
                "predicate-a",
                "--must-read",
                "handoff-node",
                "--allowed-scope",
                "phase1 only",
                "--review-question",
                "Is this one screen?",
            ]
            if role == "worker":
                command.extend(["--save-artifact"])
            if role == "writer":
                command.extend(["--collaboration-mode", "writing-no-governance"])
            payload = json.loads(run_cli(repo, *command).stdout)
            assert_delegate_payload(payload, f"packet {role}")
            packet = payload["packet"]["role_packet"]
            required = [
                "role",
                "endpoint",
                "task",
                "check",
                "goal",
                "claim",
                "hard_predicates",
                "forbidden_actions",
                "allowed_scope",
                "must_read",
                "focused_verification_or_review_questions",
                "return_template",
                "identity_boundary",
                "escalation_triggers",
                "db_write_authority",
                "closeout_authority",
                "collaboration_mode",
                "provider_guidance",
                "packet_lines",
            ]
            missing = [key for key in required if key not in packet]
            if missing:
                raise AssertionError(f"{role} packet missing required fields: {missing}\n{payload}")
            if packet["db_write_authority"] or packet["closeout_authority"]:
                raise AssertionError(f"{role} packet implied delegated authority: {payload}")
            if packet["provider_guidance"]["provider_intent"] != "impact_only":
                raise AssertionError(f"{role} packet did not default provider intent to impact_only: {payload}")
            forbidden = " ".join(packet["provider_guidance"]["forbidden_without_controller_authorization"])
            if "provider-driven closure" not in forbidden or "treat provider completion as binding" not in forbidden:
                raise AssertionError(f"{role} packet leaked provider finish/closure authority: {payload}")
            return_template = packet["return_template"]
            for field in ["changed_files", "owned_hunks_or_inspected_evidence", "factual_anchors", "tests_or_review_results", "identity_boundary", "no_closure_attestation"]:
                if field not in return_template:
                    raise AssertionError(f"{role} packet return template missed {field}: {payload}")
            if len(packet["packet_lines"]) > 12:
                raise AssertionError(f"{role} packet is not one-screen enough: {packet['packet_lines']}")
            if role == "worker":
                if not payload["persisted"] or not payload["artifact_ref"]:
                    raise AssertionError(f"worker --save-artifact did not persist artifact: {payload}")
                artifact_path = repo / payload["artifact_ref"]
                if not artifact_path.exists():
                    raise AssertionError(f"saved packet artifact missing: {artifact_path}")
                saved = json.loads(artifact_path.read_text(encoding="utf-8"))
                if saved["role"] != "worker" or saved["closeout_authority"]:
                    raise AssertionError(f"saved packet artifact changed authority: {saved}")
            elif payload["persisted"] or payload["artifact_ref"]:
                raise AssertionError(f"{role} packet persisted without --save-artifact: {payload}")


def assert_phase1_active_capsule_shape() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-capsule-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        capsule = json.loads(
            run_cli(
                repo,
                "delegate",
                "capsule",
                "--endpoint",
                "dccp",
                "--role",
                "worker",
                "--task",
                "task_a",
                "--check",
                "check_a",
                "--hard-predicate",
                "predicate-a",
                "--handoff",
                "node_handoff",
                "--warning",
                "watch authority",
                "--next-slice",
                "worker_patch",
            ).stdout
        )
        assert_delegate_payload(capsule, "capsule worker")
        body = capsule["capsule"]
        if not body["one_screen"] or not body["read_only"] or body["live_db_read"]:
            raise AssertionError(f"capsule did not stay concise/read-only/no-live-DB: {capsule}")
        if len(body["active_obligations"]) != 2 or body["next_slice"] != "worker_patch":
            raise AssertionError(f"capsule missed active surface: {capsule}")
        hidden = set(body["hidden_by_design"])
        expected_hidden = {"closed check details", "full project report", "provider history", "unrelated backlog"}
        if not expected_hidden <= hidden:
            raise AssertionError(f"capsule did not hide history/backlog surfaces: {capsule}")
        if "controller-only" not in " ".join(body["capsule_lines"]):
            raise AssertionError(f"capsule did not state controller-only closeout: {capsule}")
        if (repo / ".shujuan").exists():
            raise AssertionError("capsule without artifact save created .shujuan artifacts")


def assert_phase2_import_classification_matrix() -> None:
    expected = {
        "summary-only": ("narrative", False, False, False),
        "candidate-finding": ("narrative", False, False, False),
        "actionable": ("active", True, False, False),
        "needs-user-decision": ("active", True, False, False),
        "closure-material": ("closure_material", False, True, True),
        "provider-hypothesis": ("narrative", False, False, False),
        "invalid": ("narrative", False, False, False),
    }
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-import-matrix-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        for classification, (column, active, closure_material, evidence_candidate) in expected.items():
            payload = json.loads(
                run_cli(
                    repo,
                    "delegate",
                    "import",
                    "--endpoint",
                    "dccp",
                    "--role",
                    "worker",
                    "--classification",
                    classification,
                ).stdout
            )
            assert_delegate_payload(payload, f"import {classification}")
            row = payload["classification_row"]
            if row["classification"] != classification or row["report_column"] != column:
                raise AssertionError(f"delegate import classification row mismatch for {classification}: {payload}")
            if row["active_obligation"] is not active:
                raise AssertionError(f"delegate import active flag mismatch for {classification}: {payload}")
            if row["closure_material"] is not closure_material or row["evidence_candidate"] is not evidence_candidate:
                raise AssertionError(f"delegate import closure flags mismatch for {classification}: {payload}")
            if row["closes_check"] or row["closes_task"]:
                raise AssertionError(f"delegate import allowed direct closeout for {classification}: {payload}")
            policy = payload["import_policy"]
            if policy["closes_check"] or policy["closes_task"] or policy["controller_only_closeout"] is not True:
                raise AssertionError(f"delegate import policy did not stay controller-gated for {classification}: {payload}")
            columns = payload["report_columns"]
            if sorted(columns) != ["active", "closure_material", "narrative"]:
                raise AssertionError(f"delegate import missed three report columns: {payload}")
            selected = [item for item in columns[column] if item["classification"] == classification and item["selected"]]
            if len(selected) != 1:
                raise AssertionError(f"delegate import did not select {classification} in {column}: {payload}")

        provider_default = json.loads(
            run_cli(repo, "delegate", "import", "--role", "provider", "--import-kind", "provider_fact").stdout
        )
        if provider_default["classification"] != "provider-hypothesis":
            raise AssertionError(f"provider import did not default to provider-hypothesis: {provider_default}")
        artifact_default = json.loads(
            run_cli(repo, "delegate", "import", "--role", "worker", "--import-kind", "artifact").stdout
        )
        if artifact_default["classification"] != "closure-material":
            raise AssertionError(f"artifact import did not default to closure-material: {artifact_default}")
        if (repo / ".shujuan").exists():
            raise AssertionError("delegate import classification diagnostics created .shujuan artifacts")


def assert_phase2_import_forbidden_closeout_paths() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-import-closeout-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        forbidden_flags = ["--close-check", "--close-task", "--closeout", "--convert-to-evidence"]
        for flag in forbidden_flags:
            completed = run_cli(
                repo,
                "delegate",
                "import",
                "--classification",
                "closure-material",
                flag,
                expect_ok=False,
            )
            if "delegate import cannot close checks/tasks" not in completed.stderr:
                raise AssertionError(f"delegate import did not reject {flag}: {completed.stderr}")
        if (repo / ".shujuan").exists():
            raise AssertionError("forbidden delegate import closeout paths created .shujuan artifacts")


def assert_p0_golden_path_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-golden-path-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        endpoint = "dccp-golden"
        task = "task_golden"
        check = "check_golden"
        lane = "lane_golden"

        plan = json.loads(
            run_cli(
                repo,
                "delegate",
                "plan",
                "--collaboration-mode",
                "delegated-light-fix",
                "--endpoint",
                endpoint,
                "--task",
                task,
                "--check",
                check,
                "--lane",
                lane,
            ).stdout
        )
        assert_delegate_payload(plan, "golden plan")
        slice_names = [item["name"] for item in plan["plan"]["slices"]]
        if slice_names != ["controller_scope", "worker_patch", "controller_verify", "controller_closeout"]:
            raise AssertionError(f"delegated-light-fix plan did not expose the golden path slices: {plan}")
        if plan["next_action"]["action"] != "delegate packet":
            raise AssertionError(f"plan did not route to worker packet generation: {plan}")

        packet = json.loads(
            run_cli(
                repo,
                "delegate",
                "packet",
                "--collaboration-mode",
                "delegated-light-fix",
                "--role",
                "worker",
                "--endpoint",
                endpoint,
                "--task",
                task,
                "--check",
                check,
                "--lane",
                lane,
                "--goal",
                "Implement the scoped golden-path patch.",
                "--hard-predicate",
                "no worker closeout",
                "--save-artifact",
            ).stdout
        )
        assert_delegate_payload(packet, "golden worker packet")
        worker_packet = packet["packet"]["role_packet"]
        if worker_packet["closeout_authority"] or worker_packet["db_write_authority"]:
            raise AssertionError(f"worker packet leaked closeout or DB-write authority: {packet}")
        artifact_ref = packet["artifact_ref"]
        if not artifact_ref or not (repo / artifact_ref).exists():
            raise AssertionError(f"golden worker packet did not persist a local artifact: {packet}")

        imported = json.loads(
            run_cli(
                repo,
                "delegate",
                "import",
                "--collaboration-mode",
                "delegated-light-fix",
                "--role",
                "worker",
                "--endpoint",
                endpoint,
                "--task",
                task,
                "--check",
                check,
                "--lane",
                lane,
                "--classification",
                "closure-material",
                "--artifact",
                "worker-return.md",
            ).stdout
        )
        assert_delegate_payload(imported, "golden import")
        row = imported["classification_row"]
        if not row["closure_material"] or not row["evidence_candidate"] or row["closes_check"] or row["closes_task"]:
            raise AssertionError(f"worker return import was not closure material gated by controller: {imported}")
        if imported["import_policy"]["controller_conversion_required"] is not True:
            raise AssertionError(f"closure material did not require controller conversion: {imported}")

        for role in ("worker", "reviewer"):
            denied = json.loads(
                run_cli(repo, "delegate", "verify", "--role", role, "--claims-closeout", expect_ok=False).stdout
            )
            if denied.get("usable") is not False or denied["issues"][0]["code"] != "delegated_closeout_forbidden":
                raise AssertionError(f"{role} closeout authority was not rejected: {denied}")

        returned_to_imported = json.loads(
            run_cli(
                repo,
                "delegate",
                "status",
                "--role",
                "controller",
                "--endpoint",
                endpoint,
                "--lane",
                lane,
                "--from-state",
                "returned",
                "--to-state",
                "imported",
            ).stdout
        )
        imported_to_verified = json.loads(
            run_cli(
                repo,
                "delegate",
                "status",
                "--role",
                "controller",
                "--endpoint",
                endpoint,
                "--lane",
                lane,
                "--from-state",
                "imported",
                "--to-state",
                "verified",
            ).stdout
        )
        verified_to_closed = json.loads(
            run_cli(
                repo,
                "delegate",
                "status",
                "--role",
                "controller",
                "--endpoint",
                endpoint,
                "--lane",
                lane,
                "--from-state",
                "verified",
                "--to-state",
                "closed_by_controller",
            ).stdout
        )
        for payload, label in [
            (returned_to_imported, "returned->imported"),
            (imported_to_verified, "imported->verified"),
            (verified_to_closed, "verified->closed_by_controller"),
        ]:
            assert_delegate_payload(payload, f"golden status {label}")
            if payload["status"]["transition"]["valid"] is not True:
                raise AssertionError(f"golden lane transition failed {label}: {payload}")

        worker_close_state = json.loads(
            run_cli(
                repo,
                "delegate",
                "status",
                "--role",
                "worker",
                "--from-state",
                "verified",
                "--to-state",
                "closed_by_controller",
                expect_ok=False,
            ).stdout
        )
        if worker_close_state.get("usable") is not False:
            raise AssertionError(f"worker was allowed to enter closed_by_controller: {worker_close_state}")

        closeout = json.loads(
            run_cli(
                repo,
                "delegate",
                "controller",
                "close",
                "--endpoint",
                endpoint,
                "--task",
                task,
                "--check",
                check,
                "--lane",
                lane,
            ).stdout
        )
        assert_delegate_payload(closeout, "golden controller close")
        if closeout["dry_run"] is not True or closeout["would_close"] is not False:
            raise AssertionError(f"controller close diagnostic claimed fake success: {closeout}")
        if "prove_checks" not in closeout["routes"] or "exec stop" not in closeout["routes"]["capture_change_set"]:
            raise AssertionError(f"controller close did not expose evidence-backed closeout routes: {closeout}")

        if (repo / "shujuan.sqlite").exists() or (repo / ".shujuan" / "shujuan.sqlite").exists():
            raise AssertionError("golden path smoke created a governance database")


def assert_worker_ownership_snapshot_diagnostics() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-ownership-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        run_raw_git(repo, "init")
        run_raw_git(repo, "config", "user.email", "tests@example.invalid")
        run_raw_git(repo, "config", "user.name", "DCCP Tests")
        for name in ["pre_existing.txt", "worker_owned.txt", "unassigned.txt", "claimed_missing.txt"]:
            (repo / name).write_text(f"{name} baseline\n", encoding="utf-8")
        run_raw_git(repo, "add", ".")
        run_raw_git(repo, "commit", "-m", "baseline")
        before_count = int(run_raw_git(repo, "rev-list", "--count", "HEAD"))

        (repo / "pre_existing.txt").write_text("dirty before handoff\n", encoding="utf-8")
        packet = json.loads(
            run_cli(
                repo,
                "delegate",
                "packet",
                "--role",
                "worker",
                "--endpoint",
                "dccp",
                "--task",
                "task_a",
                "--check",
                "check_a",
                "--pre-existing-dirty-path",
                "pre_existing.txt",
                "--assigned-path",
                "worker_owned.txt",
                "--goal",
                "Patch worker owned path only.",
            ).stdout
        )
        assert_delegate_payload(packet, "ownership worker packet")
        role_packet = packet["packet"]["role_packet"]
        if role_packet["pre_existing_dirty_paths"] != ["pre_existing.txt"]:
            raise AssertionError(f"worker packet did not surface pre-existing dirty paths: {packet}")
        if role_packet["assigned_paths"] != ["worker_owned.txt"]:
            raise AssertionError(f"worker packet did not surface assigned paths: {packet}")

        (repo / "worker_owned.txt").write_text("worker after snapshot\n", encoding="utf-8")
        (repo / "unassigned.txt").write_text("unassigned after snapshot\n", encoding="utf-8")
        ownership = json.loads(
            run_cli(
                repo,
                "delegate",
                "ownership",
                "--endpoint",
                "dccp",
                "--task",
                "task_a",
                "--check",
                "check_a",
                "--lane",
                "lane_a",
                "--pre-existing-dirty-path",
                "pre_existing.txt",
                "--assigned-path",
                "worker_owned.txt",
                "--claimed-path",
                "worker_owned.txt",
                "--claimed-path",
                "claimed_missing.txt",
                "--claimed-hunk",
                "worker_owned.txt@@line1",
            ).stdout
        )
        if not ownership.get("ok"):
            raise AssertionError(f"delegate ownership snapshot was not ok: {ownership}")
        if ownership.get("db_writes") != 0 or ownership.get("capture_claim"):
            raise AssertionError(f"delegate ownership snapshot claimed governance side effects: {ownership}")
        if ownership.get("usable") or ownership["diagnostics"]["usable"]:
            raise AssertionError(f"ownership warning snapshot stayed usable: {ownership}")
        body = ownership["ownership"]
        if body["requires_commit"] or body["git_history_pollution"]:
            raise AssertionError(f"ownership snapshot implied commit/history pollution: {ownership}")
        if body["git_commit_count"] != before_count:
            raise AssertionError(f"ownership snapshot changed commit count in output: {ownership}")
        if int(run_raw_git(repo, "rev-list", "--count", "HEAD")) != before_count:
            raise AssertionError("ownership snapshot polluted git history with a commit")

        expected_after = {"pre_existing.txt", "worker_owned.txt", "unassigned.txt"}
        if not expected_after <= set(body["after_snapshot_paths"]):
            raise AssertionError(f"ownership after snapshot missed dirty paths: {ownership}")
        if body["worker_touched_paths"] != ["worker_owned.txt"]:
            raise AssertionError(f"ownership did not isolate worker-owned path: {ownership}")
        if body["ambiguous_paths"] != ["pre_existing.txt"]:
            raise AssertionError(f"ownership did not isolate ambiguous pre-existing path: {ownership}")
        if body["unassigned_paths"] != ["unassigned.txt"]:
            raise AssertionError(f"ownership did not warn on unassigned after diff path: {ownership}")
        if body["claimed_without_after_change"] != ["claimed_missing.txt"]:
            raise AssertionError(f"ownership did not warn on claimed path without after change: {ownership}")
        warning_codes = {item["code"] for item in body["warnings"]}
        expected_warnings = {
            "claimed_path_missing_after_change",
            "after_diff_contains_unassigned_paths",
            "pre_existing_dirty_paths_still_dirty",
        }
        if not expected_warnings <= warning_codes:
            raise AssertionError(f"ownership warnings missing expected codes: {ownership}")
        classes = body["controller_path_classes"]
        if classes["pre_existing"] != ["pre_existing.txt"] or classes["worker_owned"] != ["worker_owned.txt"]:
            raise AssertionError(f"controller path classes were not distinguishable: {ownership}")
        if (repo / "shujuan.sqlite").exists() or (repo / ".shujuan" / "shujuan.sqlite").exists():
            raise AssertionError("ownership snapshot created a governance database")


def assert_review_trigger_matrix_behavior() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-review-triggers-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        light_fast = json.loads(
            run_cli(
                repo,
                "delegate",
                "plan",
                "--collaboration-mode",
                "delegated-light-fix",
                "--fast-light-fix",
            ).stdout
        )
        assert_delegate_payload(light_fast, "light fast review trigger")
        review = light_fast["plan"]["reviewer_recommendation"]
        if review["required"] or review["suggested"] or review["recommendation"] != "none":
            raise AssertionError(f"fast light fix forced reviewer without high-risk trigger: {light_fast}")

        writing = json.loads(
            run_cli(repo, "delegate", "plan", "--collaboration-mode", "writing-no-governance").stdout
        )
        assert_delegate_payload(writing, "writing review trigger")
        writing_review = writing["plan"]["reviewer_recommendation"]
        if writing_review["required"] or writing_review["suggested"]:
            raise AssertionError(f"writing-no-governance forced reviewer without high-risk trigger: {writing}")

        for trigger in [
            "full-scope",
            "p0-scope",
            "p1-scope",
            "db-runtime",
            "evidence-closure",
            "named-technology-artifact",
            "ui-visual-availability",
            "broad-closure",
            "provider-boundary",
            "subagent-output-as-closure",
        ]:
            plan = json.loads(
                run_cli(
                    repo,
                    "delegate",
                    "plan",
                    "--collaboration-mode",
                    "delegated-light-fix",
                    "--fast-light-fix",
                    "--risk-trigger",
                    trigger,
                ).stdout
            )
            review = plan["plan"]["reviewer_recommendation"]
            if review["required"] is not True or trigger not in review["triggered"]:
                raise AssertionError(f"high-risk trigger did not require reviewer for {trigger}: {plan}")
            if "delegate packet --role reviewer" not in (review["reviewer_packet_command"] or ""):
                raise AssertionError(f"high-risk trigger did not route to reviewer packet: {plan}")

        writing_high_risk = json.loads(
            run_cli(
                repo,
                "delegate",
                "plan",
                "--collaboration-mode",
                "writing-no-governance",
                "--risk-trigger",
                "provider-boundary",
            ).stdout
        )
        if writing_high_risk["plan"]["reviewer_recommendation"]["required"] is not True:
            raise AssertionError(f"writing high-risk trigger did not require reviewer: {writing_high_risk}")
        if (repo / ".shujuan").exists():
            raise AssertionError("review trigger diagnostics created .shujuan artifacts")


def assert_reviewer_diagnostics_are_read_only_and_controller_gated() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-review-diag-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        packet = json.loads(
            run_cli(
                repo,
                "delegate",
                "packet",
                "--role",
                "reviewer",
                "--packet-kind",
                "review",
                "--endpoint",
                "dccp",
                "--task",
                "task_a",
                "--check",
                "check_a",
            ).stdout
        )
        assert_delegate_payload(packet, "reviewer packet")
        role_packet = packet["packet"]["role_packet"]
        if role_packet["db_write_authority"] or role_packet["closeout_authority"]:
            raise AssertionError(f"reviewer packet was not read-only/controller gated: {packet}")

        accepted = json.loads(
            run_cli(
                repo,
                "delegate",
                "review",
                "--result",
                "accept",
                "--summary",
                "Predicates covered.",
                "--covered-predicate",
                "predicate-a",
            ).stdout
        )
        assert_delegate_payload(accepted, "review accept")
        accepted_body = accepted["review"]
        if accepted_body["recommended_classification"] != "closure-material":
            raise AssertionError(f"review accept did not recommend closure-material candidate: {accepted}")
        if accepted_body["closes_check"] or accepted_body["closes_task"] or accepted_body["blocks_controller_closeout"]:
            raise AssertionError(f"review accept implied closure or blocking authority: {accepted}")

        rejected = json.loads(
            run_cli(
                repo,
                "delegate",
                "review",
                "--result",
                "reject",
                "--summary",
                "Missing predicate proof.",
                "--missing-predicate",
                "predicate-b",
                "--blocking",
            ).stdout
        )
        assert_delegate_payload(rejected, "review reject")
        rejected_body = rejected["review"]
        if rejected_body["recommended_classification"] != "actionable" or rejected_body["blocks_controller_closeout"] is not True:
            raise AssertionError(f"review reject did not route to blocking/actionable material: {rejected}")
        if rejected_body["closes_check"] or rejected_body["closes_task"]:
            raise AssertionError(f"review reject implied direct closure authority: {rejected}")

        unclear = json.loads(
            run_cli(
                repo,
                "delegate",
                "review",
                "--result",
                "unclear",
                "--summary",
                "Need controller decision.",
                "--needs-user-decision",
            ).stdout
        )
        if unclear["review"]["recommended_classification"] != "needs-user-decision":
            raise AssertionError(f"review unclear did not route to needs-user-decision: {unclear}")

        overclaim = json.loads(
            run_cli(
                repo,
                "delegate",
                "review",
                "--result",
                "accept",
                "--summary",
                "Accept but incorrectly claims closeout.",
                "--claims-closeout",
                "--fail-on-overclaim",
                expect_ok=False,
            ).stdout
        )
        if overclaim["review"]["overclaim_risk"] is not True or not overclaim["review"]["overclaim_risks"]:
            raise AssertionError(f"review overclaim was not surfaced: {overclaim}")
        if overclaim["review"]["closes_check"] or overclaim["review"]["closes_task"]:
            raise AssertionError(f"review overclaim still implied closure: {overclaim}")
        if (repo / ".shujuan").exists():
            raise AssertionError("review diagnostics created .shujuan artifacts")


def assert_observable_collaboration_diagnostics() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-observable-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        commands = [
            ("plan", "--endpoint", "dccp", "--task", "task_a", "--check", "check_a"),
            ("packet", "--role", "worker", "--goal", "Patch one file."),
            ("capsule", "--endpoint", "dccp", "--task", "task_a", "--check", "check_a", "--next-slice", "worker_patch"),
            ("import", "--classification", "closure-material", "--artifact", "worker-return.md"),
            ("status", "--from-state", "returned", "--to-state", "imported"),
            ("review", "--result", "unclear", "--summary", "Need decision.", "--needs-user-decision"),
        ]
        for command in commands:
            payload = json.loads(run_cli(repo, "delegate", *command).stdout)
            assert_delegate_payload(payload, f"observable {' '.join(command)}")
            diagnostics = payload["diagnostics"]
            if diagnostics["usable"] is not payload["usable"]:
                raise AssertionError(f"diagnostics usable did not mirror top-level usable: {payload}")
            for key in ["raw_count", "visible_count", "filtered_count"]:
                if not isinstance(diagnostics[key], int) or diagnostics[key] < 0:
                    raise AssertionError(f"diagnostic {key} was not a non-negative count: {payload}")
            if not isinstance(diagnostics["render_errors"], list) or not isinstance(diagnostics["report_errors"], list):
                raise AssertionError(f"diagnostics did not expose render/report error lists: {payload}")
            if not diagnostics["next_action"]:
                raise AssertionError(f"diagnostics missed next_action: {payload}")
        capsule = json.loads(
            run_cli(
                repo,
                "delegate",
                "capsule",
                "--endpoint",
                "dccp",
                "--task",
                "task_a",
                "--check",
                "check_a",
                "--warning",
                "watch hidden history",
                "--next-slice",
                "worker_patch",
            ).stdout
        )
        if capsule["diagnostics"]["filtered_count"] < 4:
            raise AssertionError(f"capsule did not expose filtered hidden-surface count: {capsule}")
        if (repo / ".shujuan").exists():
            raise AssertionError("observable diagnostics created .shujuan artifacts")


def assert_runtime_preflight_fast_fail_diagnostics() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v5-runtime-preflight-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        capsule = json.loads(
            run_cli(repo, "delegate", "capsule", "--endpoint", "dccp", "--runtime-preflight", expect_ok=False).stdout
        )
        if capsule.get("ok") is not False or (capsule.get("error") or {}).get("code") != "postgres_runtime_unavailable":
            raise AssertionError(f"runtime preflight did not fast-fail without PostgreSQL config: {capsule}")
        if not capsule.get("safe_next_action"):
            raise AssertionError(f"runtime unavailable diagnostic was not actionable: {capsule}")
        if (repo / ".shujuan").exists():
            raise AssertionError(f"runtime unavailable preflight performed hidden writes: {capsule}")

        report = json.loads(
            run_cli(repo, "report", "endpoint", "dccp", "--active-only", "--runtime-preflight", expect_ok=False).stdout
        )
        if report.get("ok") is not False or (report.get("error") or {}).get("code") != "postgres_runtime_unavailable":
            raise AssertionError(f"report endpoint did not return structured runtime failure: {report}")
        if (repo / ".shujuan").exists():
            raise AssertionError("report runtime preflight created hidden .shujuan layout")

        ddl_hazard = json.loads(
            run_cli(
                repo,
                "delegate",
                "packet",
                "--role",
                "worker",
                "--runtime-preflight",
                expect_ok=False,
                extra_env={"SHUJUAN_DATABASE_URL": "sqlite:///legacy.sqlite"},
            ).stdout
        )
        if ddl_hazard.get("ok") is not False or (ddl_hazard.get("error") or {}).get("code") != "migration_runtime_ddl_hazard":
            raise AssertionError(f"SQLite runtime hazard was not detected early: {ddl_hazard}")
        if (repo / ".shujuan").exists():
            raise AssertionError("runtime DDL hazard preflight created hidden .shujuan layout")

    with tempfile.TemporaryDirectory(prefix="shujuan-v5-stale-handle-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        config_dir = repo / ".shujuan" / "postgres-dev"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text(
            json.dumps({"user": "shujuan", "database": "shujuan_test", "host": "127.0.0.1", "port": 9}),
            encoding="utf-8",
        )
        (config_dir / "credentials.json").write_text(
            json.dumps({"user": "shujuan", "password": "test"}),  # pragma: allowlist secret
            encoding="utf-8",
        )
        before_paths = sorted(path.relative_to(repo).as_posix() for path in (repo / ".shujuan").rglob("*"))
        stale = json.loads(
            run_cli(repo, "delegate", "packet", "--role", "worker", "--runtime-preflight", expect_ok=False).stdout
        )
        if stale["runtime_preflight"]["code"] != "postgres_runtime_stale_handle":
            raise AssertionError(f"stale postgres-dev handle was not detected: {stale}")
        if stale["runtime_preflight"]["recovery_command"] != "python -m shujuan postgres-dev start":
            raise AssertionError(f"stale handle diagnostic lacked recovery command: {stale}")
        after_paths = sorted(path.relative_to(repo).as_posix() for path in (repo / ".shujuan").rglob("*"))
        if before_paths != after_paths:
            raise AssertionError(f"stale handle preflight created hidden files: before={before_paths} after={after_paths}")


def main() -> int:
    assert_schema_is_minimal()
    assert_delegate_cli_no_db_writes()
    assert_delegate_modes_plan_and_packet_behavior()
    assert_delegate_status_lifecycle_validation()
    assert_phase1_role_packets_and_artifacts()
    assert_phase1_active_capsule_shape()
    assert_phase2_import_classification_matrix()
    assert_phase2_import_forbidden_closeout_paths()
    assert_p0_golden_path_smoke()
    assert_worker_ownership_snapshot_diagnostics()
    assert_review_trigger_matrix_behavior()
    assert_reviewer_diagnostics_are_read_only_and_controller_gated()
    assert_observable_collaboration_diagnostics()
    assert_runtime_preflight_fast_fail_diagnostics()
    print(json.dumps({"ok": True, "v5_dccp_delegate_skeleton": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
