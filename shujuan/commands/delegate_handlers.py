from __future__ import annotations

import argparse
import json
import re
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..services.command_effects import delegate_packet_effects

DelegateHandler = Callable[[argparse.Namespace], int]




DELEGATE_DEPENDENCY_KEYS = (
    "active_audit_findings_for_endpoint",
    "active_run_path",
    "connect",
    "current_head",
    "diagnostics_payload",
    "endpoint_report_payload",
    "inspect_schema",
    "is_internal_ignored_path",
    "list_untracked_files",
    "new_id",
    "project_report_payload",
    "render_endpoint_report_markdown",
    "render_project_report_markdown",
    "run_git",
    "print_json",
    "read_arg_or_stdin",
    "sha256_text",
    "resolve_database_config",
    "write_artifact_text",
)


def _delegate_dependencies(source: Mapping[str, Any]) -> dict[str, Any]:
    keys = tuple(key for key in DELEGATE_DEPENDENCY_KEYS if key)
    missing = [key for key in keys if key not in source]
    if missing:
        raise RuntimeError(f"delegate handler boundary is missing: {', '.join(missing)}")
    return {key: source[key] for key in keys}


DELEGATE_HANDLER_NAMES = {
    "plan": "cmd_delegate_plan",
    "packet": "cmd_delegate_packet",
    "import": "cmd_delegate_import",
    "ownership": "cmd_delegate_ownership",
    "review": "cmd_delegate_review",
    "verify": "cmd_delegate_verify",
    "status": "cmd_delegate_status",
    "capsule": "cmd_delegate_capsule",
    "controller_status": "cmd_delegate_controller_status",
    "controller_close": "cmd_delegate_controller_close",
}


DCCP_MINIMAL_TABLES = [
    "delegation_lanes",
    "delegation_packets",
    "worker_ownership_snapshots",
]


DCCP_EXISTING_PRIMITIVES = {
    "active_surface": ["report endpoint --active-only", "work focus"],
    "review": ["review start", "review submit"],
    "return_import": ["audit import-agent-output", "evidence artifact", "evidence test-result"],
    "provider_impact": ["provider contract", "provider import-json"],
    "closeout": ["work close", "exec stop", "endpoint doctor", "evidence verify"],
}


DELEGATE_FORBIDDEN_ACTIONS = [
    "current_project_governance_write",
    "endpoint refresh",
    "exec stop",
    "acceptance close",
    "evidence --close-check",
    "check/task closure",
    "close task/check",
    "scope downgrade",
    "governance DB write without controller authority",
]


DELEGATE_RETURN_FIELDS = [
    "changed_files",
    "owned_hunks_or_paths",
    "pre_existing_dirty_paths",
    "ownership_manifest",
    "ownership_manifest_schema",
    "ownership_lanes",
    "ownership_manifest_material_only",
    "manifest_is_closure_evidence",
    "ownership_surface_guidance",
    "inspected_only_paths",
    "fixture_writes",
    "tests",
    "blocked_checks",
    "unresolved_risks",
    "assumptions",
    "provider_outputs",
    "no_closure_attestation",
    "tests_run",
    "check_status",
    "identity_boundary",
]


OWNERSHIP_MANIFEST_LANES = [
    "worker_owned",
    "pre_existing_dirty",
    "provider_runtime",
    "observed_only",
    "not_owned",
    "deleted_obsolete",
    "fallback",
    "out_of_scope",
]


OWNERSHIP_MANIFEST_REQUIRED_FIELDS = [
    "lane",
    "path",
    "hunk_id",
    "hunk_header",
    "range",
    "hash",
    "claimed_owner",
    "pre_existing_dirty",
    "source",
    "reason",
    "promotion_or_reopen_rule",
]


OWNERSHIP_MANIFEST_LANE_DEFINITIONS = {
    "worker_owned": "Path or hunk intentionally changed by the delegated worker for the scoped task/check.",
    "pre_existing_dirty": "Dirty before worker handoff; not worker-owned unless a later hunk-level row explicitly separates new work.",
    "provider_runtime": "Provider or GitNexus runtime/cache output; material only and not implementation ownership.",
    "observed_only": "Read or inspected for context with no ownership claim.",
    "not_owned": "Known changed material that belongs to another actor or prior pass.",
    "deleted_obsolete": "Deletion material; must remain explicit and requires deletion approval under repo policy.",
    "fallback": "Path-level fallback when hunk boundaries are unavailable; does not claim pre-existing dirty hunks.",
    "out_of_scope": "Material explicitly outside the delegated task/check scope.",
}


OWNERSHIP_MANIFEST_FALLBACK_RULES = [
    "Prefer hunk-level rows with header/range/hash when available.",
    "Use fallback only when hunk boundaries are unavailable or misleading.",
    "Fallback rows must name the owned subject and why hunk-level attribution is unavailable.",
    "Fallback rows do not claim ownership of pre-existing dirty hunks.",
]


OWNERSHIP_MANIFEST_PROMOTION_REOPEN_RULES = {
    "pre_existing_dirty": "Controller reviews dirty baselines before importing material or reopening ownership questions.",
    "provider_runtime": "Promote only as provider material; never as worker-owned implementation or closure evidence.",
    "observed_only": "Promote only if the controller creates or assigns new scoped work.",
    "not_owned": "Reopen only if ownership is disputed and the controller requests a new manifest row.",
    "deleted_obsolete": "Promote only with explicit deletion approval and matching controller evidence capture.",
    "fallback": "Re-expand into hunk-level rows before treating the path as precise closure material.",
    "out_of_scope": "Reopen only through a controller-approved scope change.",
    "worker_owned": "Controller may use as material for change review, but closure still requires evidence/check/task primitives.",
}


OWNERSHIP_MANIFEST_MATERIAL_BOUNDARY = {
    "material_only": True,
    "manifest_is_closure_evidence": False,
    "can_close_checks": False,
    "can_close_tasks": False,
    "controller_conversion_required": True,
    "note": "Ownership/hunk manifests are review material until the controller imports, verifies, and records matching evidence.",
}


DEFAULT_OWNERSHIP_SURFACE_GUIDANCE = {
    "default_command": (
        "python -m shujuan delegate ownership --endpoint <endpoint> "
        "--pre-existing-dirty-path <path> --claimed-path <path>"
    ),
    "return_fields": [
        "changed_files",
        "owned_hunks_or_paths",
        "pre_existing_dirty_paths",
        "provider_runtime_paths",
        "observed_only_paths",
        "not_owned_paths",
        "deleted_obsolete_paths",
        "fallback_paths",
        "out_of_scope_paths",
        "ownership_manifest",
        "ownership_manifest_schema",
        "manifest_is_closure_evidence",
    ],
    "lane_rule": "Return every ownership lane explicitly; do not claim pre-existing dirty or provider runtime material as worker-owned.",
    "material_boundary": "Ownership output is material only and cannot close checks/tasks without controller evidence conversion.",
}


def ownership_manifest_schema() -> dict[str, Any]:
    return {
        "schema_name": "shujuan.delegate_ownership_manifest.minimum.v1",
        "required_lanes": list(OWNERSHIP_MANIFEST_LANES),
        "required_fields": list(OWNERSHIP_MANIFEST_REQUIRED_FIELDS),
        "lane_definitions": dict(OWNERSHIP_MANIFEST_LANE_DEFINITIONS),
        "allowed_path_level_fallback": list(OWNERSHIP_MANIFEST_FALLBACK_RULES),
        "promotion_reopen_rules": dict(OWNERSHIP_MANIFEST_PROMOTION_REOPEN_RULES),
        "material_boundary": dict(OWNERSHIP_MANIFEST_MATERIAL_BOUNDARY),
    }


def _unique_strings(items: list[str] | None) -> list[str]:
    return list(dict.fromkeys(item for item in (items or []) if item))


def ownership_manifest_from_lanes(
    *,
    worker_owned: list[str] | None = None,
    pre_existing_dirty: list[str] | None = None,
    provider_runtime: list[str] | None = None,
    observed_only: list[str] | None = None,
    not_owned: list[str] | None = None,
    deleted_obsolete: list[str] | None = None,
    fallback: list[str] | None = None,
    out_of_scope: list[str] | None = None,
) -> dict[str, list[str]]:
    lanes = {
        "worker_owned": _unique_strings(worker_owned),
        "pre_existing_dirty": _unique_strings(pre_existing_dirty),
        "provider_runtime": _unique_strings(provider_runtime),
        "observed_only": _unique_strings(observed_only),
        "not_owned": _unique_strings(not_owned),
        "deleted_obsolete": _unique_strings(deleted_obsolete),
        "fallback": _unique_strings(fallback),
        "out_of_scope": _unique_strings(out_of_scope),
    }
    return {lane: lanes[lane] for lane in OWNERSHIP_MANIFEST_LANES}


DELEGATE_ROLE_DEFAULTS = {
    "worker": {
        "allowed_scope": ["edit scoped files", "run focused tests", "report changed files and risks"],
        "must_read": ["AGENTS.md", ".agents/skills/shujuan-execute/SKILL.md", ".agents/skills/shujuan-delegate/SKILL.md", ".ai/codegraph/next-action.json when produced"],
        "questions": ["What changed?", "Which focused tests passed or failed?", "What risks remain?"],
        "db_write_authority": False,
        "closeout_authority": False,
    },
    "reviewer": {
        "allowed_scope": ["read source material", "inspect diff/test output", "return read-only findings"],
        "must_read": ["AGENTS.md", ".agents/skills/shujuan-delegate/SKILL.md", ".agents/skills/shujuan-recall/SKILL.md", "active endpoint report or packet", "changed files", "test output supplied by controller"],
        "questions": ["Does the diff satisfy the scoped check?", "Are tests relevant?", "Is any finding active and source-backed?"],
        "db_write_authority": False,
        "closeout_authority": False,
    },
    "writer": {
        "allowed_scope": ["draft prose", "summarize supplied material", "return text without governance capture"],
        "must_read": ["AGENTS.md", ".agents/skills/shujuan-recall/SKILL.md", "writer packet", "source excerpts supplied by controller"],
        "questions": ["Is the text faithful to source material?", "Does it avoid closure claims?", "Does it remain outside governance by default?"],
        "db_write_authority": False,
        "closeout_authority": False,
    },
    "controller": {
        "allowed_scope": ["coordinate packets", "import evidence through existing primitives", "run closeout gates"],
        "must_read": ["AGENTS.md", ".agents/skills/shujuan-close/SKILL.md", ".agents/skills/shujuan-delegate/SKILL.md", "endpoint active report", "evidence verify output", "endpoint doctor output"],
        "questions": ["Is returned material imported?", "Are closeout gates satisfied?", "Which checks remain open?"],
        "db_write_authority": True,
        "closeout_authority": True,
    },
    "researcher": {
        "allowed_scope": ["collect source-backed facts", "separate facts from inferences", "return uncertainty"],
        "must_read": ["AGENTS.md", ".agents/skills/shujuan-recall/SKILL.md", "research question", "source constraints", "citation requirements"],
        "questions": ["Which facts are directly sourced?", "What remains uncertain?", "What next action is suggested?"],
        "db_write_authority": False,
        "closeout_authority": False,
    },
    "provider": {
        "allowed_scope": ["run impact-only provider analysis", "return provider facts with confidence"],
        "must_read": ["AGENTS.md", ".agents/skills/shujuan-delegate/SKILL.md", "provider contract", ".ai/codegraph/next-action.json when produced"],
        "questions": ["Which provider facts are observed?", "Which are inferences?", "What confidence applies?"],
        "db_write_authority": False,
        "closeout_authority": False,
    },
}


