from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan import cli
from shujuan.commands import endpoint, execution, workflow
from shujuan.services import activation_policy, agcp_policy, endpoint_projection, evidence_policy
from shujuan.services.dependencies import RuntimeDeps


def assert_activation_service() -> None:
    for mode in ("no_governance", "capture", "explore", "light", "standard", "full"):
        if cli.mode_contract_payload(mode) != activation_policy.mode_contract_payload(mode, cli.MODE_CONTRACTS):
            raise AssertionError(f"activation service payload drifted for {mode}")
    warnings = activation_policy.mode_gate_warnings("light", "P1 evidence predicate postgres closeout")
    if not warnings or warnings[0]["code"] != "mode_friction_high_risk_light":
        raise AssertionError(f"activation service lost high-risk Light warning: {warnings}")


def assert_evidence_service() -> None:
    if cli.expected_evidence_allowed("doc-update") != {"artifact", "change_set"}:
        raise AssertionError("evidence expected-type alias drifted")
    rows = evidence_policy.normalize_predicate_coverage_matrix_rows(
        [
            {
                "check_id": "check_a",
                "predicate_id": "HP-A",
                "assertion": "A",
                "result": "pass",
                "not_covered": False,
                "reason": "",
            }
        ],
        source="service-test",
    )
    if not cli.predicate_coverage_row_passed(rows[0]):
        raise AssertionError("predicate coverage pass result drifted")


def assert_projection_service() -> None:
    status = {
        "endpoint": {"id": "endpoint_1", "name": "e", "description": "d", "root_node_id": "node_1", "created_at": "2026-05-21T00:00:00"},
        "tasks": [{"id": "task_1", "created_at": "2026-05-21T00:01:00"}],
        "unlinked_scope_candidates": {"tasks": []},
    }
    if endpoint.endpoint_projection_facts(status) != endpoint_projection.endpoint_projection_facts(status):
        raise AssertionError("endpoint projection facts are not service-owned")
    if endpoint.endpoint_latest_fact_at(status) != "2026-05-21T00:01:00":
        raise AssertionError("endpoint latest fact calculation drifted")


def assert_agcp_service() -> None:
    source = inspect.getsource(endpoint.endpoint_agcp_doctor_findings)
    if "agcp_policy.endpoint_agcp_doctor_findings" not in source:
        raise AssertionError("endpoint AGCP doctor findings are not routed through agcp_policy")
    if "endpoint_agcp_doctor_findings" not in agcp_policy.__all__:
        raise AssertionError("AGCP service does not export its policy function")


def assert_dependency_object_pilot() -> None:
    if RuntimeDeps({"a": 1}).require("a") != {"a": 1}:
        raise AssertionError("RuntimeDeps did not return required dependencies")
    for module, function_name in ((workflow, "_workflow_dependencies"), (execution, "_execution_dependencies")):
        source = inspect.getsource(getattr(module, function_name))
        if "RuntimeDeps" not in source:
            raise AssertionError(f"{module.__name__}.{function_name} does not use the typed dependency object pilot")


def main() -> int:
    assert_activation_service()
    assert_evidence_service()
    assert_projection_service()
    assert_agcp_service()
    assert_dependency_object_pilot()
    print(json.dumps({"ok": True, "v6_p1_service_boundaries": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
