from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.store import postgres_dev_database_url
from tests.helpers.postgres_fixture import clean_env, postgres_fixture


def doctor_codes(payload: dict) -> set[str]:
    return {item["code"] for bucket in payload["severity_buckets"].values() for item in bucket}


def bucket_statuses(payload: dict, status: str) -> list[dict]:
    return payload.get("buckets", {}).get(status) or []


def setup_endpoint(fixture, endpoint: str) -> tuple[str, str, str]:
    repo = fixture.repo
    (repo / f"{endpoint}.md").write_text(f"# {endpoint}\n\nV7 lightweight fixture.\n", encoding="utf-8")
    doc = fixture.run_json("doc", "import", f"{endpoint}.md", "--source-type", "plan")
    source_node = str(doc["document_node_id"])
    scope = fixture.run_json("scope", "create", "--body", f"{endpoint} scope.", "--source-node", source_node)
    task = fixture.run_json("task", "add", "--contract", str(scope["contract_id"]), "--body", f"{endpoint} task.", "--from-node", source_node)
    fixture.run_json("endpoint", "create", endpoint, "--description", f"{endpoint} endpoint.", "--root-node", str(scope["node_id"]))
    return source_node, str(task["task_id"]), str(scope["node_id"])


def add_check(fixture, task_id: str, source_node: str, body: str, expected: str = "test_result") -> str:
    check = fixture.run_json(
        "acceptance",
        "add",
        "--task",
        task_id,
        "--body",
        body,
        "--expected-evidence-type",
        expected,
        "--from-node",
        source_node,
    )
    return str(check["acceptance_check_id"])