DELEGATE_RETURN_TEMPLATE = {
    "changed_files": [],
    "owned_hunks_or_paths": [],
    "pre_existing_dirty_paths": [],
    "ownership_lanes": list(OWNERSHIP_MANIFEST_LANES),
    "ownership_manifest_schema": ownership_manifest_schema(),
    "ownership_manifest": ownership_manifest_from_lanes(),
    "ownership_manifest_material_only": True,
    "manifest_is_closure_evidence": False,
    "ownership_manifest_boundary": dict(OWNERSHIP_MANIFEST_MATERIAL_BOUNDARY),
    "ownership_surface_guidance": dict(DEFAULT_OWNERSHIP_SURFACE_GUIDANCE),
    "inspected_only_paths": [],
    "fixture_writes": [],
    "tests": [],
    "blocked_checks": [],
    "unresolved_risks": [],
    "assumptions": [],
    "no_closure_attestation": "I did not close checks/tasks, refresh endpoints, stop controller runs, or write current-project governance DB facts.",
    "reviewed_claim": "",
    "owned_hunks_or_inspected_evidence": [],
    "factual_anchors": [],
    "tests_or_review_results": [],
    "provider_output_if_used": [],
    "identity_boundary": "I acted only as the delegated role named in this packet.",
}


def delegate_return_capsule(
    *,
    owned_hunks_or_paths: list[str],
    pre_existing_dirty_paths: list[str],
    inspected_only_paths: list[str],
    fixture_writes: list[str],
    blocked_checks: list[str],
    unresolved_risks: list[str],
    assumptions: list[str],
    provider_outputs: list[str],
    provider_runtime_paths: list[str] | None = None,
    observed_only_paths: list[str] | None = None,
    not_owned_paths: list[str] | None = None,
    deleted_obsolete_paths: list[str] | None = None,
    fallback_paths: list[str] | None = None,
    out_of_scope_paths: list[str] | None = None,
) -> dict[str, Any]:
    observed_paths = _unique_strings([*inspected_only_paths, *(observed_only_paths or [])])
    ownership_manifest = ownership_manifest_from_lanes(
        worker_owned=owned_hunks_or_paths,
        pre_existing_dirty=pre_existing_dirty_paths,
        provider_runtime=provider_runtime_paths,
        observed_only=observed_paths,
        not_owned=not_owned_paths,
        deleted_obsolete=deleted_obsolete_paths,
        fallback=fallback_paths,
        out_of_scope=out_of_scope_paths,
    )
    check_status = {
        "closed_by_delegate": False,
        "blocked_checks": blocked_checks,
        "note": "Delegate reports material only; controller decides and records check/task closure.",
    }
    return {
        "required_fields": DELEGATE_RETURN_FIELDS,
        "changed_files": [],
        "owned_hunks_or_paths": owned_hunks_or_paths,
        "pre_existing_dirty_paths": pre_existing_dirty_paths,
        "provider_runtime_paths": _unique_strings(provider_runtime_paths),
        "observed_only_paths": observed_paths,
        "not_owned_paths": _unique_strings(not_owned_paths),
        "deleted_obsolete_paths": _unique_strings(deleted_obsolete_paths),
        "fallback_paths": _unique_strings(fallback_paths),
        "out_of_scope_paths": _unique_strings(out_of_scope_paths),
        "ownership_lanes": list(OWNERSHIP_MANIFEST_LANES),
        "ownership_manifest_schema": ownership_manifest_schema(),
        "ownership_manifest": ownership_manifest,
        "ownership_manifest_material_only": True,
        "manifest_is_closure_evidence": False,
        "ownership_manifest_boundary": dict(OWNERSHIP_MANIFEST_MATERIAL_BOUNDARY),
        "ownership_surface_guidance": dict(DEFAULT_OWNERSHIP_SURFACE_GUIDANCE),
        "inspected_only_paths": inspected_only_paths,
        "fixture_writes": fixture_writes,
        "tests": [],
        "tests_run": [],
        "blocked_checks": blocked_checks,
        "unresolved_risks": unresolved_risks,
        "assumptions": assumptions,
        "provider_outputs": provider_outputs,
        "check_status": check_status,
        "identity_boundary": DELEGATE_RETURN_TEMPLATE["identity_boundary"],
        "no_closure_attestation": DELEGATE_RETURN_TEMPLATE["no_closure_attestation"],
    }


DELEGATE_PROVIDER_GUIDANCE = {
    "provider_intent": "impact_only",
    "default_for_worker": True,
    "material_only": True,
    "allowed_classifications": ["provider_fact", "provider_hypothesis"],
    "cannot_close_checks": True,
    "cannot_close_tasks": True,
    "output_contract": {
        "seed": "What file/symbol/input prompted provider or GitNexus analysis.",
        "question": "The impact or classification question asked.",
        "boundary": "Scope limits and forbidden closure authority.",
        "output_classification": "provider_fact or provider_hypothesis; material only.",
    },
    "allowed_commands": [
        "gitnexus impact <symbol> --direction upstream --repo .",
        "gitnexus detect-changes --scope all --repo .",
        "gitnexus query <question> --repo .",
    ],
    "forbidden_without_controller_authorization": ["provider-driven closure", "treat provider completion as binding"],
    "classification": "provider_fact/provider_hypothesis only; not closure evidence",
}


DELEGATE_PACKET_MATERIAL_CLASSIFICATION = "delegate_packet_preview_material"
DELEGATE_PACKET_GOVERNANCE_RECORD_TABLE = "delegation_packets"
DELEGATE_PACKET_NEXT_GOVERNANCE_RECORD_LABELS = [
    "controller_import_returned_material",
    "controller_exec_stop_change_set",
    "controller_evidence_test_result_or_artifact",
    "controller_acceptance_check_closure",
    "controller_task_closure_after_checks",
    "controller_endpoint_refresh_and_closeout_diagnostics",
]


def delegate_packet_truth_labels(*, artifact_saved: bool, artifact_ref: str | None) -> dict[str, Any]:
    return {
        "artifact_primary": True,
        "packet_body_source": "artifact" if artifact_saved else "cli_preview",
        "governance_db_row_written": False,
        "delegation_tables": "dormant_not_primary_storage",
        "db_persist_table": None,
        "delegation_packets_table_status": "dormant_not_written",
        "controller_import_required": True,
        "packet_material_classification": DELEGATE_PACKET_MATERIAL_CLASSIFICATION,
        "material_classification": DELEGATE_PACKET_MATERIAL_CLASSIFICATION,
        "artifact_saved": artifact_saved,
        "artifact_ref": artifact_ref,
        "artifact_is_governance_record": False,
        "governance_record_created": False,
        "governance_record_table": DELEGATE_PACKET_GOVERNANCE_RECORD_TABLE,
        "governance_record_persistence": "not_created_by_delegate_packet",
        "next_required_governance_record_labels": list(DELEGATE_PACKET_NEXT_GOVERNANCE_RECORD_LABELS),
        "next_required_governance_records": [
            {
                "label": "controller_import_returned_material",
                "owner": "controller_agent",
                "required_before_closeout": True,
                "note": "Import returned worker/reviewer/provider material before using it for any evidence decision.",
            },
            {
                "label": "controller_exec_stop_change_set",
                "owner": "controller_agent",
                "required_before_closeout": True,
                "note": "Capture the local repository change_set through the controller run.",
            },
            {
                "label": "controller_evidence_test_result_or_artifact",
                "owner": "controller_agent",
                "required_before_closeout": True,
                "note": "Record matching evidence for the acceptance check contract.",
            },
            {
                "label": "controller_acceptance_check_closure",
                "owner": "controller_agent",
                "required_before_closeout": True,
                "note": "Close acceptance checks only after matching evidence exists and is verified.",
            },
            {
                "label": "controller_task_closure_after_checks",
                "owner": "controller_agent",
                "required_before_closeout": True,
                "note": "Close task only after scoped acceptance checks are closed.",
            },
            {
                "label": "controller_endpoint_refresh_and_closeout_diagnostics",
                "owner": "controller_agent",
                "required_before_closeout": True,
                "note": "Refresh endpoint projection and run closeout diagnostics from controller authority.",
            },
        ],
        "controller_next_action": "Controller must import returned material, capture change_set/evidence, verify, close checks/tasks, and refresh/diagnose endpoint explicitly.",
    }


COLLABORATION_MODES = {
    "solo-light": {
        "agents": ["controller"],
        "default_role": "controller",
        "verification_policy": "focused smoke or change_set evidence for a narrow controller-owned edit",
        "closeout_policy": "controller may close only through existing evidence/closeout primitives",
        "review_recommendation": "none_by_default",
        "packet_behavior": "controller self-packet is optional; no delegated worker authority is created",
        "return_classification": "controller_change_material",
        "slices": ["scope", "implement", "verify", "controller_closeout"],
        "batch_size": 1,
    },
    "delegated-light-fix": {
        "agents": ["controller", "worker"],
        "default_role": "worker",
        "verification_policy": "worker reports focused tests; controller independently verifies before closeout",
        "closeout_policy": "controller_only",
        "review_recommendation": "optional_unless_high_risk",
        "packet_behavior": "one worker packet with forbidden closeout actions and required return fields",
        "return_classification": "worker_return_material",
        "slices": ["controller_scope", "worker_patch", "controller_verify", "controller_closeout"],
        "batch_size": 1,
    },
    "delegated-standard-slice": {
        "agents": ["controller", "worker", "reviewer"],
        "default_role": "worker",
        "verification_policy": "targeted tests plus controller verification; reviewer suggested for shared behavior",
        "closeout_policy": "controller_only_after_import_and_verification",
        "review_recommendation": "suggested",
        "packet_behavior": "worker packet plus optional read-only reviewer packet after return",
        "return_classification": "worker_return_then_review_material",
        "slices": ["scope_slice", "worker_patch", "import_return", "review_if_risky", "controller_verify"],
        "batch_size": 2,
    },
    "delegated-full-critical": {
        "agents": ["controller", "worker", "reviewer", "researcher"],
        "default_role": "worker",
        "verification_policy": "targeted tests, impact review, evidence verify, and strict doctor before closeout",
        "closeout_policy": "controller_only_after_mandatory_review",
        "review_recommendation": "mandatory",
        "packet_behavior": "worker packet and read-only reviewer/researcher packets are expected",
        "return_classification": "critical_return_material",
        "slices": ["source_lock", "impact_research", "worker_patch", "mandatory_review", "strict_controller_verify"],
        "batch_size": 2,
    },
    "audit-only": {
        "agents": ["controller", "reviewer"],
        "default_role": "reviewer",
        "verification_policy": "read-only audit output; controller decides whether to import as audit material",
        "closeout_policy": "no_delegate_closeout",
        "review_recommendation": "audit_is_the_review",
        "packet_behavior": "read-only reviewer packet; no implementation packet",
        "return_classification": "audit_material",
        "slices": ["collect_sources", "read_only_audit", "controller_triage"],
        "batch_size": 1,
    },
    "research-only": {
        "agents": ["controller", "researcher"],
        "default_role": "researcher",
        "verification_policy": "source-backed fact/inference separation; no closure evidence by itself",
        "closeout_policy": "no_delegate_closeout",
        "review_recommendation": "none_by_default",
        "packet_behavior": "research packet returns facts, sources, uncertainty, and suggested next action",
        "return_classification": "research_material",
        "slices": ["question", "source_research", "controller_triage"],
        "batch_size": 1,
    },
    "writing-no-governance": {
        "agents": ["writer"],
        "default_role": "writer",
        "verification_policy": "no governance capture; user/controller may separately decide whether to import",
        "closeout_policy": "no_governance_no_closeout",
        "review_recommendation": "none_by_default",
        "packet_behavior": "writer packet is outside governance by default; no DB writes or capture claim",
        "return_classification": "plain_text_handoff",
        "slices": ["draft", "return_text"],
        "batch_size": 1,
    },
}


