from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _configure(deps: Mapping[str, Any]) -> None:
    globals().update(deps)


ACTIVE_STATUSES = {"active", "current", "open"}
INACTIVE_RESIDUAL_STATUSES = {"absorbed", "superseded", "indirectly_dissolved"}
ARTIFACT_ONLY_DESTINATIONS = {"artifact", "report", "doc", "documentation", "ordered_plan"}
BROAD_PARENT_DESTINATIONS = {"umbrella", "broad_parent", "deferred_parent"}
VALID_CLASSIFICATIONS = {"P0", "P1", "P2", "P3", "non-goal", "non_goal", "deferred", "product_backlog", "out_of_scope"}


def _load_plan_artifact(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"could not read plan-to-db artifact: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"plan-to-db artifact is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("plan-to-db artifact must be a JSON object")
    return payload


def _load_plan_artifact_result(path: Path, *, gate: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        return _load_plan_artifact(path), None
    except SystemExit as exc:
        message = str(exc)
        return None, json_error_payload(
            "invalid_plan_to_db_artifact",
            message,
            gate=gate,
            read_only=True,
            artifact=str(path),
            violations=[{"code": "invalid_artifact", "message": message}],
        )


def _destination_parts(item: dict[str, Any]) -> tuple[str | None, str | None]:
    destination = item.get("graph_destination")
    if isinstance(destination, dict):
        kind = destination.get("kind") or destination.get("type")
        destination_id = destination.get("id") or destination.get("node_id") or destination.get("task_id") or destination.get("check_id")
        return str(kind) if kind else None, str(destination_id) if destination_id else None
    if isinstance(destination, str) and destination:
        return destination, None
    return None, None


def _item_id(item: dict[str, Any], index: int) -> str:
    return str(item.get("id") or item.get("source_id") or item.get("source_ref") or f"source_item[{index}]")


def _source_item_ids(payload: dict[str, Any]) -> list[str]:
    source_items = payload.get("source_items") if isinstance(payload.get("source_items"), list) else []
    return [_item_id(item, index) for index, item in enumerate(source_items) if isinstance(item, dict)]


def _inactive_source_item_ids(payload: dict[str, Any]) -> set[str]:
    source_items = payload.get("source_items") if isinstance(payload.get("source_items"), list) else []
    return {
        _item_id(item, index)
        for index, item in enumerate(source_items)
        if isinstance(item, dict) and str(item.get("status") or "active") in INACTIVE_RESIDUAL_STATUSES
    }


def _ordinary_source_refs(payload: dict[str, Any], refs: list[str]) -> list[str]:
    inactive = _inactive_source_item_ids(payload)
    return [ref for ref in refs if ref not in inactive]


def _duplicate_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _synthetic_refs(entry: dict[str, Any]) -> list[str]:
    explicit = entry.get("derived_from_source_items") or []
    if isinstance(explicit, str):
        return [explicit]
    if isinstance(explicit, list):
        return [str(value) for value in explicit if value]
    return []


def _is_synthetic(entry: dict[str, Any]) -> bool:
    return bool(entry.get("synthetic"))


def _entry_source_refs(payload: dict[str, Any], entry: dict[str, Any], *, key: str, kind: str) -> list[str]:
    explicit_refs = entry.get("source_refs") or entry.get("source_items") or entry.get("source_item_ids")
    if isinstance(explicit_refs, str):
        return _ordinary_source_refs(payload, list(dict.fromkeys([explicit_refs, *_synthetic_refs(entry)])))
    if isinstance(explicit_refs, list) and explicit_refs:
        return _ordinary_source_refs(payload, list(dict.fromkeys([*[str(value) for value in explicit_refs], *_synthetic_refs(entry)])))

    refs: list[str] = []
    inactive = _inactive_source_item_ids(payload)
    source_items = payload.get("source_items") if isinstance(payload.get("source_items"), list) else []
    id_field = "task_ids" if kind == "task" else "check_ids"
    for index, raw_item in enumerate(source_items):
        if not isinstance(raw_item, dict):
            continue
        item_id = _item_id(raw_item, index)
        if item_id in inactive:
            continue
        entry_ids = [str(value) for value in raw_item.get(id_field) or []]
        destination_kind, destination_id = _destination_parts(raw_item)
        if key in entry_ids:
            refs.append(item_id)
        elif kind == "task" and destination_kind == "task" and destination_id == key:
            refs.append(item_id)
        elif kind == "check" and destination_kind in {"acceptance_check", "check"} and destination_id == key:
            refs.append(item_id)
    return _ordinary_source_refs(payload, list(dict.fromkeys([*refs, *_synthetic_refs(entry)])))


def _source_item_mapping(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    source_items = payload.get("source_items") if isinstance(payload.get("source_items"), list) else []
    for index, raw_item in enumerate(source_items):
        if not isinstance(raw_item, dict):
            continue
        item_id = _item_id(raw_item, index)
        mapping[item_id] = {
            "classification": raw_item.get("classification") or raw_item.get("priority"),
            "status": raw_item.get("status") or "active",
            "graph_destination": raw_item.get("graph_destination"),
            "task_keys": [str(value) for value in raw_item.get("task_ids") or []],
            "check_keys": [str(value) for value in raw_item.get("check_ids") or []],
        }
    for task in payload.get("tasks") or []:
        if not isinstance(task, dict) or not task.get("key"):
            continue
        task_key = str(task["key"])
        for ref in _entry_source_refs(payload, task, key=task_key, kind="task"):
            mapping.setdefault(ref, {"task_keys": [], "check_keys": []})
            mapping[ref].setdefault("task_keys", [])
            if task_key not in mapping[ref]["task_keys"]:
                mapping[ref]["task_keys"].append(task_key)
    for check in payload.get("checks") or []:
        if not isinstance(check, dict) or not check.get("key"):
            continue
        check_key = str(check["key"])
        for ref in _entry_source_refs(payload, check, key=check_key, kind="check"):
            mapping.setdefault(ref, {"task_keys": [], "check_keys": []})
            mapping[ref].setdefault("check_keys", [])
            if check_key not in mapping[ref]["check_keys"]:
                mapping[ref]["check_keys"].append(check_key)
    return mapping


def _source_coverage(payload: dict[str, Any]) -> dict[str, Any]:
    coverage = {
        "tasks": {},
        "checks": {},
        "uncovered_tasks": [],
        "uncovered_checks": [],
        "synthetic_tasks": [],
        "synthetic_checks": [],
    }
    for task in payload.get("tasks") or []:
        if not isinstance(task, dict) or not task.get("key"):
            continue
        key = str(task["key"])
        refs = _entry_source_refs(payload, task, key=key, kind="task")
        synthetic = _is_synthetic(task)
        coverage["tasks"][key] = {"source_refs": refs, "synthetic": synthetic}
        if synthetic:
            coverage["synthetic_tasks"].append(key)
        if not refs:
            coverage["uncovered_tasks"].append(key)
    for check in payload.get("checks") or []:
        if not isinstance(check, dict) or not check.get("key"):
            continue
        key = str(check["key"])
        refs = _entry_source_refs(payload, check, key=key, kind="check")
        synthetic = _is_synthetic(check)
        coverage["checks"][key] = {"source_refs": refs, "synthetic": synthetic}
        if synthetic:
            coverage["synthetic_checks"].append(key)
        if not refs:
            coverage["uncovered_checks"].append(key)
    return coverage


def _coverage_error_code(violations: list[dict[str, Any]]) -> str:
    coverage_codes = {
        "duplicate_source_item_id",
        "ambiguous_source_ref",
        "unknown_source_ref",
        "uncovered_task_without_source_item",
        "uncovered_check_without_source_item",
        "dangling_source_item_task_id",
        "dangling_source_item_check_id",
        "source_destination_mismatch",
        "synthetic_task_missing_controller_allow",
        "synthetic_task_missing_rationale",
        "synthetic_task_missing_derived_sources",
        "synthetic_check_missing_controller_allow",
        "synthetic_check_missing_rationale",
        "synthetic_check_missing_derived_sources",
    }
    return "task_chain_source_coverage_gap" if any(item.get("code") in coverage_codes for item in violations) else "invalid_task_chain_import"


def _plan_to_db_violations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if payload.get("declares_no_closure") is not True:
        violations.append(
            {
                "code": "missing_declares_no_closure",
                "message": "decomposition artifact must declare declares_no_closure=true",
            }
        )
    source_items = payload.get("source_items")
    if not isinstance(source_items, list) or not source_items:
        violations.append(
            {
                "code": "missing_source_items",
                "message": "decomposition artifact must contain a non-empty source_items list",
            }
        )
        return violations

    for index, raw_item in enumerate(source_items):
        if not isinstance(raw_item, dict):
            violations.append({"code": "invalid_source_item", "item": f"source_item[{index}]", "message": "source item must be an object"})
            continue
        item = raw_item
        item_id = _item_id(item, index)
        status = str(item.get("status") or "active")
        classification = item.get("classification") or item.get("priority")
        destination_kind, destination_id = _destination_parts(item)
        task_ids = [str(value) for value in item.get("task_ids") or ([] if not item.get("task_id") else [item.get("task_id")])]
        check_ids = [str(value) for value in item.get("check_ids") or ([] if not item.get("check_id") else [item.get("check_id")])]
        relation_target = item.get("absorbed_by") or item.get("superseded_by") or item.get("dissolved_by") or destination_id
        rationale = item.get("rationale") or item.get("absorption_rationale") or item.get("supersession_rationale")
        required_relation_field = {
            "absorbed": "absorbed_by",
            "superseded": "superseded_by",
            "indirectly_dissolved": "dissolved_by",
        }.get(status)

        for field in ("classification", "graph_destination", "rationale", "promotion_rule", "reopen_rule"):
            if field == "classification":
                present = bool(classification)
            elif field == "graph_destination":
                present = bool(destination_kind)
            else:
                present = bool(item.get(field))
            if not present:
                violations.append({"code": "missing_output_shape_field", "item": item_id, "field": field})
        if classification and str(classification) not in VALID_CLASSIFICATIONS:
            violations.append({"code": "invalid_classification", "item": item_id, "classification": classification})

        named_deliverables = item.get("named_deliverables") or []
        decomposed_items = item.get("decomposed_items") or []
        if len(named_deliverables) > 1 and len(decomposed_items) < len(named_deliverables):
            violations.append(
                {
                    "code": "compressed_named_deliverables",
                    "item": item_id,
                    "message": "multiple named deliverables require one graph-bound decomposition entry per deliverable",
                    "named_deliverables": named_deliverables,
                }
            )

        if status in ACTIVE_STATUSES:
            if classification in {"P0", "P1"} and destination_kind in BROAD_PARENT_DESTINATIONS:
                violations.append(
                    {
                        "code": "unsafe_broad_parent_promotion",
                        "item": item_id,
                        "classification": classification,
                        "graph_destination": destination_kind,
                    }
                )
            if destination_kind in {"task", "acceptance_check", "check"} and not task_ids and not check_ids:
                violations.append(
                    {
                        "code": "active_destination_missing_task_or_check_ids",
                        "item": item_id,
                        "graph_destination": destination_kind,
                        "message": "active source-plan items mapped to task/check destinations must name explicit task_ids or check_ids",
                    }
                )
            if destination_kind in ARTIFACT_ONLY_DESTINATIONS and not task_ids and not check_ids:
                violations.append(
                    {
                        "code": "artifact_only_slice",
                        "item": item_id,
                        "message": "active source-plan items need task/check graph destinations, not artifact-only prose",
                    }
                )
        elif status in INACTIVE_RESIDUAL_STATUSES:
            if required_relation_field and not item.get(required_relation_field):
                violations.append(
                    {
                        "code": "missing_inactive_relation_field",
                        "item": item_id,
                        "status": status,
                        "field": required_relation_field,
                    }
                )
            if not relation_target:
                violations.append({"code": "unlinked_inactive_item", "item": item_id, "status": status})
            if not rationale:
                violations.append({"code": "missing_absorption_or_supersession_rationale", "item": item_id, "status": status})
        elif status in {"deferred", "product_backlog", "out_of_scope", "non_goal"}:
            if not rationale:
                violations.append({"code": "missing_non_active_rationale", "item": item_id, "status": status})
        else:
            violations.append({"code": "unknown_source_item_status", "item": item_id, "status": status})

    if payload.get("closed_by_decomposition"):
        violations.append({"code": "false_closeout_claim", "message": "plan decomposition must not claim task/check closure"})
    return violations


def plan_to_db_verify_payload(payload: dict[str, Any]) -> dict[str, Any]:
    violations = _plan_to_db_violations(payload)
    source_items = payload.get("source_items") if isinstance(payload.get("source_items"), list) else []
    return {
        "ok": not violations,
        "gate": "plan_to_db_decomposition",
        "read_only": True,
        "checked_source_items": len(source_items),
        "violations": violations,
        "required_output_shape": [
            "classification",
            "graph_destination",
            "rationale",
            "promotion_rule",
            "reopen_rule",
            "absorbed_by/superseded_by/dissolved_by when inactive",
        ],
        "catches": [
            "compressed_named_deliverables",
            "artifact_only_slice",
            "unsafe_broad_parent_promotion",
            "unlinked_inactive_item",
            "false_closeout_claim",
        ],
        "next_action": "Fix the decomposition artifact, then let the controller import or apply material through normal task/check/lifecycle commands.",
    }


def cmd_plan_to_db_verify(args: argparse.Namespace) -> int:
    artifact, error = _load_plan_artifact_result(args.artifact, gate="plan_to_db_decomposition")
    if error:
        print_json(error)
        return 1
    payload = plan_to_db_verify_payload(artifact)
    print_json(payload)
    return 0 if payload["ok"] or args.allow_fail else 1


def _task_chain_payload(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    payload, error = _load_plan_artifact_result(path, gate="plan_to_db_import_task_chain")
    if error:
        return None, error
    assert payload is not None
    violations: list[dict[str, Any]] = []
    if not isinstance(payload.get("tasks"), list):
        violations.append({"code": "missing_tasks_list", "message": "task-chain artifact must contain tasks[] list"})
    if not isinstance(payload.get("checks"), list):
        violations.append({"code": "missing_checks_list", "message": "task-chain artifact must contain checks[] list"})
    if violations:
        return None, {
            "ok": False,
            "read_only": True,
            "gate": "plan_to_db_import_task_chain",
            "artifact": str(path),
            "error": {"code": "invalid_task_chain_artifact", "message": "task-chain artifact failed structural validation"},
            "violations": violations,
        }
    return payload, None


def _task_chain_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _task_chain_parent_links(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = [item for item in payload.get("tasks") or [] if isinstance(item, dict) and str(item.get("key") or "").strip()]
    task_keys = {str(item["key"]) for item in tasks}
    parent_by_child = {
        str(item["key"]): str(item["parent_key"])
        for item in tasks
        if str(item.get("parent_key") or "").strip()
    }
    links: list[dict[str, Any]] = []
    for task in tasks:
        child_key = str(task["key"])
        parent_key = str(task.get("parent_key") or "").strip()
        if not parent_key:
            continue
        status = "ok"
        if parent_key == child_key:
            status = "self_parent_task_key"
        elif parent_key not in task_keys:
            status = "unknown_parent_task_key"
        links.append({"parent_key": parent_key, "child_key": child_key, "status": status})

    cycle_children: set[str] = set()
    for child_key in parent_by_child:
        path: list[str] = []
        seen: set[str] = set()
        current = child_key
        while current in parent_by_child:
            if current in seen:
                cycle_start = path.index(current) if current in path else 0
                cycle_children.update(path[cycle_start:])
                break
            seen.add(current)
            path.append(current)
            parent = parent_by_child[current]
            if parent not in task_keys:
                break
            current = parent
    if cycle_children:
        for link in links:
            if link["status"] == "ok" and link["child_key"] in cycle_children:
                link["status"] = "cycle_parent_task_key"
    return links


def _task_chain_validation(payload: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = list(plan_to_db_verify_payload(payload)["violations"])
    if payload.get("closed_by_decomposition") is not False:
        errors.append({"code": "closed_by_decomposition_must_be_false"})
    source_items = payload.get("source_items")
    if not isinstance(source_items, list) or not source_items:
        if not any(error.get("code") == "missing_source_items" for error in errors):
            errors.append({"code": "missing_source_items"})
    source_item_id_list = _source_item_ids(payload)
    duplicate_source_item_ids = set(_duplicate_values(source_item_id_list))
    for source_ref in sorted(duplicate_source_item_ids):
        errors.append({"code": "duplicate_source_item_id", "source_ref": source_ref})
    source_item_ids = set(source_item_id_list)
    source_item_mapping = _source_item_mapping(payload)
    task_keys: set[str] = set()
    for index, task in enumerate(payload.get("tasks") or []):
        if not isinstance(task, dict):
            errors.append({"code": "invalid_task_entry", "task_index": index})
            continue
        key = str(task.get("key") or "").strip()
        if not key:
            errors.append({"code": "missing_task_key"})
            continue
        if key in task_keys:
            errors.append({"code": "duplicate_task_key", "task_key": key})
        task_keys.add(key)
        refs = _entry_source_refs(payload, task, key=key, kind="task")
        for ref in refs:
            if ref in duplicate_source_item_ids:
                errors.append({"code": "ambiguous_source_ref", "task_key": key, "source_ref": ref})
            elif ref not in source_item_ids:
                errors.append({"code": "unknown_source_ref", "task_key": key, "source_ref": ref})
        if not refs:
            errors.append({"code": "uncovered_task_without_source_item", "task_key": key})
        if _is_synthetic(task):
            if task.get("controller_allowed_synthetic") is not True:
                errors.append({"code": "synthetic_task_missing_controller_allow", "task_key": key})
            if not str(task.get("synthetic_rationale") or "").strip():
                errors.append({"code": "synthetic_task_missing_rationale", "task_key": key})
            if not _synthetic_refs(task):
                errors.append({"code": "synthetic_task_missing_derived_sources", "task_key": key})
        for field in ("title", "body"):
            if not task.get(field):
                errors.append({"code": "missing_task_field", "task_key": key, "field": field})
    for link in _task_chain_parent_links(payload):
        if link["status"] != "ok":
            errors.append(
                {
                    "code": link["status"],
                    "task_key": link["child_key"],
                    "parent_key": link["parent_key"],
                }
            )
    check_keys: set[str] = set()
    for index, check in enumerate(payload.get("checks") or []):
        if not isinstance(check, dict):
            errors.append({"code": "invalid_check_entry", "check_index": index})
            continue
        key = str(check.get("key") or "").strip()
        if not key:
            errors.append({"code": "missing_check_key"})
            continue
        if key in check_keys:
            errors.append({"code": "duplicate_check_key", "check_key": key})
        check_keys.add(key)
        if str(check.get("task_key") or "") not in task_keys:
            errors.append({"code": "check_references_missing_task", "check_key": key, "task_key": check.get("task_key")})
        refs = _entry_source_refs(payload, check, key=key, kind="check")
        for ref in refs:
            if ref in duplicate_source_item_ids:
                errors.append({"code": "ambiguous_source_ref", "check_key": key, "source_ref": ref})
            elif ref not in source_item_ids:
                errors.append({"code": "unknown_source_ref", "check_key": key, "source_ref": ref})
        if not refs:
            errors.append({"code": "uncovered_check_without_source_item", "check_key": key})
        if _is_synthetic(check):
            if check.get("controller_allowed_synthetic") is not True:
                errors.append({"code": "synthetic_check_missing_controller_allow", "check_key": key})
            if not str(check.get("synthetic_rationale") or "").strip():
                errors.append({"code": "synthetic_check_missing_rationale", "check_key": key})
            if not _synthetic_refs(check):
                errors.append({"code": "synthetic_check_missing_derived_sources", "check_key": key})
        for field in ("body", "expected_evidence_type"):
            if not check.get(field):
                errors.append({"code": "missing_check_field", "check_key": key, "field": field})
    for source_id, mapping in source_item_mapping.items():
        for task_key in mapping.get("task_keys") or []:
            if task_key not in task_keys:
                errors.append({"code": "dangling_source_item_task_id", "source_ref": source_id, "task_key": task_key})
        for check_key in mapping.get("check_keys") or []:
            if check_key not in check_keys:
                errors.append({"code": "dangling_source_item_check_id", "source_ref": source_id, "check_key": check_key})
        destination = mapping.get("graph_destination")
        status = str(mapping.get("status") or "active")
        if status in INACTIVE_RESIDUAL_STATUSES:
            continue
        if isinstance(destination, dict):
            destination_kind = str(destination.get("kind") or destination.get("type") or "")
            destination_id = str(destination.get("id") or destination.get("node_id") or destination.get("task_id") or destination.get("check_id") or "")
            if destination_kind == "task" and destination_id and destination_id not in (mapping.get("task_keys") or []):
                errors.append({"code": "source_destination_mismatch", "source_ref": source_id, "destination_kind": destination_kind, "destination_id": destination_id})
            if destination_kind in {"acceptance_check", "check"} and destination_id and destination_id not in (mapping.get("check_keys") or []):
                errors.append({"code": "source_destination_mismatch", "source_ref": source_id, "destination_kind": destination_kind, "destination_id": destination_id})
    return errors


def _default_import_output(repo: Path, endpoint_name: str, *, dry_run: bool) -> Path:
    suffix = "preview" if dry_run else "mapping"
    return ensure_layout(repo) / "artifacts" / endpoint_name / f"task_chain_import.{suffix}.json"


def _idempotency_state_path(repo: Path, endpoint_name: str) -> Path:
    return ensure_layout(repo) / "artifacts" / endpoint_name / "task_chain_import.state.json"


def _load_idempotency_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def _endpoint_contract_row(conn: sqlite3.Connection, endpoint_name: str) -> Any:
    endpoint = query_endpoint(conn, endpoint_name)
    root_node_id = endpoint["root_node_id"]
    if not root_node_id:
        raise SystemExit(f"endpoint has no root_node_id: {endpoint_name}")
    contract = conn.execute(
        "SELECT id, node_id, source_node_id FROM scope_contracts WHERE node_id = ?",
        (root_node_id,),
    ).fetchone()
    if not contract:
        raise SystemExit(f"endpoint root is not a scope_contract: {endpoint_name}")
    return endpoint, contract


def _task_chain_review_state(payload: dict[str, Any]) -> dict[str, Any]:
    review = payload.get("review") or {}
    return {
        "packet_requested": bool(review.get("packet_requested")),
        "packet_generated": bool(review.get("packet_generated")),
        "reviewer_executed": bool(review.get("reviewer_executed")),
        "controller_adopted": bool(review.get("controller_adopted")),
        "evidence_imported": bool(review.get("evidence_imported")),
        "reviewer_role": review.get("reviewer_role"),
        "review_questions": review.get("review_questions") or [],
    }


def _task_chain_edge_plan(payload: dict[str, Any]) -> dict[str, Any]:
    source_item_ids = _source_item_ids(payload)
    tasks = [item for item in payload.get("tasks") or [] if isinstance(item, dict) and item.get("key")]
    task_keys = {str(item.get("key")) for item in tasks}
    checks = [
        item
        for item in payload.get("checks") or []
        if isinstance(item, dict) and item.get("key") and str(item.get("task_key") or "") in task_keys
    ]
    source_item_edges = len(source_item_ids)
    contract_task_edges = len(tasks)
    task_source_edges = sum(len(_entry_source_refs(payload, task, key=str(task.get("key")), kind="task")) for task in tasks)
    parent_task_edges = sum(1 for link in _task_chain_parent_links(payload) if link["status"] == "ok")
    task_check_edges = len(checks)
    check_source_edges = sum(len(_entry_source_refs(payload, check, key=str(check.get("key")), kind="check")) for check in checks)
    derived_from_edges = source_item_edges + task_source_edges + check_source_edges
    decomposes_to_edges = contract_task_edges + parent_task_edges + task_check_edges
    return {
        "total": derived_from_edges + decomposes_to_edges,
        "by_type": {
            "DERIVED_FROM": derived_from_edges,
            "DECOMPOSES_TO": decomposes_to_edges,
        },
        "by_source": {
            "source_item_derived_from_scope_source": source_item_edges,
            "contract_decomposes_to_task": contract_task_edges,
            "task_derived_from_source": task_source_edges,
            "parent_task_decomposes_to_child_task": parent_task_edges,
            "task_decomposes_to_check": task_check_edges,
            "check_derived_from_source": check_source_edges,
        },
        "count_scope": "edges_created_by_apply_task_chain_for_valid_task_check_entries",
    }


def _task_chain_relation_plan(payload: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    by_status: dict[str, int] = {}
    source_items = payload.get("source_items") if isinstance(payload.get("source_items"), list) else []
    relation_field_by_status = {
        "absorbed": "absorbed_by",
        "superseded": "superseded_by",
        "indirectly_dissolved": "dissolved_by",
    }
    edge_type_by_status = {
        "absorbed": "RESOLVES",
        "superseded": "SUPERSEDES",
        "indirectly_dissolved": "RESOLVES",
    }
    state_by_status = {
        "absorbed": "resolved",
        "superseded": "superseded",
        "indirectly_dissolved": "resolved",
    }
    for index, raw_item in enumerate(source_items):
        if not isinstance(raw_item, dict):
            continue
        status = str(raw_item.get("status") or "active")
        if status not in INACTIVE_RESIDUAL_STATUSES:
            continue
        item_id = _item_id(raw_item, index)
        relation_field = relation_field_by_status[status]
        target = raw_item.get(relation_field)
        destination_kind, destination_id = _destination_parts(raw_item)
        if not target:
            target = destination_id
        by_status[status] = by_status.get(status, 0) + 1
        items.append(
            {
                "source_item": item_id,
                "status": status,
                "target": str(target) if target else None,
                "target_field": relation_field,
                "graph_destination": raw_item.get("graph_destination"),
                "destination_kind": destination_kind,
                "edge_type": edge_type_by_status[status],
                "source_item_counts_as_ordinary_coverage": False,
                "old_node_required": True,
                "new_node_required": bool(target),
                "state_transition": f"source_item -> {state_by_status[status]}",
                "safe_next_action": (
                    "Bind the old source/lifecycle node and target task/check before applying relation edges."
                    if target
                    else f"Add {relation_field} before import can explain this inactive source item."
                ),
            }
        )
    return {
        "count": len(items),
        "by_status": by_status,
        "items": items,
        "ordinary_coverage_policy": "inactive absorbed/superseded/indirectly_dissolved source items are relation material, not task_derived_from_source coverage",
    }


def _task_chain_preview_payload(payload: dict[str, Any], *, endpoint_name: str) -> dict[str, Any]:
    review = _task_chain_review_state(payload)
    source_items = payload.get("source_items") if isinstance(payload.get("source_items"), list) else []
    violations = _task_chain_validation(payload)
    source_mapping = _source_item_mapping(payload)
    source_coverage = _source_coverage(payload)
    parent_links = _task_chain_parent_links(payload)
    edge_plan = _task_chain_edge_plan(payload)
    relation_plan = _task_chain_relation_plan(payload)
    warnings: list[str] = []
    if review["packet_requested"] and not review["reviewer_executed"]:
        warnings.append("review.packet_requested=true but reviewer_executed=false; import will not close review checks")
    return {
        "ok": not violations,
        "read_only": True,
        "gate": "plan_to_db_import_task_chain",
        "endpoint": endpoint_name,
        "counts": {
            "tasks": len(payload.get("tasks") or []),
            "checks": len(payload.get("checks") or []),
            "source_items": len(source_items),
            "edges": edge_plan["total"],
        },
        "edge_plan": edge_plan,
        "relation_plan": relation_plan,
        "source_items": {
            "count": len(source_items),
            "ids": _source_item_ids(payload),
            "duplicate_ids": _duplicate_values(_source_item_ids(payload)),
            "mapping": source_mapping,
        },
        "source_coverage": source_coverage,
        "parent_links": parent_links,
        "would_create": {
            "task_keys": [item["key"] for item in payload.get("tasks") or [] if isinstance(item, dict) and item.get("key")],
            "check_keys": [item["key"] for item in payload.get("checks") or [] if isinstance(item, dict) and item.get("key")],
        },
        "review": review,
        "violations": violations,
        "error": (
            {
                "code": _coverage_error_code(violations),
                "message": "Task-chain import failed validation; fix source coverage or structural errors before apply.",
            }
            if violations
            else None
        ),
        "warnings": warnings,
        "side_effects_if_apply": ["create task nodes", "create acceptance_check nodes", "create DECOMPOSES_TO and DERIVED_FROM edges", "optional endpoint refresh"],
        "safe_next_action": "Rerun with --apply only after controller accepts preview." if not violations else "Fix validation errors before apply.",
    }


def _apply_task_chain(conn: sqlite3.Connection, *, payload: dict[str, Any], endpoint_name: str, refresh_endpoint: bool) -> dict[str, Any]:
    endpoint, contract = _endpoint_contract_row(conn, endpoint_name)
    source_node_id = str(contract["source_node_id"] or contract["node_id"])
    task_mapping: dict[str, dict[str, Any]] = {}
    check_mapping: dict[str, dict[str, Any]] = {}
    source_item_mapping = _source_item_mapping(payload)
    source_item_nodes: dict[str, str] = {}
    source_items = payload.get("source_items") if isinstance(payload.get("source_items"), list) else []
    for index, raw_item in enumerate(source_items):
        if not isinstance(raw_item, dict):
            continue
        item_id = _item_id(raw_item, index)
        node_id = create_node(
            conn,
            "source_item",
            item_id[:80],
            str(raw_item.get("rationale") or raw_item.get("graph_destination") or item_id)[:240],
            {
                "source_item_id": item_id,
                "classification": raw_item.get("classification") or raw_item.get("priority"),
                "status": raw_item.get("status") or "active",
                "graph_destination": raw_item.get("graph_destination"),
                "promotion_rule": raw_item.get("promotion_rule"),
                "reopen_rule": raw_item.get("reopen_rule"),
            },
        )
        create_edge(conn, node_id, "DERIVED_FROM", source_node_id, reason="Task-chain source item derived from scope source node.")
        source_item_nodes[item_id] = node_id
        source_item_mapping.setdefault(item_id, {})
        source_item_mapping[item_id]["node_id"] = node_id
    ordered_tasks = sorted(payload.get("tasks") or [], key=lambda item: (int(item.get("order") or 0), str(item.get("key") or "")))
    for task in ordered_tasks:
        body = str(task["body"])
        task_key = str(task["key"])
        node_id = create_node(
            conn,
            "task",
            str(task.get("title") or task_key)[:80],
            body[:240],
            {
                "task_chain_key": task_key,
                "phase": task.get("phase"),
                "order": task.get("order"),
                "mandatory": bool(task.get("mandatory", True)),
                "parent_key": task.get("parent_key"),
                "source_refs": _entry_source_refs(payload, task, key=task_key, kind="task"),
            },
        )
        task_id = new_id("task")
        conn.execute(
            """
            INSERT INTO tasks
              (id, node_id, contract_id, parent_task_id, task_body, is_mandatory, created_from_node_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, node_id, contract["id"], None, body, 1 if task.get("mandatory", True) else 0, source_node_id),
        )
        create_edge(conn, contract["node_id"], "DECOMPOSES_TO", node_id, reason="Task-chain import decomposes scope contract to task.")
        refs = _entry_source_refs(payload, task, key=task_key, kind="task")
        for ref in refs:
            create_edge(conn, node_id, "DERIVED_FROM", source_item_nodes[ref], reason="Task-chain import task derived from source item.")
        task_mapping[task_key] = {
            "task_id": task_id,
            "node_id": node_id,
            "title": task.get("title"),
            "parent_key": task.get("parent_key"),
            "parent_task_id": None,
            "source_refs": refs,
            "synthetic": _is_synthetic(task),
        }
    for task in ordered_tasks:
        task_key = str(task["key"])
        parent_key = task.get("parent_key")
        if not parent_key:
            continue
        parent_entry = task_mapping.get(str(parent_key))
        child_entry = task_mapping[task_key]
        if not parent_entry:
            raise SystemExit(f"unknown_parent_task_key: task {task_key} references missing parent_key {parent_key}")
        conn.execute("UPDATE tasks SET parent_task_id = ? WHERE id = ?", (parent_entry["task_id"], child_entry["task_id"]))
        create_edge(conn, parent_entry["node_id"], "DECOMPOSES_TO", child_entry["node_id"], reason="Task-chain parent_key links parent task to child task.")
        child_entry["parent_task_id"] = parent_entry["task_id"]
    for check in payload.get("checks") or []:
        check_key = str(check["key"])
        task_entry = task_mapping[str(check["task_key"])]
        body = str(check["body"])
        node_id = create_node(
            conn,
            "acceptance_check",
            body[:80],
            body[:240],
            {
                "task_chain_key": check_key,
                "task_key": check["task_key"],
                "expected_evidence_type": check["expected_evidence_type"],
                "source_refs": _entry_source_refs(payload, check, key=check_key, kind="check"),
            },
        )
        check_id = new_id("check")
        conn.execute(
            """
            INSERT INTO acceptance_checks
              (id, node_id, task_id, check_body, expected_evidence_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (check_id, node_id, task_entry["task_id"], body, check["expected_evidence_type"]),
        )
        create_edge(conn, task_entry["node_id"], "DECOMPOSES_TO", node_id, reason="Task-chain import decomposes task to acceptance check.")
        refs = _entry_source_refs(payload, check, key=check_key, kind="check")
        for ref in refs:
            create_edge(conn, node_id, "DERIVED_FROM", source_item_nodes[ref], reason="Task-chain import check derived from source item.")
        check_mapping[check_key] = {"check_id": check_id, "node_id": node_id, "task_key": check["task_key"], "source_refs": refs, "synthetic": _is_synthetic(check)}
    refresh_result = None
    if refresh_endpoint:
        refresh_result = refresh_endpoint_projection(conn, endpoint_name, from_node=endpoint["root_node_id"])
    return {
        "endpoint": endpoint_name,
        "task_mapping": task_mapping,
        "check_mapping": check_mapping,
        "source_item_mapping": source_item_mapping,
        "source_coverage": _source_coverage(payload),
        "relation_plan": _task_chain_relation_plan(payload),
        "review": _task_chain_review_state(payload),
        "refresh_endpoint": bool(refresh_result),
        "refresh_result": {key: value for key, value in (refresh_result or {}).items() if key != "body"},
    }


def cmd_plan_to_db_import_task_chain(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    payload, payload_error = _task_chain_payload(args.artifact)
    if payload_error:
        print_json(payload_error)
        return 1
    assert payload is not None
    endpoint_name = args.endpoint or ((payload.get("endpoint") or {}).get("name"))
    if not endpoint_name:
        print_json(json_error_payload("missing_endpoint", "import-task-chain requires --endpoint or endpoint.name in the artifact", read_only=True))
        return 1
    dry_run = bool(args.dry_run)
    apply = bool(args.apply)
    if dry_run == apply:
        print_json(json_error_payload("mutually_exclusive_mode", "choose exactly one of --dry-run or --apply", read_only=True))
        return 1
    preview = _task_chain_preview_payload(payload, endpoint_name=endpoint_name)
    if preview["violations"]:
        preview["ok"] = False
    if dry_run:
        out_path = Path(args.out).resolve() if args.out else None
        preview["out"] = str(out_path) if out_path else None
        preview["filesystem_writes"] = (1 if out_path else 0) + (1 if args.trace else 0)
        preview["db_writes"] = 0
        preview["trace_explicit"] = bool(args.trace)
        preview["trace_written"] = bool(args.trace)
        if out_path:
            _write_json(out_path, preview)
        if args.trace:
            append_trace_event(
                repo,
                event_type="plan_to_db_import_dry_run",
                endpoint=endpoint_name,
                read_only=True,
                apply=False,
                status="preview",
                details={"out": str(out_path) if out_path else None},
            )
        print_json(preview)
        return 0 if preview["ok"] or args.allow_fail else 1
    out_path = Path(args.out).resolve() if args.out else _default_import_output(repo, endpoint_name, dry_run=dry_run)
    state_path = _idempotency_state_path(repo, endpoint_name)
    state = _load_idempotency_state(state_path)
    artifact_hash = _task_chain_hash(payload)
    idempotency_key = args.idempotency_key or payload.get("idempotency_key")
    if idempotency_key:
        prior = state.get(str(idempotency_key))
        if prior and prior.get("artifact_hash") == artifact_hash:
            mapping_path = Path(prior["mapping_path"])
            mapping = json.loads(mapping_path.read_text(encoding="utf-8")) if mapping_path.exists() else {"idempotent": True}
            mapping["idempotent"] = True
            print_json(mapping)
            return 0
        if prior and prior.get("artifact_hash") != artifact_hash:
            print_json(json_error_payload("idempotency_key_conflict", "idempotency key already maps to a different artifact hash", read_only=True))
            return 1
    if preview["violations"]:
        print_json(preview)
        return 1
    conn = connect(repo)
    try:
        mapping = _apply_task_chain(conn, payload=payload, endpoint_name=endpoint_name, refresh_endpoint=bool(args.refresh_endpoint))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    result = {
        "ok": True,
        "read_only": False,
        "apply": True,
        "dry_run": False,
        "idempotent": False,
        "endpoint": endpoint_name,
        "counts": {"tasks": len(mapping["task_mapping"]), "checks": len(mapping["check_mapping"])},
        "mapping": mapping,
        "closure_side_effects": 0,
        "out": str(out_path),
    }
    _write_json(out_path, result)
    append_trace_event(repo, event_type="plan_to_db_import_apply", endpoint=endpoint_name, read_only=False, apply=True, status="applied", details={"out": str(out_path), "tasks": result["counts"]["tasks"], "checks": result["counts"]["checks"]})
    if idempotency_key:
        state[str(idempotency_key)] = {"artifact_hash": artifact_hash, "mapping_path": str(out_path)}
        _write_json(state_path, state)
    print_json(result)
    return 0


def _endpoint_target_node_ids(conn: sqlite3.Connection, endpoint_name: str | None) -> set[str]:
    if not endpoint_name:
        return set()
    endpoint = query_endpoint(conn, endpoint_name)
    target_ids = {str(endpoint["node_id"])}
    root_node_id = endpoint["root_node_id"]
    if root_node_id:
        target_ids.add(str(root_node_id))
        task_rows: list[Any] = []
        contract = conn.execute("SELECT id FROM scope_contracts WHERE node_id = ?", (root_node_id,)).fetchone()
        if contract:
            task_rows = conn.execute("SELECT id, node_id FROM tasks WHERE contract_id = ?", (contract["id"],)).fetchall()
        else:
            root_task = conn.execute("SELECT id, node_id FROM tasks WHERE node_id = ?", (root_node_id,)).fetchone()
            if root_task:
                task_rows = [root_task]
                seen_task_ids = {str(root_task["id"])}
                frontier = [str(root_task["id"])]
                while frontier:
                    placeholders = ",".join("?" for _ in frontier)
                    descendants = conn.execute(
                        f"SELECT id, node_id FROM tasks WHERE parent_task_id IN ({placeholders})",
                        frontier,
                    ).fetchall()
                    frontier = []
                    for task in descendants:
                        task_id = str(task["id"])
                        if task_id in seen_task_ids:
                            continue
                        seen_task_ids.add(task_id)
                        frontier.append(task_id)
                        task_rows.append(task)
        target_ids.update(str(row["node_id"]) for row in task_rows if row["node_id"])
        task_ids = [str(row["id"]) for row in task_rows]
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            check_rows = conn.execute(
                f"SELECT node_id FROM acceptance_checks WHERE task_id IN ({placeholders})",
                task_ids,
            ).fetchall()
            target_ids.update(str(row["node_id"]) for row in check_rows if row["node_id"])
    return target_ids


def _applies_to_filter_sql(target_ids: set[str]) -> tuple[str, list[str]]:
    if not target_ids:
        return "", []
    placeholders = ",".join("?" for _ in target_ids)
    return (
        f"""
        AND (
          EXISTS (
            SELECT 1 FROM edges applies
            WHERE applies.from_node_id = si.node_id
              AND applies.type = 'APPLIES_TO'
              AND applies.to_node_id IN ({placeholders})
          )
          OR EXISTS (
            SELECT 1 FROM edges source_applies
            WHERE source_applies.from_node_id = e.from_node_id
              AND source_applies.type = 'APPLIES_TO'
              AND source_applies.to_node_id IN ({placeholders})
          )
        )
        """,
        [*target_ids, *target_ids],
    )


def lifecycle_reconciliation_candidates(conn: sqlite3.Connection, endpoint_name: str | None = None) -> list[dict[str, Any]]:
    target_ids = _endpoint_target_node_ids(conn, endpoint_name)
    applies_sql, applies_params = _applies_to_filter_sql(target_ids)
    rows = conn.execute(
        f"""
        SELECT si.id AS semantic_item_id,
               si.node_id AS affected_node_id,
               si.item_type,
               si.current_state,
               n.label AS affected_label,
               e.id AS edge_id,
               e.type AS edge_type,
               e.from_node_id AS source_node_id,
               e.reason AS edge_reason,
               source.type AS source_type,
               source.label AS source_label
        FROM semantic_items si
        JOIN nodes n ON n.id = si.node_id
        JOIN edges e ON e.to_node_id = si.node_id
        JOIN nodes source ON source.id = e.from_node_id
        WHERE si.current_state = 'active'
          AND e.type IN ('RESOLVES', 'SUPERSEDES')
          AND n.valid_to IS NULL
          AND source.valid_to IS NULL
          {applies_sql}
        ORDER BY n.created_at ASC, e.created_at ASC, si.id ASC
        """,
        applies_params,
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    by_affected: dict[str, list[Any]] = {}
    for row in rows:
        affected_node_id = str(row["affected_node_id"])
        by_affected.setdefault(affected_node_id, []).append(row)
    for affected_node_id, affected_rows in by_affected.items():
        edge_types = {str(row["edge_type"]) for row in affected_rows}
        if len(edge_types) > 1:
            first = affected_rows[0]
            candidates.append(
                {
                    "semantic_item_id": first["semantic_item_id"],
                    "affected_node_id": affected_node_id,
                    "affected_label": first["affected_label"],
                    "item_type": first["item_type"],
                    "current_state": first["current_state"],
                    "target_state": "conflict",
                    "event_type": "conflict",
                    "source_node_id": first["source_node_id"],
                    "source_type": first["source_type"],
                    "source_label": first["source_label"],
                    "edge_id": first["edge_id"],
                    "edge_type": "conflict",
                    "rationale": "Conflicting incoming RESOLVES and SUPERSEDES edges require controller decision.",
                    "conflicting_edges": [
                        {
                            "edge_id": row["edge_id"],
                            "edge_type": row["edge_type"],
                            "source_node_id": row["source_node_id"],
                            "source_label": row["source_label"],
                        }
                        for row in affected_rows
                    ],
                    "graph_destination": {
                        "kind": "semantic_lifecycle_conflict",
                        "command": "controller decision required",
                        "node": affected_node_id,
                    },
                }
            )
            continue
        row = affected_rows[0]
        target_state = "resolved" if row["edge_type"] == "RESOLVES" else "superseded"
        candidates.append(
            {
                "semantic_item_id": row["semantic_item_id"],
                "affected_node_id": affected_node_id,
                "affected_label": row["affected_label"],
                "item_type": row["item_type"],
                "current_state": row["current_state"],
                "target_state": target_state,
                "event_type": target_state,
                "source_node_id": row["source_node_id"],
                "source_type": row["source_type"],
                "source_label": row["source_label"],
                "edge_id": row["edge_id"],
                "edge_type": row["edge_type"],
                "rationale": row["edge_reason"] or f"incoming {row['edge_type']} edge already points at active residual",
                "graph_destination": {
                    "kind": "semantic_lifecycle",
                    "command": "semantic set-state",
                    "node": affected_node_id,
                    "state": target_state,
                    "source_node": row["source_node_id"],
                },
            }
        )
    return candidates


def cmd_plan_to_db_lifecycle_reconcile(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    candidates = lifecycle_reconciliation_candidates(conn, args.endpoint)
    applied: list[dict[str, Any]] = []
    if args.apply:
        for candidate in candidates:
            if candidate.get("target_state") == "conflict":
                raise SystemExit(
                    f"lifecycle reconciliation conflict for {candidate['affected_node_id']}; "
                    "resolve RESOLVES/SUPERSEDES ambiguity before --apply"
                )
            semantic_item_id = transition_semantic_item(
                conn,
                candidate["affected_node_id"],
                state=candidate["target_state"],
                event_type=candidate["event_type"],
                source_node=candidate["source_node_id"],
                reason=args.reason or candidate["rationale"],
            )
            applied.append({**candidate, "semantic_item_id": semantic_item_id or candidate["semantic_item_id"]})
        conn.commit()
    payload = {
        "ok": args.apply or not candidates,
        "gate": "plan_to_db_lifecycle_reconciliation",
        "endpoint": args.endpoint,
        "dry_run": not args.apply,
        "apply": bool(args.apply),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "applied_count": len(applied),
        "applied": applied,
        "output_shape": [
            "source_node_id",
            "affected_node_id",
            "current_state",
            "target_state",
            "rationale",
            "graph_destination",
        ],
        "next_action": (
            "Controller reviews dry-run candidates, then reruns with --apply only when the graph edge already records the adoption source."
            if not args.apply
            else "Controller may run endpoint/report verification after lifecycle state reconciliation."
        ),
    }
    print_json(payload)
    return 0 if payload["ok"] or args.allow_fail else 1


def build_plan_to_db_handlers(deps: Mapping[str, Any]) -> dict[str, Any]:
    _configure(deps)
    return {
        "verify": cmd_plan_to_db_verify,
        "import_task_chain": cmd_plan_to_db_import_task_chain,
        "lifecycle_reconcile": cmd_plan_to_db_lifecycle_reconcile,
    }


def register_plan_to_db(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, Any]) -> None:
    plan = subparsers.add_parser("plan-to-db", help="Verify source-plan decomposition and lifecycle reconciliation hygiene.")
    plan_sub = plan.add_subparsers(dest="plan_to_db_command", required=True)

    verify = plan_sub.add_parser("verify-artifact")
    verify.add_argument("--artifact", required=True, type=Path)
    verify.add_argument("--allow-fail", action="store_true")
    verify.set_defaults(func=handlers["verify"])

    import_task_chain = plan_sub.add_parser("import-task-chain")
    import_task_chain.add_argument("--artifact", required=True, type=Path)
    import_task_chain.add_argument("--endpoint")
    import_task_chain.add_argument("--dry-run", action="store_true")
    import_task_chain.add_argument("--apply", action="store_true")
    import_task_chain.add_argument("--idempotency-key")
    import_task_chain.add_argument("--out")
    import_task_chain.add_argument("--trace", action="store_true", help="Write a dry-run trace event explicitly; dry-run is otherwise filesystem side-effect-free unless --out is set.")
    import_task_chain.add_argument("--refresh-endpoint", action="store_true")
    import_task_chain.add_argument("--allow-fail", action="store_true")
    import_task_chain.set_defaults(func=handlers["import_task_chain"])

    lifecycle = plan_sub.add_parser("lifecycle-reconcile")
    lifecycle.add_argument("--endpoint")
    lifecycle.add_argument("--apply", action="store_true")
    lifecycle.add_argument("--reason")
    lifecycle.add_argument("--allow-fail", action="store_true")
    lifecycle.set_defaults(func=handlers["lifecycle_reconcile"])