def assert_read_only_recovery_does_not_touch_schema_version(fixture) -> None:
    source_node, task_id, _ = setup_endpoint(fixture, "v7-read-only")
    add_check(fixture, task_id, source_node, "Read-only diagnostic check.")
    schema_version = fixture.repo / ".shujuan" / "schema_version.json"
    sentinel = '{"schema_version":"sentinel","updated_at":"sentinel"}\n'
    schema_version.write_text(sentinel, encoding="utf-8")

    fixture.run("report", "project", "--markdown")
    fixture.run("report", "endpoint", "v7-read-only", "--active-only", "--markdown")
    doctor = fixture.run_json("endpoint", "doctor", "v7-read-only", "--strict-closeout", "--read-only", "--allow-fail")
    if doctor.get("refresh_policy") != "suppressed_by_read_only" or doctor.get("endpoint_refresh"):
        raise AssertionError(f"read-only doctor refreshed projection: {doctor}")
    if schema_version.read_text(encoding="utf-8") != sentinel:
        raise AssertionError("read-only recovery command rewrote .shujuan/schema_version.json")

    db_url = postgres_dev_database_url(fixture.repo)
    if not db_url:
        raise AssertionError("fixture postgres-dev URL was unavailable")
    with tempfile.TemporaryDirectory(prefix="shujuan-v7-missing-layout-", ignore_cleanup_errors=True) as missing_layout:
        missing_repo = Path(missing_layout)
        failed = subprocess.run(
            [
                sys.executable,
                "-m",
                "shujuan",
                "--repo",
                str(missing_repo),
                "report",
                "endpoint",
                "v7-read-only",
                "--active-only",
                "--markdown",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_env({"SHUJUAN_DATABASE_URL": db_url}),
        )
        if failed.returncode == 0:
            raise AssertionError(f"missing-layout command unexpectedly passed: {failed.stdout}")
        if "read-only shujuan diagnostics cannot create missing .shujuan layout metadata" not in failed.stderr:
            raise AssertionError(f"missing-layout diagnostic was not explicit: {failed.stderr}")
        if (missing_repo / ".shujuan").exists():
            raise AssertionError("read-only missing-layout diagnostic recreated .shujuan")


def assert_evidence_verify_layer_and_post_invalidation(fixture) -> None:
    source_node, task_id, _ = setup_endpoint(fixture, "v7-verify")
    check_id = add_check(fixture, task_id, source_node, "Tampered artifact check.", "artifact")
    artifact = fixture.repo / "artifact.md"
    artifact.write_text("trusted\n", encoding="utf-8")
    evidence = fixture.run_json("evidence", "artifact", "--path", "artifact.md", "--check", check_id, "--close-check", "--from-node", source_node)
    (fixture.repo / str(evidence["artifact"]["capture_ref"])).write_text("tampered\n", encoding="utf-8")

    verify = fixture.run_json("evidence", "verify", "--endpoint", "v7-verify", "--allow-fail")
    if verify.get("ok") is not False or verify.get("layer") != "evidence" or verify.get("closeout_gate") is not False:
        raise AssertionError(f"evidence verify did not expose evidence-layer failure semantics: {verify}")
    if "endpoint doctor v7-verify --strict-closeout" not in str(verify.get("next_strict_doctor_command")):
        raise AssertionError(f"evidence verify did not expose next strict doctor command: {verify}")
    if not verify.get("post_invalidation_recomputed") or not verify.get("auto_invalidated_evidence"):
        raise AssertionError(f"evidence verify did not report post-invalidation recompute: {verify}")
    if not bucket_statuses(verify, "inactive_evidence"):
        raise AssertionError(f"post-invalidation top-level result did not include inactive evidence: {verify}")


def assert_resolved_override_reason_gate(fixture) -> None:
    source_node, task_id, _ = setup_endpoint(fixture, "v7-type-override")
    unclear_check = add_check(fixture, task_id, source_node, "Unclear resolved override check.", "artifact")
    unclear = fixture.run_json(
        "evidence",
        "user-confirmation",
        "--body",
        "confirmed",
        "--check",
        unclear_check,
        "--close-check",
        "--override-evidence-type",
        "--override-reason",
        "Temporary exception.",
    )
    unclear_warning = str(unclear["check_links"]["warnings"][0])
    fixture.run_json(
        "semantic",
        "set-state",
        "--node",
        unclear_warning,
        "--state",
        "resolved",
        "--source-node",
        source_node,
        "--reason",
        "Reviewed; unclear whether risk remains accepted.",
    )
    verify_unclear = fixture.run_json("evidence", "verify", "--endpoint", "v7-type-override", "--allow-fail")
    if not bucket_statuses(verify_unclear, "evidence_type_mismatch"):
        raise AssertionError(f"unclear resolved override still authorized green verify: {verify_unclear}")
    doctor_unclear = fixture.run_json("endpoint", "doctor", "v7-type-override", "--strict-closeout", "--read-only", "--allow-fail")
    if "evidence_type_mismatch" not in doctor_codes(doctor_unclear):
        raise AssertionError(f"unclear resolved override still authorized green doctor: {doctor_unclear}")

    source_node, task_id, _ = setup_endpoint(fixture, "v7-type-override-clear")
    clear_check = add_check(fixture, task_id, source_node, "Clear resolved override check.", "artifact")
    clear = fixture.run_json(
        "evidence",
        "user-confirmation",
        "--body",
        "confirmed",
        "--check",
        clear_check,
        "--close-check",
        "--override-evidence-type",
        "--override-reason",
        "Controller accepts evidence type mismatch risk within endpoint scope for this acceptance check.",
    )
    clear_warning = str(clear["check_links"]["warnings"][0])
    fixture.run_json(
        "semantic",
        "set-state",
        "--node",
        clear_warning,
        "--state",
        "resolved",
        "--source-node",
        source_node,
        "--reason",
        "Controller accepted evidence type override risk within endpoint scope for this acceptance check.",
    )
    verify_clear = fixture.run_json("evidence", "verify", "--endpoint", "v7-type-override-clear", "--allow-fail")
    if bucket_statuses(verify_clear, "evidence_type_mismatch"):
        raise AssertionError(f"clear resolved override did not authorize verify: {verify_clear}")
    doctor_clear = fixture.run_json("endpoint", "doctor", "v7-type-override-clear", "--strict-closeout", "--read-only", "--allow-fail")
    if "evidence_type_mismatch" in doctor_codes(doctor_clear):
        raise AssertionError(f"clear resolved override did not authorize doctor: {doctor_clear}")


def assert_predicate_override_shared_by_verify_and_doctor(fixture) -> None:
    source_node, task_id, _ = setup_endpoint(fixture, "v7-predicate-override")
    check_a = add_check(fixture, task_id, source_node, "Predicate override check A.")
    check_b = add_check(fixture, task_id, source_node, "Predicate override check B.")
    fixture.run_json(
        "work",
        "intake",
        "--endpoint",
        "v7-predicate-override",
        "--source-node",
        source_node,
        "--source-locator",
        "v7-predicate-override.md",
        "--text",
        "Predicate coverage must remain explicit.",
        "--predicate",
        "HP-V7-A::A must be covered.",
        "--predicate",
        "HP-V7-B::B must be covered.",
    )
    fixture.run_json(
        "work",
        "split",
        "--endpoint",
        "v7-predicate-override",
        "--name",
        "predicate override links",
        "--link",
        f"{task_id}::{check_a}::HP-V7-A",
        "--link",
        f"{task_id}::{check_b}::HP-V7-B",
    )
    evidence = fixture.run_json(
        "evidence",
        "test-result",
        "--check",
        check_a,
        "--check",
        check_b,
        "--close-check",
        "--override-predicate-coverage",
        "--override-reason",
        "Controller accepts predicate coverage override risk within endpoint scope for these acceptance checks.",
        "--from-node",
        source_node,
        "--",
        sys.executable,
        "-c",
        "print('ok')",
    )
    warning = str(evidence["check_links"]["warnings"][0])
    fixture.run_json(
        "semantic",
        "set-state",
        "--node",
        warning,
        "--state",
        "resolved",
        "--source-node",
        source_node,
        "--reason",
        "Controller accepted predicate coverage override risk within endpoint scope for these acceptance checks.",
    )
    verify = fixture.run_json("evidence", "verify", "--endpoint", "v7-predicate-override", "--allow-fail")
    if bucket_statuses(verify, "predicate_coverage_missing"):
        raise AssertionError(f"clear resolved predicate override did not authorize verify: {verify}")
    doctor = fixture.run_json("endpoint", "doctor", "v7-predicate-override", "--strict-closeout", "--read-only", "--allow-fail")
    if "closed_check_missing_predicate_coverage" in doctor_codes(doctor):
        raise AssertionError(f"clear resolved predicate override did not authorize doctor: {doctor}")


def main() -> int:
    fixture_pair = postgres_fixture("shujuan-v7-lightweight-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    temp, fixture = fixture_pair
    try:
        assert_read_only_recovery_does_not_touch_schema_version(fixture)
        assert_evidence_verify_layer_and_post_invalidation(fixture)
        assert_resolved_override_reason_gate(fixture)
        assert_predicate_override_shared_by_verify_and_doctor(fixture)
    finally:
        fixture.stop()
        temp.cleanup()
    print(json.dumps({"ok": True, "fixture_writes": fixture.writes}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
