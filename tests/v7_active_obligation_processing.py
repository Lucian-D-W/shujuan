from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from helpers.postgres_fixture import clean_env, postgres_fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LONG_CHINESE = "继续接手这个 endpoint，但不要记录；他说：\"稳定输入\"。\n第二行保留中文和 quotes."


def run_cli(repo: Path, *args: str, expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
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
    return completed


def run_json(repo: Path, *args: str, expect_ok: bool = True) -> dict:
    return json.loads(run_cli(repo, *args, expect_ok=expect_ok).stdout)


def assert_mode_suggest_recover_and_conflicts() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v7-active-mode-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        for intent in [
            "continue/take over/v7 p1",
            "resume work from a new window",
            "继续接手这个 endpoint",
            "do not record this",
            "不要记录这个请求",
        ]:
            payload = run_json(repo, "mode", "suggest", "--intent", intent)
            if payload["suggested_mode"] != "no_governance" or payload["contract"]["db_writes"]:
                raise AssertionError(f"recover/no-record intent suggested writable mode: {intent!r} -> {payload}")
        conflict = run_json(repo, "mode", "suggest", "--intent", "v7 p1", "--mode", "std", "--no-governance", expect_ok=False)
        error_codes = {item["code"] for item in conflict.get("errors") or []}
        if conflict["ok"] or conflict["usable"] or conflict["suggested_mode"] is not None:
            raise AssertionError(f"explicit mode/no-governance conflict stayed usable: {conflict}")
        if "mode_flag_conflict_explicit_mode_overrode_no_governance" not in error_codes:
            raise AssertionError(f"explicit mode conflict diagnostic missing: {conflict}")


def assert_hook_user_prompt_content_file() -> list[str]:
    fixture_pair = postgres_fixture("shujuan-v7-active-hook-")
    if fixture_pair is None:
        return []
    temp, fixture = fixture_pair
    with temp:
        try:
            content_file = fixture.repo / "prompt-中文.txt"
            content_file.write_text(LONG_CHINESE, encoding="utf-8")
            payload = fixture.run_json(
                "hook",
                "user-prompt",
                "--session-id",
                "hook-content-file",
                "--content-file",
                str(content_file),
            )
            node = fixture.run_json("graph", "show", "--node", payload["node_id"])
            if "稳定输入" not in json.dumps(node, ensure_ascii=False):
                raise AssertionError(f"hook user-prompt did not preserve content-file text: {node}")
            return fixture.writes
        finally:
            fixture.stop()


def assert_delegate_unusable_warning_surfaces() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v7-active-delegate-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        review = run_json(repo, "delegate", "review", "--result", "accept", "--summary", "ok", "--missing-predicate", "HP1")
        if review["usable"] or review["diagnostics"]["usable"] or review["review"]["safe_to_import_without_controller_review"]:
            raise AssertionError(f"delegate review overclaim remained usable: {review}")
        failed_review = run_json(
            repo,
            "delegate",
            "review",
            "--result",
            "accept",
            "--summary",
            "ok",
            "--claims-closeout",
            "--fail-on-overclaim",
            expect_ok=False,
        )
        if failed_review["usable"] or failed_review["diagnostics"]["usable"]:
            raise AssertionError(f"delegate review fail-on-overclaim kept usable=true: {failed_review}")
        ownership = run_json(
            repo,
            "delegate",
            "ownership",
            "--claimed-path",
            "assigned.py",
            "--after-snapshot-path",
            "unassigned.py",
        )
        if ownership["usable"] or ownership["diagnostics"]["usable"] or not ownership["warnings"]:
            raise AssertionError(f"delegate ownership warnings remained usable: {ownership}")


def assert_plan_to_db_stricter_validation() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v7-active-plan-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        base = {
            "id": "deliverable",
            "classification": "P1",
            "status": "active",
            "graph_destination": {"kind": "task", "id": "task_a"},
            "task_ids": ["task_a"],
            "check_ids": ["check_a"],
            "rationale": "Maps to explicit task/check rows.",
            "promotion_rule": "Already active.",
            "reopen_rule": "Reopen by restoring the explicit task/check pair.",
        }
        cases = {
            "bad_classification": ({**base, "classification": "urgent"}, "invalid_classification"),
            "active_task_without_ids": ({**base, "task_ids": [], "check_ids": []}, "active_destination_missing_task_or_check_ids"),
            "p1_broad_parent": ({**base, "graph_destination": {"kind": "umbrella", "id": "task_parent"}}, "unsafe_broad_parent_promotion"),
            "absorbed_without_explicit_target": (
                {
                    **base,
                    "status": "absorbed",
                    "graph_destination": {"kind": "task", "id": "task_a"},
                    "absorbed_by": None,
                },
                "missing_inactive_relation_field",
            ),
        }
        for name, (item, expected_code) in cases.items():
            path = repo / f"{name}.json"
            path.write_text(json.dumps({"source_items": [item]}), encoding="utf-8")
            payload = run_json(repo, "plan-to-db", "verify-artifact", "--artifact", str(path), "--allow-fail")
            codes = {violation["code"] for violation in payload["violations"]}
            if payload["ok"] or expected_code not in codes:
                raise AssertionError(f"plan-to-DB case {name} missed {expected_code}: {payload}")


def assert_migrate_status_surfaces_missing_applied_file() -> list[str]:
    fixture_pair = postgres_fixture("shujuan-v7-active-migrate-")
    if fixture_pair is None:
        return []
    temp, fixture = fixture_pair
    with temp:
        try:
            from shujuan.store import connect, new_id, now_iso

            conn = connect(fixture.repo)
            conn.execute(
                "INSERT INTO applied_migrations (id, filename, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (new_id("migration"), "999_missing_from_repo.sql", "sha256:missing", now_iso()),
            )
            conn.commit()
            status = fixture.run_json("migrate", "status")
            if status["ok"] or status["status_kind"] != "postgres_migration_drift":
                raise AssertionError(f"migrate status did not elevate missing applied file drift: {status}")
            if "999_missing_from_repo.sql" not in {item["filename"] for item in status.get("missing_files") or []}:
                raise AssertionError(f"migrate status did not list missing applied file: {status}")
            if "migrate apply" in status["next_migration_command"] and not status["next_migration_command"].startswith("resolve migration drift"):
                raise AssertionError(f"migrate status suggested apply despite drift: {status}")
            return fixture.writes
        finally:
            fixture.stop()


def assert_json_outputs_keep_chinese_readable() -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-v7-active-json-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        completed = run_cli(repo, "mode", "suggest", "--intent", "不要记录这个请求")
        if "\\u4e0d" in completed.stdout or "不要记录" not in completed.stdout:
            raise AssertionError(f"JSON output escaped Chinese unexpectedly:\n{completed.stdout}")


def main() -> int:
    fixture_writes: list[str] = []
    assert_mode_suggest_recover_and_conflicts()
    fixture_writes.extend(assert_hook_user_prompt_content_file())
    assert_delegate_unusable_warning_surfaces()
    assert_plan_to_db_stricter_validation()
    fixture_writes.extend(assert_migrate_status_surfaces_missing_applied_file())
    assert_json_outputs_keep_chinese_readable()
    print(json.dumps({"ok": True, "v7_active_obligation_processing": "passed", "fixture_writes": sorted(set(fixture_writes))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
