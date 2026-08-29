from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.schema_roles import (  # noqa: E402
    CONTRACTION_CANDIDATE_TABLES,
    CONTRACTION_MIGRATION_FILENAME,
    REVIEW_LANE_ACTIVATION_CRITERIA,
    contraction_candidate_policy,
    predicate_table_replacement_carriers,
)
from tests.helpers.postgres_fixture import postgres_fixture  # noqa: E402


def connect_fixture_db(fixture):
    import psycopg
    from psycopg.rows import dict_row

    url = fixture.run_json("postgres-dev", "url")["database_url"]
    return psycopg.connect(url, row_factory=dict_row)


def table_counts(fixture, tables: tuple[str, ...]) -> dict[str, int]:
    conn = connect_fixture_db(fixture)
    try:
        counts = {}
        for table in tables:
            exists = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = %s",
                (table,),
            ).fetchone()
            counts[table] = (
                int(conn.execute(f'SELECT COUNT(*) AS count FROM "{table}"').fetchone()["count"])
                if exists
                else 0
            )
        return counts
    finally:
        conn.close()


def assert_static_p1_p2_policy() -> None:
    if len(REVIEW_LANE_ACTIVATION_CRITERIA) != 4:
        raise AssertionError(f"review lane criteria drifted: {REVIEW_LANE_ACTIVATION_CRITERIA}")
    for criterion in [
        "independent_lifecycle",
        "independent_query_or_workbench_need",
        "strong_check_and_evidence_constraints",
        "structured_conclusions_beyond_artifact_text",
    ]:
        if criterion not in REVIEW_LANE_ACTIVATION_CRITERIA:
            raise AssertionError(f"missing formal review lane criterion: {criterion}")
    carriers = predicate_table_replacement_carriers()
    for table in [
        "source_promises",
        "hard_predicates",
        "forbidden_substitutes",
        "task_predicate_links",
        "evidence_predicate_coverage",
    ]:
        if not carriers.get(table):
            raise AssertionError(f"missing predicate replacement carriers for {table}: {carriers}")
    candidates = contraction_candidate_policy()
    if [item["table"] for item in candidates] != list(CONTRACTION_CANDIDATE_TABLES):
        raise AssertionError(f"P2 candidate list drifted: {candidates}")
    for item in candidates:
        if item["user_confirmation_required"] is not False or item["physical_contraction_allowed"] is not True:
            raise AssertionError(f"candidate is not marked user-approved for contraction: {item}")
        if item["preflight_status"] != "passed" or item["blockers"]:
            raise AssertionError(f"candidate should have a clear zero-row preflight: {item}")
        if item["default_write_dependency_status"] != "contracted_no_default_write_path":
            raise AssertionError(f"candidate has a default write path: {item}")
        if item["contraction_migration"] != CONTRACTION_MIGRATION_FILENAME:
            raise AssertionError(f"candidate lost contraction migration trace: {item}")
    blocked = {item["table"]: item for item in contraction_candidate_policy({"source_promises": 1})}
    source_promise = blocked["source_promises"]
    if source_promise["physical_contraction_allowed"] is not False:
        raise AssertionError(f"non-empty candidate table was not blocked: {source_promise}")
    if source_promise["preflight_status"] != "blocked" or "table_has_rows" not in source_promise["blockers"]:
        raise AssertionError(f"non-empty candidate table lost blocker detail: {source_promise}")


