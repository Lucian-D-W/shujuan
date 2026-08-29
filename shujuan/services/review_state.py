from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_REVIEW_STATE: dict[str, Any] = {
    "packet_requested": False,
    "packet_generated": False,
    "reviewer_executed": False,
    "controller_adopted": False,
    "evidence_imported": False,
    "material_only": False,
}
RUNTIME_REVIEW_STATE_KEYS = {"endpoint", "active_obligation", "state_kind", "state_path", "state_file_exists"}


def review_state_path(repo: Path, endpoint_name: str) -> Path:
    return repo / ".shujuan" / "artifacts" / endpoint_name / "review_state.json"


def default_review_state() -> dict[str, Any]:
    return dict(DEFAULT_REVIEW_STATE)


def normalize_review_state(payload: dict[str, Any] | None, *, endpoint_name: str, state_path: Path | None = None, repo: Path | None = None) -> dict[str, Any]:
    state = default_review_state()
    if isinstance(payload, dict):
        state.update(payload)
    packet_seen = bool(state.get("packet_requested") or state.get("packet_generated"))
    reviewer_executed = bool(state.get("reviewer_executed"))
    controller_adopted = bool(state.get("controller_adopted"))
    active_obligation = packet_seen and (not reviewer_executed or not controller_adopted)
    if packet_seen and not state.get("evidence_imported"):
        state["material_only"] = True
    state["endpoint"] = endpoint_name
    state["active_obligation"] = active_obligation
    state["state_kind"] = (
        "review_material_waiting_for_reviewer"
        if packet_seen and not reviewer_executed
        else "review_material_waiting_for_controller_adoption"
        if packet_seen and not controller_adopted
        else "review_material_adopted"
        if packet_seen
        else "review_not_requested"
    )
    if state_path is not None:
        try:
            state["state_path"] = str(state_path.relative_to(repo)).replace("\\", "/") if repo else str(state_path)
        except ValueError:
            state["state_path"] = str(state_path)
        state["state_file_exists"] = state_path.exists()
    return state


def load_review_state(repo: Path, endpoint_name: str) -> dict[str, Any]:
    path = review_state_path(repo, endpoint_name)
    if not path.exists():
        return normalize_review_state(None, endpoint_name=endpoint_name, state_path=path, repo=repo)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"state_read_error": True}
    if not isinstance(payload, dict):
        payload = {"state_read_error": True}
    return normalize_review_state(payload, endpoint_name=endpoint_name, state_path=path, repo=repo)


def write_review_state(repo: Path, endpoint_name: str, payload: dict[str, Any]) -> Path:
    path = review_state_path(repo, endpoint_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = {key: value for key, value in payload.items() if key not in RUNTIME_REVIEW_STATE_KEYS}
    path.write_text(json.dumps(stored, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    return path


def review_material_obligations(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not state or not state.get("active_obligation"):
        return []
    endpoint_name = str(state.get("endpoint") or "")
    if not state.get("reviewer_executed"):
        summary = "Review packet exists, but reviewer_executed=false; returned review material is still missing."
        next_action = "Run the reviewer and record the return artifact with `review record-return`."
    else:
        summary = "Reviewer returned material, but controller_adopted=false; the controller has not adopted it."
        next_action = "Run `review adopt` after controller review, then close checks only with matching evidence."
    return [
        {
            "id": f"review_state:{endpoint_name}",
            "type": "review_material",
            "endpoint": endpoint_name,
            "state_kind": state.get("state_kind"),
            "summary": summary,
            "detail_ref": state.get("state_path") or f".shujuan/artifacts/{endpoint_name}/review_state.json",
            "material_only": bool(state.get("material_only")),
            "reviewer_executed": bool(state.get("reviewer_executed")),
            "controller_adopted": bool(state.get("controller_adopted")),
            "evidence_imported": bool(state.get("evidence_imported")),
            "next_action": next_action,
        }
    ]