DELEGATE_LIFECYCLE_STATES = [
    "open",
    "packeted",
    "returned",
    "imported",
    "verified",
    "reviewed",
    "closed_by_controller",
]


DELEGATE_LIFECYCLE_TRANSITIONS = {
    "open": {"packeted"},
    "packeted": {"returned"},
    "returned": {"imported"},
    "imported": {"verified", "reviewed"},
    "verified": {"reviewed", "closed_by_controller"},
    "reviewed": {"verified", "closed_by_controller"},
    "closed_by_controller": set(),
}


DELEGATE_IMPORT_CLASSIFICATIONS = {
    "summary-only": {
        "label": "summary-only",
        "report_column": "narrative",
        "narrative": True,
        "active_obligation": False,
        "closure_material": False,
        "evidence_candidate": False,
        "provider_hypothesis": False,
        "invalid": False,
        "default_lifecycle": "non_active",
        "next_action": "Controller may import as audit narrative or leave as handoff context.",
        "route": "python -m shujuan audit import-agent-output --classification summary --endpoint <endpoint> --source-node <source>",
    },
    "candidate-finding": {
        "label": "candidate-finding",
        "report_column": "narrative",
        "narrative": True,
        "active_obligation": False,
        "closure_material": False,
        "evidence_candidate": False,
        "provider_hypothesis": False,
        "invalid": False,
        "default_lifecycle": "candidate_non_active",
        "next_action": "Controller triages before creating any active audit finding or task.",
        "route": "python -m shujuan audit import-agent-output --classification summary --endpoint <endpoint> --source-node <source>",
    },
    "actionable": {
        "label": "actionable",
        "report_column": "active",
        "narrative": False,
        "active_obligation": True,
        "closure_material": False,
        "evidence_candidate": False,
        "provider_hypothesis": False,
        "invalid": False,
        "default_lifecycle": "active",
        "next_action": "Controller may import as active audit/action material, then decide the owning work item.",
        "route": "python -m shujuan audit import-agent-output --classification actionable --endpoint <endpoint> --source-node <source>",
    },
    "needs-user-decision": {
        "label": "needs-user-decision",
        "report_column": "active",
        "narrative": False,
        "active_obligation": True,
        "closure_material": False,
        "evidence_candidate": False,
        "provider_hypothesis": False,
        "invalid": False,
        "default_lifecycle": "active_needs_user_decision",
        "next_action": "Controller asks for or records the user decision before closeout.",
        "route": "python -m shujuan audit import-agent-output --classification needs_user_decision --endpoint <endpoint> --source-node <source>",
    },
    "closure-material": {
        "label": "closure-material",
        "report_column": "closure_material",
        "narrative": False,
        "active_obligation": False,
        "closure_material": True,
        "evidence_candidate": True,
        "provider_hypothesis": False,
        "invalid": False,
        "default_lifecycle": "non_closing_material",
        "next_action": "Controller must convert through evidence primitives and verify before any check/task closure.",
        "route": "python -m shujuan evidence artifact --path <file> --from-node <source>",
    },
    "provider-hypothesis": {
        "label": "provider-hypothesis",
        "report_column": "narrative",
        "narrative": True,
        "active_obligation": False,
        "closure_material": False,
        "evidence_candidate": False,
        "provider_hypothesis": True,
        "invalid": False,
        "default_lifecycle": "provider_non_active",
        "next_action": "Controller may record provider facts/hypotheses, but they are not closure evidence.",
        "route": "python -m shujuan provider import-json --endpoint <endpoint> --path <provider-output.json>",
    },
    "invalid": {
        "label": "invalid",
        "report_column": "narrative",
        "narrative": True,
        "active_obligation": False,
        "closure_material": False,
        "evidence_candidate": False,
        "provider_hypothesis": False,
        "invalid": True,
        "default_lifecycle": "invalid_non_active",
        "next_action": "Controller rejects or asks the delegate to resubmit a valid return packet.",
        "route": None,
    },
}


DELEGATE_IMPORT_CLASSIFICATION_ALIASES = {
    "summary": "summary-only",
    "summary_only": "summary-only",
    "summary-only": "summary-only",
    "candidate": "candidate-finding",
    "candidate_finding": "candidate-finding",
    "candidate-finding": "candidate-finding",
    "finding": "candidate-finding",
    "action": "actionable",
    "actionable": "actionable",
    "needs_user_decision": "needs-user-decision",
    "needs-user-decision": "needs-user-decision",
    "user-decision": "needs-user-decision",
    "closure": "closure-material",
    "closure_material": "closure-material",
    "closure-material": "closure-material",
    "evidence_candidate": "closure-material",
    "evidence-candidate": "closure-material",
    "provider_fact": "provider-hypothesis",
    "provider_hypothesis": "provider-hypothesis",
    "provider-hypothesis": "provider-hypothesis",
    "invalid": "invalid",
}


DELEGATE_REVIEW_HIGH_RISK_TRIGGERS = {
    "full-scope": "Full scope or broad cross-cutting implementation.",
    "p0-scope": "P0-critical acceptance or release-blocking behavior.",
    "p1-scope": "P1 follow-on with broader product implications.",
    "db-runtime": "Database, migration, runtime, or persistence behavior.",
    "evidence-closure": "Evidence, closure, acceptance, task, or endpoint closeout behavior.",
    "named-technology-artifact": "Named technology, artifact, provider, or external contract.",
    "ui-visual-availability": "UI/visual surface where browser-viewable verification is available.",
    "broad-closure": "Broad closure or endpoint-level completion claim.",
    "provider-boundary": "Provider boundary, provider fact, or external-provider authority.",
    "subagent-output-as-closure": "Subagent output proposed as closure material or evidence.",
}


DELEGATE_REVIEW_TRIGGER_ALIASES = {
    "full": "full-scope",
    "full-scope": "full-scope",
    "p0": "p0-scope",
    "p0-scope": "p0-scope",
    "p1": "p1-scope",
    "p1-scope": "p1-scope",
    "db": "db-runtime",
    "database": "db-runtime",
    "runtime": "db-runtime",
    "db-runtime": "db-runtime",
    "evidence": "evidence-closure",
    "closure": "evidence-closure",
    "evidence-closure": "evidence-closure",
    "named-technology": "named-technology-artifact",
    "named-artifact": "named-technology-artifact",
    "named-technology-artifact": "named-technology-artifact",
    "ui": "ui-visual-availability",
    "visual": "ui-visual-availability",
    "ui-visual-availability": "ui-visual-availability",
    "broad": "broad-closure",
    "broad-closure": "broad-closure",
    "provider": "provider-boundary",
    "provider-boundary": "provider-boundary",
    "subagent-output": "subagent-output-as-closure",
    "subagent-output-as-closure": "subagent-output-as-closure",
}


def collaboration_mode_policy(mode: str | None) -> dict[str, Any]:
    selected = mode or "delegated-light-fix"
    if selected not in COLLABORATION_MODES:
        allowed = ", ".join(sorted(COLLABORATION_MODES))
        raise SystemExit(f"collaboration mode must be one of: {allowed}")
    policy = dict(COLLABORATION_MODES[selected])
    policy["mode"] = selected
    return policy


