from __future__ import annotations

import json
import os
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.schema_roles import (  # noqa: E402
    ADMISSION_GATES,
    SCHEMA_FREEZE_POLICY,
    advanced_schema_visibility,
    schema_role_rows,
    schema_visibility_policy,
    verify_schema_roles,
)
from tests.helpers.postgres_fixture import clean_env, postgres_fixture  # noqa: E402


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


def connect_fixture_db(fixture):
    import psycopg
    from psycopg.rows import dict_row

    url = fixture.run_json("postgres-dev", "url")["database_url"]
    return psycopg.connect(url, row_factory=dict_row)


def assert_static_role_policy() -> None:
    rows = schema_role_rows()
    tables = {row["table"] for row in rows}
    if len(rows) != 47 or len(tables) != 47:
        raise AssertionError(f"schema role registry must cover exactly 47 role rows: {len(rows)} rows / {len(tables)} tables")
    if "schema_roles" in tables:
        raise AssertionError("schema_roles must not become a DB table or schema role row")
    if len(ADMISSION_GATES) != 8:
        raise AssertionError(f"new-table admission gates drifted: {ADMISSION_GATES}")
    for gate in [
        "independent_factuality",
        "independent_lifecycle",
        "stable_write_path",
        "stable_read_path",
        "insufficient_existing_alternatives",
        "no_added_default_cognitive_burden",
        "trusted_migrations_no_drift",
        "tests_for_write_read_empty_table_migration_default_hidden",
    ]:
        if gate not in ADMISSION_GATES:
            raise AssertionError(f"missing new-table admission gate: {gate}")
    if SCHEMA_FREEZE_POLICY["business_table_additions_allowed"] is not False:
        raise AssertionError(f"schema freeze policy permits business table additions: {SCHEMA_FREEZE_POLICY}")
    verification = verify_schema_roles()
    if not verification["ok"]:
        raise AssertionError(f"schema roles do not match schema.py: {verification}")
    if verification["physical_schema_table_count"] != 38:
        raise AssertionError(f"physical schema count drifted: {verification}")
    if verification["role_registry_count"] != 47 or verification["contracted_legacy_role_count"] != 9:
        raise AssertionError(f"role/contracted counts are unclear: {verification}")
    role_by_table = {row["table"]: row for row in rows}
    for table in ("interaction_events", "discussion_segments", "discussion_messages", "discussion_lifecycle_events"):
        if role_by_table[table]["role"] != "capture_support_fact":
            raise AssertionError(f"{table} should be capture support, not dormant: {role_by_table[table]}")
    for table in ("delegation_lanes", "delegation_packets", "worker_ownership_snapshots"):
        if role_by_table[table]["role"] != "dormant_extension":
            raise AssertionError(f"{table} should remain dormant extension: {role_by_table[table]}")
    advanced = advanced_schema_visibility()
    if not advanced:
        raise AssertionError("advanced schema visibility is empty")
    for item in advanced:
        if item["role"] not in {"dormant_extension", "merge_candidate", "contracted_table"}:
            raise AssertionError(f"advanced surface leaked a default role: {item}")
        if item["has_default_write_path"] or item["has_current_product_surface"]:
            raise AssertionError(f"dormant/merge_candidate role became default-active: {item}")
        for key in ("has_rows", "has_default_write_path", "has_current_product_surface", "replacement_path"):
            if key not in item:
                raise AssertionError(f"advanced schema item omitted {key}: {item}")
    visibility = schema_visibility_policy()
    if visibility["default_surface"] != "governance_objects":
        raise AssertionError(f"default surface is not governance-object first: {visibility}")
    if "non_goal" not in visibility["non_goal_visibility_rule"] or "product_backlog" not in visibility["non_goal_visibility_rule"]:
        raise AssertionError(f"non-goal/product_backlog distinction missing: {visibility}")


