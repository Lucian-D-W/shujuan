from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


ROLE_ALIASES = {
    "controller": "controller_agent",
    "controller_agent": "controller_agent",
    "worker": "worker_agent",
    "worker_agent": "worker_agent",
    "reviewer": "reviewer_agent",
    "reviewer_agent": "reviewer_agent",
    "researcher": "researcher_agent",
    "researcher_agent": "researcher_agent",
    "writer": "writer_agent",
    "writer_agent": "writer_agent",
}


@dataclass(frozen=True)
class RoleCard:
    role: str
    authority: str
    current_project_governance_write_authorized: bool
    can_close_checks_or_tasks: bool
    allowed_actions: tuple[str, ...]
    forbidden_actions: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        return asdict(self)


ROLE_CARDS: dict[str, RoleCard] = {
    "controller_agent": RoleCard(
        role="controller_agent",
        authority="owns governance DB writes, scope changes, endpoint refresh, exec stop, evidence import, and closure claims",
        current_project_governance_write_authorized=True,
        can_close_checks_or_tasks=True,
        allowed_actions=("governance orchestration", "evidence import", "endpoint closeout"),
        forbidden_actions=("treat provider facts or worker prose as closure evidence without verification",),
    ),
    "worker_agent": RoleCard(
        role="worker_agent",
        authority="implements scoped code, docs, templates, or tests and returns material to the controller",
        current_project_governance_write_authorized=False,
        can_close_checks_or_tasks=False,
        allowed_actions=("scoped implementation", "focused tests", "material handoff"),
        forbidden_actions=("current_project_governance_write", "endpoint_refresh", "exec_stop", "close_checks_or_tasks", "reinterpret_acceptance_criteria"),
    ),
    "reviewer_agent": RoleCard(
        role="reviewer_agent",
        authority="performs independent read-only review and returns advisory findings",
        current_project_governance_write_authorized=False,
        can_close_checks_or_tasks=False,
        allowed_actions=("read-only review", "risk report"),
        forbidden_actions=("mutate governance state", "close checks or tasks"),
    ),
    "researcher_agent": RoleCard(
        role="researcher_agent",
        authority="gathers source-backed facts and separates observations from inferences",
        current_project_governance_write_authorized=False,
        can_close_checks_or_tasks=False,
        allowed_actions=("source research", "impact context"),
        forbidden_actions=("create active findings", "close checks or tasks"),
    ),
    "writer_agent": RoleCard(
        role="writer_agent",
        authority="drafts summaries, reports, packets, or external prose",
        current_project_governance_write_authorized=False,
        can_close_checks_or_tasks=False,
        allowed_actions=("writing_no_governance by default",),
        forbidden_actions=("governance DB writes", "capture claims", "closure claims"),
    ),
}


def normalize_role(role: str | None) -> tuple[str | None, dict[str, Any] | None]:
    requested = (role or "worker_agent").strip() or "worker_agent"
    normalized = ROLE_ALIASES.get(requested.lower())
    if normalized is None:
        return None, {
            "code": "invalid_role",
            "message": "Unknown DCCP role; use controller, worker, reviewer, researcher, writer or *_agent.",
            "requested_role": requested,
            "valid_roles": sorted(ROLE_ALIASES),
        }
    return normalized, None


def role_capsule(role: str | None) -> dict[str, Any]:
    normalized, error = normalize_role(role)
    if error:
        return {"role": role, "ok": False, "error": error, "detail_ref": "AGENTS.md#DCCP Role Cards"}
    assert normalized is not None
    return {"ok": True, **ROLE_CARDS[normalized].payload(), "detail_ref": "AGENTS.md#DCCP Role Cards"}