def delegate_plan_slices(policy: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    slices = []
    for index, name in enumerate(policy["slices"], start=1):
        if "review" in name or "audit" in name:
            owner = "reviewer" if "reviewer" in policy["agents"] else policy["agents"][-1]
        elif "research" in name or "impact" in name:
            owner = "researcher" if "researcher" in policy["agents"] else policy["agents"][-1]
        elif "worker" in name or "patch" in name or "implement" in name:
            owner = "worker" if "worker" in policy["agents"] else policy["agents"][0]
        elif "draft" in name or "text" in name:
            owner = "writer" if "writer" in policy["agents"] else policy["agents"][-1]
        else:
            owner = "controller" if "controller" in policy["agents"] else policy["agents"][0]
        slices.append(
            {
                "id": f"slice_{index:02d}",
                "name": name,
                "owner_role": owner,
                "endpoint": getattr(args, "endpoint", None),
                "task": getattr(args, "task", None),
                "check": getattr(args, "check", None),
                "closeout_allowed": owner == "controller" and "closeout" in name,
            }
        )
    return slices


def delegate_plan_batches(slices: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    batch_size = max(1, int(policy.get("batch_size") or 1))
    batches = []
    for index in range(0, len(slices), batch_size):
        batch_slices = slices[index : index + batch_size]
        batches.append(
            {
                "id": f"batch_{len(batches) + 1:02d}",
                "slice_ids": [item["id"] for item in batch_slices],
                "owner_roles": list(dict.fromkeys(str(item["owner_role"]) for item in batch_slices)),
                "requires_controller_import": any(item["owner_role"] != "controller" for item in batch_slices),
            }
        )
    return batches


def delegate_review_suggestion(policy: dict[str, Any]) -> dict[str, Any]:
    recommendation = str(policy["review_recommendation"])
    return {
        "recommendation": recommendation,
        "required": recommendation == "mandatory",
        "reason": {
            "none_by_default": "Mode is low-risk or non-governance by default.",
            "optional_unless_high_risk": "Review is optional unless implementation touches high-risk/shared behavior.",
            "suggested": "Standard delegated slices benefit from independent read-only review.",
            "mandatory": "Critical delegated work needs reviewer material before controller closeout.",
            "audit_is_the_review": "Audit-only mode is itself read-only review.",
        }.get(recommendation, "Mode policy supplies the review recommendation."),
        "route": "python -m shujuan review start --endpoint <endpoint>",
    }


def normalize_delegate_review_triggers(triggers: list[str] | None) -> list[str]:
    normalized: set[str] = set()
    for trigger in triggers or []:
        key = trigger.strip().lower().replace("_", "-").replace(" ", "-")
        if not key:
            continue
        value = DELEGATE_REVIEW_TRIGGER_ALIASES.get(key)
        if not value:
            allowed = ", ".join(sorted(DELEGATE_REVIEW_HIGH_RISK_TRIGGERS))
            raise SystemExit(f"delegate review risk trigger must be one of: {allowed}")
        normalized.add(value)
    return sorted(normalized)


def delegate_review_trigger_matrix(policy: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    triggers = normalize_delegate_review_triggers(getattr(args, "risk_trigger", []) or [])
    mode = str(policy["mode"])
    base = delegate_review_suggestion(policy)
    high_risk = bool(triggers) or mode == "delegated-full-critical"
    low_risk_fast_path = bool(getattr(args, "fast_light_fix", False)) and mode == "delegated-light-fix"
    no_governance_writing = mode == "writing-no-governance"
    required = bool(base["required"] or high_risk)
    suggested = required or base["recommendation"] in {"suggested", "audit_is_the_review"}
    if (low_risk_fast_path or no_governance_writing) and not high_risk and not base["required"]:
        suggested = False
        required = False
    rows = [
        {
            "trigger": key,
            "description": description,
            "present": key in triggers,
            "requires_reviewer": key in triggers,
        }
        for key, description in DELEGATE_REVIEW_HIGH_RISK_TRIGGERS.items()
    ]
    reasons = []
    if triggers:
        reasons.append("high-risk trigger present")
    if mode == "delegated-full-critical":
        reasons.append("delegated-full-critical requires review by mode")
    if low_risk_fast_path and not triggers:
        reasons.append("fast delegated-light-fix keeps reviewer optional/absent")
    if no_governance_writing and not triggers:
        reasons.append("writing-no-governance does not force reviewer")
    if not reasons:
        reasons.append(str(base["reason"]))
    return {
        "mode": mode,
        "triggered": triggers,
        "high_risk": high_risk,
        "fast_light_fix": low_risk_fast_path,
        "no_governance_writing": no_governance_writing,
        "suggested": suggested,
        "required": required,
        "recommendation": "mandatory" if required else ("suggested" if suggested else "none"),
        "base_recommendation": base,
        "matrix": rows,
        "reason": "; ".join(reasons),
        "reviewer_packet_command": (
            f"python -m shujuan delegate packet --role reviewer --collaboration-mode {mode}"
            if suggested
            else None
        ),
    }


def validate_delegate_transition(from_state: str | None, to_state: str | None, role: str) -> dict[str, Any]:
    if not from_state and not to_state:
        return {"checked": False, "valid": True}
    if not from_state or not to_state:
        raise SystemExit("delegate status transition validation requires both --from-state and --to-state")
    if from_state not in DELEGATE_LIFECYCLE_TRANSITIONS or to_state not in DELEGATE_LIFECYCLE_STATES:
        allowed = ", ".join(DELEGATE_LIFECYCLE_STATES)
        return {"checked": True, "valid": False, "reason": f"state must be one of: {allowed}"}
    if to_state == "closed_by_controller" and role != "controller":
        return {"checked": True, "valid": False, "reason": "closed_by_controller requires controller role"}
    allowed_to = DELEGATE_LIFECYCLE_TRANSITIONS[from_state]
    if to_state not in allowed_to:
        return {"checked": True, "valid": False, "reason": f"invalid transition {from_state}->{to_state}"}
    return {"checked": True, "valid": True, "from_state": from_state, "to_state": to_state}


def normalize_delegate_import_classification(classification: str | None, import_kind: str, role: str) -> str:
    if classification:
        key = classification.strip().lower().replace(" ", "-")
    elif import_kind == "provider_fact" or role == "provider":
        key = "provider-hypothesis"
    elif import_kind in {"artifact", "test_result"}:
        key = "closure-material"
    elif import_kind == "review":
        key = "candidate-finding"
    else:
        key = "summary-only"
    normalized = DELEGATE_IMPORT_CLASSIFICATION_ALIASES.get(key)
    if not normalized:
        allowed = ", ".join(sorted(DELEGATE_IMPORT_CLASSIFICATIONS))
        raise SystemExit(f"delegate import classification must be one of: {allowed}")
    return normalized


def delegate_import_matrix() -> list[dict[str, Any]]:
    matrix = []
    for name, policy in DELEGATE_IMPORT_CLASSIFICATIONS.items():
        matrix.append(
            {
                "classification": name,
                "report_column": policy["report_column"],
                "narrative": policy["narrative"],
                "active_obligation": policy["active_obligation"],
                "closure_material": policy["closure_material"],
                "evidence_candidate": policy["evidence_candidate"],
                "provider_hypothesis": policy["provider_hypothesis"],
                "default_lifecycle": policy["default_lifecycle"],
                "controller_conversion_required": bool(policy["closure_material"]),
                "closes_check": False,
                "closes_task": False,
            }
        )
    return matrix


def delegate_import_report_columns(selected: str) -> dict[str, list[dict[str, Any]]]:
    columns: dict[str, list[dict[str, Any]]] = {"narrative": [], "active": [], "closure_material": []}
    for row in delegate_import_matrix():
        columns[row["report_column"]].append(
            {
                "classification": row["classification"],
                "selected": row["classification"] == selected,
                "active_obligation": row["active_obligation"],
                "closure_material": row["closure_material"],
                "evidence_candidate": row["evidence_candidate"],
                "closes_check": False,
                "closes_task": False,
            }
        )
    return columns


def delegate_import_classification_row(classification: str) -> dict[str, Any]:
    policy = DELEGATE_IMPORT_CLASSIFICATIONS[classification]
    return {
        "classification": classification,
        "report_column": policy["report_column"],
        "narrative": policy["narrative"],
        "active_obligation": policy["active_obligation"],
        "closure_material": policy["closure_material"],
        "evidence_candidate": policy["evidence_candidate"],
        "provider_hypothesis": policy["provider_hypothesis"],
        "invalid": policy["invalid"],
        "default_lifecycle": policy["default_lifecycle"],
        "controller_conversion_required": bool(policy["closure_material"]),
        "closes_check": False,
        "closes_task": False,
        "route": policy["route"],
        "next_action": policy["next_action"],
    }


def normalize_delegate_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def unique_normalized_paths(paths: list[str] | None) -> list[str]:
    return sorted({normalize_delegate_path(path) for path in (paths or []) if normalize_delegate_path(path)})


def delegate_role_policy(role: str) -> dict[str, Any]:
    if role not in DELEGATE_ROLE_DEFAULTS:
        raise SystemExit(f"delegate role unsupported: {role}")
    policy = dict(DELEGATE_ROLE_DEFAULTS[role])
    policy["role"] = role
    return policy


def delegate_role_packet(args: argparse.Namespace, payload: dict[str, Any], authority: str) -> dict[str, Any]:
    role_policy = delegate_role_policy(args.role)
    mode_policy = payload["collaboration_mode"]
    allowed_scope = list(dict.fromkeys([*role_policy["allowed_scope"], *getattr(args, "allowed_scope", [])]))
    must_read = list(dict.fromkeys([*role_policy["must_read"], *getattr(args, "must_read", [])]))
    review_questions = list(dict.fromkeys([*role_policy["questions"], *getattr(args, "review_question", [])]))
    escalation_triggers = list(
        dict.fromkeys(
            [
                "scope mismatch",
                "test failure",
                "need for governance DB write",
                "closeout or endpoint refresh requested from delegated role",
                *getattr(args, "escalation_trigger", []),
            ]
        )
    )
    requested_role_closeout_authority = bool(role_policy["closeout_authority"])
    requested_role_db_write_authority = bool(role_policy["db_write_authority"])
    db_write_authority = False
    closeout_authority = False
    authority_assertion_is_self_reported = args.role == "controller"
    hard_predicates = list(getattr(args, "hard_predicate", []) or [])
    pre_existing_dirty_paths = unique_normalized_paths(getattr(args, "pre_existing_dirty_path", []) or [])
    assigned_paths = unique_normalized_paths(getattr(args, "assigned_path", []) or [])
    owned_hunks_or_paths = list(dict.fromkeys([*assigned_paths, *unique_normalized_paths(getattr(args, "owned_hunk_or_path", []) or [])]))
    inspected_only_paths = unique_normalized_paths(getattr(args, "inspected_only_path", []) or [])
    provider_runtime_paths = unique_normalized_paths(getattr(args, "provider_runtime_path", []) or [])
    observed_only_paths = unique_normalized_paths(getattr(args, "observed_only_path", []) or [])
    not_owned_paths = unique_normalized_paths(getattr(args, "not_owned_path", []) or [])
    deleted_obsolete_paths = unique_normalized_paths(getattr(args, "deleted_obsolete_path", []) or [])
    fallback_paths = unique_normalized_paths(getattr(args, "fallback_path", []) or [])
    out_of_scope_paths = unique_normalized_paths(getattr(args, "out_of_scope_path", []) or [])
    fixture_writes = list(dict.fromkeys(getattr(args, "fixture_write", []) or []))
    blocked_checks = list(dict.fromkeys(getattr(args, "blocked_check", []) or []))
    unresolved_risks = list(dict.fromkeys(getattr(args, "unresolved_risk", []) or []))
    assumptions = list(dict.fromkeys(getattr(args, "assumption", []) or []))
    known_reds = list(dict.fromkeys(getattr(args, "known_red", []) or []))
    provider_outputs = list(dict.fromkeys(getattr(args, "provider_output", []) or []))
    provider_material = {
        "material_only": True,
        "cannot_close_checks": True,
        "cannot_close_tasks": True,
        "seed": getattr(args, "provider_seed", None),
        "question": getattr(args, "provider_question", None),
        "boundary": getattr(args, "provider_boundary", None)
        or "codegraph/GitNexus/provider output is input material only and cannot directly close checks or tasks.",
        "output_classification": getattr(args, "provider_output_classification", None) or "provider_hypothesis",
        "outputs": provider_outputs,
    }
    return_requirements = delegate_return_capsule(
        owned_hunks_or_paths=owned_hunks_or_paths,
        pre_existing_dirty_paths=pre_existing_dirty_paths,
        inspected_only_paths=inspected_only_paths,
        fixture_writes=fixture_writes,
        blocked_checks=blocked_checks,
        unresolved_risks=unresolved_risks,
        assumptions=assumptions,
        provider_outputs=provider_outputs,
        provider_runtime_paths=provider_runtime_paths,
        observed_only_paths=observed_only_paths,
        not_owned_paths=not_owned_paths,
        deleted_obsolete_paths=deleted_obsolete_paths,
        fallback_paths=fallback_paths,
        out_of_scope_paths=out_of_scope_paths,
    )
    governance_write_boundary = {
        "current_project_governance_write_allowed": db_write_authority,
        "current_project_governance_write_prohibited": True,
        "forbidden_current_project_actions": [
            "endpoint refresh",
            "exec stop",
            "evidence close/check close",
            "task close",
            "scope change/defer/unresolved/assumption writes",
        ],
        "isolated_fixture_writes": fixture_writes,
        "isolated_fixture_writes_are_material_only": True,
        "isolated_fixture_write_reporting_allowed_when_packet_authorized": True,
    }
    safe_verification = {
        "allowed_test_context": [
            "focused local tests",
            "isolated fixture governance writes when packet-authorized",
            "read-only reports or capsules",
        ],
        "forbidden_verification_shortcuts": [
            "provider output as closure evidence",
            "reviewer acceptance as direct closure",
            "current-project governance writes from worker role",
        ],
        "known_reds": known_reds,
    }
    body = (
        read_arg_or_stdin(getattr(args, "body", None), file_path=getattr(args, "body_file", None), label="body")
        if (getattr(args, "body", None) is not None or getattr(args, "body_file", None) is not None)
        else None
    )
    goal = getattr(args, "goal", None) or body or f"{args.role} packet"
    claim = getattr(args, "claim", None) or goal
    packet = {
        "one_screen": True,
        "role": args.role,
        "requested_role": args.role,
        "actual_authority": "delegate_packet_material_only",
        "authority_assertion_is_self_reported": authority_assertion_is_self_reported,
        "usable_as_delegation_packet": args.role != "controller",
        "can_write_governance_db": False,
        "can_close_checks": False,
        "can_close_tasks": False,
        "requested_role_policy": {
            "role": args.role,
            "db_write_authority": requested_role_db_write_authority,
            "closeout_authority": requested_role_closeout_authority,
            "note": "Describes the named role; it is not authority granted by this delegate packet command.",
        },
        "endpoint": getattr(args, "endpoint", None),
        "task": getattr(args, "task", None),
        "check": getattr(args, "check", None),
        "goal": goal,
        "body": body,
        "claim": claim,
        "hard_predicates": hard_predicates,
        "pre_existing_dirty_paths": pre_existing_dirty_paths,
        "assigned_paths": assigned_paths,
        "owned_hunks_or_paths": owned_hunks_or_paths,
        "inspected_only_paths": inspected_only_paths,
        "provider_runtime_paths": provider_runtime_paths,
        "observed_only_paths": observed_only_paths,
        "not_owned_paths": not_owned_paths,
        "deleted_obsolete_paths": deleted_obsolete_paths,
        "fallback_paths": fallback_paths,
        "out_of_scope_paths": out_of_scope_paths,
        "ownership_lanes": list(OWNERSHIP_MANIFEST_LANES),
        "ownership_manifest_schema": ownership_manifest_schema(),
        "ownership_manifest": return_requirements["ownership_manifest"],
        "ownership_manifest_material_only": True,
        "manifest_is_closure_evidence": False,
        "ownership_surface_guidance": dict(DEFAULT_OWNERSHIP_SURFACE_GUIDANCE),
        "fixture_writes": fixture_writes,
        "blocked_checks": blocked_checks,
        "unresolved_risks": unresolved_risks,
        "assumptions": assumptions,
        "known_reds": known_reds,
        "forbidden_actions": DELEGATE_FORBIDDEN_ACTIONS,
        "allowed_scope": allowed_scope,
        "must_read": must_read,
        "focused_verification_or_review_questions": review_questions,
        "safe_verification": safe_verification,
        "return_template": DELEGATE_RETURN_TEMPLATE,
        "return_requirements": return_requirements,
        "return_capsule": return_requirements,
        "identity_boundary": DELEGATE_RETURN_TEMPLATE["identity_boundary"],
        "escalation_triggers": escalation_triggers,
        "db_write_authority": db_write_authority,
        "closeout_authority": closeout_authority,
        "role_authority": {
            "requested_role": args.role,
            "actual_authority": "delegate_packet_material_only",
            "db_write_authority": db_write_authority,
            "closeout_authority": closeout_authority,
            "can_write_governance_db": False,
            "can_close_checks": False,
            "can_close_tasks": False,
            "authority_assertion_is_self_reported": authority_assertion_is_self_reported,
            "authority_boundary": authority,
        },
        "authority_boundary": authority,
        "source_label": "delegate packet CLI arguments plus DCCP role policy; not a closure evidence node",
        "material_classification": DELEGATE_PACKET_MATERIAL_CLASSIFICATION,
        "packet_material_classification": DELEGATE_PACKET_MATERIAL_CLASSIFICATION,
        "artifact_saved": False,
        "artifact_ref": None,
        "artifact_is_governance_record": False,
        "governance_record_created": False,
        "governance_record_table": DELEGATE_PACKET_GOVERNANCE_RECORD_TABLE,
        "next_required_governance_record_labels": list(DELEGATE_PACKET_NEXT_GOVERNANCE_RECORD_LABELS),
        "next_required_governance_records": delegate_packet_truth_labels(
            artifact_saved=False,
            artifact_ref=None,
        )["next_required_governance_records"],
        "db_backed": False,
        "fact_source": {
            "kind": "source_labeled_cli_args",
            "db_backed": False,
            "detail_ref": "delegate packet",
        },
        "collaboration_mode": mode_policy["mode"],
        "provider_guidance": DELEGATE_PROVIDER_GUIDANCE,
        "provider_impact_classification": provider_material,
        "controller_only_closeout": True,
        "governance_write_boundary": governance_write_boundary,
        "no_closure_attestation_required": True,
    }
    packet["packet_lines"] = [
        f"Requested role: {args.role}",
        "Actual authority: delegate packet material only",
        f"Goal: {goal}",
        f"Endpoint: {packet['endpoint'] or '<unspecified>'}",
        f"Task/check: {packet['task'] or '<unspecified>'} / {packet['check'] or '<unspecified>'}",
        f"Mode: {mode_policy['mode']}",
        f"Authority: {authority}",
        "DB writes: no",
        "Closeout: not allowed by this packet; controller must use verified closeout primitives separately",
        f"Provider: {DELEGATE_PROVIDER_GUIDANCE['provider_intent']}; provider output remains material only",
        "Return: changed_files, owned_hunks_or_paths, fixture_writes, tests, ownership_lanes, blocked_checks, unresolved_risks, assumptions, no_closure_attestation",
    ]
    return packet


def delegate_after_snapshot_paths(repo: Path) -> list[str]:
    tracked = [
        normalize_delegate_path(line)
        for line in run_git(repo, ["diff", "--name-only", "HEAD"], allow_fail=True).splitlines()
        if line.strip()
    ]
    untracked = list_untracked_files(repo)
    return sorted({path for path in [*tracked, *untracked] if path and not is_internal_ignored_path(path)})

def delegate_git_commit_count(repo: Path) -> int | None:
    count_text = run_git(repo, ["rev-list", "--count", "HEAD"], allow_fail=True).strip()
    if not count_text:
        return None
    try:
        return int(count_text)
    except ValueError:
        return None

def runtime_preflight_payload(repo: Path, *, command: str) -> dict[str, Any]:
    recovery_init = "python -m shujuan init --postgres-dev"
    recovery_start = "python -m shujuan postgres-dev start"
    try:
        config = resolve_database_config(repo)
    except SystemExit as exc:
        message = str(exc)
        sqlite_hazard = "SQLite database URLs are disabled" in message or "SHUJUAN_DB_PROFILE=sqlite" in message
        code = "migration_runtime_ddl_hazard" if sqlite_hazard else "postgres_runtime_unavailable"
        return {
            "ok": False,
            "usable": False,
            "command": command,
            "db_writes": 0,
            "capture_claim": False,
                "runtime_preflight": {
                    "checked": True,
                    "ok": False,
                    "code": code,
                    "status_kind": code,
                    "next_schema_check_command": recovery_init,
                    "message": message,
                "recovery_command": recovery_init,
                "hidden_writes": 0,
            },
            "diagnostics": diagnostics_payload(
                usable=False,
                report_errors=[message],
                next_action=recovery_init,
            ),
            "next_action": recovery_init,
        }
    if config.source == "postgres-dev" and config.url:
        parsed = urlparse(config.url)
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or 5432)
        try:
            with socket.create_connection((host, port), timeout=0.25):
                pass
        except OSError as exc:
            message = f"Project-owned postgres-dev handle is configured but not reachable at {host}:{port}: {exc}"
            return {
                "ok": False,
                "usable": False,
                "command": command,
                "db_writes": 0,
                "capture_claim": False,
                "runtime_preflight": {
                    "checked": True,
                    "ok": False,
                    "code": "postgres_runtime_stale_handle",
                    "status_kind": "postgres_runtime_stale_handle",
                    "next_schema_check_command": recovery_start,
                    "message": message,
                    "source": config.source,
                    "recovery_command": recovery_start,
                    "hidden_writes": 0,
                },
                "diagnostics": diagnostics_payload(
                    usable=False,
                    report_errors=[message],
                    next_action=recovery_start,
                ),
                "next_action": recovery_start,
            }
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        message = "PostgreSQL backend requires the `psycopg[binary]` package; no SQLite fallback was used."
        return {
            "ok": False,
            "usable": False,
            "command": command,
            "db_writes": 0,
            "capture_claim": False,
                "runtime_preflight": {
                    "checked": True,
                    "ok": False,
                    "code": "postgres_driver_missing",
                    "status_kind": "postgres_driver_missing",
                    "next_schema_check_command": "python -m pip install psycopg[binary]",
                    "message": message,
                "recovery_command": "python -m pip install psycopg[binary]",
                "hidden_writes": 0,
            },
            "diagnostics": diagnostics_payload(
                usable=False,
                report_errors=[message],
                next_action="python -m pip install psycopg[binary]",
            ),
            "next_action": "python -m pip install psycopg[binary]",
        }
    assert config.url is not None
    try:
        raw = psycopg.connect(config.url, row_factory=dict_row, connect_timeout=2)
    except psycopg.OperationalError as exc:
        stale = config.source == "postgres-dev"
        code = "postgres_runtime_stale_handle" if stale else "postgres_runtime_unavailable"
        recovery = recovery_start if stale else "Check SHUJUAN_DATABASE_URL or run `python -m shujuan postgres-dev start`."
        message = str(exc)
        return {
            "ok": False,
            "usable": False,
            "command": command,
            "db_writes": 0,
            "capture_claim": False,
                "runtime_preflight": {
                    "checked": True,
                    "ok": False,
                    "code": code,
                    "status_kind": code,
                    "next_schema_check_command": recovery,
                    "message": message,
                "source": config.source,
                "recovery_command": recovery,
                "hidden_writes": 0,
            },
            "diagnostics": diagnostics_payload(
                usable=False,
                report_errors=[message],
                next_action=recovery,
            ),
            "next_action": recovery,
        }
    conn = None
    try:
        conn = type("_RuntimePreflightConnection", (), {"raw": raw})()
        # Reuse the PostgresConnection wrapper so inspect_schema gets the same SQL conversion behavior.
        from ..store import PostgresConnection

        wrapped = PostgresConnection(raw)
        schema = inspect_schema(wrapped)
        hazard = schema["state"] != "current" or not schema.get("has_migration_ledger")
        if hazard:
            message = f"runtime schema state is {schema['state']}; migration/runtime DDL review is required before this command reads governed facts."
            recovery = "python -m shujuan migrate status"
            return {
                "ok": False,
                "usable": False,
                "command": command,
                "db_writes": 0,
                "capture_claim": False,
                "runtime_preflight": {
                    "checked": True,
                    "ok": False,
                    "code": "migration_runtime_ddl_hazard",
                    "status_kind": "migration_runtime_ddl_hazard",
                    "next_schema_check_command": recovery,
                    "message": message,
                    "schema": schema,
                    "recovery_command": recovery,
                    "hidden_writes": 0,
                },
                "diagnostics": diagnostics_payload(
                    usable=False,
                    raw_count=len(schema.get("tables") or []),
                    visible_count=len(schema.get("tables") or []),
                    report_errors=[message],
                    next_action=recovery,
                ),
                "next_action": recovery,
            }
        return {
            "ok": True,
            "usable": True,
            "command": command,
            "runtime_preflight": {
                "checked": True,
                "ok": True,
                "code": "runtime_ready",
                "status_kind": "postgres_runtime_schema_current",
                "next_schema_check_command": "python -m shujuan migrate status",
                "schema": schema,
                "hidden_writes": 0,
            },
            "diagnostics": diagnostics_payload(
                usable=True,
                raw_count=len(schema.get("tables") or []),
                visible_count=len(schema.get("tables") or []),
                next_action="Proceed with read-only report/capsule diagnostics.",
            ),
            "next_action": "Proceed with read-only report/capsule diagnostics.",
        }
    finally:
        try:
            raw.close()
        except Exception:
            pass
        _ = conn

def maybe_runtime_preflight(args: argparse.Namespace, command: str) -> dict[str, Any] | None:
    if not getattr(args, "runtime_preflight", False):
        return None
    payload = runtime_preflight_payload(args.repo.resolve(), command=command)
    if not payload.get("ok"):
        return payload
    return None

def persist_delegate_packet_artifact(repo: Path, packet: dict[str, Any]) -> str:
    role = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(packet.get("role") or "role"))
    name = f"delegate_packet_{role}_{new_id('packet_artifact')}.json"
    artifact_ref = f".shujuan/artifacts/{name}"
    packet.update(delegate_packet_truth_labels(artifact_saved=True, artifact_ref=artifact_ref))
    return write_artifact_text(repo, name, json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))

