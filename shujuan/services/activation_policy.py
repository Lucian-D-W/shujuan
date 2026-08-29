from __future__ import annotations

import re
from typing import Any

from .sovereignty_gate import NO_GOVERNANCE_MARKERS
from .sovereignty_gate import explicit_no_governance_reasons as _sovereignty_explicit_no_governance_reasons

EXPLICIT_NO_GOVERNANCE_INTENT_MARKERS = list(NO_GOVERNANCE_MARKERS)

_MARKER_NORMALIZE_RE = re.compile(r"[\s，。,.!?！？、；;：:'\"`“”‘’（）()\[\]{}<>《》/\\|_-]+")


def _normalize_marker_text(value: str) -> str:
    return _MARKER_NORMALIZE_RE.sub("", value.lower())

RECOVER_LIKE_INTENT_MARKERS = [
    "continue",
    "take over",
    "takeover",
    "resume",
    "recover",
    "new window",
    "handoff",
    "pick up",
    "where were we",
    "接手",
    "接管",
    "继续",
    "恢复",
    "续上",
    "新窗口",
]

HIGH_RISK_MODE_TERMS = [
    "full",
    "p0",
    "p1",
    "closeout",
    "closure",
    "schema",
    "migration",
    "security",
    "release",
    "cross-module",
    "broad",
    "named technology",
    "antv",
    "g6",
    "postgres",
    "provider",
    "evidence",
    "predicate",
    "proof matrix",
    "review",
    "endpoint propagation",
]


def normalize_mode(value: str | None, contracts: dict[str, dict[str, Any]]) -> str:
    normalized = (value or "standard").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "none": "no_governance",
        "no": "no_governance",
        "no_governance": "no_governance",
        "nogovernance": "no_governance",
        "capture_only": "capture",
        "cap": "capture",
        "exploration": "explore",
        "std": "standard",
        "default": "standard",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in contracts:
        allowed = ", ".join(sorted(contracts))
        raise SystemExit(f"mode must be one of: {allowed}")
    return normalized


def explicit_no_governance_reasons(intent: str) -> list[str]:
    return _sovereignty_explicit_no_governance_reasons(intent)


def recover_like_reasons(intent: str) -> list[str]:
    lowered = intent.lower()
    compact_intent = _normalize_marker_text(intent)
    matches = []
    for marker in RECOVER_LIKE_INTENT_MARKERS:
        normalized_marker = marker.lower()
        if normalized_marker in lowered or _normalize_marker_text(marker) in compact_intent:
            matches.append(f"recover_like:{marker}")
    return matches


def acceptance_template_for_mode(mode: str) -> dict[str, Any]:
    if mode in {"no_governance", "capture", "explore"}:
        return {"required": False, "expected_evidence_type": None, "body": "No acceptance check is created by this mode."}
    expected = "test_result" if mode in {"standard", "full"} else "change_set"
    body = {
        "light": "Scoped change is captured and linked to the task without broad closeout claims.",
        "standard": "Targeted tests or smoke evidence verify the scoped behavior.",
        "full": "Impact review, targeted tests, and endpoint doctor/evidence verify support closeout.",
    }[mode]
    return {"required": True, "expected_evidence_type": expected, "body": body}


def allowed_side_effects_for_mode(mode: str) -> list[str]:
    return {
        "no_governance": ["no shujuan DB writes", "no capture claim", "no agent_run"],
        "capture": ["write interaction/discussion source material", "no agent_run", "no change_set"],
        "explore": ["write interaction/discussion source material", "record questions or exploratory source material", "no agent_run", "no change_set"],
        "light": ["start scoped agent_run", "write current_work handle", "closeout may later record a change_set through exec stop/work close"],
        "standard": ["start scoped agent_run", "write current_work handle", "targeted evidence may later close checks", "closeout may later record a change_set through exec stop/work close"],
        "full": ["start scoped agent_run", "write current_work handle", "strict closeout may refresh endpoint projection", "targeted evidence and verification may later close checks"],
    }[mode]


def forbidden_side_effects_for_mode(mode: str) -> list[str]:
    if mode == "no_governance":
        return ["DB fact writes", "interaction capture claim", "agent_run", "change_set", "check/task closure"]
    if mode in {"capture", "explore"}:
        return ["agent_run", "change_set", "check/task closure", "endpoint closeout claim"]
    if mode == "light":
        return ["broad impact closeout", "strict endpoint completion claim", "provider output as closure evidence"]
    if mode == "standard":
        return ["unstated Full closeout", "provider output as closure evidence", "check/task closure without matching evidence"]
    return ["provider output as closure evidence", "override closeout without explicit reason"]


def mode_contract_payload(mode: str, contracts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    contract = dict(contracts[mode])
    contract["mode"] = mode
    contract["aliases"] = {
        "no": "no_governance",
        "capture_only": "capture",
        "exploration": "explore",
        "std": "standard",
    }
    contract["acceptance_template"] = acceptance_template_for_mode(mode)
    contract["side_effect_boundary"] = {
        "selected_mode": mode,
        "direct_exec_default": "exec start defaults to Standard when --mode is omitted.",
        "diagnostic_route": "Use mode suggest --intent <intent> or workflow begin output to inspect the mode contract before execution.",
        "allowed_side_effects": allowed_side_effects_for_mode(mode),
        "forbidden_side_effects": forbidden_side_effects_for_mode(mode),
    }
    return contract


def mode_gate_warnings(mode: str, intent: str | None) -> list[dict[str, Any]]:
    text = (intent or "").lower()
    high_risk_terms = [term for term in HIGH_RISK_MODE_TERMS if term in text]
    warnings: list[dict[str, Any]] = []
    if mode == "light" and high_risk_terms:
        warnings.append(
            {
                "code": "mode_friction_high_risk_light",
                "gate": "G7",
                "message": "High-risk work must not silently downgrade to Light.",
                "matched_terms": high_risk_terms,
            }
        )
    if mode == "full" and not high_risk_terms and text:
        warnings.append(
            {
                "code": "mode_friction_low_risk_full",
                "gate": "G7",
                "message": "Low-risk work should not be forced to Full without an explicit reason.",
            }
        )
    return warnings


__all__ = [
    "EXPLICIT_NO_GOVERNANCE_INTENT_MARKERS",
    "HIGH_RISK_MODE_TERMS",
    "RECOVER_LIKE_INTENT_MARKERS",
    "acceptance_template_for_mode",
    "allowed_side_effects_for_mode",
    "explicit_no_governance_reasons",
    "forbidden_side_effects_for_mode",
    "mode_contract_payload",
    "mode_gate_warnings",
    "normalize_mode",
    "recover_like_reasons",
]
