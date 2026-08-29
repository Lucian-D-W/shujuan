from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

CORE_FILES = [
    "AGENTS.md",
    ".agents/skills/shujuan-core/SKILL.md",
    ".agents/skills/shujuan-core/references/activation-first.md",
    ".agents/skills/shujuan-core/references/evidence-closeout.md",
    ".agents/skills/shujuan-core/references/delegation.md",
    ".agents/skills/shujuan-core/references/modes-and-terms.md",
    ".agents/skills/shujuan-core/references/postgres-runtime.md",
    ".agents/skills/shujuan-core/templates/delegate-return.md",
    ".agents/skills/shujuan-core/templates/closeout-handoff.md",
    "README.md",
    "shujuan/cli.py",
]

ROUTES = {
    "Recover": [
        "new window",
        "resumed thread",
        "handoff",
        "report endpoint <endpoint> --active-only --markdown",
        "endpoint doctor <endpoint> --strict-closeout --read-only --allow-fail",
    ],
    "Recall": [
        "history",
        "rationale",
        "why did this change",
        "report endpoint <endpoint> --full --markdown",
        "report project --markdown",
    ],
    "Execute": [
        "DB readiness gate",
        "workflow begin",
        "exec start",
        "exec stop",
    ],
    "Close": [
        "endpoint",
        "task_id",
        "check_id",
        "expected_evidence_type",
        "current_matching_evidence_ref",
        "evidence verify",
        "strict doctor",
    ],
    "Delegate": [
        "role-bounded",
        "provider/impact output",
        "same adoption rule still applies",
        "not closure evidence by themselves",
        "controller adoption",
        "import",
        "independent verification",
    ],
}

TERMS = [
    "endpoint",
    "active",
    "closed",
    "resolved",
    "deferred",
    "product_backlog",
    "audit_finding",
    "evidence",
    "provider_fact",
    "provider_hypothesis",
    "PostgreSQL success",
    "interaction_event",
    "discussion_segment",
    "discussion_message",
    "mode_router",
    "projection payload",
    "read-only workbench",
    "detail_ref",
    "hidden_source_count",
]

OLD_DEVIATION_MARKERS = [
    "forbidden actions",
    "must also say they were not closed",
    "No-closure attestation",
    "Read-only attestation",
    "manifest_is_closure_evidence=false",
]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def assert_contains(text: str, needle: str, rel: str) -> None:
    if needle not in text:
        raise AssertionError(f"{rel} missing {needle!r}")


def assert_shared_route_grammar() -> None:
    for rel in ["AGENTS.md", ".agents/skills/shujuan-core/SKILL.md", "shujuan/cli.py"]:
        text = read(rel)
        assert_contains(text, "Shared Route Grammar", rel)
        for phrase in ["Trigger", "First surface", "Action chain", "Evidence/adoption", "Handoff"]:
            if phrase not in text and phrase.lower() not in text:
                raise AssertionError(f"{rel} missing route grammar slot {phrase}")


def assert_route_scenarios() -> None:
    combined = "\n".join(read(rel) for rel in CORE_FILES)
    for route, expectations in ROUTES.items():
        assert_contains(combined, f"`{route}`", "combined route surface")
        missing = [item for item in expectations if item not in combined]
        if missing:
            raise AssertionError(f"{route} route is missing scenario/command cues: {missing}")


def assert_unified_terms_in_agents() -> None:
    agents = read("AGENTS.md")
    missing = [term for term in TERMS if f"`{term}`" not in agents and term not in agents]
    if missing:
        raise AssertionError(f"AGENTS.md no longer carries unified terms: {missing}")
    if re.search(r"\bv[34]\b|\bV[34]\b", agents):
        raise AssertionError("AGENTS.md should not expose version labels in default terminology")


def assert_positive_boundary_wording() -> None:
    core = "\n".join(
        read(rel)
        for rel in [
            "AGENTS.md",
            ".agents/skills/shujuan-core/SKILL.md",
            ".agents/skills/shujuan-core/references/activation-first.md",
            ".agents/skills/shujuan-core/references/delegation.md",
            ".agents/skills/shujuan-core/templates/delegate-return.md",
            ".agents/skills/shujuan-core/templates/closeout-handoff.md",
        ]
    )
    lowered = core.lower()
    found = [marker for marker in OLD_DEVIATION_MARKERS if marker.lower() in lowered]
    if found:
        raise AssertionError(f"old negative/boundary-heavy markers remain in core agent surfaces: {found}")
    positive_markers = [
        "controller adoption",
        "controller evidence route",
        "bounded material",
        "authority boundary",
        "returns changed files",
        "route's commands in order",
    ]
    missing = [marker for marker in positive_markers if marker not in core]
    if missing:
        raise AssertionError(f"positive route/adoption markers missing: {missing}")


def assert_closeout_handoff_inputs() -> None:
    handoff = read(".agents/skills/shujuan-core/templates/closeout-handoff.md")
    for phrase in ["Expected evidence type", "Current matching evidence ref", "Controller closeout action"]:
        assert_contains(handoff, phrase, "closeout-handoff.md")


def assert_db_gate_is_hard_and_early() -> None:
    for rel in ["AGENTS.md", "shujuan/cli.py"]:
        text = read(rel)
        gate = "DB readiness gate"
        start = "python -m shujuan postgres-dev start"
        status = "python -m shujuan postgres-dev status"
        for phrase in [gate, start, status, "continue only after"]:
            assert_contains(text, phrase, rel)
    skill = read(".agents/skills/shujuan-core/SKILL.md")
    if "DB readiness gate" not in skill:
        raise AssertionError("SKILL.md lost the DB readiness route cue")
    if "python -m shujuan " in skill:
        raise AssertionError("SKILL.md should not duplicate command maps while preserving the DB gate cue")


def main() -> int:
    assert_shared_route_grammar()
    assert_route_scenarios()
    assert_unified_terms_in_agents()
    assert_positive_boundary_wording()
    assert_closeout_handoff_inputs()
    assert_db_gate_is_hard_and_early()
    print(json.dumps({"ok": True, "n04_positive_route_alignment": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