def assert_live_p1_p2_package() -> None:
    fixture_pair = postgres_fixture("schema-stewardship-p1-p2-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return
    temp, fixture = fixture_pair
    try:
        (fixture.repo / "plan.md").write_text(
            "# Schema P1/P2\n\nDerived work-chain views and non-destructive contraction gates.\n",
            encoding="utf-8",
        )
        doc = fixture.run_json("doc", "import", "plan.md", "--source-type", "plan")
        scope = fixture.run_json(
            "scope",
            "create",
            "--body",
            "Schema stewardship P1/P2 fixture scope.",
            "--source-node",
            doc["document_node_id"],
        )
        fixture.run_json("endpoint", "create", "schema-parent", "--root-node", scope["node_id"])
        fixture.run_json("endpoint", "create", "schema-child", "--root-node", scope["node_id"])
        fixture.run_json("endpoint", "link-child", "--parent", "schema-parent", "--child", "schema-child")
        task = fixture.run_json(
            "task",
            "add",
            "--contract",
            scope["contract_id"],
            "--body",
            "Implement derived schema stewardship views.",
            "--from-node",
            doc["document_node_id"],
        )
        check = fixture.run_json(
            "acceptance",
            "add",
            "--task",
            task["task_id"],
            "--body",
            "Derived package exposes work_chain_view without writing work_chains.",
            "--expected-evidence-type",
            "test_result",
            "--from-node",
            doc["document_node_id"],
        )
        fixture.run_json(
            "audit",
            "record",
            "--endpoint",
            "schema-parent",
            "--source-node",
            doc["document_node_id"],
            "--body",
            "Parent blocker applies to the child task.",
            "--task",
            task["task_id"],
            "--finding",
            "Parent blocker applies to the child task.",
        )
        before = table_counts(fixture, CONTRACTION_CANDIDATE_TABLES)
        if any(before.values()):
            raise AssertionError(f"contracted tables should be absent in fixture after migration: {before}")
        out_path = fixture.repo / "docs" / "schema_stewardship_p1_p2_gate_package_test.json"
        result = fixture.run_json(
            "schema",
            "p1-p2-package",
            "--endpoint",
            "schema-child",
            "--path",
            str(out_path),
        )
        after = table_counts(fixture, CONTRACTION_CANDIDATE_TABLES)
        if after != before:
            raise AssertionError(f"read-only package changed candidate table counts: before={before}, after={after}")
        if not result["physical_contraction_allowed"] or result["user_confirmation_required"]:
            raise AssertionError(f"P2 command did not report approved physical contraction: {result}")
        if result["contraction_migration"] != CONTRACTION_MIGRATION_FILENAME:
            raise AssertionError(f"P2 command lost migration trace: {result}")
        package = json.loads(out_path.read_text(encoding="utf-8"))
        if not package["read_only"] or package["governance_db_rows_written"]:
            raise AssertionError(f"package is not read-only material: {package}")
        work_view = package["p1"]["work_chain_view"]
        if "work_chains" in work_view["source_tables"]:
            raise AssertionError(f"work_chain_view uses frozen work_chains table as source: {work_view}")
        if work_view["chain"]["task_count"] != 1 or work_view["chain"]["check_count"] != 1:
            raise AssertionError(f"work_chain_view did not derive task/check context: {work_view}")
        inherited = package["p1"]["dynamic_inherited_blockers"]
        if "endpoint_inherited_blockers" in inherited["source_tables"]:
            raise AssertionError(f"inherited blocker view reads frozen table as primary: {inherited}")
        if inherited["blocker_count"] < 1:
            raise AssertionError(f"dynamic inherited blocker was not derived: {inherited}")
        review_policy = package["p1"]["review_adoption_policy"]
        if review_policy["review_results_default_closure_source"] is not False:
            raise AssertionError(f"review_results became a closure source: {review_policy}")
        if review_policy["physical_status"] != "contracted_absent_expected":
            raise AssertionError(f"review_results absence was not expected: {review_policy}")
        if review_policy["activation_criteria"] != list(REVIEW_LANE_ACTIVATION_CRITERIA):
            raise AssertionError(f"review criteria missing from package: {review_policy}")
        predicate_view = package["p1"]["predicate_table_replacement"]
        for table in predicate_view["frozen_default_write_tables"]:
            if not predicate_view["replacement_carriers"].get(table):
                raise AssertionError(f"missing carrier proof for {table}: {predicate_view}")
        delegation = package["p1"]["delegation_reconciliation"]
        if delegation["artifact_primary_mode"] is not True or "future_productization_boundary" not in delegation:
            raise AssertionError(f"delegation reconciliation is not artifact-primary: {delegation}")
        p2 = package["p2_non_destructive_gate"]
        if p2["candidate_count"] != 9 or p2["required_candidate_tables"] != list(CONTRACTION_CANDIDATE_TABLES):
            raise AssertionError(f"P2 candidate package is incomplete: {p2}")
        if p2["contracted_tables_present"]:
            raise AssertionError(f"contracted tables should not be present in fixture package: {p2}")
        if p2["contraction_migration"] != CONTRACTION_MIGRATION_FILENAME:
            raise AssertionError(f"P2 package lost contraction migration: {p2}")
        for candidate in p2["candidates"]:
            if candidate["physical_action"] != "contracted_absent_expected":
                raise AssertionError(f"P2 candidate is not marked absent as expected: {candidate}")
            if not candidate["replacement_path_proof_status"]:
                raise AssertionError(f"P2 candidate lacks replacement proof status: {candidate}")
        if check["acceptance_check_id"] not in json.dumps(package, sort_keys=True):
            raise AssertionError("fixture check id was not visible in derived work chain package")

        intake = fixture.run_json(
            "work",
            "intake",
            "--endpoint",
            "schema-child",
            "--source-node",
            doc["document_node_id"],
            "--text",
            "Contracted source promise should not be persisted to removed tables.",
            "--predicate",
            "HP-CONTRACTED::Contracted predicate material",
            expect_ok=False,
        )
        if intake["ok"] is not False or intake["status"] != "contracted_legacy_command_disabled" or intake["db_writes"] != 0:
            raise AssertionError(f"work intake did not disable cleanly for contracted tables: {intake}")
        if intake["closure_claim"] or not intake["diagnostic_only"]:
            raise AssertionError(f"work intake disabled diagnostic can be mistaken for closure: {intake}")
        split = fixture.run_json(
            "work",
            "split",
            "--endpoint",
            "schema-child",
            "--name",
            "Contracted split",
            "--task",
            task["task_id"],
            "--check",
            check["acceptance_check_id"],
            "--predicate",
            "HP-CONTRACTED",
            expect_ok=False,
        )
        if split["ok"] is not False or split["status"] != "contracted_legacy_command_disabled" or split["requested_links"]:
            raise AssertionError(f"work split did not disable cleanly for contracted tables: {split}")
        if split["closure_claim"] or not split["diagnostic_only"]:
            raise AssertionError(f"work split disabled diagnostic can be mistaken for closure: {split}")
        focus = fixture.run_json("work", "focus", "--endpoint", "schema-child", "--work-chain", "WC-CONTRACTED")
        if focus["derived_work_chains"] or focus["legacy_predicates"] or focus["legacy_task_predicate_links"]:
            raise AssertionError(f"work focus should expose empty derived/legacy material: {focus}")
        if not focus["derived"] or focus["legacy_contract"]["hard_predicates_current_db_truth"]:
            raise AssertionError(f"work focus did not relabel legacy predicate material as derived/read-only: {focus}")
        review_start = fixture.run_json("review", "start", "--endpoint", "schema-child", "--check", check["acceptance_check_id"])
        if review_start["mandatory_input_bundle"]["predicate_coverage_matrix_status"] != "contracted_absent_expected":
            raise AssertionError(f"review start did not mark predicate coverage as contracted: {review_start}")
        if review_start["mandatory_input_bundle"]["review_material_status"] != "advisory_material_until_controller_adoption":
            raise AssertionError(f"review start did not label review as advisory material: {review_start}")
        review_submit = fixture.run_json(
            "review",
            "submit",
            "--endpoint",
            "schema-child",
            "--result",
            "reject",
            "--summary",
            "Material-only review on contracted schema.",
            expect_ok=False,
        )
        if review_submit["ok"] is not False or review_submit["status"] != "contracted_legacy_command_disabled" or review_submit["db_writes"] != 0:
            raise AssertionError(f"review submit did not disable cleanly for contracted review_results: {review_submit}")
        if review_submit["closure_claim"] or not review_submit["diagnostic_only"]:
            raise AssertionError(f"review submit disabled diagnostic can be mistaken for closure: {review_submit}")
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


def main() -> int:
    assert_static_p1_p2_policy()
    assert_live_p1_p2_package()
    print(json.dumps({"ok": True, "schema_stewardship_p1_p2": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