def delegate_base_payload(args: argparse.Namespace, command: str, *, role: str | None = None) -> dict[str, Any]:
    endpoint = getattr(args, "endpoint", None)
    task = getattr(args, "task", None)
    check = getattr(args, "check", None)
    lane = getattr(args, "lane", None)
    selected_role = role or getattr(args, "role", None) or "worker"
    mode_policy = collaboration_mode_policy(getattr(args, "collaboration_mode", None))
    return {
        "ok": True,
        "usable": True,
        "command": f"delegate {command}",
        "phase": "p0_skeleton",
        "db_writes": 0,
        "capture_claim": False,
        "schema_tables": DCCP_MINIMAL_TABLES,
        "controller_only_closeout": True,
        "role": selected_role,
        "lane": lane,
        "collaboration_mode": mode_policy,
        "endpoint": endpoint,
        "task": task,
        "check": check,
        "forbidden_actions": DELEGATE_FORBIDDEN_ACTIONS,
        "expected_return_fields": DELEGATE_RETURN_FIELDS,
        "ownership_lanes": list(OWNERSHIP_MANIFEST_LANES),
        "ownership_manifest_schema": ownership_manifest_schema(),
        "ownership_manifest_material_only": True,
        "manifest_is_closure_evidence": False,
        "ownership_surface_guidance": dict(DEFAULT_OWNERSHIP_SURFACE_GUIDANCE),
        "existing_primitives": DCCP_EXISTING_PRIMITIVES,
        "diagnostics": diagnostics_payload(
            usable=True,
            next_action="Use the command-specific next_action field for the next controller-owned step.",
        ),
    }

