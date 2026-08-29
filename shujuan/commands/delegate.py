from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from typing import Any


_CONTROLLER_ROLE_ALIASES = {
    "controller": "controller",
    "controller_agent": "controller",
    "worker": "worker",
    "worker_agent": "worker",
    "reviewer": "reviewer",
    "reviewer_agent": "reviewer",
    "researcher": "researcher",
    "researcher_agent": "researcher",
    "writer": "writer",
    "writer_agent": "writer",
    "provider": "provider",
    "provider_agent": "provider",
}
_DELEGATED_ROLE_ALIASES = {
    role: normalized
    for role, normalized in _CONTROLLER_ROLE_ALIASES.items()
    if normalized != "controller"
}
CONTROLLER_ROLES = sorted(_CONTROLLER_ROLE_ALIASES)
DELEGATED_ROLES = sorted(_DELEGATED_ROLE_ALIASES)
CONTROLLER_ROLE_METAVAR = "{" + ",".join(CONTROLLER_ROLES) + "}"
DELEGATED_ROLE_METAVAR = "{" + ",".join(DELEGATED_ROLES) + "}"


def _normalize_role(value: str, aliases: dict[str, str], label: str) -> str:
    normalized = aliases.get(value)
    if normalized is None:
        choices = ", ".join(sorted(aliases))
        raise argparse.ArgumentTypeError(f"invalid {label} role: {value!r}; choose one of: {choices}")
    return normalized


def _controller_role(value: str) -> str:
    return _normalize_role(value, _CONTROLLER_ROLE_ALIASES, "controller")


def _delegated_role(value: str) -> str:
    return _normalize_role(value, _DELEGATED_ROLE_ALIASES, "delegated")


