from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

METHOD_POLICY_VERSION = "shujuan-method-policy-v11.3"


@dataclass(frozen=True)
class MethodContract:
    method: str
    skill_name: str
    routes: tuple[str, ...]
    write_posture: str
    allowed_roles: tuple[str, ...]
    first_surface_kind: str
    completion_rule: str
    required_output_fields: tuple[str, ...]
    allowed_transitions: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        return asdict(self)


METHOD_CONTRACTS: dict[str, MethodContract] = {
    "harness": MethodContract(
        method="harness",
        skill_name="shujuan-harness",
        routes=("Recover",),
        write_posture="read_first",
        allowed_roles=("controller_agent", "worker_agent"),
        first_surface_kind="route_contract",
        completion_rule="sovereignty, relation, method, endpoint, role/mode, and next safe action are selected.",
        required_output_fields=("primary_method", "endpoint", "role", "mode", "first_surface", "safe_next_action"),
        allowed_transitions=("Recall", "Execute", "Delegate", "Close", "No Governance"),
    ),
    "recall": MethodContract(
        method="recall",
        skill_name="shujuan-recall",
        routes=("Recall",),
        write_posture="read_only",
        allowed_roles=("controller_agent", "worker_agent", "reviewer_agent", "researcher_agent", "writer_agent"),
        first_surface_kind="report",
        completion_rule="answer claims with anchors, contradictions, unsearched frontier, and stop reason.",
        required_output_fields=("claim_ledger", "anchors", "contradictions", "unsearched_frontier", "stop_reason"),
        allowed_transitions=("Delegate", "Execute", "Close", "No Governance"),
    ),
    "capture": MethodContract(
        method="capture",
        skill_name="shujuan-capture",
        routes=("Capture",),
        write_posture="capture_only",
        allowed_roles=("controller_agent",),
        first_surface_kind="source_material",
        completion_rule="source material has provenance and no task/check/closure claim is inferred.",
        required_output_fields=("source_ref", "provenance", "capture_boundary"),
        allowed_transitions=("Recall", "Execute", "Delegate", "No Governance"),
    ),
    "execute": MethodContract(
        method="execute",
        skill_name="shujuan-execute",
        routes=("Execute",),
        write_posture="writeful_after_runtime_gate",
        allowed_roles=("controller_agent", "worker_agent"),
        first_surface_kind="endpoint_active_surface",
        completion_rule="scoped implementation returns changed files, tests, risks, and no closure claim.",
        required_output_fields=("changed_files", "tests_run", "risks_or_blockers", "no_closure_attestation"),
        allowed_transitions=("Delegate", "Close", "Recall", "No Governance"),
    ),
    "delegate": MethodContract(
        method="delegate",
        skill_name="shujuan-delegate",
        routes=("Delegate",),
        write_posture="material_only",
        allowed_roles=("controller_agent", "worker_agent", "reviewer_agent", "researcher_agent", "writer_agent"),
        first_surface_kind="review_bundle",
        completion_rule="packet or return material states authority boundary and controller adoption requirement.",
        required_output_fields=("role", "scope", "authority_boundary", "expected_return", "material_boundary"),
        allowed_transitions=("Recall", "Execute", "Close", "No Governance"),
    ),
    "close": MethodContract(
        method="close",
        skill_name="shujuan-close",
        routes=("Close",),
        write_posture="controller_closeout_only",
        allowed_roles=("controller_agent",),
        first_surface_kind="closeout_inputs",
        completion_rule="matching evidence is adopted, endpoint refreshed, evidence verified, and strict doctor run by controller.",
        required_output_fields=("endpoint", "task_id", "check_id", "expected_evidence_type", "current_matching_evidence_ref"),
        allowed_transitions=("Recall", "Delegate", "No Governance"),
    ),
    "evolve": MethodContract(
        method="evolve",
        skill_name="shujuan-evolve",
        routes=("Execute", "Recover"),
        write_posture="repo_change_with_history_guard",
        allowed_roles=("controller_agent", "worker_agent", "reviewer_agent"),
        first_surface_kind="source_plan_and_impact",
        completion_rule="policy/schema/skill/package changes preserve non-goals and produce repeatable verification material.",
        required_output_fields=("pre_edit_impact_analysis", "changed_files", "tests_run", "task_chain_coverage", "risks_or_blockers"),
        allowed_transitions=("Delegate", "Close", "Recall", "No Governance"),
    ),
}

ROUTE_TO_METHOD = {
    "No Governance": "harness",
    "Recover": "harness",
    "Recall": "recall",
    "Capture": "capture",
    "Execute": "execute",
    "Delegate": "delegate",
    "Close": "close",
}


def contract_for_route(route: str, *, intent: str = "") -> MethodContract:
    lowered = intent.lower()
    evolve_tokens = (
        "shujuan",
        "skill",
        "method",
        "agents.md",
        "installer",
        "hook",
        "package",
        "route guard",
        "route behavior",
        "role policy",
        "schema",
        "sqlite",
        "postgresql",
        "db table",
        "database table",
        "fact-plane",
        "fact plane",
        "assets",
        ".agents",
    )
    if route in {"Execute", "Recover"} and any(token in lowered for token in evolve_tokens):
        return METHOD_CONTRACTS["evolve"]
    return METHOD_CONTRACTS[ROUTE_TO_METHOD.get(route, "execute")]


def method_payload(route: str, *, intent: str = "") -> dict[str, Any]:
    contract = contract_for_route(route, intent=intent)
    return {
        "recommended_skill": contract.skill_name,
        "method_version": METHOD_POLICY_VERSION,
        "method_contract": contract.payload(),
    }
