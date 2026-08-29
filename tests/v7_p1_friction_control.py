from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from helpers.postgres_fixture import clean_env, postgres_fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.services import evidence_policy

LONG_CHINESE = "他说：\"稳定输入不能靠密集 quoting\"；请保留中文、引号、冒号：以及多行。\n第二行包含 'single quotes' 和 \"double quotes\"。"


def run_no_db(repo: Path, *args: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_env(),
    )
    if completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    return json.loads(completed.stdout)


def assert_stable_file_input_preserves_long_chinese() -> list[str]:
    fixture_pair = postgres_fixture("shujuan-v7-p1-stable-input-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return []
    temp, fixture = fixture_pair
    with temp:
        repo = fixture.repo
        try:
            (repo / "plan.md").write_text("# P1 Input\n\n## Scope\n\nStable input.\n", encoding="utf-8")
            body_file = repo / "long-body.txt"
            body_file.write_text(LONG_CHINESE, encoding="utf-8")
            content_file = repo / "long-content.txt"
            content_file.write_text(LONG_CHINESE, encoding="utf-8")

            doc = fixture.run_json("doc", "import", "plan.md", "--source-type", "plan")
            source_node = doc["document_node_id"]
            scope = fixture.run_json("scope", "create", "--source-node", source_node, "--body-file", str(body_file))
            if LONG_CHINESE not in scope["node_id"] and not scope.get("ok"):
                raise AssertionError(f"scope create failed unexpectedly: {scope}")
            endpoint = fixture.run_json("endpoint", "create", "p1-input", "--root-node", scope["node_id"])
            if not endpoint.get("ok"):
                raise AssertionError(f"endpoint create failed: {endpoint}")

            workflow = fixture.run_json(
                "workflow",
                "begin",
                "--session-id",
                "p1-session",
                "--endpoint",
                "p1-input",
                "--content-file",
                str(content_file),
            )
            message_node = fixture.run_json("graph", "show", "--node", workflow["node_id"])
            message_blob = json.dumps(message_node, ensure_ascii=False)
            if "稳定输入不能靠密集 quoting" not in message_blob or "single quotes" not in message_blob:
                raise AssertionError(f"workflow begin did not preserve content-file text: {message_node}")

            packet = fixture.run_json("delegate", "packet", "--role", "worker", "--body-file", str(body_file))
            role_packet = ((packet.get("packet") or {}).get("role_packet") or {})
            if role_packet.get("body") != LONG_CHINESE:
                raise AssertionError(f"delegate packet did not preserve body-file text: {packet}")
            return fixture.writes
        finally:
            fixture.stop()


def assert_compact_verbose_json_report_modes() -> list[str]:
    fixture_pair = postgres_fixture("shujuan-v7-p1-output-")
    if fixture_pair is None:
        return []
    temp, fixture = fixture_pair
    with temp:
        repo = fixture.repo
        try:
            (repo / "plan.md").write_text("# P1 Output\n\n## Scope\n\nCompact output.\n", encoding="utf-8")
            doc = fixture.run_json("doc", "import", "plan.md", "--source-type", "plan")
            source_node = doc["document_node_id"]
            scope = fixture.run_json("scope", "create", "--source-node", source_node, "--body", "P1 output scope.")
            task = fixture.run_json("task", "add", "--from-node", source_node, "--contract", scope["contract_id"], "--body", "Open task.")
            fixture.run_json("acceptance", "add", "--from-node", source_node, "--task", task["task_id"], "--body", "Open check.")
            fixture.run_json("endpoint", "create", "p1-output", "--root-node", scope["node_id"])

            compact = fixture.run("report", "endpoint", "p1-output", "--active-only", "--compact").stdout
            verbose = fixture.run("report", "endpoint", "p1-output", "--active-only", "--verbose").stdout
            json_payload = fixture.run_json("report", "endpoint", "p1-output", "--active-only", "--json")
            if "# Endpoint Active Surface" not in compact or "Exact Next Commands" not in compact:
                raise AssertionError(f"compact report missing first-screen commands:\n{compact}")
            if "# Endpoint Active Report" not in verbose or "Active Obligations" not in verbose:
                raise AssertionError(f"verbose report did not render full markdown:\n{verbose}")
            if json_payload.get("output_mode") != "json" or "active_obligations" not in json_payload:
                raise AssertionError(f"json report mode was not parseable/distinct: {json_payload}")
            if len(compact) >= len(verbose):
                raise AssertionError("compact report was not smaller than verbose report")
            return fixture.writes
        finally:
            fixture.stop()


def assert_runtime_status_kind_fields() -> list[str]:
    fixture_pair = postgres_fixture("shujuan-v7-p1-runtime-")
    if fixture_pair is None:
        return []
    temp, fixture = fixture_pair
    with temp:
        try:
            pg_status = fixture.run_json("postgres-dev", "status")
            migrate_status = fixture.run_json("migrate", "status")
            for label, payload in [("postgres-dev status", pg_status), ("migrate status", migrate_status)]:
                for field in ("status_kind", "runtime_status_kind", "migration_status_kind", "writability_status_kind", "next_schema_check_command"):
                    if field not in payload:
                        raise AssertionError(f"{label} omitted {field}: {payload}")
            if migrate_status["status_kind"] != "postgres_runtime_schema_current":
                raise AssertionError(f"migrate status allowed false PostgreSQL success: {migrate_status}")
            return fixture.writes
        finally:
            fixture.stop()


def assert_delegate_ownership_surface_guidance() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v7-p1-delegate-") as temp:
        repo = Path(temp)
        payload = run_no_db(
            repo,
            "delegate",
            "packet",
            "--role",
            "worker",
            "--owned-hunk-or-path",
            "shujuan/commands/delegate_handlers.py",
        )
        role_packet = ((payload.get("packet") or {}).get("role_packet") or {})
        guidance = role_packet.get("ownership_surface_guidance") or {}
        requirements = role_packet.get("return_requirements") or {}
        if "delegate ownership" not in guidance.get("default_command", ""):
            raise AssertionError(f"delegate packet omitted ownership default command: {payload}")
        for field in ("provider_runtime_paths", "observed_only_paths", "not_owned_paths", "out_of_scope_paths"):
            if field not in requirements:
                raise AssertionError(f"delegate return requirements omitted ownership field {field}: {requirements}")
        if (repo / ".shujuan").exists():
            raise AssertionError("delegate packet without --save-artifact created current-project governance artifacts")


def assert_suite_manifest_and_reality_protocol_material_boundaries() -> None:
    suite = (ROOT / "docs" / "v7_p1_05_evidence_suite_manifest_schema_2026-05-22.md").read_text(encoding="utf-8")
    reality = (ROOT / "docs" / "v7_p1_06_reality_check_protocol_2026-05-22.md").read_text(encoding="utf-8")
    for phrase in ("manifest_is_closure_evidence=false", "Critical Entrypoint Mapping", "cannot satisfy a `test_result` check"):
        if phrase not in suite:
            raise AssertionError(f"suite manifest artifact omitted {phrase!r}")
    for phrase in ("3-agent smoke", "Ten-Agent Escalation Rule", "Deviation Classification Taxonomy", "do not close checks/tasks"):
        if phrase not in reality:
            raise AssertionError(f"reality-check protocol omitted {phrase!r}")
    if "suite_manifest" in evidence_policy.EVIDENCE_NODE_TYPES:
        raise AssertionError("suite manifest was introduced as a new evidence node type")
    if evidence_policy.expected_evidence_allowed("suite_manifest") & evidence_policy.EVIDENCE_NODE_TYPES:
        raise AssertionError("suite_manifest unexpectedly maps to accepted closure evidence")


def assert_docs_teach_stable_input() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for flag in ("--content-file", "--body-file"):
        if flag not in readme:
            raise AssertionError(f"README.md does not teach stable {flag} input")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    if "--content-file" not in agents:
        raise AssertionError("AGENTS.md does not teach stable prompt file input")
    skill = (ROOT / ".agents" / "skills" / "shujuan-core" / "SKILL.md").read_text(encoding="utf-8")
    if "--content-file" in skill or "--body-file" in skill:
        raise AssertionError("SKILL.md should route stable file-input details to canonical/reference surfaces")


def main() -> int:
    fixture_writes: list[str] = []
    fixture_writes.extend(assert_stable_file_input_preserves_long_chinese())
    fixture_writes.extend(assert_compact_verbose_json_report_modes())
    fixture_writes.extend(assert_runtime_status_kind_fields())
    assert_delegate_ownership_surface_guidance()
    assert_suite_manifest_and_reality_protocol_material_boundaries()
    assert_docs_teach_stable_input()
    print(json.dumps({"ok": True, "v7_p1_friction_control": "passed", "fixture_writes": sorted(set(fixture_writes))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