def cmd_delegate_plan(args: argparse.Namespace) -> int:
    preflight = maybe_runtime_preflight(args, "delegate plan")
    if preflight:
        print_json(preflight)
        return 1
    payload = delegate_base_payload(args, "plan")
    policy = payload["collaboration_mode"]
    slices = delegate_plan_slices(policy, args)
    batches = delegate_plan_batches(slices, policy)
    review_triggers = delegate_review_trigger_matrix(policy, args)
    payload.update(
        {
            "plan": {
                "lane_lifecycle": ["planned", "active", "returned", "verified", "cancelled"],
                "minimal_tables_only": True,
                "new_tables": DCCP_MINIMAL_TABLES,
                "slices": slices,
                "batches": batches,
                "focused_verification": policy["verification_policy"],
                "return_classification": policy["return_classification"],
                "reviewer_recommendation": review_triggers,
                "reused_tables": {
                    "review_results": "review state and reviewer output",
                    "nodes/evidence_records": "return artifacts and evidence material",
                    "provider_facts": "provider-derived impact facts",
                    "audit findings": "existing nodes plus semantic lifecycle",
                    "acceptance_checks/tasks": "controller-only closure",
                },
            },
            "diagnostics": diagnostics_payload(
                usable=True,
                raw_count=len(slices),
                visible_count=len(slices),
                filtered_count=0,
                next_action="Generate the next packet or reviewer packet indicated by next_action.command.",
            ),
            "routes": {
                "focus": "python -m shujuan work focus --endpoint <endpoint>",
                "active_report": "python -m shujuan report endpoint <endpoint> --active-only --markdown",
                "review_bundle": "python -m shujuan review start --endpoint <endpoint>",
            },
            "next_action": {
                "action": "delegate packet" if not review_triggers["required"] else "delegate reviewer packet",
                "mode": policy["mode"],
                "command": review_triggers["reviewer_packet_command"]
                or f"python -m shujuan delegate packet --collaboration-mode {policy['mode']} --role {policy['default_role']}",
                "controller_reminder": "Returned delegate material must be imported and verified by the controller before any closeout.",
            },
        }
    )
    print_json(payload)
    return 0

def cmd_delegate_packet(args: argparse.Namespace) -> int:
    preflight = maybe_runtime_preflight(args, "delegate packet")
    if preflight:
        print_json(preflight)
        return 1
    payload = delegate_base_payload(args, "packet", role=args.role)
    policy = payload["collaboration_mode"]
    authority = (
        "requested controller role is self-reported here; this delegate packet command grants material-only authority and cannot close or write governance DB"
        if args.role == "controller"
        else "delegated role is implementation/review/research/writing only; no closeout authority"
    )
    role_packet = delegate_role_packet(args, payload, authority)
    artifact_ref = persist_delegate_packet_artifact(args.repo.resolve(), role_packet) if args.save_artifact else None
    if not artifact_ref:
        role_packet.update(delegate_packet_truth_labels(artifact_saved=False, artifact_ref=None))
    truth_labels = delegate_packet_truth_labels(artifact_saved=bool(artifact_ref), artifact_ref=artifact_ref)
    command_effects = delegate_packet_effects(
        role=args.role,
        save_artifact=bool(args.save_artifact),
        runtime_preflight=bool(getattr(args, "runtime_preflight", False)),
    )
    payload.update(
        {
            "packet": {
                "packet_kind": args.packet_kind,
                "authority_boundary": authority,
                "body": role_packet.get("body"),
                "role_packet": role_packet,
                "collaboration_mode": policy["mode"],
                "agent_combination": policy["agents"],
                "verification_policy": policy["verification_policy"],
                "closeout_policy": policy["closeout_policy"],
                "reviewer_recommendation": delegate_review_suggestion(policy),
                "mode_packet_behavior": policy["packet_behavior"],
                "artifact_primary": True,
                "db_persist_table": None,
                "delegation_packets_table_status": "dormant_not_written",
                "persisted": bool(artifact_ref),
                "artifact_ref": artifact_ref,
                **truth_labels,
                "dry_run": True,
                "command_effects": command_effects,
            },
            "persisted": bool(artifact_ref),
            "artifact_ref": artifact_ref,
            **truth_labels,
            "command_effects": command_effects,
            "diagnostics": diagnostics_payload(
                usable=args.role != "controller",
                raw_count=len(role_packet.get("packet_lines") or []),
                visible_count=len(role_packet.get("packet_lines") or []),
                filtered_count=0,
                next_action=(
                    "Use delegate controller status/close diagnostics or controller-owned primitives; this self-reported controller packet is material only."
                    if args.role == "controller"
                    else "Return the packet to the scoped role; controller owns import and closeout."
                ),
            ),
            "usable": args.role != "controller",
            "usable_as_delegation_packet": args.role != "controller",
            "next_action": (
                "Controller-role delegate packets are source-labeled material only; use controller-owned closeout primitives separately."
                if args.role == "controller"
                else "Controller can import saved packet artifacts later; saving a packet artifact does not write governance DB rows."
            ),
        }
    )
    print_json(payload)
    return 0

def cmd_delegate_import(args: argparse.Namespace) -> int:
    preflight = maybe_runtime_preflight(args, "delegate import")
    if preflight:
        print_json(preflight)
        return 1
    payload = delegate_base_payload(args, "import", role=args.role)
    forbidden_closeout_requests = {
        "close_check": args.close_check,
        "close_task": args.close_task,
        "closeout": args.closeout,
        "convert_to_evidence": args.convert_to_evidence,
    }
    requested = [name for name, enabled in forbidden_closeout_requests.items() if enabled]
    if requested:
        joined = ", ".join(requested)
        raise SystemExit(
            "delegate import cannot close checks/tasks or convert closure material to evidence; "
            f"controller-only path required for: {joined}"
        )
    classification = normalize_delegate_import_classification(args.classification, args.import_kind, args.role)
    classification_row = delegate_import_classification_row(classification)
    payload.update(
        {
            "import_kind": args.import_kind,
            "artifact": args.artifact,
            "classification": classification,
            "classification_row": classification_row,
            "classification_matrix": delegate_import_matrix(),
            "report_columns": delegate_import_report_columns(classification),
            "import_policy": {
                "active_obligation": classification_row["active_obligation"],
                "closure_material": classification_row["closure_material"],
                "evidence_candidate": classification_row["evidence_candidate"],
                "controller_conversion_required": classification_row["controller_conversion_required"],
                "controller_only_closeout": True,
                "forbidden_closeout_sources": [
                    "worker return packet",
                    "reviewer accept",
                    "provider output",
                    "summary",
                    "candidate finding",
                    "closure material",
                ],
                "closes_check": False,
                "closes_task": False,
            },
            "diagnostics": diagnostics_payload(
                usable=True,
                raw_count=len(delegate_import_matrix()),
                visible_count=sum(len(items) for items in delegate_import_report_columns(classification).values()),
                filtered_count=0,
                next_action=classification_row["next_action"],
            ),
            "persisted": False,
            "routes": {
                "narrative": "python -m shujuan audit import-agent-output --endpoint <endpoint> --source-node <source>",
                "active": "python -m shujuan audit import-agent-output --classification actionable --endpoint <endpoint> --source-node <source>",
                "closure_material": "python -m shujuan evidence artifact --path <file> --from-node <source>",
                "test_result_candidate": "python -m shujuan evidence test-result --from-node <source> -- <command>",
                "review_material": "python -m shujuan review submit --endpoint <endpoint> --read-only-attested",
            },
            "next_action": classification_row["next_action"],
        }
    )
    print_json(payload)
    return 0