def register_delegate(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    collaboration_modes: Mapping[str, Any],
    delegate_lifecycle_states: list[str],
    handlers: Mapping[str, Callable[[argparse.Namespace], int]],
) -> None:
    collaboration_mode_choices = sorted(collaboration_modes)

    delegate = subparsers.add_parser("delegate", help="DCCP delegation diagnostics and controller-only closeout skeletons.")
    delegate_sub = delegate.add_subparsers(dest="delegate_command", required=True)

    delegate_plan = delegate_sub.add_parser("plan")
    delegate_plan.add_argument("--endpoint")
    delegate_plan.add_argument("--task")
    delegate_plan.add_argument("--check")
    delegate_plan.add_argument("--lane")
    delegate_plan.add_argument("--role", default="worker", type=_controller_role, metavar=CONTROLLER_ROLE_METAVAR)
    delegate_plan.add_argument("--collaboration-mode", default="delegated-light-fix", choices=collaboration_mode_choices)
    delegate_plan.add_argument("--risk-trigger", action="append", default=[])
    delegate_plan.add_argument("--fast-light-fix", action="store_true")
    delegate_plan.add_argument("--runtime-preflight", action="store_true")
    delegate_plan.set_defaults(func=handlers["plan"])

    delegate_packet = delegate_sub.add_parser("packet")
    delegate_packet.add_argument("--endpoint")
    delegate_packet.add_argument("--task")
    delegate_packet.add_argument("--check")
    delegate_packet.add_argument("--lane")
    delegate_packet.add_argument("--role", default="worker", type=_controller_role, metavar=CONTROLLER_ROLE_METAVAR)
    delegate_packet.add_argument("--collaboration-mode", default="delegated-light-fix", choices=collaboration_mode_choices)
    delegate_packet.add_argument("--packet-kind", default="delegation", choices=["delegation", "return", "review", "research", "writer"])
    delegate_packet.add_argument("--body")
    delegate_packet.add_argument("--body-file", help="Read long packet body text from a UTF-8 file.")
    delegate_packet.add_argument("--goal")
    delegate_packet.add_argument("--claim")
    delegate_packet.add_argument("--hard-predicate", action="append", default=[])
    delegate_packet.add_argument("--pre-existing-dirty-path", action="append", default=[])
    delegate_packet.add_argument("--assigned-path", action="append", default=[])
    delegate_packet.add_argument("--owned-hunk-or-path", action="append", default=[])
    delegate_packet.add_argument("--inspected-only-path", action="append", default=[])
    delegate_packet.add_argument("--provider-runtime-path", action="append", default=[])
    delegate_packet.add_argument("--observed-only-path", action="append", default=[])
    delegate_packet.add_argument("--not-owned-path", action="append", default=[])
    delegate_packet.add_argument("--deleted-obsolete-path", action="append", default=[])
    delegate_packet.add_argument("--fallback-path", action="append", default=[])
    delegate_packet.add_argument("--out-of-scope-path", action="append", default=[])
    delegate_packet.add_argument("--fixture-write", action="append", default=[])
    delegate_packet.add_argument("--blocked-check", action="append", default=[])
    delegate_packet.add_argument("--unresolved-risk", action="append", default=[])
    delegate_packet.add_argument("--assumption", action="append", default=[])
    delegate_packet.add_argument("--known-red", action="append", default=[])
    delegate_packet.add_argument("--provider-seed")
    delegate_packet.add_argument("--provider-question")
    delegate_packet.add_argument("--provider-boundary")
    delegate_packet.add_argument("--provider-output-classification", choices=["provider_fact", "provider_hypothesis"])
    delegate_packet.add_argument("--provider-output", action="append", default=[])
    delegate_packet.add_argument("--allowed-scope", action="append", default=[])
    delegate_packet.add_argument("--must-read", action="append", default=[])
    delegate_packet.add_argument("--review-question", action="append", default=[])
    delegate_packet.add_argument("--escalation-trigger", action="append", default=[])
    delegate_packet.add_argument("--save-artifact", action="store_true")
    delegate_packet.add_argument("--runtime-preflight", action="store_true")
    delegate_packet.set_defaults(func=handlers["packet"])

    delegate_import = delegate_sub.add_parser("import")
    delegate_import.add_argument("--endpoint")
    delegate_import.add_argument("--task")
    delegate_import.add_argument("--check")
    delegate_import.add_argument("--lane")
    delegate_import.add_argument("--role", default="worker", type=_delegated_role, metavar=DELEGATED_ROLE_METAVAR)
    delegate_import.add_argument("--collaboration-mode", default="delegated-light-fix", choices=collaboration_mode_choices)
    delegate_import.add_argument("--import-kind", default="summary", choices=["summary", "artifact", "test_result", "review", "provider_fact"])
    delegate_import.add_argument("--classification")
    delegate_import.add_argument("--artifact")
    delegate_import.add_argument("--close-check", action="store_true")
    delegate_import.add_argument("--close-task", action="store_true")
    delegate_import.add_argument("--closeout", action="store_true")
    delegate_import.add_argument("--convert-to-evidence", action="store_true")
    delegate_import.add_argument("--runtime-preflight", action="store_true")
    delegate_import.set_defaults(func=handlers["import"])

    delegate_ownership = delegate_sub.add_parser("ownership")
    delegate_ownership.add_argument("--endpoint")
    delegate_ownership.add_argument("--task")
    delegate_ownership.add_argument("--check")
    delegate_ownership.add_argument("--lane")
    delegate_ownership.add_argument("--role", default="worker", type=_controller_role, metavar=CONTROLLER_ROLE_METAVAR)
    delegate_ownership.add_argument("--collaboration-mode", default="delegated-light-fix", choices=collaboration_mode_choices)
    delegate_ownership.add_argument("--pre-existing-dirty-path", action="append", default=[])
    delegate_ownership.add_argument("--assigned-path", action="append", default=[])
    delegate_ownership.add_argument("--claimed-path", action="append", default=[])
    delegate_ownership.add_argument("--claimed-hunk", action="append", default=[])
    delegate_ownership.add_argument("--provider-runtime-path", action="append", default=[])
    delegate_ownership.add_argument("--observed-only-path", action="append", default=[])
    delegate_ownership.add_argument("--not-owned-path", action="append", default=[])
    delegate_ownership.add_argument("--deleted-obsolete-path", action="append", default=[])
    delegate_ownership.add_argument("--fallback-path", action="append", default=[])
    delegate_ownership.add_argument("--out-of-scope-path", action="append", default=[])
    delegate_ownership.add_argument("--after-snapshot-path", action="append", default=[])
    delegate_ownership.add_argument("--runtime-preflight", action="store_true")
    delegate_ownership.set_defaults(func=handlers["ownership"])

    delegate_review_diag = delegate_sub.add_parser("review")
    delegate_review_diag.add_argument("--endpoint")
    delegate_review_diag.add_argument("--task")
    delegate_review_diag.add_argument("--check")
    delegate_review_diag.add_argument("--lane")
    delegate_review_diag.add_argument("--collaboration-mode", default="delegated-light-fix", choices=collaboration_mode_choices)
    delegate_review_diag.add_argument("--result", required=True, choices=["accept", "reject", "unclear"])
    delegate_review_diag.add_argument("--summary", required=True)
    delegate_review_diag.add_argument("--covered-predicate", action="append", default=[])
    delegate_review_diag.add_argument("--missing-predicate", action="append", default=[])
    delegate_review_diag.add_argument("--recommended-classification")
    delegate_review_diag.add_argument("--blocking", action="store_true")
    delegate_review_diag.add_argument("--needs-user-decision", action="store_true")
    delegate_review_diag.add_argument("--claims-closeout", action="store_true")
    delegate_review_diag.add_argument("--fail-on-overclaim", action="store_true")
    delegate_review_diag.add_argument("--runtime-preflight", action="store_true")
    delegate_review_diag.set_defaults(func=handlers["review"])

    delegate_verify = delegate_sub.add_parser("verify")
    delegate_verify.add_argument("--endpoint")
    delegate_verify.add_argument("--task")
    delegate_verify.add_argument("--check")
    delegate_verify.add_argument("--lane")
    delegate_verify.add_argument("--role", default="worker", type=_controller_role, metavar=CONTROLLER_ROLE_METAVAR)
    delegate_verify.add_argument("--collaboration-mode", default="delegated-light-fix", choices=collaboration_mode_choices)
    delegate_verify.add_argument("--claims-closeout", action="store_true")
    delegate_verify.add_argument("--allow-fail", action="store_true")
    delegate_verify.add_argument("--runtime-preflight", action="store_true")
    delegate_verify.set_defaults(func=handlers["verify"])

    delegate_status = delegate_sub.add_parser("status")
    delegate_status.add_argument("--endpoint")
    delegate_status.add_argument("--task")
    delegate_status.add_argument("--check")
    delegate_status.add_argument("--lane")
    delegate_status.add_argument("--role", default="controller", type=_controller_role, metavar=CONTROLLER_ROLE_METAVAR)
    delegate_status.add_argument("--collaboration-mode", default="delegated-light-fix", choices=collaboration_mode_choices)
    delegate_status.add_argument("--state", choices=delegate_lifecycle_states)
    delegate_status.add_argument("--from-state", choices=delegate_lifecycle_states)
    delegate_status.add_argument("--to-state", choices=delegate_lifecycle_states)
    delegate_status.add_argument("--allow-fail", action="store_true")
    delegate_status.add_argument("--runtime-preflight", action="store_true")
    delegate_status.set_defaults(func=handlers["status"])

    delegate_capsule = delegate_sub.add_parser("capsule")
    delegate_capsule.add_argument("--endpoint")
    delegate_capsule.add_argument("--task")
    delegate_capsule.add_argument("--check")
    delegate_capsule.add_argument("--lane")
    delegate_capsule.add_argument("--role", default="worker", type=_controller_role, metavar=CONTROLLER_ROLE_METAVAR)
    delegate_capsule.add_argument("--collaboration-mode", default="delegated-light-fix", choices=collaboration_mode_choices)
    delegate_capsule.add_argument("--hard-predicate", action="append", default=[])
    delegate_capsule.add_argument("--pre-existing-dirty-path", action="append", default=[])
    delegate_capsule.add_argument("--owned-hunk-or-path", action="append", default=[])
    delegate_capsule.add_argument("--inspected-only-path", action="append", default=[])
    delegate_capsule.add_argument("--provider-runtime-path", action="append", default=[])
    delegate_capsule.add_argument("--observed-only-path", action="append", default=[])
    delegate_capsule.add_argument("--not-owned-path", action="append", default=[])
    delegate_capsule.add_argument("--deleted-obsolete-path", action="append", default=[])
    delegate_capsule.add_argument("--fallback-path", action="append", default=[])
    delegate_capsule.add_argument("--out-of-scope-path", action="append", default=[])
    delegate_capsule.add_argument("--fixture-write", action="append", default=[])
    delegate_capsule.add_argument("--blocked-check", action="append", default=[])
    delegate_capsule.add_argument("--unresolved-risk", action="append", default=[])
    delegate_capsule.add_argument("--assumption", action="append", default=[])
    delegate_capsule.add_argument("--known-red", action="append", default=[])
    delegate_capsule.add_argument("--provider-seed")
    delegate_capsule.add_argument("--provider-question")
    delegate_capsule.add_argument("--provider-boundary")
    delegate_capsule.add_argument("--provider-output-classification", choices=["provider_fact", "provider_hypothesis"])
    delegate_capsule.add_argument("--provider-output", action="append", default=[])
    delegate_capsule.add_argument("--handoff", action="append", default=[])
    delegate_capsule.add_argument("--warning", action="append", default=[])
    delegate_capsule.add_argument("--must-read", action="append", default=[])
    delegate_capsule.add_argument("--next-slice")
    delegate_capsule.add_argument("--runtime-preflight", action="store_true")
    delegate_capsule.set_defaults(func=handlers["capsule"])

    delegate_controller = delegate_sub.add_parser("controller")
    delegate_controller_sub = delegate_controller.add_subparsers(dest="delegate_controller_command", required=True)

    delegate_controller_status = delegate_controller_sub.add_parser("status")
    delegate_controller_status.add_argument("--endpoint")
    delegate_controller_status.add_argument("--task")
    delegate_controller_status.add_argument("--check")
    delegate_controller_status.add_argument("--lane")
    delegate_controller_status.add_argument("--collaboration-mode", default="delegated-light-fix", choices=collaboration_mode_choices)
    delegate_controller_status.set_defaults(func=handlers["controller_status"])

    delegate_controller_close = delegate_controller_sub.add_parser("close")
    delegate_controller_close.add_argument("--endpoint")
    delegate_controller_close.add_argument("--task")
    delegate_controller_close.add_argument("--check")
    delegate_controller_close.add_argument("--lane")
    delegate_controller_close.add_argument("--collaboration-mode", default="delegated-light-fix", choices=collaboration_mode_choices)
    delegate_controller_close.add_argument("--apply", action="store_true")
    delegate_controller_close.set_defaults(func=handlers["controller_close"])
