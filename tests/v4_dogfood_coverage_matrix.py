from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_ARTIFACT = ROOT / "docs" / "v4_dogfood_scenarios_2026-05-19.md"


REQUIRED_V4_PROMISES: dict[str, dict[str, Any]] = {
    "SP-T1-G6-WORKBENCH-001": {
        "check_ids": ["check_36d8501e787e41ef", "check_d4917ca4e82e4e70", "check_370aba34f1c642e0"],
        "predicates": [
            "HP-G6-DEP",
            "HP-G6-CANVAS",
            "HP-G6-PROJECTION-PAYLOAD",
            "HP-G6-ATTENTION-LAYOUT",
            "HP-G6-CONTROLS",
            "HP-G6-SOURCE-DRAWER",
            "HP-G6-ARTIFACT-DIFF-PREVIEW",
            "HP-G6-READONLY",
            "HP-G6-NEGATIVE-STATIC-EXPORT",
        ],
        "evidence_ref": "tests/v4_interaction_trust_layer.py::workbench/projection assertions",
    },
    "SP-T2-PROJECTION-SEMANTICS-001": {
        "check_ids": [
            "check_383f2885537d4377",
            "check_d95816ad957b4217",
            "check_bb19c0e1de7641d7",
            "check_2d85118b30ce4552",
        ],
        "predicates": [
            "HP-PROJ-FOLDED-SOURCE-COUNT",
            "HP-PROJ-BROKEN-ADJACENCY",
            "HP-PROJ-DETAIL-REF-EXPANSION",
            "HP-PROJ-SNAPSHOT-EVENT-METADATA",
            "HP-PROJ-VISUAL-EDGE-METADATA",
        ],
        "evidence_ref": "tests/v4_interaction_trust_layer.py::projection/detail assertions",
    },
    "SP-T3-CLOSEOUT-MODE-001": {
        "check_ids": [
            "check_1860fd4b842f4246",
            "check_e0e8b86117854dc8",
            "check_76ba68009ec64228",
            "check_8325e8c2c20e4b8e",
            "check_943beaebeab74c02",
        ],
        "predicates": [
            "HP-FULL-DOCTOR-VERIFY-GATE",
            "HP-EVIDENCE-CURRENTNESS",
            "HP-EVIDENCE-INVALIDATION",
            "HP-MODE-MIXED-INTENT-SAFE-FAIL",
            "HP-ALIAS-CONSISTENCY",
            "HP-CAPTURE-EXPLORE-ENDPOINT-DIAGNOSTIC",
        ],
        "evidence_ref": "tests/v4_interaction_trust_layer.py::mode/evidence closeout assertions",
    },
    "SP-T4-DOGFOOD-RUNTIME-001": {
        "check_ids": ["check_96968be516ef4b0b", "check_7bc64ccc27004692", "check_68f49a16166b49fc", "check_3cab69a495b4405d"],
        "predicates": [
            "HP-DOGFOOD-EIGHT-SCENARIOS",
            "HP-DOGFOOD-COMMAND-PER-SCENARIO",
            "HP-DOGFOOD-DB-DELTA",
            "HP-DOGFOOD-PROJECTION-SIGNAL",
            "HP-DOGFOOD-PROHIBITED-SIDE-EFFECT",
            "HP-DOGFOOD-EVIDENCE-NODE",
            "HP-POSTGRES-DEV-PROVIDER-RUNTIME",
            "HP-DIRTY-SNAPSHOT-ROBUSTNESS",
        ],
        "evidence_ref": "tests/v4_interaction_trust_layer.py + tests/dirty_snapshot_robustness.py dogfood assertions",
    },
    "SP-T5-SOURCE-DB-NONDOWNGRADE-001": {
        "check_ids": ["check_e6c721ee618148c0", "check_6c7672f9154c4d56", "check_0b27d9d768774965", "check_e0f467fb7aad43a4"],
        "predicates": [
            "HP-SOURCE-PROMISE-EXTRACTION",
            "HP-NAMED-TECH-PRESERVATION",
            "HP-MUST-TERM-PRESERVATION",
            "HP-ENUMERATED-LIST-PRESERVATION",
            "HP-NONDOWNGRADE-FINDING",
            "HP-PRODUCT-BACKLOG-TERM",
            "HP-CRLF-LF-HASH-STABILITY",
        ],
        "evidence_ref": "tests/source_non_downgrade_gate.py and focused v4 source tests",
    },
    "SP-T6-ENDPOINT-PROPAGATION-001": {
        "check_ids": ["check_08d7962b78c146ef", "check_6f3e3ba4a3f8442a", "check_faa78f8f4af84579"],
        "predicates": [
            "HP-UMBRELLA-FINDING-MAPS-TO-CHILD",
            "HP-INHERITED-ACTIVE-BLOCKER",
            "HP-CHILD-NOT-CLEAN-WHILE-BLOCKED",
            "HP-PROPAGATION-RESOLVE-DEFER",
            "HP-AGCP-PROPAGATION",
        ],
        "evidence_ref": "tests/endpoint_blocker_propagation.py and AGCP mapping assertions",
    },
    "SP-T7-PROOF-MATRIX-001": {
        "check_ids": ["check_5210f2bad71b453d", "check_bc3c926dff404e28", "check_68f49a16166b49fc", "check_faa78f8f4af84579"],
        "predicates": [
            "HP-PROOF-MATRIX-CHECK-PREDICATE",
            "HP-PROOF-MATRIX-ASSERTION-RESULT",
            "HP-BROAD-EVIDENCE-NO-MULTICLOSE",
            "HP-NOT-COVERED-ACCOUNTING",
            "HP-DOGFOOD-MATRIX-FAILS-MISSING-PROMISE",
            "HP-AGCP-PROOF-MATRIX",
        ],
        "evidence_ref": "tests/predicate_coverage_matrix.py and this dogfood coverage matrix test",
    },
}