def cmd_delegate_ownership(args: argparse.Namespace) -> int:
    preflight = maybe_runtime_preflight(args, "delegate ownership")
    if preflight:
        print_json(preflight)
        return 1
    repo = args.repo.resolve()
    payload = delegate_base_payload(args, "ownership", role=args.role)
    pre_existing_dirty_paths = unique_normalized_paths(args.pre_existing_dirty_path)
    assigned_paths = unique_normalized_paths(args.assigned_path)
    claimed_paths = unique_normalized_paths(args.claimed_path)
    claimed_hunks = list(args.claimed_hunk or [])
    provider_runtime_paths = unique_normalized_paths(getattr(args, "provider_runtime_path", []) or [])
    observed_only_paths = unique_normalized_paths(getattr(args, "observed_only_path", []) or [])
    not_owned_paths = unique_normalized_paths(getattr(args, "not_owned_path", []) or [])
    deleted_obsolete_paths = unique_normalized_paths(getattr(args, "deleted_obsolete_path", []) or [])
    fallback_paths = unique_normalized_paths(getattr(args, "fallback_path", []) or [])
    out_of_scope_paths = unique_normalized_paths(getattr(args, "out_of_scope_path", []) or [])
    after_snapshot_paths = unique_normalized_paths(args.after_snapshot_path) if args.after_snapshot_path else delegate_after_snapshot_paths(repo)

    pre_existing_set = set(pre_existing_dirty_paths)
    assigned_set = set(assigned_paths)
    claimed_set = set(claimed_paths)
    after_set = set(after_snapshot_paths)
    owned_candidates = assigned_set | claimed_set
    non_worker_classified_set = (
        set(provider_runtime_paths)
        | set(observed_only_paths)
        | set(not_owned_paths)
        | set(deleted_obsolete_paths)
        | set(fallback_paths)
        | set(out_of_scope_paths)
    )

    worker_touched_paths = sorted((after_set & owned_candidates) - pre_existing_set - non_worker_classified_set)
    ambiguous_paths = sorted((after_set & pre_existing_set) - non_worker_classified_set)
    unassigned_paths = sorted(after_set - owned_candidates - pre_existing_set - non_worker_classified_set)
    claimed_without_after_change = sorted(claimed_set - after_set)
    assigned_without_after_change = sorted(assigned_set - after_set)
    ownership_manifest = ownership_manifest_from_lanes(
        worker_owned=worker_touched_paths,
        pre_existing_dirty=pre_existing_dirty_paths,
        provider_runtime=provider_runtime_paths,
        observed_only=observed_only_paths,
        not_owned=not_owned_paths,
        deleted_obsolete=deleted_obsolete_paths,
        fallback=fallback_paths,
        out_of_scope=out_of_scope_paths,
    )

    warnings = []
    for path in claimed_without_after_change:
        warnings.append(
            {
                "code": "claimed_path_missing_after_change",
                "path": path,
                "message": "Worker claimed this path, but the after snapshot has no worktree change for it.",
            }
        )
    if unassigned_paths:
        warnings.append(
            {
                "code": "after_diff_contains_unassigned_paths",
                "paths": unassigned_paths,
                "message": "After snapshot contains paths that were not assigned, claimed, or recorded as pre-existing dirty.",
            }
        )
    if ambiguous_paths:
        warnings.append(
            {
                "code": "pre_existing_dirty_paths_still_dirty",
                "paths": ambiguous_paths,
                "message": "These paths were dirty before handoff and still appear in the after snapshot; ownership is ambiguous without a content baseline.",
            }
        )

    payload.update(
        {
            "usable": not warnings,
            "ownership": {
                "read_only": True,
                "requires_commit": False,
                "git_history_pollution": False,
                "git_commit_count": delegate_git_commit_count(repo),
                "pre_existing_dirty_paths": pre_existing_dirty_paths,
                "assigned_paths": assigned_paths,
                "claimed_paths": claimed_paths,
                "claimed_hunks": claimed_hunks,
                "provider_runtime_paths": provider_runtime_paths,
                "observed_only_paths": observed_only_paths,
                "not_owned_paths": not_owned_paths,
                "deleted_obsolete_paths": deleted_obsolete_paths,
                "fallback_paths": fallback_paths,
                "out_of_scope_paths": out_of_scope_paths,
                "after_snapshot_paths": after_snapshot_paths,
                "worker_touched_paths": worker_touched_paths,
                "worker_owned_paths": worker_touched_paths,
                "ambiguous_paths": ambiguous_paths,
                "unassigned_paths": unassigned_paths,
                "claimed_without_after_change": claimed_without_after_change,
                "assigned_without_after_change": assigned_without_after_change,
                "controller_path_classes": {
                    "pre_existing": pre_existing_dirty_paths,
                    "worker_owned": worker_touched_paths,
                    "ambiguous": ambiguous_paths,
                    "unassigned": unassigned_paths,
                    "pre_existing_dirty": pre_existing_dirty_paths,
                    "provider_runtime": provider_runtime_paths,
                    "observed_only": observed_only_paths,
                    "not_owned": not_owned_paths,
                    "deleted_obsolete": deleted_obsolete_paths,
                    "fallback": fallback_paths,
                    "out_of_scope": out_of_scope_paths,
                },
                "ownership_lanes": list(OWNERSHIP_MANIFEST_LANES),
                "ownership_manifest_schema": ownership_manifest_schema(),
                "ownership_manifest": ownership_manifest,
                "ownership_manifest_material_only": True,
                "manifest_is_closure_evidence": False,
                "ownership_manifest_boundary": dict(OWNERSHIP_MANIFEST_MATERIAL_BOUNDARY),
                "ownership_surface_guidance": dict(DEFAULT_OWNERSHIP_SURFACE_GUIDANCE),
                "warnings": warnings,
            },
            "warnings": warnings,
            "diagnostics": diagnostics_payload(
                usable=not warnings,
                raw_count=len(after_snapshot_paths),
                visible_count=len(worker_touched_paths) + len(ambiguous_paths) + len(unassigned_paths),
                filtered_count=0,
                report_errors=[warning["code"] for warning in warnings],
                next_action="Controller reviews ownership warnings before importing return material or converting any evidence.",
            ),
            "next_action": "Controller reviews ownership warnings before importing return material or converting any evidence.",
        }
    )
    print_json(payload)
    return 0

def cmd_delegate_review(args: argparse.Namespace) -> int:
    preflight = maybe_runtime_preflight(args, "delegate review")
    if preflight:
        print_json(preflight)
        return 1
    payload = delegate_base_payload(args, "review", role="reviewer")
    covered = list(dict.fromkeys(args.covered_predicate or []))
    missing = list(dict.fromkeys(args.missing_predicate or []))
    overclaim_risks = []
    if args.claims_closeout:
        overclaim_risks.append("reviewer_accept_or_reject_cannot_close_checks")
    if args.result == "accept" and missing:
        overclaim_risks.append("accept_with_missing_predicate_coverage")
    if args.result == "reject" and not (missing or args.blocking):
        overclaim_risks.append("reject_without_missing_predicate_or_blocking_reason")

    if args.recommended_classification:
        recommended_classification = normalize_delegate_import_classification(args.recommended_classification, "review", "reviewer")
    elif args.result == "accept":
        recommended_classification = "closure-material"
    elif args.result == "reject" and (args.blocking or missing):
        recommended_classification = "actionable"
    elif args.result == "unclear":
        recommended_classification = "needs-user-decision" if args.needs_user_decision else "candidate-finding"
    else:
        recommended_classification = "candidate-finding"

    blocks_controller_closeout = args.result == "reject" and bool(args.blocking or missing)
    usable = not overclaim_risks
    payload.update(
        {
            "usable": usable,
            "review": {
                "read_only": True,
                "result": args.result,
                "accept_reject_unclear": args.result,
                "summary": args.summary,
                "predicate_coverage": {
                    "covered": covered,
                    "missing": missing,
                    "all_claimed_predicates_covered": not missing,
                },
                "recommended_classification": recommended_classification,
                "classification_row": delegate_import_classification_row(recommended_classification),
                "overclaim_risk": bool(overclaim_risks),
                "overclaim_risks": overclaim_risks,
                "safe_to_import_without_controller_review": usable,
                "blocks_controller_closeout": blocks_controller_closeout,
                "creates_candidate_or_actionable_material": args.result in {"reject", "unclear"},
                "controller_only_closeout": True,
                "closes_check": False,
                "closes_task": False,
                "routes": {
                    "accept": "controller may import as closure-material candidate, then verify through evidence primitives",
                    "reject": "controller imports as candidate-finding or actionable audit material; no automatic closure",
                    "unclear": "controller imports as candidate-finding or needs-user-decision material",
                },
            },
            "diagnostics": diagnostics_payload(
                usable=usable,
                raw_count=len(covered) + len(missing),
                visible_count=len(covered) + len(missing),
                filtered_count=0,
                report_errors=overclaim_risks,
                next_action="Controller imports reviewer output as material, then decides verification, finding creation, or evidence conversion.",
            ),
            "next_action": "Controller imports reviewer output as material, then decides verification, finding creation, or evidence conversion.",
        }
    )
    print_json(payload)
    return 0 if not (args.fail_on_overclaim and overclaim_risks) else 1

def cmd_delegate_verify(args: argparse.Namespace) -> int:
    preflight = maybe_runtime_preflight(args, "delegate verify")
    if preflight:
        print_json(preflight)
        return 1
    payload = delegate_base_payload(args, "verify", role=args.role)
    issues = []
    if args.claims_closeout and args.role != "controller":
        issues.append(
            {
                "code": "delegated_closeout_forbidden",
                "message": "Only the controller can close checks/tasks or run endpoint closeout.",
                "route": "Use review/audit/evidence import as material, then let the controller run work close/exec stop.",
            }
        )
    payload.update(
        {
            "usable": not issues,
            "issues": issues,
            "verification": {
                "controller_only_closeout": True,
                "packet_fields_required": DELEGATE_RETURN_FIELDS,
                "schema_scope": DCCP_MINIMAL_TABLES,
            },
            "diagnostics": diagnostics_payload(
                usable=not issues,
                raw_count=len(DELEGATE_RETURN_FIELDS),
                visible_count=len(DELEGATE_RETURN_FIELDS),
                filtered_count=0,
                report_errors=[item["message"] for item in issues],
                next_action="Proceed through existing primitives if usable; otherwise remove closeout claims from delegated material.",
            ),
            "next_action": "Proceed through existing primitives if usable; otherwise remove closeout claims from delegated material.",
        }
    )
    print_json(payload)
    return 0 if not issues or args.allow_fail else 1

def cmd_delegate_status(args: argparse.Namespace) -> int:
    preflight = maybe_runtime_preflight(args, "delegate status")
    if preflight:
        print_json(preflight)
        return 1
    payload = delegate_base_payload(args, "status", role=getattr(args, "role", None) or "controller")
    transition = validate_delegate_transition(args.from_state, args.to_state, payload["role"])
    issues = [] if transition.get("valid") else [{"code": "invalid_delegate_transition", "message": transition.get("reason")}]
    payload.update(
        {
            "usable": not issues,
            "issues": issues,
            "status": {
                "read_only": True,
                "states": DELEGATE_LIFECYCLE_STATES,
                "allowed_transitions": {key: sorted(value) for key, value in DELEGATE_LIFECYCLE_TRANSITIONS.items()},
                "transition": transition,
                "lane_lifecycle_source": "delegation_lanes.lifecycle",
                "packet_source": "delegation_packets",
                "ownership_snapshot_source": "worker_ownership_snapshots",
                "closure_source": "tasks/acceptance_checks closed_by_node_id",
                "lane": {
                    "id": args.lane,
                    "state": args.state,
                    "collaboration_mode": payload["collaboration_mode"]["mode"],
                    "controller_only_closeout": True,
                },
                "controller_closeout_gates": [
                    "returned material imported through existing primitives",
                    "focused verification passes",
                    "review recommendation satisfied or explicitly deferred by controller",
                    "evidence verify passes",
                    "endpoint doctor strict-closeout is acceptable",
                ],
            },
            "diagnostics": diagnostics_payload(
                usable=not issues,
                raw_count=len(DELEGATE_LIFECYCLE_STATES),
                visible_count=len(DELEGATE_LIFECYCLE_STATES),
                filtered_count=0,
                report_errors=[item["message"] for item in issues],
                next_action="Use endpoint reports and work focus for live facts until full DCCP status persistence is implemented.",
            ),
            "routes": {
                "active_report": "python -m shujuan report endpoint <endpoint> --active-only --markdown",
                "current_work": "python -m shujuan work current",
                "review_bundle": "python -m shujuan review start --endpoint <endpoint>",
            },
            "next_action": "Use endpoint reports and work focus for live facts until full DCCP status persistence is implemented.",
        }
    )
    print_json(payload)
    return 0 if not issues or args.allow_fail else 1

