from __future__ import annotations

from typing import Any

from ..commands.provider import (
    DEFAULT_IMPACT_SOURCE,
    GITNEXUS_INDEX_PATH,
    PREFERRED_IMPACT_PROVIDER,
    provider_closure_evidence_boundary,
)


COVERED_COMMAND_BOUNDARY = [
    "endpoint doctor --strict-closeout --read-only",
    "endpoint doctor --strict-closeout",
    "exec stop",
    "scope change",
    "delegate packet",
    "schema guard",
    "work close --dry-run",
]


def _effects(
    command: str,
    *,
    read_db: bool,
    write_db: bool,
    refresh_projection: bool,
    close_scope: bool,
    provider_output: bool,
    role_limited: bool,
    role: dict[str, Any],
    writes: list[str] | None = None,
    reads: list[str] | None = None,
    refresh: dict[str, Any] | None = None,
    close: dict[str, Any] | None = None,
    provider: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "command_effects.v1",
        "coverage": "finite_v7_p0_01_high_risk_commands",
        "covered_command_boundary": COVERED_COMMAND_BOUNDARY,
        "command": command,
        "read_db": read_db,
        "write_db": write_db,
        "refresh_projection": refresh_projection,
        "close_scope": close_scope,
        "provider_output": provider_output,
        "role_limited": role_limited,
        "reads": reads or [],
        "writes": writes or [],
        "refresh": refresh or {"projection": refresh_projection},
        "close": close or {"can_close": close_scope},
        "provider": provider
        or {
            "runs_provider": False,
            "emits_provider_facts": False,
            "material_only": True,
        },
        "role": role,
        "notes": notes or [],
    }


def endpoint_doctor_effects(*, strict_closeout: bool, read_only: bool) -> dict[str, Any]:
    writeful_strict = bool(strict_closeout and not read_only)
    return _effects(
        "endpoint doctor",
        read_db=True,
        write_db=writeful_strict,
        refresh_projection=writeful_strict,
        close_scope=False,
        provider_output=False,
        role_limited=writeful_strict,
        reads=["endpoint projection facts", "tasks/checks/evidence/blockers"],
        writes=["endpoint projection body"] if writeful_strict else [],
        refresh={
            "projection": writeful_strict,
            "policy": "strict_closeout_refresh" if writeful_strict else "suppressed_by_read_only" if strict_closeout else "diagnostic_only",
        },
        close={"can_close": False, "closes_checks": False, "closes_tasks": False},
        role={
            "required_authority": "controller_agent" if writeful_strict else "read_only_diagnostic",
            "worker_allowed": not writeful_strict,
            "controller_only_closeout": writeful_strict,
        },
        notes=[
            "Read-only strict doctor diagnoses closeout blockers without refreshing projection.",
            "Strict doctor without read-only is the controller closeout diagnostic path.",
        ],
    )


def exec_stop_effects(*, close_check: bool, close_task: bool, impact: bool, no_impact: bool) -> dict[str, Any]:
    provider_runs = bool(impact and not no_impact)
    close_requested = bool(close_check or close_task)
    return _effects(
        "exec stop",
        read_db=True,
        write_db=True,
        refresh_projection=False,
        close_scope=close_requested,
        provider_output=provider_runs,
        role_limited=True,
        reads=["active run handle", "before snapshot", "endpoint obligations"],
        writes=["after snapshot", "agent run stop fields", "change_set", "endpoint closeout body"],
        refresh={"projection": False, "writes_endpoint_closeout_body": True},
        close={
            "can_close": True,
            "requested": close_requested,
            "closes_checks": bool(close_check),
            "closes_tasks": bool(close_task),
            "requires_matching_evidence": True,
        },
        provider={
            "runs_provider": provider_runs,
            "emits_provider_facts": provider_runs,
            "material_only": True,
            "default_source": DEFAULT_IMPACT_SOURCE,
            "entrypoint_used": "gitnexus_cli_opt_in" if provider_runs else "default_skipped_no_impact",
            "provider_detail": {
                "name": PREFERRED_IMPACT_PROVIDER,
                "role": "optional direct graph provider",
                "index_path": GITNEXUS_INDEX_PATH.as_posix(),
                "invoked": provider_runs,
            },
            "closure_evidence_boundary": provider_closure_evidence_boundary(),
            "default": "GitNexus is the only impact provider; direct CLI execution is skipped unless --impact is set.",
        },
        role={
            "required_authority": "controller_agent",
            "worker_allowed": False,
            "controller_only_closeout": True,
        },
        notes=["Provider output remains material only and cannot close checks/tasks by itself."],
    )