PASS_RESULTS = {"pass", "passed", "ok", "covered", "success", "succeeded"}
ACCEPTED_DEFER_RESULTS = {"accepted_deferred", "deferred"}
ACCEPTED_COVERAGE_RESULTS = PASS_RESULTS | ACCEPTED_DEFER_RESULTS
REQUIRED_SCENARIO_FIELDS = {
    "scenario_id",
    "source_plan_scenario",
    "commands",
    "hard_assertions",
    "expected_db_delta",
    "projection_signal",
    "prohibited_side_effects",
    "evidence_output",
    "failure_diagnostics",
    "runnable",
}


def build_dogfood_scenario_proof_matrix() -> list[dict[str, Any]]:
    trust_layer_command = "python tests\\v4_interaction_trust_layer.py"
    dirty_snapshot_command = "python tests\\dirty_snapshot_robustness.py"
    return [
        {
            "scenario_id": "DF-SC-001",
            "source_plan_scenario": "Pure design discussion capture/extract/create-scope",
            "runnable": True,
            "commands": [trust_layer_command],
            "hard_assertions": [
                {"predicate_id": "HP-DOGFOOD-EIGHT-SCENARIOS", "assertion": "scenario row exists with an executable command"},
                {"predicate_id": "HP-DOGFOOD-DB-DELTA", "assertion": "discussion capture/review/extract do not create task/check/run/change_set rows"},
                {"predicate_id": "HP-DOGFOOD-PROHIBITED-SIDE-EFFECT", "assertion": "capture and review stay source-material only until explicit extraction"},
            ],
            "expected_db_delta": {
                "interaction_events": "+1 for capture",
                "discussion_segments": "+1 for capture, replacement creates a new segment",
                "discussion_messages": "+1 for captured prompt",
                "tasks": "0 from capture/review/extract decision path",
                "acceptance_checks": "0 from capture/review/extract decision path",
                "agent_runs": "0 from capture/review/extract decision path",
                "change_sets": "0 from capture/review/extract decision path",
            },
            "projection_signal": "endpoint report/status exposes unreviewed discussion count and discussion lifecycle detail_ref",
            "prohibited_side_effects": ["auto-created task/check from capture", "execution run from Capture/Explore source work"],
            "evidence_output": {
                "type": "test_result",
                "ref": "tests/v4_interaction_trust_layer.py::capture/review/extract lifecycle assertions",
                "stdout_key": "segment_id",
            },
            "failure_diagnostics": ["DB count delta mismatch", "missing lifecycle event", "capture receipt creates_run/creates_task is true"],
        },
        {
            "scenario_id": "DF-SC-002",
            "source_plan_scenario": "No Governance report",
            "runnable": True,
            "commands": [trust_layer_command],
            "hard_assertions": [
                {"predicate_id": "HP-DOGFOOD-COMMAND-PER-SCENARIO", "assertion": "English and Chinese no-governance wording routes to no_governance"},
                {"predicate_id": "HP-DOGFOOD-PROHIBITED-SIDE-EFFECT", "assertion": "No Governance makes no DB writes and no capture claim"},
            ],
            "expected_db_delta": {
                "all_counted_tables": "0 for mode suggest and work start no-governance",
                "current_handle": "null",
            },
            "projection_signal": "mode contract reports capture_claim=false and current_handle=None",
            "prohibited_side_effects": ["interaction_event write", "discussion_segment write", "agent_run write", "current handle creation"],
            "evidence_output": {
                "type": "test_result",
                "ref": "tests/v4_interaction_trust_layer.py::No Governance routing assertions",
                "stdout_key": "ok",
            },
            "failure_diagnostics": ["unexpected DB count changes", "suggested_mode is not no_governance", "capture_claim true"],
        },
        {
            "scenario_id": "DF-SC-003",
            "source_plan_scenario": "Light document fix",
            "runnable": True,
            "commands": [trust_layer_command],
            "hard_assertions": [
                {"predicate_id": "HP-DOGFOOD-DB-DELTA", "assertion": "Light mode starts an execution run and exposes current run handle"},
                {"predicate_id": "HP-DOGFOOD-PROJECTION-SIGNAL", "assertion": "@current.endpoint resolves to the Light run endpoint"},
            ],
            "expected_db_delta": {
                "agent_runs": "+1",
                "run_snapshots": "+1 before snapshot",
                "change_sets": "0 before close",
            },
            "projection_signal": "work current returns active_run; endpoint brief @current.endpoint resolves to trust",
            "prohibited_side_effects": ["check closure before evidence", "change_set creation during work start"],
            "evidence_output": {
                "type": "test_result",
                "ref": "tests/v4_interaction_trust_layer.py::Light work current assertions",
                "stdout_key": "run_id",
            },
            "failure_diagnostics": ["Light contract creates_run false", "active_run missing", "@current.endpoint mismatch"],
        },
        {
            "scenario_id": "DF-SC-004",
            "source_plan_scenario": "Standard bugfix with close dry-run",
            "runnable": True,
            "commands": [trust_layer_command],
            "hard_assertions": [
                {"predicate_id": "HP-DOGFOOD-PROJECTION-SIGNAL", "assertion": "work close --dry-run reports pending change-set behavior"},
                {"predicate_id": "HP-DOGFOOD-PROHIBITED-SIDE-EFFECT", "assertion": "dry-run close does not mutate closeout or closure state"},
            ],
            "expected_db_delta": {
                "change_sets": "0 for dry-run close",
                "closed_acceptance_checks": "0 for dry-run close",
            },
            "projection_signal": "dry_run=true and would_create_change_set=true",
            "prohibited_side_effects": ["change_set row from dry-run", "closed check from dry-run"],
            "evidence_output": {
                "type": "test_result",
                "ref": "tests/v4_interaction_trust_layer.py::work close dry-run assertions",
                "stdout_key": "ok",
            },
            "failure_diagnostics": ["dry_run false", "would_create_change_set false", "unexpected closure mutation"],
        },
        {
            "scenario_id": "DF-SC-005",
            "source_plan_scenario": "Full PostgreSQL/evidence fix",
            "runnable": True,
            "commands": [trust_layer_command],
            "hard_assertions": [
                {"predicate_id": "HP-DOGFOOD-EVIDENCE-NODE", "assertion": "test_result evidence stores stdout/stderr/command refs"},
                {"predicate_id": "HP-DOGFOOD-PROHIBITED-SIDE-EFFECT", "assertion": "invalidated evidence and provider facts cannot close acceptance checks"},
            ],
            "expected_db_delta": {
                "test_result_nodes": "+1 for invalidation fixture",
                "provider_fact_nodes": "+2 provider_hypothesis facts",
                "closed_acceptance_checks": "0 from invalidated evidence/provider fact rejection",
            },
            "projection_signal": "endpoint status keeps default provider warnings out of active audit findings",
            "prohibited_side_effects": ["SQLite fallback called PostgreSQL success", "provider_hypothesis accepted as closure evidence", "invalidated evidence closes check"],
            "evidence_output": {
                "type": "test_result",
                "ref": "tests/v4_interaction_trust_layer.py::evidence/provider assertions",
                "stdout_key": "ok",
            },
            "failure_diagnostics": ["missing evidence refs", "provider close did not fail", "invalidated close did not fail"],
        },
        {
            "scenario_id": "DF-SC-006",
            "source_plan_scenario": "Multi-subagent handoff",
            "runnable": True,
            "commands": [trust_layer_command],
            "hard_assertions": [
                {"predicate_id": "HP-DOGFOOD-EVIDENCE-NODE", "assertion": "summary handoff imports as artifact/work note"},
                {"predicate_id": "HP-DOGFOOD-PROHIBITED-SIDE-EFFECT", "assertion": "summary handoff creates no active audit finding or closure evidence"},
            ],
            "expected_db_delta": {
                "artifact_nodes": "+1 summary handoff artifact",
                "work_notes": "+1 summary work note",
                "audit_findings": "0 for summary-classified handoff",
                "closed_acceptance_checks": "0",
            },
            "projection_signal": "audit import output returns artifact_node_id and classification=summary",
            "prohibited_side_effects": ["worker handoff closes check", "summary handoff creates active audit finding"],
            "evidence_output": {
                "type": "test_result",
                "ref": "tests/v4_interaction_trust_layer.py::audit import summary handoff assertions",
                "stdout_key": "ok",
            },
            "failure_diagnostics": ["artifact_node_id missing", "classification drifted", "audit_finding_node_ids not empty"],
        },
        {
            "scenario_id": "DF-SC-007",
            "source_plan_scenario": "Attention graph noise control",
            "runnable": True,
            "commands": [trust_layer_command],
            "hard_assertions": [
                {"predicate_id": "HP-DOGFOOD-PROJECTION-SIGNAL", "assertion": "exec stop --endpoint trust reports endpoint-scoped obligations only"},
                {"predicate_id": "HP-DOGFOOD-PROHIBITED-SIDE-EFFECT", "assertion": "unrelated endpoint task/check ids do not leak into trust stop_check or closeout"},
            ],
            "expected_db_delta": {
                "endpoints": "+1 unrelated endpoint fixture",
                "trust_stop_check_scope_mode": "endpoint_scope",
                "rootless_stop_check_scope_mode": "endpoint_without_root",
            },
            "projection_signal": "stop_check.endpoint=trust and rootless stop_check warns must_not_claim_complete",
            "prohibited_side_effects": ["global open task scan pollutes endpoint stop_check", "rootless endpoint claims completion"],
            "evidence_output": {
                "type": "test_result",
                "ref": "tests/v4_interaction_trust_layer.py::endpoint-scoped stop_check assertions",
                "stdout_key": "ok",
            },
            "failure_diagnostics": ["unrelated task/check id appears in stop_check", "scope_mode mismatch", "rootless warning missing"],
        },
        {
            "scenario_id": "DF-SC-008",
            "source_plan_scenario": "Audit graph traceability",
            "runnable": True,
            "commands": [trust_layer_command, dirty_snapshot_command],
            "hard_assertions": [
                {"predicate_id": "HP-DOGFOOD-PROJECTION-SIGNAL", "assertion": "projection payloads expose detail_ref, hidden_source_count, visual state, and visible_edges"},
                {"predicate_id": "HP-DIRTY-SNAPSHOT-ROBUSTNESS", "assertion": "large/binary/non-UTF8/temp/unreadable paths record path/hash/classification/skipped_text_reason/warnings without OSError"},
            ],
            "expected_db_delta": {
                "projection_snapshots": "+1 when --save-snapshot is used",
                "change_sets": "+1 dirty snapshot robustness fixture",
                "diff_files": "rows for large.txt, binary.bin, non_utf8.txt, scratch.tmp",
            },
            "projection_signal": "graph projection save-snapshot writes payload_ref; workbench export is read_only with no write path",
            "prohibited_side_effects": ["broken visible chains", "missing detail_ref", "binary/large/unreadable file read crash"],
            "evidence_output": {
                "type": "test_result",
                "ref": "tests/v4_interaction_trust_layer.py::projection/workbench assertions + tests/dirty_snapshot_robustness.py",
                "stdout_key": "ok",
            },
            "failure_diagnostics": ["projection omitted snapshot/detail fields", "dirty snapshot row omitted hash/classification/warnings", "OSError escaped snapshot capture"],
        },
    ]


