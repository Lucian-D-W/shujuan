from __future__ import annotations

from typing import Any


ENDPOINT_PROJECTION_KEYS = [
    "endpoint",
    "scope_contract",
    "tasks",
    "deferred_tasks",
    "open_checks",
    "deferred_checks",
    "closed_checks",
    "evidence",
    "recent_audit_findings",
    "inherited_active_blockers",
    "unresolved",
    "scope_changes",
    "defer_decisions",
    "assumptions",
    "recent_work_notes",
    "unlinked_scope_candidates",
    "semantic_projection",
    "discussion_brief",
    "chain_brief",
    "chain_children",
    "recent_discussions",
]


def endpoint_projection_facts(status: dict[str, Any]) -> dict[str, Any]:
    facts = {key: status.get(key) for key in ENDPOINT_PROJECTION_KEYS}
    endpoint = facts.get("endpoint") or {}
    if isinstance(endpoint, dict):
        facts["endpoint"] = {
            "id": endpoint.get("id"),
            "name": endpoint.get("name"),
            "description": endpoint.get("description"),
            "root_node_id": endpoint.get("root_node_id"),
        }
    return facts


def endpoint_latest_fact_at(status: dict[str, Any]) -> str | None:
    values: list[str] = []
    for key in ["tasks", "deferred_tasks", "open_checks", "deferred_checks", "closed_checks"]:
        for item in status.get(key) or []:
            for field in ["closed_at", "created_at", "updated_at"]:
                value = item.get(field)
                if value:
                    values.append(str(value))
    for key in ["evidence", "recent_audit_findings", "unresolved", "scope_changes", "defer_decisions", "assumptions", "recent_work_notes"]:
        for item in status.get(key) or []:
            value = item.get("created_at") or item.get("updated_at")
            if value:
                values.append(str(value))
    for item in status.get("recent_discussions") or []:
        value = item.get("created_at") or item.get("reviewed_at")
        if value:
            values.append(str(value))
    endpoint = status.get("endpoint") or {}
    if endpoint.get("created_at"):
        values.append(str(endpoint["created_at"]))
    return max(values) if values else None


__all__ = ["ENDPOINT_PROJECTION_KEYS", "endpoint_latest_fact_at", "endpoint_projection_facts"]