def cmd_delegate_capsule(args: argparse.Namespace) -> int:
    preflight = maybe_runtime_preflight(args, "delegate capsule")
    if preflight:
        print_json(preflight)
        return 1
    payload = delegate_base_payload(args, "capsule", role=args.role)
    policy = payload["collaboration_mode"]
    role_policy = delegate_role_policy(args.role)
    hard_predicates = list(args.hard_predicate or [])
    pre_existing_dirty_paths = unique_normalized_paths(getattr(args, "pre_existing_dirty_path", []) or [])
    owned_hunks_or_paths = unique_normalized_paths(getattr(args, "owned_hunk_or_path", []) or [])
    inspected_only_paths = unique_normalized_paths(getattr(args, "inspected_only_path", []) or [])
    provider_runtime_paths = unique_normalized_paths(getattr(args, "provider_runtime_path", []) or [])
    observed_only_paths = unique_normalized_paths(getattr(args, "observed_only_path", []) or [])
    not_owned_paths = unique_normalized_paths(getattr(args, "not_owned_path", []) or [])
    deleted_obsolete_paths = unique_normalized_paths(getattr(args, "deleted_obsolete_path", []) or [])
    fallback_paths = unique_normalized_paths(getattr(args, "fallback_path", []) or [])
    out_of_scope_paths = unique_normalized_paths(getattr(args, "out_of_scope_path", []) or [])
    fixture_writes = list(dict.fromkeys(getattr(args, "fixture_write", []) or []))
    blocked_checks = list(dict.fromkeys([*(getattr(args, "blocked_check", []) or []), *([args.check] if args.check else [])]))
    unresolved_risks = list(dict.fromkeys(getattr(args, "unresolved_risk", []) or []))
    assumptions = list(dict.fromkeys(getattr(args, "assumption", []) or []))
    known_reds = list(dict.fromkeys(getattr(args, "known_red", []) or []))
    provider_outputs = list(dict.fromkeys(getattr(args, "provider_output", []) or []))
    active_obligations = [
        item
        for item in [
            {"kind": "task", "id": args.task, "visible": bool(args.task)},
            {"kind": "check", "id": args.check, "visible": bool(args.check)},
        ]
        if item["visible"]
    ]
    next_slice = args.next_slice or (policy["slices"][0] if policy.get("slices") else None)
    capsule = {
        "one_screen": True,
        "read_only": True,
        "live_db_read": False,
        "role": args.role,
        "endpoint": args.endpoint,
        "active_obligations": active_obligations,
        "next_slice": next_slice,
        "hard_predicates": hard_predicates,
        "scope": {
            "endpoint": args.endpoint,
            "task": args.task,
            "check": args.check,
            "lane": args.lane,
            "collaboration_mode": policy["mode"],
        },
        "recent_handoff_refs": list(args.handoff or []),
        "relevant_ids": {
            "task": args.task,
            "check": args.check,
            "lane": args.lane,
        },
        "warnings": list(args.warning or []),
        "known_reds": known_reds,
        "must_read": list(dict.fromkeys([*role_policy["must_read"], *args.must_read])),
        "role_authority": {
            "role": args.role,
            "db_write_authority": bool(role_policy["db_write_authority"]),
            "closeout_authority": bool(role_policy["closeout_authority"]),
            "controller_only_closeout": True,
        },
        "forbidden_actions": DELEGATE_FORBIDDEN_ACTIONS,
        "safe_verification": {
            "allowed_test_context": [
                "focused local tests",
                "isolated fixture governance writes when packet-authorized",
                "read-only reports or capsules",
            ],
            "known_reds": known_reds,
            "blocked_checks": blocked_checks,
        },
        "return_requirements": delegate_return_capsule(
            owned_hunks_or_paths=owned_hunks_or_paths,
            pre_existing_dirty_paths=pre_existing_dirty_paths,
            inspected_only_paths=inspected_only_paths,
            fixture_writes=fixture_writes,
            blocked_checks=blocked_checks,
            unresolved_risks=unresolved_risks,
            assumptions=assumptions,
            provider_outputs=provider_outputs,
            provider_runtime_paths=provider_runtime_paths,
            observed_only_paths=observed_only_paths,
            not_owned_paths=not_owned_paths,
            deleted_obsolete_paths=deleted_obsolete_paths,
            fallback_paths=fallback_paths,
            out_of_scope_paths=out_of_scope_paths,
        ),
        "return_capsule": delegate_return_capsule(
            owned_hunks_or_paths=owned_hunks_or_paths,
            pre_existing_dirty_paths=pre_existing_dirty_paths,
            inspected_only_paths=inspected_only_paths,
            fixture_writes=fixture_writes,
            blocked_checks=blocked_checks,
            unresolved_risks=unresolved_risks,
            assumptions=assumptions,
            provider_outputs=provider_outputs,
            provider_runtime_paths=provider_runtime_paths,
            observed_only_paths=observed_only_paths,
            not_owned_paths=not_owned_paths,
            deleted_obsolete_paths=deleted_obsolete_paths,
            fallback_paths=fallback_paths,
            out_of_scope_paths=out_of_scope_paths,
        ),
        "governance_write_boundary": {
            "current_project_governance_write_allowed": bool(role_policy["db_write_authority"]),
            "current_project_governance_write_prohibited": not bool(role_policy["db_write_authority"]),
            "forbidden_current_project_actions": [
                "endpoint refresh",
                "exec stop",
                "evidence close/check close",
                "task close",
            ],
            "isolated_fixture_writes": fixture_writes,
            "isolated_fixture_writes_are_material_only": True,
        },
        "provider_impact_classification": {
            "material_only": True,
            "cannot_close_checks": True,
            "seed": getattr(args, "provider_seed", None),
            "question": getattr(args, "provider_question", None),
            "boundary": getattr(args, "provider_boundary", None)
            or "codegraph/GitNexus/provider output is material only and cannot directly close checks.",
            "output_classification": getattr(args, "provider_output_classification", None) or "provider_hypothesis",
            "outputs": provider_outputs,
        },
        "source_label": "delegate capsule CLI arguments plus DCCP role policy; not a closure evidence node",
        "db_backed": False,
        "fact_source": {
            "kind": "source_labeled_cli_args",
            "db_backed": False,
            "detail_ref": "delegate capsule",
        },
        "hidden_by_design": [
            "closed check details",
            "full project report",
            "provider history",
            "unrelated backlog",
        ],
        "controller_only_closeout": True,
        "provider_guidance": DELEGATE_PROVIDER_GUIDANCE,
    }
    capsule["capsule_lines"] = [
        f"Role: {args.role}",
        f"Endpoint: {args.endpoint or '<unspecified>'}",
        f"Active obligations: {len(active_obligations)}",
        f"Next slice: {next_slice or '<unspecified>'}",
        f"Hard predicates: {len(hard_predicates)}",
        "History hidden: closed checks, full project report, provider history, unrelated backlog",
        "Closeout: controller-only through existing primitives",
    ]
    payload.update(
        {
            "capsule": capsule,
            "diagnostics": diagnostics_payload(
                usable=True,
                raw_count=len(active_obligations) + len(hard_predicates) + len(capsule["warnings"]),
                visible_count=len(capsule["capsule_lines"]),
                filtered_count=len(capsule["hidden_by_design"]),
                next_action="Use this capsule as the role-scoped active surface; ask the controller for a live endpoint report when DB-backed facts are needed.",
            ),
            "next_action": "Use this capsule as the role-scoped active surface; ask the controller for a live endpoint report when DB-backed facts are needed.",
        }
    )
    print_json(payload)
    return 0

def cmd_delegate_controller_status(args: argparse.Namespace) -> int:
    payload = delegate_base_payload(args, "controller status", role="controller")
    payload.update(
        {
            "controller_status": {
                "closeout_authority": "controller_only",
                "must_verify_before_close": ["evidence verify", "endpoint doctor --strict-closeout"],
                "delegate_material_is_not_closure": True,
            },
            "diagnostics": diagnostics_payload(
                usable=True,
                raw_count=3,
                visible_count=3,
                filtered_count=0,
                next_action="Run active endpoint report, endpoint doctor, and evidence verify from the controller run.",
            ),
            "routes": {
                "status": "python -m shujuan report endpoint <endpoint> --active-only --markdown",
                "doctor": "python -m shujuan endpoint doctor <endpoint> --strict-closeout --allow-fail",
                "evidence": "python -m shujuan evidence verify --endpoint <endpoint>",
            },
        }
    )
    print_json(payload)
    return 0

def cmd_delegate_controller_close(args: argparse.Namespace) -> int:
    if args.apply:
        raise SystemExit(
            "delegate controller close is a diagnostic skeleton and will not close checks/tasks. "
            "Use work close/exec stop/evidence close primitives from the controller run after verification."
        )
    payload = delegate_base_payload(args, "controller close", role="controller")
    payload.update(
        {
            "dry_run": True,
            "would_close": False,
            "diagnostics": diagnostics_payload(
                usable=True,
                raw_count=3,
                visible_count=3,
                filtered_count=0,
                next_action="Controller must run existing closeout primitives explicitly; this delegate command only reports the safe path.",
            ),
            "routes": {
                "capture_change_set": "python -m shujuan exec stop --endpoint <endpoint>",
                "prove_checks": "python -m shujuan work prove --evidence-node <node> --check <check> --apply",
                "close": "python -m shujuan work close --apply --endpoint <endpoint>",
            },
            "next_action": "Controller must run existing closeout primitives explicitly; this delegate command only reports the safe path.",
        }
    )
    print_json(payload)
    return 0


@dataclass(frozen=True)
class DelegateCommandBoundary:
    collaboration_modes: Mapping[str, Any]
    delegate_lifecycle_states: list[str]
    handlers: Mapping[str, DelegateHandler]


def build_delegate_boundary(source: Mapping[str, Any]) -> DelegateCommandBoundary:
    """Create the delegate command-family boundary from shared CLI runtime helpers."""
    globals().update(_delegate_dependencies(source))
    handlers: dict[str, DelegateHandler] = {
        "plan": cmd_delegate_plan,
        "packet": cmd_delegate_packet,
        "import": cmd_delegate_import,
        "ownership": cmd_delegate_ownership,
        "review": cmd_delegate_review,
        "verify": cmd_delegate_verify,
        "status": cmd_delegate_status,
        "capsule": cmd_delegate_capsule,
        "controller_status": cmd_delegate_controller_status,
        "controller_close": cmd_delegate_controller_close,
    }
    return DelegateCommandBoundary(
        collaboration_modes=COLLABORATION_MODES,
        delegate_lifecycle_states=list(DELEGATE_LIFECYCLE_STATES),
        handlers=handlers,
    )