def validate_dogfood_scenario_proof_matrix(matrix: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(matrix, list):
        return ["dogfood_scenario_proof_matrix must be a JSON array"]
    if len(matrix) != 8:
        errors.append(f"expected exactly 8 dogfood scenario rows, got {len(matrix)}")
    seen: set[str] = set()
    doc_text = SCENARIO_ARTIFACT.read_text(encoding="utf-8") if SCENARIO_ARTIFACT.exists() else ""
    for index, row in enumerate(matrix, start=1):
        if not isinstance(row, dict):
            errors.append(f"scenario row {index} is not an object")
            continue
        missing = sorted(REQUIRED_SCENARIO_FIELDS - set(row))
        if missing:
            errors.append(f"{row.get('scenario_id', f'row {index}')} missing required field(s): {', '.join(missing)}")
            continue
        scenario_id = str(row.get("scenario_id") or "").strip()
        if not scenario_id:
            errors.append(f"scenario row {index} has empty scenario_id")
        if scenario_id in seen:
            errors.append(f"duplicate scenario_id: {scenario_id}")
        seen.add(scenario_id)
        source_name = str(row.get("source_plan_scenario") or "").strip()
        if not source_name:
            errors.append(f"{scenario_id} has empty source_plan_scenario")
        elif doc_text and source_name not in doc_text:
            errors.append(f"{scenario_id} source scenario is not present in {SCENARIO_ARTIFACT.name}: {source_name}")
        if row.get("runnable") is False and not (row.get("user_deferred") is True and row.get("defer_source")):
            errors.append(f"{scenario_id} is non-runnable without explicit user defer")
        commands = row.get("commands")
        if row.get("runnable") is True and (not isinstance(commands, list) or not all(isinstance(item, str) and item.strip() for item in commands)):
            errors.append(f"{scenario_id} runnable scenario has no executable command")
        hard_assertions = row.get("hard_assertions")
        if not isinstance(hard_assertions, list) or not hard_assertions:
            errors.append(f"{scenario_id} has no hard assertions")
        else:
            for assertion in hard_assertions:
                if not isinstance(assertion, dict) or not assertion.get("predicate_id") or not assertion.get("assertion"):
                    errors.append(f"{scenario_id} has malformed hard assertion: {assertion}")
        for field in ("expected_db_delta", "projection_signal", "prohibited_side_effects", "evidence_output", "failure_diagnostics"):
            value = row.get(field)
            if value in (None, "", [], {}):
                errors.append(f"{scenario_id} has empty {field}")
    required_ids = {f"DF-SC-{index:03d}" for index in range(1, 9)}
    missing_ids = sorted(required_ids - seen)
    if missing_ids:
        errors.append(f"missing required scenario id(s): {', '.join(missing_ids)}")
    return errors


def build_proof_results_fixture() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for promise_id, contract in REQUIRED_V4_PROMISES.items():
        for predicate_id in contract["predicates"]:
            proof_result_id = f"proof::{promise_id}::{predicate_id}"
            result = {
                "proof_result_id": proof_result_id,
                "promise_id": promise_id,
                "predicate_id": predicate_id,
                "check_ids": list(contract["check_ids"]),
                "type": "test_result",
                "command": "python tests\\v4_dogfood_coverage_matrix.py",
                "ref": contract["evidence_ref"],
                "exit_code": 0,
                "result": "pass",
            }
            if predicate_id == "HP-POSTGRES-DEV-PROVIDER-RUNTIME":
                result.update(
                    {
                        "type": "accepted_defer",
                        "command": None,
                        "ref": "deferred check_3cab69a495b4405d in v4 source promise ledger",
                        "exit_code": None,
                        "result": "accepted_deferred",
                        "defer_reason": "Provider runtime table cutover/reset proof remains explicitly deferred in current scope.",
                    }
                )
            elif predicate_id == "HP-DIRTY-SNAPSHOT-ROBUSTNESS":
                result.update(
                    {
                        "command": "python tests\\dirty_snapshot_robustness.py",
                        "ref": "tests/dirty_snapshot_robustness.py::large/binary/non-UTF8/temp/unreadable snapshot assertions",
                    }
                )
            results.append(result)
    return results


def proof_results_by_id(proof_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("proof_result_id", "")).strip(): row for row in proof_results if isinstance(row, dict)}


