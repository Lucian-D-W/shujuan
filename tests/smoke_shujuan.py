from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


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


def run_with_env(repo: Path, env_updates: dict[str, str], *args: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
        env.pop(key, None)
    env.update(env_updates)
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


class RowAdapter:
    def __init__(self, row: Any) -> None:
        self.row = row

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(self.row, dict):
            if isinstance(key, int):
                return list(self.row.values())[key]
            return self.row[key]
        return self.row[key]

    def __eq__(self, other: object) -> bool:
        values = tuple(self.row.values()) if isinstance(self.row, dict) else tuple(self.row)
        if isinstance(other, (tuple, list)):
            return values == tuple(other)
        if isinstance(other, RowAdapter):
            return values == (tuple(other.row.values()) if isinstance(other.row, dict) else tuple(other.row))
        return False

    def __repr__(self) -> str:
        return repr(self.row)


class CursorAdapter:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def fetchone(self) -> RowAdapter | None:
        row = self.cursor.fetchone()
        return RowAdapter(row) if row is not None else None

    def fetchall(self) -> list[RowAdapter]:
        return [RowAdapter(row) for row in self.cursor.fetchall()]


class ConnectionAdapter:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def execute(self, sql: str, params: Any = None) -> CursorAdapter:
        return CursorAdapter(self.conn.execute(sql, params or ()))

    def close(self) -> None:
        self.conn.close()


def count(conn: ConnectionAdapter, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def change_paths(conn: ConnectionAdapter, change_set_id: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT COALESCE(path_new, path_old) FROM diff_files WHERE change_set_id = ?",
            (change_set_id,),
        ).fetchall()
    }


def connect_db(repo: Path) -> ConnectionAdapter:
    return ConnectionAdapter(connect(repo))


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-smoke-") as temp:
        repo = Path(temp)
        try:
            git(repo, "init")
            (repo / "app.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
            (repo / ".gitignore").write_text("ignored.txt\n.ai/codegraph/\n", encoding="utf-8")
            (repo / "plan.md").write_text(
                "# Smoke Plan\n\n"
                "Implement a runnable skeleton.\n\n"
                "## Constraint\n\n"
                "The agent must not close tasks without evidence.\n\n"
                "## Decision\n\n"
                "Use project-owned PostgreSQL as the canonical memory store.\n\n"
                "## Acceptance\n\n"
                "init, doc import, exec start, and exec stop must run.\n",
                encoding="utf-8",
            )
            (repo / "transcript.jsonl").write_text(
                '{"actor":"user","content":"Extract a smoke requirement."}\n'
                '{"actor":"assistant","content":"The smoke requirement is ready for manual extraction."}\n',
                encoding="utf-8",
            )
            git(repo, "add", ".gitignore", "app.py", "plan.md", "transcript.jsonl")
            git(repo, "-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid", "commit", "-m", "seed")

            init_result = run(repo, "init", "--name", "smoke", "--postgres-dev", "--postgres-dev-port", str(free_port()))
            postgres_started = True
            if init_result["database"]["backend"] != "postgres" or (repo / ".shujuan" / "shujuan.db").exists():
                raise AssertionError("init did not use project-owned PostgreSQL without SQLite fallback")
            postgres_status = run(repo, "postgres-dev", "status")
            if postgres_status["state"] != "ready" or not postgres_status["running"] or not postgres_status["ready"]:
                raise AssertionError(f"postgres-dev did not become ready: {postgres_status}")
            agents_text = (repo / "AGENTS.md").read_text(encoding="utf-8")
            if init_result["agents_md"]["action"] != "created" or "Capture applies only to source/provenance" not in agents_text:
                raise AssertionError("init did not create the current AGENTS.md capture discipline")
            if init_result["skill"]["action"] != "created" or not (repo / ".agents" / "skills" / "shujuan-core" / "SKILL.md").exists():
                raise AssertionError("init did not install the shujuan-core compatibility skill")
            doc_result = run(repo, "doc", "import", "plan.md", "--source-type", "plan")
            if len(doc_result["section_ids"]) < 3:
                raise AssertionError(f"document import did not return sliced section ids: {doc_result}")
            conn = connect_db(repo)
            acceptance_section = conn.execute(
                "SELECT id, node_id FROM document_sections WHERE heading = ? LIMIT 1",
                ("Acceptance",),
            ).fetchone()
            constraint_section = conn.execute(
                "SELECT id, node_id FROM document_sections WHERE heading = ? LIMIT 1",
                ("Constraint",),
            ).fetchone()
            decision_section = conn.execute(
                "SELECT id, node_id FROM document_sections WHERE heading = ? LIMIT 1",
                ("Decision",),
            ).fetchone()
            conn.close()
            if not acceptance_section or not constraint_section or not decision_section:
                raise AssertionError("expected acceptance/constraint/decision document sections were not imported")
            candidate_result = run(
                repo,
                "graph",
                "candidates",
                "--from-document",
                doc_result["document_id"],
                "--type",
                "acceptance_check",
            )
            if not candidate_result["candidates"]:
                raise AssertionError("document candidate extraction did not surface acceptance_check hints")
            section_extract_result = run(
                repo,
                "graph",
                "extract",
                "--from-section",
                acceptance_section[0],
                "--type",
                "constraint",
                "--label",
                "Document acceptance source",
                "--summary",
                "Manual semantic node from document acceptance section.",
            )
            constraint_extract_result = run(
                repo,
                "graph",
                "extract",
                "--from-section",
                constraint_section[0],
                "--type",
                "constraint",
                "--label",
                "Evidence-only closure constraint",
                "--summary",
                "Tasks must not close without evidence.",
            )
            decision_extract_result = run(
                repo,
                "graph",
                "extract",
                "--from-section",
                decision_section[0],
                "--type",
                "decision",
                "--label",
                "Repo-local SQLite decision",
                "--summary",
                "Repo-local SQLite is the canonical memory store.",
            )
            hook_result = run(
                repo,
                "hook",
                "user-prompt",
                "--session-id",
                "session_smoke",
                "--content",
                "User prompt captured by hook.",
            )
            duplicate_hook_result = run(
                repo,
                "hook",
                "user-prompt",
                "--session-id",
                "session_smoke",
                "--content",
                "User prompt captured by hook.",
            )
            stop_hook_result = run(
                repo,
                "hook",
                "stop",
                "--session-id",
                "session_smoke",
                "--content",
                "Agent final response captured by hook.",
            )
            import_result = run(repo, "session", "import", "--transcript", "transcript.jsonl")
            extract_result = run(
                repo,
                "graph",
                "extract",
                "--from-session",
                hook_result["session_id"],
                "--from-message",
                hook_result["message_id"],
                "--type",
                "requirement",
                "--label",
                "Hook requirement",
                "--summary",
                "Manual requirement node from hook transcript.",
            )
            graph_show_result = run(repo, "graph", "show", "--node", extract_result["node_id"])
            graph_edges_result = run(repo, "graph", "edges", "--from-node", extract_result["node_id"])
            center_result = run(
                repo,
                "center",
                "update",
                "--body",
                "Updated smoke center body.",
                "--from-node",
                extract_result["node_id"],
            )
            term_result = run(
                repo,
                "term",
                "define",
                "center",
                "--definition",
                "The active project identity and long-term boundary body.",
                "--scope-node",
                extract_result["node_id"],
                "--from-node",
                extract_result["node_id"],
            )
            export_center_result = run(repo, "export", "center")
            export_glossary_result = run(repo, "export", "glossary")
            scope_result = run(
                repo,
                "scope",
                "create",
                "--body",
                "Smoke contract keeps the CLI skeleton runnable.",
                "--source-node",
                doc_result["document_node_id"],
                "--non-downgrade-rules",
                "Do not skip hook/session/evidence smoke paths.",
            )
            extracted_task_result = run(
                repo,
                "graph",
                "extract",
                "--from-section",
                acceptance_section[0],
                "--type",
                "task",
                "--label",
                "Extracted task row",
                "--summary",
                "Graph extraction can create a real task row.",
                "--contract",
                scope_result["contract_id"],
            )
            extracted_check_result = run(
                repo,
                "graph",
                "extract",
                "--from-section",
                acceptance_section[0],
                "--type",
                "acceptance_check",
                "--label",
                "Extracted acceptance row",
                "--summary",
                "Graph extraction can create a real acceptance check row.",
                "--task",
                extracted_task_result["task_id"],
                "--expected-evidence-type",
                "user_confirmation",
            )
            task_result = run(
                repo,
                "task",
                "add",
                "--contract",
                scope_result["contract_id"],
                "--body",
                "Capture smoke implementation evidence.",
                "--from-node",
                acceptance_section[1],
            )
            check_result = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task_result["task_id"],
                "--body",
                "exec stop links the change set to this check.",
                "--expected-evidence-type",
                "diff",
                "--from-node",
                acceptance_section[1],
            )
            test_check_result = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task_result["task_id"],
                "--body",
                "test result evidence can close this check.",
                "--expected-evidence-type",
                "test",
                "--from-node",
                acceptance_section[1],
            )
            test_evidence_result = run(
                repo,
                "evidence",
                "test-result",
                "--check",
                test_check_result["acceptance_check_id"],
                "--close-check",
                "--from-node",
                acceptance_section[1],
                "--validates-node",
                section_extract_result["node_id"],
                "--",
                sys.executable,
                "-c",
                "print('test evidence ok')",
            )
            (repo / "smoke_artifact.txt").write_text("artifact evidence\n", encoding="utf-8")
            artifact_result = run(
                repo,
                "evidence",
                "artifact",
                "--path",
                "smoke_artifact.txt",
                "--from-node",
                acceptance_section[1],
                "--validates-node",
                extract_result["node_id"],
            )
            if not artifact_result["artifact"]["capture_ref"].startswith(".shujuan/artifacts/artifact_"):
                raise AssertionError(f"artifact evidence did not use a captured unique ref: {artifact_result}")
            unicode_artifact_check = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task_result["task_id"],
                "--body",
                "unicode artifact paths do not crash JSON output.",
                "--expected-evidence-type",
                "artifact",
                "--from-node",
                acceptance_section[1],
            )
            unicode_artifact_rel = Path("子目录 空格") / "資料-ß.txt"
            unicode_artifact_path = repo / unicode_artifact_rel
            unicode_artifact_path.parent.mkdir(parents=True, exist_ok=True)
            unicode_artifact_path.write_text("unicode artifact evidence\n", encoding="utf-8")
            unicode_artifact_result = run_with_env(
                repo,
                {"PYTHONIOENCODING": "gbk"},
                "evidence",
                "artifact",
                "--path",
                str(unicode_artifact_rel),
                "--check",
                unicode_artifact_check["acceptance_check_id"],
            )
            if unicode_artifact_result["artifact"]["original_path"] != str(unicode_artifact_rel).replace("\\", "/"):
                raise AssertionError(f"unicode artifact path was not preserved: {unicode_artifact_result}")
            confirmation_check_result = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task_result["task_id"],
                "--body",
                "user confirmation evidence can close this check.",
                "--expected-evidence-type",
                "user_confirmation",
                "--from-node",
                acceptance_section[1],
            )
            open_check_result = run(
                repo,
                "acceptance",
                "add",
                "--task",
                task_result["task_id"],
                "--body",
                "this acceptance remains open so exec stop must report it.",
                "--expected-evidence-type",
                "artifact",
                "--from-node",
                acceptance_section[1],
            )
            invalid_close = run_fails(
                repo,
                "acceptance",
                "close",
                "--check",
                open_check_result["acceptance_check_id"],
                "--evidence-node",
                extract_result["node_id"],
            )
            if "closing acceptance checks requires evidence node type" not in invalid_close.stderr:
                raise AssertionError(f"non-evidence close failed for the wrong reason: {invalid_close.stderr}")
            extracted_confirmation_result = run(
                repo,
                "evidence",
                "user-confirmation",
                "--body",
                "User confirmed the extracted task acceptance.",
                "--from-node",
                hook_result["node_id"],
                "--check",
                extracted_check_result["acceptance_check_id"],
                "--close-check",
                "--close-task",
            )
            confirmation_result = run(
                repo,
                "evidence",
                "user-confirmation",
                "--body",
                "User confirmed the smoke evidence path.",
                "--from-node",
                hook_result["node_id"],
                "--check",
                confirmation_check_result["acceptance_check_id"],
                "--close-check",
            )
            scope_change_result = run(
                repo,
                "scope",
                "change",
                "--body",
                "Defer nonessential smoke work with explicit source evidence.",
                "--source-node",
                acceptance_section[1],
                "--task",
                task_result["task_id"],
                "--state-changing",
                "--ack-defer-like",
            )
            task_defer_result = run(
                repo,
                "task",
                "defer",
                "--task",
                task_result["task_id"],
                "--body",
                "Second defer record for explicit task defer command.",
                "--source-node",
                acceptance_section[1],
            )
            assumption_result = run(
                repo,
                "assumption",
                "add",
                "--body",
                "Smoke assumes local Python is available.",
                "--source-node",
                acceptance_section[1],
                "--applies-to",
                task_result["node_id"],
            )
            unresolved_result = run(
                repo,
                "unresolved",
                "add",
                "--body",
                "Smoke keeps one unresolved question visible.",
                "--source-node",
                acceptance_section[1],
                "--applies-to",
                task_result["node_id"],
            )
            endpoint_result = run(
                repo,
                "endpoint",
                "create",
                "smoke",
                "--description",
                "Smoke endpoint workbench.",
                "--root-node",
                scope_result["node_id"],
            )
            endpoint_update_result = run(
                repo,
                "endpoint",
                "bind-root",
                "smoke",
                "--description",
                "Smoke endpoint workbench.",
                "--root-node",
                scope_result["node_id"],
            )
            endpoint_status_result = run(repo, "endpoint", "status", "smoke")
            if (
                endpoint_update_result["endpoint_id"] != endpoint_result["endpoint_id"]
                or endpoint_status_result["endpoint"]["description"] != "Smoke endpoint workbench."
                or endpoint_status_result["endpoint"]["root_node_id"] != scope_result["node_id"]
                or endpoint_status_result["scope_contract"]["id"] != scope_result["contract_id"]
            ):
                raise AssertionError(f"endpoint update/status failed to bind root workbench facts: {endpoint_status_result}")
            if task_result["task_id"] in {item["id"] for item in endpoint_status_result["current_tasks"]}:
                raise AssertionError(f"deferred task still appeared in current active endpoint tasks: {endpoint_status_result}")
            if task_result["task_id"] not in {item["id"] for item in endpoint_status_result["deferred_tasks"]}:
                raise AssertionError(f"deferred task was not retained in deferred endpoint facts: {endpoint_status_result}")
            (repo / "six_way_audit.md").write_text(
                "# Six-way audit summary\n\nP0: failed tests must not close checks.\n\nP1: endpoint needs DB-backed workbench facts.\n",
                encoding="utf-8",
            )
            audit_result = run(
                repo,
                "audit",
                "record",
                "--endpoint",
                "smoke",
                "--source-node",
                acceptance_section[1],
                "--path",
                "six_way_audit.md",
                "--task",
                task_result["task_id"],
                "--check",
                check_result["acceptance_check_id"],
                "--finding",
                "P0 failed tests must not close acceptance checks.",
                "--finding",
                "P1 endpoint workbench must be generated from DB facts.",
                "--refresh-endpoint",
            )
            if len(audit_result["audit_finding_node_ids"]) != 2 or not audit_result["endpoint_refresh"]:
                raise AssertionError(f"audit record did not persist structured findings and refresh endpoint: {audit_result}")
            if not audit_result["artifact"]["capture_ref"].startswith(".shujuan/artifacts/audit_"):
                raise AssertionError(f"audit artifact was not captured with a unique ref: {audit_result}")
            refreshed_status_result = run(repo, "endpoint", "status", "smoke")
            if len(refreshed_status_result["recent_audit_findings"]) < 2:
                raise AssertionError(f"endpoint status did not surface audit findings: {refreshed_status_result}")
            endpoint_refresh_result = run(repo, "endpoint", "refresh", "smoke")
            if "Recent audit findings:" not in endpoint_refresh_result["body"]:
                raise AssertionError(f"endpoint refresh did not render workbench body: {endpoint_refresh_result}")
            jot_result = run(
                repo,
                "jot",
                "handoff",
                "--endpoint",
                "smoke",
                "--body",
                "Resume from the smoke task and keep future ideas out of active obligations.",
                "--source-node",
                acceptance_section[1],
                "--applies-to",
                task_result["node_id"],
                "--refresh-endpoint",
            )
            if not jot_result["node_id"] or not jot_result["endpoint_refresh"]:
                raise AssertionError(f"jot handoff did not create and refresh: {jot_result}")
            agent_output_result = run(
                repo,
                "audit",
                "import-agent-output",
                "--endpoint",
                "smoke",
                "--source-node",
                acceptance_section[1],
                "--body",
                "Agent output imported as audit evidence.",
                "--finding",
                "Imported agent output is source-backed.",
                "--refresh-endpoint",
            )
            if not agent_output_result["artifact_node_id"] or not agent_output_result["audit_finding_node_ids"]:
                raise AssertionError(f"agent output import did not persist artifact/finding: {agent_output_result}")
            doctor_result = run(repo, "endpoint", "doctor", "smoke")
            if doctor_result["severity_buckets"]["P0"] or doctor_result["severity_buckets"]["P1"]:
                raise AssertionError(f"endpoint doctor reported hard issues after refresh: {doctor_result}")
            verify_result = run(repo, "evidence", "verify", "--endpoint", "smoke")
            if not verify_result["ok"] or verify_result["buckets"]["tampered"] or verify_result["buckets"]["missing_file"]:
                raise AssertionError(f"evidence verify did not pass for captured evidence: {verify_result}")
            report_result = subprocess.run(
                [sys.executable, "-m", "shujuan", "--repo", str(repo), "report", "project", "--markdown"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ.copy(), "PYTHONPATH": str(ROOT)},
            )
            if report_result.returncode or "shujuan Project Report" not in report_result.stdout or "Open Obligations" not in report_result.stdout:
                raise AssertionError(f"project report markdown failed:\n{report_result.stdout}\n{report_result.stderr}")
            open_obligations_section = report_result.stdout.split("## Open Obligations", 1)[1].split("## Evidence-Backed Closures", 1)[0]
            deferred_open_ids = {
                task_result["task_id"],
                check_result["acceptance_check_id"],
                open_check_result["acceptance_check_id"],
            }
            if any(item_id in open_obligations_section for item_id in deferred_open_ids):
                raise AssertionError(f"project report listed deferred obligations as open:\n{open_obligations_section}")
            project_report_json = run(repo, "report", "project")
            if task_result["task_id"] in {item["id"] for item in project_report_json["current_tasks"]}:
                raise AssertionError(f"project JSON listed deferred task as current: {project_report_json}")
            if task_result["task_id"] not in {item["id"] for item in project_report_json["deferred_tasks"]}:
                raise AssertionError(f"project JSON did not retain deferred task facts: {project_report_json}")
            if {
                check_result["acceptance_check_id"],
                open_check_result["acceptance_check_id"],
            } & {item["id"] for item in project_report_json["open_checks"]}:
                raise AssertionError(f"project JSON listed deferred checks as open: {project_report_json}")
            project_overview = run(repo, "report", "project", "--overview")
            if project_overview["active_task_count"] or project_overview["open_check_count"]:
                raise AssertionError(f"project overview counted deferred obligations as active: {project_overview}")
            context_result = run(repo, "context", "load", "--task", "Run smoke skeleton", "--endpoint", "smoke")
            start_result = run(repo, "exec", "start", "--endpoint", "smoke", "--summary", "Smoke run", "--task-node", task_result["node_id"])

            (repo / "app.py").write_text("def hello():\n    return 'hello, shujuan ß'\n", encoding="utf-8")
            (repo / "new_module.py").write_text("def new_value():\n    return 42\n", encoding="utf-8")
            (repo / "unicode_diff_資料-ß.txt").write_text("unicode diff content ß\n", encoding="utf-8")
            (repo / "ignored.txt").write_text("do not capture\n", encoding="utf-8")
            (repo / "__pycache__").mkdir(exist_ok=True)
            (repo / "__pycache__" / "ignored.py").write_text("do not capture\n", encoding="utf-8")
            (repo / ".ai" / "codegraph" / "reports").mkdir(parents=True, exist_ok=True)
            (repo / ".ai" / "codegraph" / "reports" / "ignored.json").write_text('{"provider":"cache"}\n', encoding="utf-8")
            stop_result = run_with_env(
                repo,
                {"PYTHONIOENCODING": "gbk"},
                "exec",
                "stop",
                "--summary",
                "Smoke diff capture",
                "--task",
                task_result["task_id"],
                "--check",
                check_result["acceptance_check_id"],
                "--close-check",
                "--endpoint",
                "smoke",
                "--endpoint-body",
                "Agent semantic closeout for smoke endpoint.",
            )
            why_result = run(repo, "why", "--path", "app.py")
            why_new_result = run(repo, "why", "--path", "new_module.py")
            why_symbol_result = run(repo, "why", "--symbol", "app.hello")
            rank_task_result = run(
                repo,
                "task",
                "add",
                "--body",
                "Review app.py hello and new_module new_value context ranking.",
                "--contract",
                scope_result["contract_id"],
                "--from-node",
                acceptance_section[1],
            )
            ranked_context_result = run(
                repo,
                "context",
                "load",
                "--task",
                "center smoke app.py new_module.py hello new_value context ranking",
                "--endpoint",
                "smoke",
            )

            db_ref = init_result["database"].get("url_redacted") or init_result["database"]["backend"]
            conn = connect_db(repo)
            required_counts = {
                "project_meta": 1,
                "source_documents": 1,
                "document_sections": 1,
                "agent_runs": 1,
                "run_snapshots": 2,
                "change_sets": 1,
                "diff_files": 2,
                "diff_hunks": 2,
                "code_objects": 4,
                "endpoint_bodies": 1,
                "activation_logs": 1,
                "conversation_sessions": 2,
                "messages": 4,
                "scope_contracts": 1,
                "tasks": 1,
                "acceptance_checks": 1,
                "terms": 1,
            }
            observed = {table: count(conn, table) for table in required_counts}
            for table, minimum in required_counts.items():
                if observed[table] < minimum:
                    raise AssertionError(f"{table} count {observed[table]} < {minimum}")
            captured_paths = {
                row[0]
                for row in conn.execute(
                    "SELECT COALESCE(path_new, path_old) FROM diff_files ORDER BY COALESCE(path_new, path_old)"
                ).fetchall()
            }
            code_paths = {row[0] for row in conn.execute("SELECT path FROM code_objects").fetchall()}
            symbols = {
                (row[0], row[1])
                for row in conn.execute(
                    "SELECT type, qualified_name FROM code_objects WHERE qualified_name IS NOT NULL"
                ).fetchall()
            }
            structured_task = conn.execute(
                "SELECT node_id, contract_id FROM tasks WHERE id = ?",
                (extracted_task_result["task_id"],),
            ).fetchone()
            structured_check = conn.execute(
                "SELECT node_id, task_id, expected_evidence_type FROM acceptance_checks WHERE id = ?",
                (extracted_check_result["acceptance_check_id"],),
            ).fetchone()
            if "app.py" not in captured_paths or "new_module.py" not in captured_paths:
                raise AssertionError(f"expected tracked and untracked paths in diff_files, got {captured_paths}")
            if "ignored.txt" in captured_paths or "__pycache__/ignored.py" in captured_paths or ".ai/codegraph/reports/ignored.json" in captured_paths:
                raise AssertionError(f"ignored paths were captured: {captured_paths}")
            if "new_module.py" not in code_paths:
                raise AssertionError(f"expected untracked new file in code_objects, got {code_paths}")
            if ".ai/codegraph/reports/ignored.json" in code_paths:
                raise AssertionError(f"provider cache path was captured in code_objects: {code_paths}")
            for expected_symbol in {("function", "app.hello"), ("function", "new_module.new_value")}:
                if expected_symbol not in symbols:
                    raise AssertionError(f"missing symbol code_object {expected_symbol}: {symbols}")
            if not structured_task or structured_task[0] != extracted_task_result["node_id"] or structured_task[1] != scope_result["contract_id"]:
                raise AssertionError("graph extract task did not create a real task row")
            if (
                not structured_check
                or structured_check[0] != extracted_check_result["node_id"]
                or structured_check[1] != extracted_task_result["task_id"]
                or structured_check[2] != "user_confirmation"
            ):
                raise AssertionError("graph extract acceptance_check did not create a real acceptance row")
            closed_task = conn.execute(
                "SELECT closed_by_node_id FROM tasks WHERE id = ?",
                (task_result["task_id"],),
            ).fetchone()[0]
            closed_extracted_task = conn.execute(
                "SELECT closed_by_node_id FROM tasks WHERE id = ?",
                (extracted_task_result["task_id"],),
            ).fetchone()[0]
            closed_check = conn.execute(
                "SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?",
                (check_result["acceptance_check_id"],),
            ).fetchone()[0]
            closed_test_check = conn.execute(
                "SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?",
                (test_check_result["acceptance_check_id"],),
            ).fetchone()[0]
            closed_confirmation_check = conn.execute(
                "SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?",
                (confirmation_check_result["acceptance_check_id"],),
            ).fetchone()[0]
            open_check = conn.execute(
                "SELECT closed_by_node_id FROM acceptance_checks WHERE id = ?",
                (open_check_result["acceptance_check_id"],),
            ).fetchone()[0]
            current_endpoint_body = conn.execute(
                """
                SELECT b.body
                FROM endpoints e
                JOIN endpoint_bodies b ON b.id = e.current_body_id
                WHERE e.name = ?
                """,
                ("smoke",),
            ).fetchone()[0]
            snapshot_refs = {
                row[0]: row[1]
                for row in conn.execute("SELECT id, patch_ref FROM run_snapshots").fetchall()
            }
            for snapshot_id, patch_ref in snapshot_refs.items():
                if snapshot_id not in patch_ref:
                    raise AssertionError(f"snapshot capture ref does not include snapshot id: {snapshot_refs}")
            if stop_result["change_set"]["change_set_id"] not in stop_result["change_set"]["patch_ref"]:
                raise AssertionError(f"change_set patch ref does not include change_set id: {stop_result['change_set']}")
            change_verify_result = run(repo, "evidence", "verify", "--node", stop_result["change_set"]["change_set_node_id"])
            if (
                not change_verify_result["ok"]
                or change_verify_result["buckets"]["tampered"]
                or change_verify_result["buckets"]["missing_file"]
            ):
                raise AssertionError(f"change_set evidence verify did not pass: {change_verify_result}")
            node_type_counts = {
                row[0]: row[1]
                for row in conn.execute(
                    """
                    SELECT type, COUNT(*)
                    FROM nodes
                    WHERE type IN ('test_result', 'artifact', 'user_confirmation', 'scope_change',
                                   'defer_decision', 'assumption', 'unresolved_question',
                                   'constraint', 'decision', 'audit_finding')
                    GROUP BY type
                    """
                ).fetchall()
            }
            deferred_edges = int(
                conn.execute(
                    "SELECT COUNT(*) FROM edges WHERE type = 'DEFERRED_BY' AND to_node_id IN (?, ?)",
                    (scope_change_result["node_id"], task_defer_result["node_id"]),
                ).fetchone()[0]
            )
            if closed_task is not None:
                raise AssertionError("task closed even though one acceptance check remains open")
            if closed_extracted_task != extracted_confirmation_result["node_id"]:
                raise AssertionError("task with all checks closed was not closed by evidence")
            if closed_check != stop_result["change_set"]["change_set_node_id"]:
                raise AssertionError("acceptance check was not closed by change set evidence")
            if closed_test_check != test_evidence_result["node_id"]:
                raise AssertionError("test result did not close its acceptance check")
            if closed_confirmation_check != confirmation_result["node_id"]:
                raise AssertionError("user confirmation did not close its acceptance check")
            if open_check is not None:
                raise AssertionError("open acceptance check was unexpectedly closed")
            if (
                stop_result["stop_check"]["open_acceptance_count"] < 1
                or stop_result["stop_check"]["mandatory_task_count"] < 1
                or not stop_result["stop_check"]["must_not_claim_complete"]
            ):
                raise AssertionError(f"exec stop did not report open acceptance checks: {stop_result['stop_check']}")
            if open_check_result["acceptance_check_id"] not in current_endpoint_body:
                raise AssertionError("endpoint closeout did not include open acceptance check report")
            if stop_result["endpoint_closeout"]["endpoint"] != "smoke":
                raise AssertionError("exec stop did not write endpoint closeout")
            for node_type in [
                "test_result",
                "artifact",
                "user_confirmation",
                "scope_change",
                "defer_decision",
                "assumption",
                "unresolved_question",
                "constraint",
                "decision",
                "audit_finding",
            ]:
                if node_type_counts.get(node_type, 0) < 1:
                    raise AssertionError(f"missing node type {node_type}: {node_type_counts}")
            if deferred_edges < 2:
                raise AssertionError("scope change/defer did not leave DEFERRED_BY task edges")
            conn.close()
            if duplicate_hook_result["message_id"] != hook_result["message_id"]:
                raise AssertionError("duplicate hook import did not reuse the existing message")
            if not why_result["found"]:
                raise AssertionError("why did not find captured code object")
            if not why_new_result["found"]:
                raise AssertionError("why did not find captured untracked code object")
            if not why_symbol_result["found"]:
                raise AssertionError("why --symbol did not find captured Python symbol")
            if not why_symbol_result["recent_change_links"]:
                raise AssertionError("why --symbol did not surface symbol change links")
            if not why_result["related_edges"]:
                raise AssertionError("why did not surface graph edges for the change set/run path")
            if stop_result["change_set"]["impact"]["status"] != "skipped":
                raise AssertionError("expected default provider skip in smoke repository")
            if stop_result["change_set"]["impact"].get("reason") != "explicit_opt_in_required":
                raise AssertionError(f"default provider skip did not explain opt-in boundary: {stop_result['change_set']['impact']}")
            if (
                stop_result["change_set"]["impact"].get("reports") != []
                or stop_result["change_set"]["impact"].get("index_path") != ".gitnexus"
                or (stop_result["change_set"]["impact"].get("provider_detail") or {}).get("name") != "gitnexus"
            ):
                raise AssertionError(f"provider fallback lost diagnostic shape: {stop_result['change_set']['impact']}")
            if not graph_show_result["outgoing"] or not graph_edges_result["edges"]:
                raise AssertionError("graph inspect did not show extracted evidence edge")
            if not context_result["semantic_context"]:
                raise AssertionError("context load did not activate assumption/unresolved/scope_change nodes")
            ranked_context = ranked_context_result["ranked_context"]
            ranked_blob = json.dumps(ranked_context, sort_keys=True)
            required_ranked = {
                "code_function": "app.hello",
                "code_file": "new_module.py",
                "term": "center",
                "task": rank_task_result["task_id"],
            }
            for kind, fragment in required_ranked.items():
                if not any(item["kind"] == kind and fragment in json.dumps(item, sort_keys=True) for item in ranked_context):
                    raise AssertionError(f"ranked_context missing {kind}/{fragment}: {ranked_blob}")
            conn = connect_db(repo)
            section_edges = int(
                conn.execute(
                    "SELECT COUNT(*) FROM edges WHERE type = 'DERIVED_FROM' AND to_node_id = ?",
                    (acceptance_section[1],),
                ).fetchone()[0]
            )
            conn.close()
            if section_edges < 3:
                raise AssertionError(f"document section evidence edges were not created: {section_edges}")
            if not (repo / ".shujuan" / "exports" / "center.md").exists():
                raise AssertionError("center export was not written")
            if not (repo / ".shujuan" / "exports" / "glossary.md").exists():
                raise AssertionError("glossary export was not written")

            git(repo, "add", "app.py", "new_module.py")
            git(repo, "-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid", "commit", "-m", "capture smoke changes")

            (repo / "pre_dirty.txt").write_text("clean\n", encoding="utf-8")
            git(repo, "add", "pre_dirty.txt")
            git(repo, "-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid", "commit", "-m", "add dirty baseline")
            (repo / "pre_dirty.txt").write_text("dirty before start\n", encoding="utf-8")
            run(
                repo,
                "exec",
                "start",
                "--endpoint",
                "smoke",
                "--summary",
                "Dirty baseline run",
                "--allow-preflight-warning",
                "--allow-reason",
                "dirty delta scenario has no scoped task",
            )
            (repo / "during_delta.txt").write_text("created after start\n", encoding="utf-8")
            dirty_stop = run(
                repo,
                "exec",
                "stop",
                "--summary",
                "Dirty delta capture",
                "--no-impact",
                "--endpoint",
                "smoke",
            )
            conn = connect_db(repo)
            dirty_paths = change_paths(conn, dirty_stop["change_set"]["change_set_id"])
            conn.close()
            if "during_delta.txt" not in dirty_paths or "pre_dirty.txt" in dirty_paths:
                raise AssertionError(f"dirty baseline polluted change_set: {dirty_paths}")
            git(repo, "add", "pre_dirty.txt", "during_delta.txt")
            git(repo, "-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid", "commit", "-m", "commit dirty scenario")

            (repo / "delete_me.txt").write_text("one\ntwo\n", encoding="utf-8")
            git(repo, "add", "delete_me.txt")
            git(repo, "-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid", "commit", "-m", "add delete target")
            run(
                repo,
                "exec",
                "start",
                "--endpoint",
                "smoke",
                "--summary",
                "Delete run",
                "--allow-preflight-warning",
                "--allow-reason",
                "delete delta scenario has no scoped task",
            )
            (repo / "delete_me.txt").unlink()
            delete_stop = run(
                repo,
                "exec",
                "stop",
                "--summary",
                "Delete delta capture",
                "--no-impact",
                "--endpoint",
                "smoke",
            )
            conn = connect_db(repo)
            deleted = conn.execute(
                """
                SELECT df.id, df.path_old, df.path_new, df.change_type, COUNT(dh.id)
                FROM diff_files df
                LEFT JOIN diff_hunks dh ON dh.diff_file_id = df.id
                WHERE df.change_set_id = ?
                GROUP BY df.id
                """,
                (delete_stop["change_set"]["change_set_id"],),
            ).fetchall()
            conn.close()
            if not any(row[1] == "delete_me.txt" and row[2] is None and row[3] == "deleted" and row[4] > 0 for row in deleted):
                raise AssertionError(f"deleted file hunk was not attributed to path_old: {deleted}")
            git(repo, "add", "-A", "delete_me.txt")
            git(repo, "-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid", "commit", "-m", "commit deletion")

            (repo / "rename_old.txt").write_text("same content\n", encoding="utf-8")
            git(repo, "add", "rename_old.txt")
            git(repo, "-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid", "commit", "-m", "add rename target")
            run(
                repo,
                "exec",
                "start",
                "--endpoint",
                "smoke",
                "--summary",
                "Rename run",
                "--allow-preflight-warning",
                "--allow-reason",
                "rename delta scenario has no scoped task",
            )
            (repo / "rename_old.txt").rename(repo / "rename_new.txt")
            rename_stop = run(
                repo,
                "exec",
                "stop",
                "--summary",
                "Rename delta capture",
                "--no-impact",
                "--endpoint",
                "smoke",
            )
            conn = connect_db(repo)
            renamed = conn.execute(
                "SELECT path_old, path_new, change_type FROM diff_files WHERE change_set_id = ?",
                (rename_stop["change_set"]["change_set_id"],),
            ).fetchall()
            conn.close()
            if ("rename_old.txt", "rename_new.txt", "renamed") not in renamed:
                raise AssertionError(f"rename was not recorded as renamed: {renamed}")
            git(repo, "add", "-A", "rename_old.txt", "rename_new.txt")
            git(repo, "-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid", "commit", "-m", "commit rename")

            run(
                repo,
                "exec",
                "start",
                "--endpoint",
                "smoke",
                "--summary",
                "Binary run",
                "--allow-preflight-warning",
                "--allow-reason",
                "binary delta scenario has no scoped task",
            )
            (repo / "binary.bin").write_bytes(b"\x00\x01binary evidence\xff")
            (repo / "non_utf8.txt").write_bytes(b"\xff\xfe\xfd")
            (repo / "scratch.tmp").write_text("temporary work file\n", encoding="utf-8")
            binary_stop = run(
                repo,
                "exec",
                "stop",
                "--summary",
                "Binary delta capture",
                "--no-impact",
                "--endpoint",
                "smoke",
            )
            conn = connect_db(repo)
            binary_row = conn.execute(
                "SELECT id FROM diff_files WHERE change_set_id = ? AND path_new = ?",
                (binary_stop["change_set"]["change_set_id"], "binary.bin"),
            ).fetchone()
            binary_hunks = 0 if not binary_row else int(
                conn.execute("SELECT COUNT(*) FROM diff_hunks WHERE diff_file_id = ?", (binary_row[0],)).fetchone()[0]
            )
            metadata = json.loads(
                conn.execute("SELECT metadata FROM change_sets WHERE id = ?", (binary_stop["change_set"]["change_set_id"],)).fetchone()[0]
            )
            conn.close()
            binary_evidence = [item for item in metadata["file_evidence"] if item["path_new"] == "binary.bin"]
            non_utf8_evidence = [item for item in metadata["file_evidence"] if item["path_new"] == "non_utf8.txt"]
            temp_evidence = [item for item in metadata["file_evidence"] if item["path_new"] == "scratch.tmp"]
            if not binary_evidence or not binary_evidence[0]["is_binary"] or not binary_evidence[0]["file_hash_after"]:
                raise AssertionError(f"binary evidence missing from metadata: {metadata}")
            if not non_utf8_evidence or non_utf8_evidence[0]["skipped_text_reason"] != "non_utf8" or not non_utf8_evidence[0]["file_hash_after"]:
                raise AssertionError(f"non-UTF8 evidence missing classification/hash: {metadata}")
            if not temp_evidence or temp_evidence[0]["skipped_text_reason"] != "temporary_path" or not temp_evidence[0]["file_hash_after"]:
                raise AssertionError(f"temporary file evidence missing classification/hash: {metadata}")
            if binary_hunks != 0:
                raise AssertionError("binary file should not create text hunks")
            if binary_stop["change_set"]["patch_hash"] == metadata["text_patch_hash"]:
                raise AssertionError("binary-only change did not affect package fingerprint beyond text patch hash")

            print(
                json.dumps(
                    {
                        "ok": True,
                        "db": db_ref,
                        "init": init_result["project_id"],
                        "document": doc_result["document_id"],
                        "document_candidate": candidate_result["candidates"][0]["candidate_id"],
                        "section_extracted_node": section_extract_result["node_id"],
                        "hook_session": hook_result["session_id"],
                        "stop_hook_message": stop_hook_result["message"]["message_id"],
                        "import_session": import_result["session_id"],
                        "extracted_node": extract_result["node_id"],
                        "center": center_result["center_body_id"],
                        "term": term_result["term_id"],
                        "export_center": export_center_result["path"],
                        "export_glossary": export_glossary_result["path"],
                        "scope": scope_result["contract_id"],
                        "task": task_result["task_id"],
                        "check": check_result["acceptance_check_id"],
                        "endpoint": endpoint_result["endpoint_id"],
                        "activation": context_result["activation_log_id"],
                        "run": start_result["run_id"],
                        "change_set": stop_result["change_set"]["change_set_id"],
                        "counts": observed,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        finally:
            if postgres_started:
                run(repo, "postgres-dev", "stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
