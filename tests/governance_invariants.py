from __future__ import annotations

import atexit
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


def run(repo: Path, *args: str) -> dict:
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
    if completed.returncode:
        raise AssertionError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def run_fails(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
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
    if completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def git(repo: Path, *args: str) -> None:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise AssertionError(f"git {' '.join(args)} failed\n{completed.stderr}")


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


def db(repo: Path):
    return connect(repo)


def row_scalar(row) -> object:
    if row is None:
        return None
    try:
        return row[0]
    except KeyError:
        return row[next(iter(row.keys()))]


def stop_postgres_dev(repo: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
        env.pop(key, None)
    subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), "postgres-dev", "stop"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def register_postgres_cleanup(repo: Path) -> None:
    atexit.register(stop_postgres_dev, repo)


def assert_v3_terms(text: str, label: str) -> None:
    required_terms = [
        "endpoint",
        "closed",
        "resolved",
        "active",
        "deferred",
        "product_backlog",
        "audit_finding",
        "evidence",
        "provider_fact",
        "PostgreSQL success",
    ]
    missing = [term for term in required_terms if term not in text]
    if missing:
        raise AssertionError(f"{label} is missing canonical v3 terms: {missing}")


def assert_compatibility_shim(text: str, label: str) -> None:
    required = [
        "Compatibility Shim",
        "ordinary v11 work",
        "controller adoption",
        "PostgreSQL remains the runtime/write path",
        "References from v10",
    ]
    missing = [term for term in required if term not in text]
    if missing:
        raise AssertionError(f"{label} is missing compatibility-shim terms: {missing}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    assert_v3_terms((ROOT / "README.md").read_text(encoding="utf-8"), "README.md")
    assert_compatibility_shim((ROOT / ".agents" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8"), "shujuan-core SKILL.md")
    with tempfile.TemporaryDirectory(prefix="shujuan-invariants-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        postgres_started = False
        no_skill_repo = Path(temp) / "no-skill"
        no_skill_postgres_started = False
        git(repo, "init")
        (repo / ".gitignore").write_text(".shujuan/\n.ai/codegraph/\n__pycache__/\n", encoding="utf-8")
        (repo / "AGENTS.md").write_text("# Existing Agent Rules\n\nKeep local conventions.\n", encoding="utf-8")
        (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        (repo / "plan.md").write_text(
            "# Plan\n\n"
            "## Scope\n\nKeep the governance loop thin.\n\n"
            "## Acceptance\n\nSmoke evidence must close acceptance.\n",
            encoding="utf-8",
        )
        git(repo, "add", ".gitignore", "AGENTS.md", "app.py", "plan.md")
        git(repo, "-c", "user.name=Invariant", "-c", "user.email=invariant@example.invalid", "commit", "-m", "seed")

        init = run(
            repo,
            "init",
            "--name",
            "invariants",
            "--postgres-dev",
            "--postgres-dev-port",
            str(free_port()),
        )
        postgres_started = True
        register_postgres_cleanup(repo)
        if init["database"]["backend"] != "postgres":
            raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init}")
        if not (repo / ".shujuan" / "schema_version.json").exists():
            raise AssertionError("schema_version.json was not written")
        if not (repo / ".agents" / "skills" / "shujuan-core" / "SKILL.md").exists():
            raise AssertionError("init did not install repo-local shujuan-core skill")
        agents_text = (repo / "AGENTS.md").read_text(encoding="utf-8")
        if init["agents_md"]["action"] != "injected" or "shujuan Repository Instructions" not in agents_text or "First Route" not in agents_text:
            raise AssertionError("init did not inject the current compact shujuan AGENTS.md discipline")
        skill_text = (repo / ".agents" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")
        assert_compatibility_shim(skill_text, "generated shujuan-core SKILL.md")

        no_skill_repo.mkdir()
        no_skill_init = run(
            no_skill_repo,
            "init",
            "--no-install-skill",
            "--postgres-dev",
            "--postgres-dev-port",
            str(free_port()),
        )
        no_skill_postgres_started = True
        register_postgres_cleanup(no_skill_repo)
        if no_skill_init["database"]["backend"] != "postgres":
            raise AssertionError(f"--no-install-skill init did not use PostgreSQL: {no_skill_init}")
        no_skill_agents = (no_skill_repo / "AGENTS.md").read_text(encoding="utf-8")
        if (no_skill_repo / ".agents" / "skills" / "shujuan-core" / "SKILL.md").exists():
            raise AssertionError(f"--no-install-skill still installed the skill: {no_skill_init}")
        if "- Read `.agents/skills/shujuan-core/SKILL.md`" in no_skill_agents:
            raise AssertionError("--no-install-skill left an unconditional AGENTS.md reference to a missing skill")

        migrations_dir = repo / "migrations" / "shujuan"
        migrations_dir.mkdir(parents=True, exist_ok=True)
        (migrations_dir / "001_marker.sql").write_text(
            "CREATE TABLE IF NOT EXISTS invariant_marker (id TEXT PRIMARY KEY);\n",
            encoding="utf-8",
        )
        conn = db(repo)
        conn.execute("UPDATE project_meta SET schema_version = ?", ("0.0.1",))
        conn.commit()
        conn.close()
        status_before = run(repo, "migrate", "status")
        pending_before = [item["filename"] for item in status_before["pending"]]
        if status_before["schema_state"] != "needs_migration" or "001_marker.sql" not in pending_before:
            raise AssertionError(f"migration was not pending: {status_before}")
        if status_before["migration_policy"] != "tracked_repo_sql" or status_before["migrations_dir"] != "migrations/shujuan":
            raise AssertionError(f"migration policy did not point at tracked repo SQL: {status_before}")
        apply_result = run(repo, "migrate", "apply")
        applied_filenames = [item["filename"] for item in apply_result["applied"]]
        if "001_marker.sql" not in applied_filenames:
            raise AssertionError(f"migration did not apply: {apply_result}")
        status_after = run(repo, "migrate", "status")
        if status_after["schema_state"] != "current" or status_after["pending"]:
            raise AssertionError(f"migration still pending: {status_after}")
        (migrations_dir / "001_marker.sql").write_text(
            "CREATE TABLE IF NOT EXISTS invariant_marker (id TEXT PRIMARY KEY, changed TEXT);\n",
            encoding="utf-8",
        )
        mismatch = run_fails(repo, "migrate", "apply")
        if "migration checksum mismatch" not in mismatch.stderr:
            raise AssertionError(f"migration checksum mismatch was not a hard failure: {mismatch.stderr}")

        transcript = repo / "manual.txt"
        transcript.write_text("User: capture this\nAssistant: captured\nTool: tool output\n", encoding="utf-8")
        events = run(repo, "adapter", "manual", "events", "--transcript", "manual.txt", "--session-id", "manual_session")
        if [event["event_type"] for event in events["events"]] != ["user_prompt", "assistant_message", "tool_event"]:
            raise AssertionError(f"manual adapter event mapping changed: {events}")
        imported = run(repo, "adapter", "manual", "import", "--transcript", "manual.txt", "--session-id", "manual_session")
        conn = db(repo)
        event_count = row_scalar(conn.execute("SELECT COUNT(*) FROM interaction_events WHERE session_id = ?", ("manual_session",)).fetchone())
        message_count = row_scalar(conn.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", ("manual_session",)).fetchone())
        conn.close()
        if event_count != 3 or message_count != 3:
            raise AssertionError(f"manual adapter did not persist events/messages: {imported}")

        provider = run(repo, "provider", "contract")
        if provider["contract_version"] != "shujuan.impact_provider.v1" or provider["required"]:
            raise AssertionError(f"provider contract drifted: {provider}")
        if (
            "installed" not in provider
            or "indexed" not in provider
            or provider["index_path"] != ".gitnexus"
            or (provider.get("provider_detail") or {}).get("name") != "gitnexus"
        ):
            raise AssertionError(f"provider contract lost diagnostic shape: {provider}")

        workflow = run(
            repo,
            "workflow",
            "begin",
            "--session-id",
            "workflow_session",
            "--content",
            "Begin guarded work.",
            "--task",
            "Begin guarded work.",
            "--endpoint",
            "workflow",
        )
        if not workflow["message_id"] or not workflow["context"]["activation_log_id"]:
            raise AssertionError(f"workflow begin did not record prompt and context: {workflow}")
        conn = db(repo)
        workflow_message = conn.execute(
            "SELECT actor, content FROM messages WHERE id = ?",
            (workflow["message_id"],),
        ).fetchone()
        workflow_activation = conn.execute(
            "SELECT task_text FROM activation_logs WHERE id = ?",
            (workflow["context"]["activation_log_id"],),
        ).fetchone()
        conn.close()
        if not workflow_message or workflow_message["actor"] != "user" or workflow_activation["task_text"] != "Begin guarded work.":
            raise AssertionError("workflow begin failed to persist prompt/context guard")

        doc = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
        conn = db(repo)
        section_edge_count = row_scalar(conn.execute(
            "SELECT COUNT(*) FROM edges WHERE type = 'DERIVED_FROM' AND to_node_id = ?",
            (doc["document_node_id"],),
        ).fetchone())
        acceptance_section = conn.execute(
            "SELECT id, node_id FROM document_sections WHERE heading = 'Acceptance'"
        ).fetchone()
        conn.close()
        if section_edge_count < 2 or not acceptance_section:
            raise AssertionError("document section DERIVED_FROM evidence is not traceable")

        contract = run(
            repo,
            "scope",
            "create",
            "--body",
            "Invariant contract.",
            "--source-node",
            doc["document_node_id"],
        )
        task = run(
            repo,
            "task",
            "add",
            "--contract",
            contract["contract_id"],
            "--body",
            "Invariant task.",
            "--from-node",
            acceptance_section["node_id"],
        )
        check_a = run(
            repo,
            "acceptance",
            "add",
            "--task",
            task["task_id"],
            "--body",
            "First check.",
            "--expected-evidence-type",
            "user_confirmation",
            "--from-node",
            acceptance_section["node_id"],
        )
        check_b = run(
            repo,
            "acceptance",
            "add",
            "--task",
            task["task_id"],
            "--body",
            "Second check stays open.",
            "--from-node",
            acceptance_section["node_id"],
        )
        missing_contract_source = run_fails(repo, "scope", "create", "--body", "no source")
        if "--source-node" not in missing_contract_source.stderr:
            raise AssertionError(f"scope contract without source was not rejected clearly: {missing_contract_source.stderr}")
        missing_task_source = run_fails(repo, "task", "add", "--body", "no source")
        if "--from-node" not in missing_task_source.stderr:
            raise AssertionError(f"task without source was not rejected clearly: {missing_task_source.stderr}")
        missing_check_source = run_fails(
            repo,
            "acceptance",
            "add",
            "--task",
            task["task_id"],
            "--body",
            "no source",
        )
        if "--from-node" not in missing_check_source.stderr:
            raise AssertionError(f"acceptance check without source was not rejected clearly: {missing_check_source.stderr}")
        orphan_check = run_fails(
            repo,
            "graph",
            "extract",
            "--from-section",
            acceptance_section["id"],
            "--type",
            "acceptance_check",
            "--label",
            "orphan check",
            "--summary",
            "must fail",
        )
        if "requires --task" not in orphan_check.stderr:
            raise AssertionError(f"orphan graph acceptance_check failed for wrong reason: {orphan_check.stderr}")
        failed_test = run(
            repo,
            "evidence",
            "test-result",
            "--check",
            check_b["acceptance_check_id"],
            "--close-check",
            "--allow-fail",
            "--from-node",
            acceptance_section["node_id"],
            "--",
            sys.executable,
            "-c",
            "import sys; print('negative test'); sys.exit(7)",
        )
        if not failed_test["close_skipped"]["skipped"]:
            raise AssertionError(f"failed test did not report close skipped: {failed_test}")
        conn = db(repo)
        still_open = row_scalar(conn.execute(
            "SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?",
            (check_b["acceptance_check_id"],),
        ).fetchone())
        conn.close()
        if still_open is not None:
            raise AssertionError("failed test_result closed an acceptance check")
        second_failed_test = run(
            repo,
            "evidence",
            "test-result",
            "--allow-fail",
            "--",
            sys.executable,
            "-c",
            "import sys; sys.exit(3)",
        )
        if failed_test["stdout_ref"] == second_failed_test["stdout_ref"]:
            raise AssertionError("test_result stdout capture ref was reused")
        failed_manual_close = run_fails(
            repo,
            "acceptance",
            "close",
            "--check",
            check_b["acceptance_check_id"],
            "--evidence-node",
            failed_test["node_id"],
            "--override-evidence-type",
            "--override-reason",
            "negative test must still be rejected",
        )
        if "failed test_result" not in failed_manual_close.stderr:
            raise AssertionError(f"manual close accepted or misreported failed test_result: {failed_manual_close.stderr}")
        override_task = run(
            repo,
            "task",
            "add",
            "--contract",
            contract["contract_id"],
            "--body",
            "Evidence type override task.",
            "--from-node",
            acceptance_section["node_id"],
        )
        override_check = run(
            repo,
            "acceptance",
            "add",
            "--task",
            override_task["task_id"],
            "--body",
            "Artifact-expected check closed by test_result only with explicit override.",
            "--expected-evidence-type",
            "artifact",
            "--from-node",
            acceptance_section["node_id"],
        )
        override_test = run(
            repo,
            "evidence",
            "test-result",
            "--check",
            override_check["acceptance_check_id"],
            "--close-check",
            "--override-evidence-type",
            "--override-reason",
            "Packaging smoke is acceptable evidence for this artifact check.",
            "--from-node",
            acceptance_section["node_id"],
            "--",
            sys.executable,
            "-c",
            "print('override closure ok')",
        )
        override_warning_ids = override_test["check_links"]["warnings"]
        if not override_warning_ids:
            raise AssertionError(f"test_result override closure did not report warning node: {override_test}")
        conn = db(repo)
        override_closed_by = row_scalar(conn.execute(
            "SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?",
            (override_check["acceptance_check_id"],),
        ).fetchone())
        override_warning = conn.execute(
            "SELECT type, props FROM nodes WHERE id = ?",
            (override_warning_ids[0],),
        ).fetchone()
        conn.close()
        if override_closed_by != override_test["node_id"]:
            raise AssertionError("test_result override did not close artifact-expected check")
        if not override_warning or override_warning["type"] != "audit_finding":
            raise AssertionError(f"test_result override warning was not an audit_finding: {override_warning}")
        override_warning_props = json.loads(override_warning["props"])
        if override_warning_props.get("kind") != "evidence_type_override":
            raise AssertionError(f"test_result override warning kind was wrong: {override_warning_props}")
        manual_override_check = run(
            repo,
            "acceptance",
            "add",
            "--task",
            override_task["task_id"],
            "--body",
            "Artifact-expected check manually closed by confirmation only with explicit override.",
            "--expected-evidence-type",
            "artifact",
            "--from-node",
            acceptance_section["node_id"],
        )
        confirmation_evidence = run(
            repo,
            "evidence",
            "user-confirmation",
            "--body",
            "User accepted this mismatched evidence type for a narrow reason.",
            "--from-node",
            acceptance_section["node_id"],
        )
        no_override_close = run_fails(
            repo,
            "acceptance",
            "close",
            "--check",
            manual_override_check["acceptance_check_id"],
            "--evidence-node",
            confirmation_evidence["node_id"],
        )
        if "--override-evidence-type" not in no_override_close.stderr:
            raise AssertionError(f"mismatched manual close failed for wrong reason: {no_override_close.stderr}")
        manual_override_close = run(
            repo,
            "acceptance",
            "close",
            "--check",
            manual_override_check["acceptance_check_id"],
            "--evidence-node",
            confirmation_evidence["node_id"],
            "--override-evidence-type",
            "--override-reason",
            "User confirmation is accepted for this artifact check during repair.",
        )
        if not manual_override_close["warning_node_ids"]:
            raise AssertionError(f"manual override close did not return warning node: {manual_override_close}")
        conn = db(repo)
        manual_closed_by = row_scalar(conn.execute(
            "SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?",
            (manual_override_check["acceptance_check_id"],),
        ).fetchone())
        manual_warning = conn.execute(
            "SELECT type, props FROM nodes WHERE id = ?",
            (manual_override_close["warning_node_ids"][0],),
        ).fetchone()
        conn.close()
        if manual_closed_by != confirmation_evidence["node_id"]:
            raise AssertionError("manual override did not close artifact-expected check")
        if not manual_warning or manual_warning["type"] != "audit_finding":
            raise AssertionError(f"manual override warning was not an audit_finding: {manual_warning}")
        manual_warning_props = json.loads(manual_warning["props"])
        if manual_warning_props.get("kind") != "evidence_type_override":
            raise AssertionError(f"manual override warning kind was wrong: {manual_warning_props}")
        predicate_task = run(
            repo,
            "task",
            "add",
            "--contract",
            contract["contract_id"],
            "--body",
            "Predicate evidence lifecycle task.",
            "--from-node",
            acceptance_section["node_id"],
        )
        predicate_check = run(
            repo,
            "acceptance",
            "add",
            "--task",
            predicate_task["task_id"],
            "--body",
            "Predicate evidence must satisfy stdout requirements.",
            "--expected-evidence-type",
            "test_result",
            "--from-node",
            acceptance_section["node_id"],
        )
        predicate_failed = run(
            repo,
            "evidence",
            "test-result",
            "--check",
            predicate_check["acceptance_check_id"],
            "--close-check",
            "--require-stdout",
            "--allow-fail",
            "--from-node",
            acceptance_section["node_id"],
            "--",
            sys.executable,
            "-c",
            "pass",
        )
        if predicate_failed["predicate_ok"] or not predicate_failed["close_skipped"]["skipped"]:
            raise AssertionError(f"stdout predicate failure did not skip closure: {predicate_failed}")
        conn = db(repo)
        predicate_props = json.loads(conn.execute("SELECT props FROM nodes WHERE id = ?", (predicate_failed["node_id"],)).fetchone()["props"])
        predicate_still_open = row_scalar(conn.execute(
            "SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?",
            (predicate_check["acceptance_check_id"],),
        ).fetchone())
        conn.close()
        if predicate_still_open is not None:
            raise AssertionError("predicate-failed test_result closed an acceptance check")
        if (
            predicate_props.get("argv", [])[:2] != [sys.executable, "-c"]
            or not predicate_props.get("command")
            or not predicate_props.get("cwd_hash")
            or not predicate_props.get("env_hash")
            or not predicate_props.get("stdout_ref")
            or not predicate_props.get("stderr_ref")
            or predicate_props.get("predicate_ok") is not False
        ):
            raise AssertionError(f"test_result did not store argv/cwd/env/stdout/stderr predicate trust props: {predicate_props}")
        predicate_manual_close = run_fails(
            repo,
            "acceptance",
            "close",
            "--check",
            predicate_check["acceptance_check_id"],
            "--evidence-node",
            predicate_failed["node_id"],
        )
        if "required predicates" not in predicate_manual_close.stderr:
            raise AssertionError(f"predicate-failed evidence was not rejected clearly: {predicate_manual_close.stderr}")
        predicate_pass = run(
            repo,
            "evidence",
            "test-result",
            "--check",
            predicate_check["acceptance_check_id"],
            "--close-check",
            "--require-stdout",
            "--stdout-contains",
            "predicate-ok",
            "--from-node",
            acceptance_section["node_id"],
            "--",
            sys.executable,
            "-c",
            "print('predicate-ok')",
        )
        if not predicate_pass["predicate_ok"] or predicate_pass["close_skipped"]["skipped"]:
            raise AssertionError(f"predicate-passing evidence did not close: {predicate_pass}")
        predicate_status = run(repo, "evidence", "status", "--node", predicate_pass["node_id"])
        if predicate_status["current_state"] != "active" or predicate_check["acceptance_check_id"] not in {
            item["id"] for item in predicate_status["closures"]["acceptance_checks"]
        }:
            raise AssertionError(f"active evidence status did not report current closure: {predicate_status}")
        invalidated_evidence = run(
            repo,
            "evidence",
            "set-state",
            "--node",
            predicate_pass["node_id"],
            "--state",
            "invalidated",
            "--source-node",
            acceptance_section["node_id"],
            "--reason",
            "Predicate evidence was invalidated by later review.",
        )
        if predicate_check["acceptance_check_id"] not in invalidated_evidence["cleared_closures"]["acceptance_checks"]:
            raise AssertionError(f"invalidating evidence did not reopen closed check: {invalidated_evidence}")
        invalidated_status = run(repo, "evidence", "status", "--node", predicate_pass["node_id"])
        if invalidated_status["current_state"] != "invalidated" or invalidated_status["closures"]["acceptance_checks"]:
            raise AssertionError(f"invalidated evidence still looked current: {invalidated_status}")
        invalidated_manual_close = run_fails(
            repo,
            "acceptance",
            "close",
            "--check",
            predicate_check["acceptance_check_id"],
            "--evidence-node",
            predicate_pass["node_id"],
        )
        if "only current valid evidence" not in invalidated_manual_close.stderr:
            raise AssertionError(f"invalidated evidence was not rejected clearly: {invalidated_manual_close.stderr}")
        mismatch_close = run_fails(
            repo,
            "evidence",
            "user-confirmation",
            "--body",
            "wrong evidence type",
            "--check",
            check_b["acceptance_check_id"],
            "--close-check",
        )
        if "expects evidence type diff" not in mismatch_close.stderr:
            raise AssertionError(f"mismatched evidence type was not rejected clearly: {mismatch_close.stderr}")
        premature = run_fails(
            repo,
            "evidence",
            "user-confirmation",
            "--body",
            "close too early",
            "--check",
            check_a["acceptance_check_id"],
            "--close-check",
            "--close-task",
        )
        if "cannot close while acceptance checks remain open" not in premature.stderr:
            raise AssertionError(f"premature task close failed for wrong reason: {premature.stderr}")

        immutable_task = run(
            repo,
            "task",
            "add",
            "--contract",
            contract["contract_id"],
            "--body",
            "Closure immutability task.",
            "--from-node",
            acceptance_section["node_id"],
        )
        immutable_check = run(
            repo,
            "acceptance",
            "add",
            "--task",
            immutable_task["task_id"],
            "--body",
            "Closure evidence cannot be overwritten.",
            "--expected-evidence-type",
            "user_confirmation",
            "--from-node",
            acceptance_section["node_id"],
        )
        first_close = run(
            repo,
            "evidence",
            "user-confirmation",
            "--body",
            "first immutable closure",
            "--check",
            immutable_check["acceptance_check_id"],
            "--close-check",
            "--close-task",
        )
        overwrite_close = run_fails(
            repo,
            "evidence",
            "user-confirmation",
            "--body",
            "second immutable closure",
            "--check",
            immutable_check["acceptance_check_id"],
            "--close-check",
        )
        if "already closed by" not in overwrite_close.stderr or first_close["node_id"] not in overwrite_close.stderr:
            raise AssertionError(f"closed check overwrite was not rejected clearly: {overwrite_close.stderr}")
        replacement_evidence = run(
            repo,
            "evidence",
            "user-confirmation",
            "--body",
            "replacement immutable closure",
        )
        replace_closure = run(
            repo,
            "acceptance",
            "replace-closure",
            "--check",
            immutable_check["acceptance_check_id"],
            "--evidence-node",
            replacement_evidence["node_id"],
            "--reason",
            "Replace incorrect closure evidence with verified evidence.",
        )
        if (
            replace_closure["old_evidence_node_id"] != first_close["node_id"]
            or replace_closure["new_evidence_node_id"] != replacement_evidence["node_id"]
            or not replace_closure["task_updated"]
        ):
            raise AssertionError(f"replace-closure did not update check/task closure: {replace_closure}")
        conn = db(repo)
        replaced_check = conn.execute(
            "SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?",
            (immutable_check["acceptance_check_id"],),
        ).fetchone()
        replaced_task = conn.execute(
            "SELECT closed_by_node_id FROM tasks WHERE id = ?",
            (immutable_task["task_id"],),
        ).fetchone()
        supersedes_edge = conn.execute(
            """
            SELECT id
            FROM edges
            WHERE from_node_id = ? AND type = 'SUPERSEDES' AND to_node_id = ?
            """,
            (replacement_evidence["node_id"], first_close["node_id"]),
        ).fetchone()
        conn.close()
        if (
            replaced_check["closed_by_node_id"] != replacement_evidence["node_id"]
            or replaced_task["closed_by_node_id"] != replacement_evidence["node_id"]
            or not supersedes_edge
        ):
            raise AssertionError("replace-closure did not persist replacement evidence and SUPERSEDES edge")
        old_evidence_status = run(repo, "evidence", "status", "--node", first_close["node_id"])
        if old_evidence_status["current_state"] != "superseded":
            raise AssertionError(f"replace-closure did not supersede old evidence lifecycle: {old_evidence_status}")

        matrix_task = run(
            repo,
            "task",
            "add",
            "--contract",
            contract["contract_id"],
            "--body",
            "Predicate coverage matrix task.",
            "--from-node",
            acceptance_section["node_id"],
        )

        def add_matrix_check(body: str) -> dict:
            return run(
                repo,
                "acceptance",
                "add",
                "--task",
                matrix_task["task_id"],
                "--body",
                body,
                "--expected-evidence-type",
                "test_result",
                "--from-node",
                acceptance_section["node_id"],
            )

        broad_a = add_matrix_check("Broad test matrix check A.")
        broad_b = add_matrix_check("Broad test matrix check B.")
        broad_without_matrix = run_fails(
            repo,
            "evidence",
            "test-result",
            "--check",
            broad_a["acceptance_check_id"],
            "--check",
            broad_b["acceptance_check_id"],
            "--close-check",
            "--from-node",
            acceptance_section["node_id"],
            "--",
            sys.executable,
            "-c",
            "print('broad no matrix')",
        )
        if "predicate_coverage_matrix" not in broad_without_matrix.stderr:
            raise AssertionError(f"broad test_result without matrix failed for wrong reason: {broad_without_matrix.stderr}")
        conn = db(repo)
        broad_closed = row_scalar(conn.execute(
            "SELECT COUNT(*) FROM acceptance_checks WHERE id IN (?, ?) AND closed_by_node_id IS NOT NULL",
            (broad_a["acceptance_check_id"], broad_b["acceptance_check_id"]),
        ).fetchone())
        conn.close()
        if broad_closed:
            raise AssertionError("broad test_result without matrix partially closed checks")

        missing_not_covered_a = add_matrix_check("Matrix row missing not_covered check A.")
        missing_not_covered_b = add_matrix_check("Matrix row missing not_covered check B.")
        missing_not_covered_path = repo / "matrix_missing_not_covered.json"
        missing_not_covered_path.write_text(
            json.dumps(
                {
                    "predicate_coverage_matrix": [
                        {
                            "check_id": missing_not_covered_a["acceptance_check_id"],
                            "predicate_id": "HP-MISSING-NOT-COVERED-A",
                            "assertion": "A is covered.",
                            "result": "pass",
                            "reason": "",
                        },
                        {
                            "check_id": missing_not_covered_b["acceptance_check_id"],
                            "predicate_id": "HP-MISSING-NOT-COVERED-B",
                            "assertion": "B is covered.",
                            "result": "pass",
                            "reason": "",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        missing_not_covered = run_fails(
            repo,
            "evidence",
            "test-result",
            "--predicate-coverage-matrix",
            "matrix_missing_not_covered.json",
            "--check",
            missing_not_covered_a["acceptance_check_id"],
            "--check",
            missing_not_covered_b["acceptance_check_id"],
            "--close-check",
            "--from-node",
            acceptance_section["node_id"],
            "--",
            sys.executable,
            "-c",
            "print('missing not_covered')",
        )
        if "not_covered" not in missing_not_covered.stderr:
            raise AssertionError(f"matrix missing not_covered failed for wrong reason: {missing_not_covered.stderr}")

        failed_row_a = add_matrix_check("Matrix failed row check A.")
        failed_row_b = add_matrix_check("Matrix failed row check B.")
        failed_row_path = repo / "matrix_failed_row.json"
        failed_row_path.write_text(
            json.dumps(
                {
                    "predicate_coverage_matrix": [
                        {
                            "check_id": failed_row_a["acceptance_check_id"],
                            "predicate_id": "HP-FAILED-ROW-A",
                            "assertion": "A is covered.",
                            "result": "pass",
                            "not_covered": False,
                            "reason": "",
                        },
                        {
                            "check_id": failed_row_b["acceptance_check_id"],
                            "predicate_id": "HP-FAILED-ROW-B",
                            "assertion": "B is not covered.",
                            "result": "fail",
                            "not_covered": True,
                            "reason": "Predicate B did not run.",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        failed_row = run_fails(
            repo,
            "evidence",
            "test-result",
            "--predicate-coverage-matrix",
            "matrix_failed_row.json",
            "--check",
            failed_row_a["acceptance_check_id"],
            "--check",
            failed_row_b["acceptance_check_id"],
            "--close-check",
            "--from-node",
            acceptance_section["node_id"],
            "--",
            sys.executable,
            "-c",
            "print('failed row')",
        )
        if "failed or not-covered" not in failed_row.stderr:
            raise AssertionError(f"matrix failed/not-covered row failed for wrong reason: {failed_row.stderr}")

        valid_matrix_a = add_matrix_check("Valid matrix check A.")
        valid_matrix_b = add_matrix_check("Valid matrix check B.")
        valid_matrix_path = repo / "matrix_valid.json"
        valid_matrix_rows = [
            {
                "check_id": valid_matrix_a["acceptance_check_id"],
                "predicate_id": "HP-VALID-MATRIX-A",
                "assertion": "A is covered by a named predicate.",
                "result": "pass",
                "not_covered": False,
                "reason": "",
            },
            {
                "check_id": valid_matrix_b["acceptance_check_id"],
                "predicate_id": "HP-VALID-MATRIX-B",
                "assertion": "B is covered by a named predicate.",
                "result": "pass",
                "not_covered": False,
                "reason": "",
            },
        ]
        valid_matrix_path.write_text(json.dumps({"predicate_coverage_matrix": valid_matrix_rows}), encoding="utf-8")
        valid_matrix = run(
            repo,
            "evidence",
            "test-result",
            "--predicate-coverage-matrix",
            "matrix_valid.json",
            "--check",
            valid_matrix_a["acceptance_check_id"],
            "--check",
            valid_matrix_b["acceptance_check_id"],
            "--close-check",
            "--from-node",
            acceptance_section["node_id"],
            "--",
            sys.executable,
            "-c",
            "print('valid matrix')",
        )
        conn = db(repo)
        valid_closed = row_scalar(conn.execute(
            "SELECT COUNT(*) FROM acceptance_checks WHERE id IN (?, ?) AND closed_by_node_id = ?",
            (valid_matrix_a["acceptance_check_id"], valid_matrix_b["acceptance_check_id"], valid_matrix["node_id"]),
        ).fetchone())
        valid_props = json.loads(conn.execute("SELECT props FROM nodes WHERE id = ?", (valid_matrix["node_id"],)).fetchone()["props"])
        conn.close()
        if valid_closed != 2:
            raise AssertionError(f"valid predicate coverage matrix did not close both checks: {valid_matrix}")
        if (
            valid_props.get("predicate_coverage_matrix_row_count") != 2
            or set(valid_props.get("predicate_coverage_matrix_covered_check_ids", []))
            != {valid_matrix_a["acceptance_check_id"], valid_matrix_b["acceptance_check_id"]}
            or valid_props.get("predicate_coverage_matrix_not_covered_check_ids")
            or not valid_props.get("predicate_coverage_matrix_ref")
            or not valid_props.get("predicate_coverage_matrix_sha256")
        ):
            raise AssertionError(f"valid matrix metadata was not stored on test_result: {valid_props}")

        reuse_a = add_matrix_check("Single-check test_result reuse check A.")
        reuse_b = add_matrix_check("Single-check test_result reuse check B.")
        single_check_test = run(
            repo,
            "evidence",
            "test-result",
            "--check",
            reuse_a["acceptance_check_id"],
            "--close-check",
            "--from-node",
            acceptance_section["node_id"],
            "--",
            sys.executable,
            "-c",
            "print('single check')",
        )
        reuse_rejected = run_fails(
            repo,
            "acceptance",
            "close",
            "--check",
            reuse_b["acceptance_check_id"],
            "--evidence-node",
            single_check_test["node_id"],
        )
        if "predicate_coverage_matrix" not in reuse_rejected.stderr:
            raise AssertionError(f"manual reuse without matrix failed for wrong reason: {reuse_rejected.stderr}")
        conn = db(repo)
        reuse_b_open = row_scalar(conn.execute(
            "SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?",
            (reuse_b["acceptance_check_id"],),
        ).fetchone())
        conn.close()
        if reuse_b_open is not None:
            raise AssertionError("manual reuse without matrix closed the second check")

        override_a = add_matrix_check("Predicate coverage override check A.")
        override_b = add_matrix_check("Predicate coverage override check B.")
        override_c = add_matrix_check("Predicate coverage override untouched check C.")
        override_test = run(
            repo,
            "evidence",
            "test-result",
            "--check",
            override_a["acceptance_check_id"],
            "--close-check",
            "--from-node",
            acceptance_section["node_id"],
            "--",
            sys.executable,
            "-c",
            "print('override first')",
        )
        override_close = run(
            repo,
            "acceptance",
            "close",
            "--check",
            override_b["acceptance_check_id"],
            "--evidence-node",
            override_test["node_id"],
            "--override-predicate-coverage",
            "--override-reason",
            "Broad smoke result is accepted during manual repair with explicit warning.",
        )
        if not override_close["warning_node_ids"]:
            raise AssertionError(f"predicate coverage override did not return warning node: {override_close}")
        conn = db(repo)
        override_warning = conn.execute(
            "SELECT type, props FROM nodes WHERE id = ?",
            (override_close["warning_node_ids"][0],),
        ).fetchone()
        override_rows = conn.execute(
            "SELECT id, closed_by_node_id FROM acceptance_checks WHERE id IN (?, ?, ?)",
            (override_a["acceptance_check_id"], override_b["acceptance_check_id"], override_c["acceptance_check_id"]),
        ).fetchall()
        conn.close()
        override_closed = {row["id"]: row["closed_by_node_id"] for row in override_rows}
        if (
            override_closed[override_a["acceptance_check_id"]] != override_test["node_id"]
            or override_closed[override_b["acceptance_check_id"]] != override_test["node_id"]
            or override_closed[override_c["acceptance_check_id"]] is not None
        ):
            raise AssertionError(f"predicate coverage override closed the wrong checks: {override_closed}")
        if not override_warning or override_warning["type"] != "audit_finding":
            raise AssertionError(f"predicate coverage override warning was not an audit_finding: {override_warning}")
        override_warning_props = json.loads(override_warning["props"])
        if override_warning_props.get("kind") != "predicate_coverage_override":
            raise AssertionError(f"predicate coverage override warning kind was wrong: {override_warning_props}")

        missing_source = run_fails(repo, "scope", "change", "--body", "no source")
        if "--source-node" not in missing_source.stderr:
            raise AssertionError(f"scope change without evidence was not rejected clearly: {missing_source.stderr}")
        missing_scope_target = run_fails(
            repo,
            "scope",
            "change",
            "--body",
            "source but no target",
            "--source-node",
            acceptance_section["node_id"],
        )
        if "requires at least one --task or --applies-to" not in missing_scope_target.stderr:
            raise AssertionError(f"scope change without a target was not rejected clearly: {missing_scope_target.stderr}")

        endpoint = run(repo, "endpoint", "create", "invariant", "--description", "Invariant endpoint.", "--root-node", contract["node_id"])
        endpoint_node_id = endpoint["node_id"]
        targeted_scope_change = run(
            repo,
            "scope",
            "change",
            "--body",
            "A scope change may apply to an endpoint without deferring a task.",
            "--source-node",
            acceptance_section["node_id"],
            "--applies-to",
            endpoint_node_id,
        )
        if not targeted_scope_change["applies_edges"]:
            raise AssertionError(f"scope change --applies-to did not create APPLIES_TO edge: {targeted_scope_change}")
        legacy_orphan = run(
            repo,
            "graph",
            "extract",
            "--from-section",
            acceptance_section["id"],
            "--type",
            "scope_change",
            "--label",
            "legacy orphan scope change",
            "--summary",
            "Historical semantic node needs formal attachment.",
        )
        orphan_doctor = run(repo, "db", "doctor", "--allow-fail")
        orphan_ids = {item["node_id"] for item in orphan_doctor["severity_buckets"]["P1"] if item["code"] == "orphan_semantic_node"}
        if legacy_orphan["node_id"] not in orphan_ids:
            raise AssertionError(f"db doctor did not surface legacy orphan semantic node: {orphan_doctor}")
        link = run(
            repo,
            "graph",
            "link",
            "--from-node",
            legacy_orphan["node_id"],
            "--type",
            "APPLIES_TO",
            "--to-node",
            endpoint_node_id,
            "--reason",
            "Attach historical scope-change node to the endpoint.",
        )
        if link["type"] != "APPLIES_TO":
            raise AssertionError(f"graph link did not create the requested edge: {link}")
        db_doctor_after_link = run(repo, "db", "doctor")
        if not db_doctor_after_link["ok"]:
            raise AssertionError(f"db doctor still reported active semantic hygiene issues after graph link: {db_doctor_after_link}")
        resolved_unresolved = run(
            repo,
            "unresolved",
            "add",
            "--body",
            "This transient unresolved should be resolved by later evidence.",
            "--source-node",
            acceptance_section["node_id"],
            "--applies-to",
            task["node_id"],
        )
        status_with_unresolved = run(repo, "endpoint", "status", "invariant")
        if resolved_unresolved["node_id"] not in {item["id"] for item in status_with_unresolved["unresolved"]}:
            raise AssertionError(f"endpoint status did not show active unresolved before resolution: {status_with_unresolved}")
        strict_with_unresolved = run_fails(repo, "endpoint", "doctor", "invariant", "--strict-closeout")
        strict_unresolved_payload = json.loads(strict_with_unresolved.stdout)
        strict_unresolved_codes = {
            item["code"]
            for bucket in strict_unresolved_payload["severity_buckets"].values()
            for item in bucket
        }
        if "active_unresolved_questions" not in strict_unresolved_codes or not strict_unresolved_payload.get("endpoint_refresh"):
            raise AssertionError(f"strict closeout did not block active unresolved and refresh projection: {strict_unresolved_payload}")
        conn = db(repo)
        unresolved_state = conn.execute(
            "SELECT current_state FROM semantic_items WHERE node_id = ?",
            (resolved_unresolved["node_id"],),
        ).fetchone()
        conn.close()
        if not unresolved_state or unresolved_state["current_state"] != "active":
            raise AssertionError("unresolved semantic item was not registered as active")
        resolve_unresolved = run(
            repo,
            "graph",
            "resolve",
            "--node",
            resolved_unresolved["node_id"],
            "--source-node",
            acceptance_section["node_id"],
            "--body",
            "Later evidence resolves the transient unresolved.",
            "--endpoint",
            "invariant",
        )
        if resolve_unresolved["resolved_node_id"] != resolved_unresolved["node_id"] or not resolve_unresolved["endpoint_refresh"]:
            raise AssertionError(f"graph resolve did not resolve and refresh: {resolve_unresolved}")
        status_after_resolve = run(repo, "endpoint", "status", "invariant")
        if resolved_unresolved["node_id"] in {item["id"] for item in status_after_resolve["unresolved"]}:
            raise AssertionError(f"resolved unresolved still appears active in endpoint status: {status_after_resolve}")
        conn = db(repo)
        resolved_state = conn.execute(
            "SELECT current_state FROM semantic_items WHERE node_id = ?",
            (resolved_unresolved["node_id"],),
        ).fetchone()
        resolved_events = int(
            row_scalar(conn.execute(
                "SELECT COUNT(*) FROM semantic_lifecycle_events WHERE node_id = ? AND to_state = 'resolved'",
                (resolved_unresolved["node_id"],),
            ).fetchone())
        )
        conn.close()
        if not resolved_state or resolved_state["current_state"] != "resolved" or resolved_events < 1:
            raise AssertionError("graph resolve did not update semantic lifecycle projection")
        (repo / "resolved_audit.md").write_text("finding fixed by later evidence\n", encoding="utf-8")
        audit_to_resolve = run(
            repo,
            "audit",
            "record",
            "--endpoint",
            "invariant",
            "--source-node",
            acceptance_section["node_id"],
            "--path",
            "resolved_audit.md",
            "--finding",
            "Transient audit finding should be resolved by a resolution note.",
        )
        audit_node_id = audit_to_resolve["audit_finding_node_ids"][0]
        status_with_audit = run(repo, "endpoint", "status", "invariant")
        if audit_node_id not in {item["id"] for item in status_with_audit["recent_audit_findings"]}:
            raise AssertionError(f"endpoint status did not show active audit finding before resolution: {status_with_audit}")
        strict_with_audit = run_fails(repo, "endpoint", "doctor", "invariant", "--strict-closeout")
        strict_audit_payload = json.loads(strict_with_audit.stdout)
        strict_audit_codes = {
            item["code"]
            for bucket in strict_audit_payload["severity_buckets"].values()
            for item in bucket
        }
        if "active_audit_findings" not in strict_audit_codes:
            raise AssertionError(f"strict closeout did not block active audit finding: {strict_audit_payload}")
        run(
            repo,
            "graph",
            "resolve",
            "--node",
            audit_node_id,
            "--source-node",
            acceptance_section["node_id"],
            "--body",
            "Later evidence resolves the transient audit finding.",
            "--endpoint",
            "invariant",
        )
        status_after_audit_resolve = run(repo, "endpoint", "status", "invariant")
        if audit_node_id in {item["id"] for item in status_after_audit_resolve["recent_audit_findings"]}:
            raise AssertionError(f"resolved audit finding still appears active in endpoint status: {status_after_audit_resolve}")
        conn = db(repo)
        audit_state = conn.execute("SELECT current_state FROM semantic_items WHERE node_id = ?", (audit_node_id,)).fetchone()
        conn.close()
        if not audit_state or audit_state["current_state"] != "resolved":
            raise AssertionError("resolved audit finding did not leave a lifecycle projection")
        decision_note = run(
            repo,
            "jot",
            "add",
            "--endpoint",
            "invariant",
            "--kind",
            "needs_user_decision",
            "--body",
            "This decision note should block strict closeout until resolved.",
            "--source-node",
            acceptance_section["node_id"],
            "--applies-to",
            task["node_id"],
        )
        strict_with_decision = run_fails(repo, "endpoint", "doctor", "invariant", "--strict-closeout")
        strict_decision_payload = json.loads(strict_with_decision.stdout)
        decision_blockers = [
            item
            for item in strict_decision_payload["severity_buckets"]["P0"]
            if item["code"] == "needs_user_decision"
        ]
        if not decision_blockers or decision_note["node_id"] not in decision_blockers[0]["node_ids"]:
            raise AssertionError(f"strict closeout did not block active needs_user_decision note: {strict_decision_payload}")
        run(
            repo,
            "semantic",
            "set-state",
            "--node",
            decision_note["node_id"],
            "--state",
            "resolved",
            "--source-node",
            acceptance_section["node_id"],
            "--endpoint",
            "invariant",
        )
        strict_after_decision_resolve = run_fails(repo, "endpoint", "doctor", "invariant", "--strict-closeout")
        strict_after_payload = json.loads(strict_after_decision_resolve.stdout)
        decision_blockers_after = [
            item
            for item in strict_after_payload["severity_buckets"]["P0"]
            if item["code"] == "needs_user_decision"
        ]
        if any(decision_note["node_id"] in item.get("node_ids", []) for item in decision_blockers_after):
            raise AssertionError(f"resolved needs_user_decision still blocked strict closeout: {strict_after_payload}")
        status_after_decision_resolve = run(repo, "endpoint", "status", "invariant")
        if decision_note["node_id"] in {item["id"] for item in status_after_decision_resolve["recent_work_notes"]}:
            raise AssertionError(f"resolved decision note still appeared in active work notes: {status_after_decision_resolve}")
        inactive_ids = {item["node_id"] for item in status_after_decision_resolve["semantic_projection"]["inactive"]}
        if decision_note["node_id"] not in inactive_ids:
            raise AssertionError(f"resolved decision note was not retained in lifecycle history projection: {status_after_decision_resolve}")
        summary_import = run(
            repo,
            "audit",
            "import-agent-output",
            "--endpoint",
            "invariant",
            "--source-node",
            acceptance_section["node_id"],
            "--body",
            "Summary-only subagent handoff with changed files and tests, no actionable finding.",
        )
        if (
            summary_import["classification"] != "summary"
            or not summary_import["work_note"]
            or summary_import["audit_finding_node_ids"]
            or "unclassified_agent_output_defaulted_to_summary" not in summary_import["warnings"]
        ):
            raise AssertionError(f"summary handoff was not classified as artifact/work_note only: {summary_import}")
        actionable_import = run(
            repo,
            "audit",
            "import-agent-output",
            "--endpoint",
            "invariant",
            "--source-node",
            acceptance_section["node_id"],
            "--classification",
            "actionable",
            "--body",
            "Actionable subagent output.",
            "--finding",
            "Actionable issue from subagent output.",
        )
        if len(actionable_import["audit_finding_node_ids"]) != 1 or actionable_import["work_note"] is not None:
            raise AssertionError(f"actionable handoff did not create a focused audit finding: {actionable_import}")
        status_with_actionable_handoff = run(repo, "endpoint", "status", "invariant")
        if actionable_import["audit_finding_node_ids"][0] not in {item["id"] for item in status_with_actionable_handoff["recent_audit_findings"]}:
            raise AssertionError(f"actionable handoff finding was not active: {status_with_actionable_handoff}")
        run(
            repo,
            "semantic",
            "set-state",
            "--node",
            actionable_import["audit_finding_node_ids"][0],
            "--state",
            "resolved",
            "--source-node",
            actionable_import["artifact_node_id"],
            "--endpoint",
            "invariant",
        )
        decision_import = run(
            repo,
            "audit",
            "import-agent-output",
            "--endpoint",
            "invariant",
            "--source-node",
            acceptance_section["node_id"],
            "--classification",
            "needs_user_decision",
            "--body",
            "Subagent needs user decision before proceeding.",
        )
        decision_import_node = decision_import["work_note"]["node_id"]
        strict_with_import_decision = run_fails(repo, "endpoint", "doctor", "invariant", "--strict-closeout")
        strict_import_decision_payload = json.loads(strict_with_import_decision.stdout)
        if not any(
            item["code"] == "needs_user_decision" and decision_import_node in item.get("node_ids", [])
            for item in strict_import_decision_payload["severity_buckets"]["P0"]
        ):
            raise AssertionError(f"classified needs_user_decision handoff did not block closeout: {strict_import_decision_payload}")
        run(
            repo,
            "semantic",
            "set-state",
            "--node",
            decision_import_node,
            "--state",
            "resolved",
            "--source-node",
            decision_import["artifact_node_id"],
            "--endpoint",
            "invariant",
        )
        product_backlog_import = run(
            repo,
            "audit",
            "import-agent-output",
            "--endpoint",
            "invariant",
            "--source-node",
            acceptance_section["node_id"],
            "--classification",
            "product_backlog",
            "--body",
            "Future product-grade idea from subagent.",
        )
        provider_import = run(
            repo,
            "audit",
            "import-agent-output",
            "--endpoint",
            "invariant",
            "--source-node",
            acceptance_section["node_id"],
            "--classification",
            "provider_hypothesis",
            "--body",
            "Provider hypothesis needs independent verification.",
        )
        conn = db(repo)
        classified_states = {
            row["node_id"]: row["current_state"]
            for row in conn.execute(
                "SELECT node_id, current_state FROM semantic_items WHERE node_id IN (?, ?, ?)",
                (
                    summary_import["work_note"]["node_id"],
                    product_backlog_import["work_note"]["node_id"],
                    provider_import["work_note"]["node_id"],
                ),
            ).fetchall()
        }
        conn.close()
        if set(classified_states.values()) != {"product_backlog"}:
            raise AssertionError(f"non-actionable handoff classifications were not non-active product_backlog items: {classified_states}")
        lifecycle_assumption = run(
            repo,
            "assumption",
            "add",
            "--body",
            "Lifecycle command assumption should leave and re-enter active context.",
            "--source-node",
            acceptance_section["node_id"],
            "--applies-to",
            task["node_id"],
        )
        invalidated = run(
            repo,
            "semantic",
            "set-state",
            "--node",
            lifecycle_assumption["node_id"],
            "--state",
            "invalidated",
            "--source-node",
            acceptance_section["node_id"],
            "--endpoint",
            "invariant",
        )
        context_after_invalidate = run(repo, "context", "load", "--task", "invalidated item stays out", "--endpoint", "invariant")
        if lifecycle_assumption["node_id"] in {item["id"] for item in context_after_invalidate["semantic_context"]}:
            raise AssertionError(f"invalidated lifecycle item remained active in context: {context_after_invalidate}")
        reopened = run(
            repo,
            "semantic",
            "set-state",
            "--node",
            lifecycle_assumption["node_id"],
            "--state",
            "reopened",
            "--source-node",
            acceptance_section["node_id"],
            "--endpoint",
            "invariant",
        )
        if invalidated["state"] != "invalidated" or reopened["state"] != "active":
            raise AssertionError(f"semantic set-state did not report invalidated/reopened states: {invalidated}, {reopened}")
        context_after_reopen = run(repo, "context", "load", "--task", "reopened item returns", "--endpoint", "invariant")
        if lifecycle_assumption["node_id"] not in {item["id"] for item in context_after_reopen["semantic_context"]}:
            raise AssertionError(f"reopened lifecycle item did not return to active context: {context_after_reopen}")
        context_after_resolve = run(repo, "context", "load", "--task", "resolved semantic nodes stay historical", "--endpoint", "invariant")
        active_context_ids = {item["id"] for item in context_after_resolve["semantic_context"]}
        if {resolved_unresolved["node_id"], audit_node_id} & active_context_ids:
            raise AssertionError(f"resolved semantic nodes still appear active in context load: {context_after_resolve}")
        report_after_resolve = run(repo, "report", "project")
        active_report_ids = {item["id"] for item in report_after_resolve["risks_and_notes"]}
        if {resolved_unresolved["node_id"], audit_node_id} & active_report_ids:
            raise AssertionError(f"resolved unresolved still appears active in project report: {report_after_resolve}")
        (repo / "old_audit.md").write_text("historical audit evidence\n", encoding="utf-8")
        historical_audit = run(
            repo,
            "audit",
            "record",
            "--endpoint",
            "invariant",
            "--source-node",
            acceptance_section["node_id"],
            "--path",
            "old_audit.md",
            "--finding",
            "Historical audit evidence should remain visible only in history mode.",
        )
        historical_ref = repo / historical_audit["artifact"]["capture_ref"]
        historical_ref.write_text("tampered historical audit evidence\n", encoding="utf-8")
        run(repo, "exec", "start", "--endpoint", "invariant", "--summary", "Invariant run", "--task-node", task["node_id"])
        (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        stop = run(
            repo,
            "exec",
            "stop",
            "--summary",
            "Invariant stop",
            "--task",
            task["task_id"],
            "--check",
            check_a["acceptance_check_id"],
            "--endpoint",
            "invariant",
        )
        if not stop["stop_check"]["must_not_claim_complete"] or stop["stop_check"]["open_acceptance_count"] < 2:
            raise AssertionError(f"stop did not report open checks: {stop['stop_check']}")
        changed_paths = {item["path_new"] or item["path_old"] for item in stop["change_set"]["files"]}
        if "app.py" not in changed_paths:
            raise AssertionError(f"snapshot delta did not capture changed file: {stop['change_set']}")
        active_verify = run(repo, "evidence", "verify", "--endpoint", "invariant")
        active_node_ids = {item["node_id"] for item in active_verify["checks"]}
        if not active_verify["ok"] or historical_audit["artifact_node_id"] in active_node_ids:
            raise AssertionError(f"default endpoint verify included historical tampered audit evidence: {active_verify}")
        history_verify = run_fails(repo, "evidence", "verify", "--endpoint", "invariant", "--include-history")
        history_payload = json.loads(history_verify.stdout)
        tampered_history_ids = {item["node_id"] for item in history_payload["buckets"]["tampered"]}
        if historical_audit["artifact_node_id"] not in tampered_history_ids:
            raise AssertionError(f"include-history did not surface historical tampered audit evidence: {history_payload}")

        if no_skill_postgres_started:
            stop_postgres_dev(no_skill_repo)
            no_skill_postgres_started = False
        if postgres_started:
            stop_postgres_dev(repo)
            postgres_started = False
        print(json.dumps({"ok": True, "init": init["project_id"], "migration": "001_marker.sql"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