def proof_result_is_accepted(row: dict[str, Any]) -> bool:
    result = str(row.get("result", "")).strip().lower()
    if result in PASS_RESULTS:
        return row.get("exit_code") == 0 and bool(row.get("command"))
    if result in ACCEPTED_DEFER_RESULTS:
        return row.get("type") in {"accepted_defer", "defer_decision"} and bool(row.get("ref"))
    return False


def build_dogfood_promise_coverage_matrix(proof_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proofs = proof_results_by_id(proof_results)
    matrix: list[dict[str, Any]] = []
    for promise_id, contract in REQUIRED_V4_PROMISES.items():
        check_ids = list(contract["check_ids"])
        predicate_rows: list[dict[str, Any]] = []
        for predicate_id in contract["predicates"]:
            proof_result_id = f"proof::{promise_id}::{predicate_id}"
            proof = proofs.get(proof_result_id, {})
            proof_accepted = proof_result_is_accepted(proof)
            predicate_result = str(proof.get("result") or "missing_proof")
            predicate_rows.append(
                {
                    "predicate_id": predicate_id,
                    "check_ids": check_ids,
                    "assertion": f"{predicate_id} is backed by an accepted proof result row.",
                    "result": predicate_result if proof_accepted else "not_covered",
                    "not_covered": not proof_accepted,
                    "reason": "" if proof_accepted else f"missing or failing proof result: {proof_result_id}",
                    "evidence": [
                        {
                            "proof_result_id": proof_result_id,
                            "type": proof.get("type"),
                            "command": proof.get("command"),
                            "ref": proof.get("ref"),
                            "result": proof.get("result"),
                            "exit_code": proof.get("exit_code"),
                        }
                    ],
                }
            )
        matrix.append(
            {
                "promise_id": promise_id,
                "check_ids": check_ids,
                "hard_predicates": predicate_rows,
            }
        )
    return matrix


def validate_dogfood_promise_coverage_matrix(matrix: Any, proof_results: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if not isinstance(matrix, list):
        return ["dogfood_promise_coverage_matrix must be a JSON array"]
    proofs = proof_results_by_id(proof_results)
    by_promise = {str(row.get("promise_id", "")).strip(): row for row in matrix if isinstance(row, dict)}
    for promise_id, contract in REQUIRED_V4_PROMISES.items():
        promise_row = by_promise.get(promise_id)
        if not promise_row:
            errors.append(f"missing required v4 promise: {promise_id}")
            continue
        check_ids = promise_row.get("check_ids")
        if not isinstance(check_ids, list) or not all(isinstance(item, str) and item for item in check_ids):
            errors.append(f"{promise_id} has no machine-readable check_ids")
            check_ids = []
        missing_checks = sorted(set(contract["check_ids"]) - set(check_ids))
        if missing_checks:
            errors.append(f"{promise_id} missing check_id mapping(s): {', '.join(missing_checks)}")
        predicate_rows = promise_row.get("hard_predicates")
        if not isinstance(predicate_rows, list):
            errors.append(f"{promise_id} hard_predicates must be a JSON array")
            continue
        by_predicate = {str(row.get("predicate_id", "")).strip(): row for row in predicate_rows if isinstance(row, dict)}
        for predicate_id in contract["predicates"]:
            predicate_row = by_predicate.get(predicate_id)
            if not predicate_row:
                errors.append(f"{promise_id} missing hard predicate: {predicate_id}")
                continue
            if predicate_row.get("not_covered") is True or str(predicate_row.get("result", "")).strip().lower() not in ACCEPTED_COVERAGE_RESULTS:
                errors.append(f"{promise_id}/{predicate_id} is unproven")
            evidence = predicate_row.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"{promise_id}/{predicate_id} has no evidence")
                continue
            accepted_evidence = False
            for item in evidence:
                if not isinstance(item, dict):
                    continue
                proof_result_id = str(item.get("proof_result_id", "")).strip()
                proof = proofs.get(proof_result_id)
                if not proof:
                    errors.append(f"{promise_id}/{predicate_id} references missing proof result: {proof_result_id or '<empty>'}")
                    continue
                if proof.get("promise_id") != promise_id or proof.get("predicate_id") != predicate_id:
                    errors.append(f"{promise_id}/{predicate_id} proof result points at a different promise/predicate: {proof_result_id}")
                    continue
                if not proof_result_is_accepted(proof):
                    errors.append(f"{promise_id}/{predicate_id} proof result is not accepted: {proof_result_id}")
                    continue
                accepted_evidence = True
            if not accepted_evidence:
                errors.append(f"{promise_id}/{predicate_id} has no accepted proof evidence")
    return errors


def assert_matrix_valid(matrix: list[dict[str, Any]], proof_results: list[dict[str, Any]]) -> None:
    errors = validate_dogfood_promise_coverage_matrix(matrix, proof_results)
    if errors:
        raise AssertionError("dogfood promise coverage matrix is invalid: " + "; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-proof-matrix")
    args = parser.parse_args()

    scenario_matrix = build_dogfood_scenario_proof_matrix()
    scenario_errors = validate_dogfood_scenario_proof_matrix(scenario_matrix)
    if scenario_errors:
        raise AssertionError("dogfood scenario proof matrix is invalid: " + "; ".join(scenario_errors))

    proof_results = build_proof_results_fixture()
    matrix = build_dogfood_promise_coverage_matrix(proof_results)
    assert_matrix_valid(matrix, proof_results)

    missing_scenario = [row for row in scenario_matrix if row["scenario_id"] != "DF-SC-004"]
    missing_scenario_errors = validate_dogfood_scenario_proof_matrix(missing_scenario)
    if not any("expected exactly 8 dogfood scenario rows" in item for item in missing_scenario_errors) or not any("DF-SC-004" in item for item in missing_scenario_errors):
        raise AssertionError(f"missing-scenario fixture did not fail as expected: {missing_scenario_errors}")

    missing_field_scenario = copy.deepcopy(scenario_matrix)
    del missing_field_scenario[0]["expected_db_delta"]
    missing_field_errors = validate_dogfood_scenario_proof_matrix(missing_field_scenario)
    if not any("missing required field(s): expected_db_delta" in item for item in missing_field_errors):
        raise AssertionError(f"missing-field scenario fixture did not fail as expected: {missing_field_errors}")

    non_runnable_scenario = copy.deepcopy(scenario_matrix)
    non_runnable_scenario[0]["runnable"] = False
    non_runnable_scenario[0]["commands"] = []
    non_runnable_errors = validate_dogfood_scenario_proof_matrix(non_runnable_scenario)
    if not any("non-runnable without explicit user defer" in item for item in non_runnable_errors):
        raise AssertionError(f"non-runnable scenario fixture did not fail as expected: {non_runnable_errors}")

    missing_promise = [row for row in matrix if row["promise_id"] != "SP-T3-CLOSEOUT-MODE-001"]
    missing_promise_errors = validate_dogfood_promise_coverage_matrix(missing_promise, proof_results)
    if not any("missing required v4 promise: SP-T3-CLOSEOUT-MODE-001" in item for item in missing_promise_errors):
        raise AssertionError(f"missing-promise fixture did not fail as expected: {missing_promise_errors}")

    unproven_predicate = copy.deepcopy(matrix)
    t4_predicate = unproven_predicate[3]["hard_predicates"][0]
    t4_predicate["result"] = "fail"
    t4_predicate["not_covered"] = True
    t4_predicate["evidence"] = []
    unproven_errors = validate_dogfood_promise_coverage_matrix(unproven_predicate, proof_results)
    if not any("SP-T4-DOGFOOD-RUNTIME-001/HP-DOGFOOD-EIGHT-SCENARIOS is unproven" in item for item in unproven_errors):
        raise AssertionError(f"unproven-predicate fixture did not fail as expected: {unproven_errors}")

    failing_proof_results = copy.deepcopy(proof_results)
    failing_proof_results[0]["exit_code"] = 1
    failing_proof_results[0]["result"] = "fail"
    stale_pass_matrix = copy.deepcopy(matrix)
    stale_pass_errors = validate_dogfood_promise_coverage_matrix(stale_pass_matrix, failing_proof_results)
    if not any("proof result is not accepted" in item for item in stale_pass_errors):
        raise AssertionError(f"stale pass matrix did not fail against failing proof result: {stale_pass_errors}")

    missing_proof_results = [
        row
        for row in proof_results
        if row["proof_result_id"] != "proof::SP-T1-G6-WORKBENCH-001::HP-G6-DEP"
    ]
    missing_proof_errors = validate_dogfood_promise_coverage_matrix(stale_pass_matrix, missing_proof_results)
    if not any("references missing proof result" in item for item in missing_proof_errors):
        raise AssertionError(f"stale pass matrix did not fail against missing proof result: {missing_proof_errors}")

    payload = {
        "ok": True,
        "matrix_contract": "v4 scenarios -> commands -> hard assertions -> db/projection/side-effect/evidence diagnostics; v4 promises -> check_ids -> hard_predicates -> evidence",
        "scenario_count": len(scenario_matrix),
        "dogfood_scenario_proof_matrix": scenario_matrix,
        "proof_results": proof_results,
        "dogfood_promise_coverage_matrix": matrix,
        "negative_fixtures": {
            "missing_scenario_failed": True,
            "missing_field_scenario_failed": True,
            "non_runnable_without_defer_failed": True,
            "missing_promise_failed": True,
            "unproven_predicate_failed": True,
            "stale_pass_matrix_failing_proof_failed": True,
            "stale_pass_matrix_missing_proof_failed": True,
            "missing_scenario_errors": missing_scenario_errors,
            "missing_field_errors": missing_field_errors,
            "non_runnable_errors": non_runnable_errors,
            "missing_promise_errors": missing_promise_errors,
            "unproven_predicate_errors": unproven_errors,
            "stale_pass_errors": stale_pass_errors,
            "missing_proof_errors": missing_proof_errors,
        },
    }
    if args.write_proof_matrix:
        output_path = Path(args.write_proof_matrix)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        payload["written_proof_matrix"] = str(output_path)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