def scope_change_effects(*, task_count: int, applies_to_count: int) -> dict[str, Any]:
    defers_task = task_count > 0
    state_effects = {
        "task_targets": {
            "count": task_count,
            "deferred_by_edge_added": defers_task,
            "endpoint_lifecycle_effect": "treated_as_deferred_non_active" if defers_task else "unchanged",
            "recommended_defer_route": "task defer --task",
        },
        "applies_to_targets": {
            "count": applies_to_count,
            "deferred_by_edge_added": False,
            "endpoint_lifecycle_effect": "unchanged",
        },
    }
    return _effects(
        "scope change",
        read_db=True,
        write_db=True,
        refresh_projection=False,
        close_scope=False,
        provider_output=False,
        role_limited=True,
        reads=["source node", "target tasks or nodes"],
        writes=["scope_change node", "semantic_item", "DERIVED_FROM edge", "APPLIES_TO edge"]
        + (["DEFERRED_BY edge"] if defers_task else []),
        refresh={"projection": False},
        close={
            "can_close": False,
            "defers_task": defers_task,
            "task_targets": task_count,
            "applies_to_targets": applies_to_count,
            "state_effects": state_effects,
        },
        role={
            "required_authority": "controller_agent",
            "worker_allowed": False,
            "controller_only_scope_change": True,
        },
        notes=[
            "`scope change --task` is state-changing/defer-like: it adds DEFERRED_BY task edges and endpoint reports treat those tasks as deferred/non-active.",
            "`scope change --applies-to` records a scope note only and does not change active/deferred lifecycle state.",
            "For ordinary task deferral, prefer `task defer --task`.",
        ],
    )


def delegate_packet_effects(*, role: str, save_artifact: bool, runtime_preflight: bool) -> dict[str, Any]:
    return _effects(
        "delegate packet",
        read_db=bool(runtime_preflight),
        write_db=False,
        refresh_projection=False,
        close_scope=False,
        provider_output=True,
        role_limited=True,
        reads=["runtime/schema preflight"] if runtime_preflight else [],
        writes=["packet artifact file"] if save_artifact else [],
        refresh={"projection": False},
        close={"can_close": False, "controller_only_closeout": True},
        provider={
            "runs_provider": False,
            "emits_provider_facts": False,
            "material_only": True,
            "guides_provider_outputs": True,
        },
        role={
            "packet_role": role,
            "requested_role": role,
            "actual_authority": "delegate_packet_material_only",
            "required_authority": "delegate_packet_material_only",
            "worker_allowed": role != "controller",
            "controller_only_closeout": True,
            "authority_assertion_is_self_reported": role == "controller",
        },
        notes=["Saving a packet artifact writes a file only; it does not persist a governance delegation row."],
    )


def work_close_dry_run_effects(*, mode: str, endpoint: str | None, has_active_run: bool) -> dict[str, Any]:
    would_create_change_set = bool(has_active_run and mode in {"light", "standard", "full"})
    return _effects(
        "work close --dry-run",
        read_db=bool(endpoint),
        write_db=False,
        refresh_projection=False,
        close_scope=False,
        provider_output=False,
        role_limited=True,
        reads=["endpoint active report", "closeout gate matrix"] if endpoint else ["active run handle"],
        writes=[],
        refresh={"projection": False},
        close={
            "can_close": False,
            "dry_run": True,
            "would_create_change_set": would_create_change_set,
            "would_require_strict_doctor": mode == "full",
        },
        role={
            "required_authority": "controller_agent",
            "worker_allowed": True,
            "worker_allowed_reason": "dry-run diagnostic only",
            "controller_only_apply": True,
        },
        notes=["Dry-run is the worksheet surface; use --apply to perform controller-owned closeout writes."],
    )


__all__ = [
    "COVERED_COMMAND_BOUNDARY",
    "delegate_packet_effects",
    "endpoint_doctor_effects",
    "exec_stop_effects",
    "scope_change_effects",
    "work_close_dry_run_effects",
]