def assert_live_guard_and_advanced_surface() -> None:
    fixture_pair = postgres_fixture("schema-stewardship-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return
    temp, fixture = fixture_pair
    try:
        roles = fixture.run_json("schema", "roles", "--live", "--advanced")
        if not roles["verification"]["ok"] or roles["schema_roles_db_table"] is not False:
            raise AssertionError(f"live schema role verification failed: {roles}")
        if roles["verification"]["contracted_tables_present"]:
            raise AssertionError(f"contracted tables should be absent from a migrated live schema: {roles['verification']}")
        advanced = roles["visibility_policy"]["advanced_schema_visibility"]
        if not advanced or not all("replacement_path" in item for item in advanced):
            raise AssertionError(f"advanced schema visibility lacks labels: {advanced}")
        default_roles = fixture.run_json("schema", "roles", "--live")
        if default_roles["advanced_material_omitted"] is not True:
            raise AssertionError(f"default schema roles did not omit advanced material: {default_roles}")
        if any(row["role"] in {"dormant_extension", "contracted_table"} for row in default_roles["roles"]):
            raise AssertionError(f"default schema roles exposed dormant/contracted rows: {default_roles}")
        if "advanced_schema_visibility" in default_roles["visibility_policy"]:
            raise AssertionError(f"default schema roles exposed advanced visibility details: {default_roles}")
        verify = fixture.run_json("schema", "verify", "--live")
        if verify["physical_schema_table_count"] != 38 or verify["role_registry_count"] != 47 or verify["contracted_legacy_role_count"] != 9:
            raise AssertionError(f"schema verify count labels are unclear: {verify}")
        guard = fixture.run_json("schema", "guard", "--live")
        if guard["schema_guard_passed"] is not True or guard["schema_integrity_ok"] is not True:
            raise AssertionError(f"clean fixture schema guard did not pass: {guard}")
        if guard["ordinary_schema_change_allowed"] is not False or guard["business_table_addition_allowed"] is not False:
            raise AssertionError(f"schema guard implied ordinary business schema changes are allowed: {guard}")

        conn = connect_fixture_db(fixture)
        try:
            conn.execute("CREATE TABLE ordinary_business_table_without_role (id TEXT PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()
        failed = fixture.run("schema", "verify", "--live", expect_ok=False).json()
        if "ordinary_business_table_without_role" not in failed["verification"]["missing_from_roles"]:
            raise AssertionError(f"live verifier did not catch an unruled business table: {failed}")
        failed_guard = fixture.run("schema", "guard", "--live", expect_ok=False).json()
        if failed_guard["ordinary_schema_change_allowed"] is not False or "schema_roles_verification_failed" not in failed_guard["blockers"]:
            raise AssertionError(f"schema guard did not block extra table: {failed_guard}")
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


def assert_contraction_migration_preflight() -> None:
    fixture_pair = postgres_fixture("schema-contraction-preflight-")
    if fixture_pair is None:
        return
    temp, fixture = fixture_pair
    try:
        shutil.copytree(ROOT / "migrations", fixture.repo / "migrations", dirs_exist_ok=True)
        conn = connect_fixture_db(fixture)
        try:
            conn.execute("DELETE FROM applied_migrations WHERE filename = %s", ("004_p2_physical_schema_contraction.sql",))
            conn.commit()
        finally:
            conn.close()
        dry_run = fixture.run_json("migrate", "apply", "--dry-run")
        preflight = dry_run["contraction_preflights"][0]
        if preflight["migration"] != "004_p2_physical_schema_contraction.sql" or preflight["ok"] is not True:
            raise AssertionError(f"empty contraction candidates did not allow dry-run: {dry_run}")

        conn = connect_fixture_db(fixture)
        try:
            conn.execute("DELETE FROM applied_migrations WHERE filename = %s", ("004_p2_physical_schema_contraction.sql",))
            conn.execute("CREATE TABLE hard_predicates (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO hard_predicates (id) VALUES (%s)", ("HP-NONEMPTY",))
            conn.commit()
        finally:
            conn.close()
        blocked = fixture.run("migrate", "apply", "--dry-run", expect_ok=False).json()
        if blocked["ok"] is not False or blocked["blocked_by"] != "non_empty_contraction_candidate_tables":
            raise AssertionError(f"non-empty contraction candidate was not blocked: {blocked}")
        if blocked["tables"] != [
            {
                "table": "hard_predicates",
                "row_count": 1,
                "replacement_path": "single-intent acceptance_checks + acceptance_checks.expected_evidence_type",
            }
        ]:
            raise AssertionError(f"blocked contraction table details are incomplete: {blocked}")
        if "normal migrate apply will not drop non-empty contracted tables" not in blocked["next_action"]:
            raise AssertionError(f"blocked contraction next action is unclear: {blocked}")
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


def assert_ledger_repair_path() -> None:
    fixture_pair = postgres_fixture("schema-ledger-repair-")
    if fixture_pair is None:
        return
    temp, fixture = fixture_pair
    try:
        shutil.copytree(ROOT / "migrations", fixture.repo / "migrations", dirs_exist_ok=True)
        conn = connect_fixture_db(fixture)
        try:
            migration_rows = []
            for path in sorted((fixture.repo / "migrations" / "shujuan").glob("*.sql")):
                checksum = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                if path.name == "003_v5_runtime_schema_ownership.sql":
                    checksum = "bad-checksum-for-p0-test"
                migration_rows.append((f"migration_{path.stem}", path.name, checksum, "2026-05-26T00:00:00+00:00"))
            for row in migration_rows:
                conn.execute(
                    """
                    INSERT INTO applied_migrations (id, filename, checksum, applied_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (filename) DO UPDATE SET checksum = EXCLUDED.checksum
                    """,
                    row,
                )
            conn.execute(
                "UPDATE applied_migrations SET checksum = %s WHERE filename = %s",
                ("bad-checksum-for-p0-test", "003_v5_runtime_schema_ownership.sql"),
            )
            conn.commit()
        finally:
            conn.close()
        drifted = fixture.run_json("migrate", "status")
        if not (drifted.get("migration_drift") or {}).get("present"):
            raise AssertionError(f"fixture did not enter drift state: {drifted}")
        dry_run = fixture.run_json(
            "migrate",
            "repair-ledger",
            "--filename",
            "003_v5_runtime_schema_ownership.sql",
            "--reason",
            "isolated P0 ledger repair regression",
        )
        if dry_run["repair_scope"] != "migration_ledger_only" or dry_run["physical_schema_changes"] or dry_run["drop_archive_shrink"]:
            raise AssertionError(f"repair dry-run exceeded ledger-only scope: {dry_run}")
        applied = fixture.run_json(
            "migrate",
            "repair-ledger",
            "--filename",
            "003_v5_runtime_schema_ownership.sql",
            "--reason",
            "isolated P0 ledger repair regression",
            "--apply",
        )
        if not applied["applied"] or applied["drop_archive_shrink"]:
            raise AssertionError(f"ledger repair did not apply cleanly: {applied}")
        repaired = fixture.run_json("migrate", "status")
        if (repaired.get("migration_drift") or {}).get("present") or repaired["status_kind"] != "postgres_runtime_schema_current":
            raise AssertionError(f"repair did not restore current PostgreSQL migration status: {repaired}")
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


def assert_truth_labels() -> None:
    with tempfile.TemporaryDirectory(prefix="schema-truth-labels-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        packet = run_json(
            repo,
            "delegate",
            "packet",
            "--role",
            "worker",
            "--endpoint",
            "schema-stewardship",
            "--body",
            "Return material only.",
            "--save-artifact",
        )
        for surface in (packet, packet["packet"], packet["packet"]["role_packet"]):
            if surface["artifact_primary"] is not True:
                raise AssertionError(f"delegate packet did not label artifact-primary mode: {surface}")
            if surface["governance_db_row_written"] is not False:
                raise AssertionError(f"delegate packet implied governance DB write: {surface}")
            if surface["delegation_tables"] != "dormant_not_primary_storage":
                raise AssertionError(f"delegate packet did not label dormant delegation tables: {surface}")
            if surface["db_persist_table"] is not None or surface["delegation_packets_table_status"] != "dormant_not_written":
                raise AssertionError(f"delegate packet implied DB persistence: {surface}")
        review = run_json(
            repo,
            "delegate",
            "review",
            "--result",
            "accept",
            "--summary",
            "Looks good as material only.",
        )
        if review["review"]["closes_check"] or review["review"]["closes_task"] or not review["review"]["controller_only_closeout"]:
            raise AssertionError(f"reviewer material was allowed to close scope: {review}")


def main() -> int:
    assert_static_role_policy()
    assert_live_guard_and_advanced_surface()
    assert_contraction_migration_preflight()
    assert_ledger_repair_path()
    assert_truth_labels()
    print(json.dumps({"ok": True, "schema_stewardship_p0": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
