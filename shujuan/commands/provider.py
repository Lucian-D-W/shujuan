from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


ProviderHandler = Callable[[argparse.Namespace], int]
PROVIDER_HANDLER_KEYS = ("contract", "import_json")
PROVIDER_DEPENDENCY_KEYS = (
    "PRODUCT_BACKLOG_STATE",
    "capture_artifact_file",
    "connect",
    "create_edge",
    "create_node",
    "create_semantic_item",
    "json_dumps",
    "new_id",
    "now_iso",
    "print_json",
    "query_endpoint",
    "require_node",
)

PROVIDER_CONTRACT_VERSION = "shujuan.impact_provider.v1"
DEFAULT_IMPACT_SOURCE = "GitNexus direct CLI and global gitnexus-* skills"
PREFERRED_IMPACT_PROVIDER = "gitnexus"
PREFERRED_IMPACT_SKILL = "gitnexus-impact-analysis"
GITNEXUS_INDEX_PATH = Path(".gitnexus")

PRODUCT_BACKLOG_STATE: str | None = None
capture_artifact_file: Callable[..., Any] | None = None
connect: Callable[..., Any] | None = None
create_edge: Callable[..., Any] | None = None
create_node: Callable[..., Any] | None = None
create_semantic_item: Callable[..., Any] | None = None
json_dumps: Callable[[Any], str] | None = None
new_id: Callable[[str], str] | None = None
now_iso: Callable[[], str] | None = None
print_json: Callable[[Any], None] | None = None
query_endpoint: Callable[..., Any] | None = None
require_node: Callable[..., Any] | None = None


def _validate_handlers(handlers: Mapping[str, ProviderHandler]) -> None:
    missing = [key for key in PROVIDER_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"provider command boundary is missing: {', '.join(missing)}")


def _provider_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in PROVIDER_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"provider handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in PROVIDER_DEPENDENCY_KEYS}


def _require_dependency(name: str) -> Any:
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"provider command dependency is not configured: {name}")
    return value


def build_provider_handlers(deps: Mapping[str, Any]) -> dict[str, ProviderHandler]:
    """Build provider handlers from cli.py-owned DB and semantic helpers."""
    globals().update(_provider_dependencies(deps))
    return {
        "contract": cmd_provider_contract,
        "import_json": cmd_provider_import_json,
    }


def provider_closure_evidence_boundary() -> dict[str, Any]:
    return {
        "material_only": True,
        "output_classification": "provider_fact or provider_hypothesis",
        "cannot_close_checks": True,
        "cannot_close_tasks": True,
        "note": "GitNexus and other provider output informs controller review but is not closure evidence by itself.",
    }


def gitnexus_provider_detail(repo: Path | None = None, *, invoked: bool = False) -> dict[str, Any]:
    installed = shutil.which("gitnexus") is not None
    indexed = (repo / GITNEXUS_INDEX_PATH).exists() if repo else False
    return {
        "name": PREFERRED_IMPACT_PROVIDER,
        "role": "optional direct graph provider",
        "index_path": GITNEXUS_INDEX_PATH.as_posix(),
        "installed": installed,
        "indexed": indexed,
        "invoked": invoked,
    }


def gitnexus_command(*args: str) -> list[str]:
    executable = shutil.which("gitnexus")
    if not executable:
        return ["gitnexus", *args]
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", executable, *args]
    return [executable, *args]


def impact_metadata(
    repo: Path | None = None,
    *,
    entrypoint_used: str,
    provider_invoked: bool = False,
) -> dict[str, Any]:
    return {
        "contract_version": PROVIDER_CONTRACT_VERSION,
        "default_source": DEFAULT_IMPACT_SOURCE,
        "entrypoint_used": entrypoint_used,
        "provider_detail": gitnexus_provider_detail(repo, invoked=provider_invoked),
        "closure_evidence_boundary": provider_closure_evidence_boundary(),
    }


def impact_provider_contract(repo: Path | None = None) -> dict[str, Any]:
    provider_detail = gitnexus_provider_detail(repo)
    return {
        "contract_version": PROVIDER_CONTRACT_VERSION,
        "role": "optional impact/graph provider",
        "required": False,
        "default_source": DEFAULT_IMPACT_SOURCE,
        "preferred_agent_skill": PREFERRED_IMPACT_SKILL,
        "preferred_provider": PREFERRED_IMPACT_PROVIDER,
        "entrypoint_used": "contract_metadata_only",
        "closure_evidence_boundary": provider_closure_evidence_boundary(),
        "installed": provider_detail["installed"],
        "indexed": provider_detail["indexed"],
        "index_path": provider_detail["index_path"],
        "provider_detail": provider_detail,
        "input": {
            "repo_root": "path to repository root",
            "changed_files": "list of changed file paths from captured change_set",
            "run_id": "optional shujuan agent_run id",
        },
        "output": {
            "status": "executed | provider_missing | provider_index_missing | failed | skipped",
            "reports": [
                {
                    "scope": "all",
                    "changed_files": ["path"],
                    "exit_code": "integer",
                    "report_path": "optional artifact path",
                    "stdout": "bounded provider stdout",
                    "stderr": "bounded provider stderr",
                }
            ],
        },
        "failure_policy": "Provider absence or failure is recorded as metadata/fact evidence and must not block shujuan's own diff/task/evidence loop.",
        "current_provider": PREFERRED_IMPACT_PROVIDER,
    }


def cmd_provider_contract(args: argparse.Namespace) -> int:
    _require_dependency("print_json")({"ok": True, **impact_provider_contract(args.repo.resolve())})
    return 0


def load_provider_structured_json(repo: Path, path_arg: str) -> tuple[dict[str, Any], Path]:
    path = Path(path_arg)
    if not path.is_absolute():
        path = repo / path
    if not path.exists() or not path.is_file():
        raise SystemExit(f"provider structured JSON file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"provider structured JSON could not be parsed: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("provider structured JSON must be an object")
    if not any(key in payload for key in ("facts", "warnings", "entity_map")):
        raise SystemExit("provider structured JSON is missing facts, warnings, or entity_map; stdout text is not accepted as the only API")
    return payload, path


def provider_payload_items(value: Any, *, field: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise SystemExit(f"provider structured JSON field {field!r} must be a list of objects")
    return [dict(item) for item in value]


def provider_lookup_entity_map(conn: sqlite3.Connection, provider: str, external_id: str | None) -> str | None:
    if not external_id:
        return None
    row = conn.execute(
        "SELECT node_id FROM provider_entity_map WHERE provider = ? AND external_id = ?",
        (provider, external_id),
    ).fetchone()
    return str(row["node_id"]) if row else None


def upsert_provider_entity_map(
    conn: sqlite3.Connection,
    *,
    provider: str,
    external_id: str,
    node_id: str,
    confidence: float | None,
    metadata: dict[str, Any],
) -> str:
    json_dumps_fn = _require_dependency("json_dumps")
    new_id_fn = _require_dependency("new_id")
    now_iso_fn = _require_dependency("now_iso")
    existing = conn.execute(
        "SELECT id FROM provider_entity_map WHERE provider = ? AND external_id = ?",
        (provider, external_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE provider_entity_map SET node_id = ?, confidence = ?, metadata = ? WHERE id = ?",
            (node_id, confidence, json_dumps_fn(metadata), existing["id"]),
        )
        return str(existing["id"])
    map_id = new_id_fn("provider_map")
    conn.execute(
        """
        INSERT INTO provider_entity_map
          (id, provider, external_id, node_id, confidence, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (map_id, provider, external_id, node_id, confidence, now_iso_fn(), json_dumps_fn(metadata)),
    )
    return map_id


def cmd_provider_import_json(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    payload, path = load_provider_structured_json(repo, args.path)
    provider = str(payload.get("provider") or args.provider or "unknown-provider")
    contract_version = str(payload.get("contract_version") or PROVIDER_CONTRACT_VERSION)
    if contract_version != PROVIDER_CONTRACT_VERSION:
        raise SystemExit(f"provider structured JSON contract_version {contract_version!r} does not match {PROVIDER_CONTRACT_VERSION}")

    product_backlog_state = _require_dependency("PRODUCT_BACKLOG_STATE")
    capture_artifact_file_fn = _require_dependency("capture_artifact_file")
    connect_fn = _require_dependency("connect")
    create_edge_fn = _require_dependency("create_edge")
    create_node_fn = _require_dependency("create_node")
    create_semantic_item_fn = _require_dependency("create_semantic_item")
    json_dumps_fn = _require_dependency("json_dumps")
    new_id_fn = _require_dependency("new_id")
    now_iso_fn = _require_dependency("now_iso")
    print_json_fn = _require_dependency("print_json")
    query_endpoint_fn = _require_dependency("query_endpoint")
    require_node_fn = _require_dependency("require_node")

    conn = connect_fn(repo)
    endpoint = query_endpoint_fn(conn, args.endpoint) if args.endpoint else None
    if args.source_node:
        require_node_fn(conn, args.source_node, "provider import source node")
    captured = capture_artifact_file_fn(repo, path, prefix="provider")
    run_node_id = create_node_fn(
        conn,
        "provider_run",
        args.label or f"provider run: {provider}",
        str(payload.get("status") or "imported"),
        {"provider": provider, "contract_version": contract_version, "endpoint": args.endpoint},
    )
    run_id = new_id_fn("provider_run")
    conn.execute(
        """
        INSERT INTO provider_runs
          (id, node_id, provider, contract_version, status, command, started_at, ended_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            run_node_id,
            provider,
            contract_version,
            str(payload.get("status") or "imported"),
            json_dumps_fn(payload.get("command")) if payload.get("command") is not None else None,
            str(payload.get("started_at") or now_iso_fn()),
            str(payload.get("ended_at") or now_iso_fn()),
            json_dumps_fn({"endpoint": args.endpoint, "raw_status": payload.get("status")}),
        ),
    )
    artifact_node_id = create_node_fn(
        conn,
        "provider_artifact",
        f"provider artifact: {path.name}",
        captured.get("sha256"),
        {**captured, "provider": provider, "contract_version": contract_version, "endpoint": args.endpoint},
    )
    artifact_id = new_id_fn("provider_artifact")
    conn.execute(
        """
        INSERT INTO provider_artifacts
          (id, run_id, node_id, path, capture_ref, sha256, content_type, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            run_id,
            artifact_node_id,
            captured.get("original_path"),
            captured["capture_ref"],
            captured["sha256"],
            "application/json",
            now_iso_fn(),
            json_dumps_fn({"size": captured.get("size"), "normalized_text_hash": captured.get("normalized_text_hash")}),
        ),
    )
    if args.source_node:
        create_edge_fn(conn, artifact_node_id, "DERIVED_FROM", args.source_node, reason="Provider artifact derived from source node.", created_by="agent")
    if endpoint:
        create_edge_fn(conn, artifact_node_id, "APPLIES_TO", endpoint["node_id"], reason="Provider artifact applies to endpoint.", created_by="agent")

    entity_maps = []
    for item in provider_payload_items(payload.get("entity_map"), field="entity_map"):
        external_id = str(item.get("external_id") or "")
        node_id = str(item.get("node_id") or "")
        if not external_id or not node_id:
            raise SystemExit("provider entity_map entries require external_id and node_id")
        require_node_fn(conn, node_id, "provider entity map node")
        entity_maps.append(
            {
                "id": upsert_provider_entity_map(
                    conn,
                    provider=provider,
                    external_id=external_id,
                    node_id=node_id,
                    confidence=float(item["confidence"]) if item.get("confidence") is not None else None,
                    metadata={key: value for key, value in item.items() if key not in {"external_id", "node_id", "confidence"}},
                ),
                "external_id": external_id,
                "node_id": node_id,
            }
        )

    facts = []
    for item in provider_payload_items(payload.get("facts"), field="facts"):
        summary = str(item.get("summary") or "").strip()
        if not summary:
            raise SystemExit("provider fact entries require summary")
        external_id = str(item.get("external_id") or "") or None
        mapped_node_id = item.get("mapped_node_id") or provider_lookup_entity_map(conn, provider, external_id)
        if mapped_node_id:
            require_node_fn(conn, str(mapped_node_id), "provider mapped node")
        classification = str(item.get("classification") or "provider_hypothesis")
        if not mapped_node_id:
            classification = "provider_hypothesis"
        fact_node_id = create_node_fn(
            conn,
            "provider_fact",
            str(item.get("label") or item.get("fact_type") or "provider fact"),
            summary[:240],
            {
                "provider": provider,
                "external_id": external_id,
                "classification": classification,
                "mapped_node_id": mapped_node_id,
                "provenance": item.get("provenance") or {},
            },
        )
        semantic_item_id = create_semantic_item_fn(
            conn,
            fact_node_id,
            "provider_fact",
            state=product_backlog_state,
            source_node=artifact_node_id,
            scope_node=str(mapped_node_id) if mapped_node_id else (endpoint["node_id"] if endpoint else artifact_node_id),
            event_type="provider_imported",
            reason="Provider fact imported as non-closure provider hypothesis.",
            props={"classification": classification, "external_id": external_id},
        )
        create_edge_fn(conn, fact_node_id, "DERIVED_FROM", artifact_node_id, reason="Provider fact derived from structured provider artifact.", created_by="provider")
        if mapped_node_id:
            create_edge_fn(conn, fact_node_id, "APPLIES_TO", str(mapped_node_id), reason="Provider fact mapped to shujuan node.", created_by="provider")
        elif endpoint:
            create_edge_fn(conn, fact_node_id, "APPLIES_TO", endpoint["node_id"], reason="Unmapped provider fact stays endpoint-scoped provider_hypothesis.", created_by="provider")
        fact_id = new_id_fn("provider_fact")
        conn.execute(
            """
            INSERT INTO provider_facts
              (id, run_id, artifact_id, node_id, external_id, fact_type, summary, confidence, provenance, classification, mapped_node_id, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact_id,
                run_id,
                artifact_id,
                fact_node_id,
                external_id,
                str(item.get("fact_type") or "provider_fact"),
                summary,
                float(item["confidence"]) if item.get("confidence") is not None else None,
                json_dumps_fn(item.get("provenance") or {}),
                classification,
                str(mapped_node_id) if mapped_node_id else None,
                now_iso_fn(),
                json_dumps_fn({key: value for key, value in item.items() if key not in {"external_id", "fact_type", "summary", "confidence", "provenance", "classification", "mapped_node_id"}}),
            ),
        )
        facts.append({"id": fact_id, "node_id": fact_node_id, "semantic_item_id": semantic_item_id, "classification": classification, "mapped_node_id": mapped_node_id})

    warning_nodes = []
    for item in provider_payload_items(payload.get("warnings"), field="warnings"):
        summary = str(item.get("summary") or item.get("message") or "").strip()
        if not summary:
            raise SystemExit("provider warning entries require summary or message")
        classification = str(item.get("classification") or "provider_hypothesis")
        if classification == "actionable":
            node_id = create_node_fn(
                conn,
                "audit_finding",
                str(item.get("label") or "provider warning"),
                summary[:240],
                {"provider": provider, "classification": classification, "endpoint": args.endpoint},
            )
            semantic_item_id = create_semantic_item_fn(
                conn,
                node_id,
                "audit_finding",
                state="active",
                source_node=artifact_node_id,
                scope_node=endpoint["node_id"] if endpoint else artifact_node_id,
                event_type="provider_warning_actionable",
                reason="Provider warning explicitly classified actionable.",
                props={"classification": classification, "provider": provider},
            )
            create_edge_fn(conn, node_id, "DERIVED_FROM", artifact_node_id, reason="Actionable provider warning derived from provider artifact.", created_by="provider")
            if endpoint:
                create_edge_fn(conn, node_id, "APPLIES_TO", endpoint["node_id"], reason="Actionable provider warning applies to endpoint.", created_by="provider")
        else:
            node_id = create_node_fn(
                conn,
                "work_note",
                str(item.get("label") or "provider hypothesis"),
                summary[:240],
                {"kind": "provider_hypothesis", "provider": provider, "classification": classification, "endpoint": args.endpoint},
            )
            semantic_item_id = create_semantic_item_fn(
                conn,
                node_id,
                "work_note",
                state=product_backlog_state,
                source_node=artifact_node_id,
                scope_node=endpoint["node_id"] if endpoint else artifact_node_id,
                event_type="provider_warning_imported",
                reason="Provider warning imported as non-active provider_hypothesis.",
                props={"classification": classification, "provider": provider},
            )
            create_edge_fn(conn, node_id, "DERIVED_FROM", artifact_node_id, reason="Provider warning derived from provider artifact.", created_by="provider")
            if endpoint:
                create_edge_fn(conn, node_id, "APPLIES_TO", endpoint["node_id"], reason="Provider hypothesis applies to endpoint.", created_by="provider")
        warning_nodes.append({"node_id": node_id, "semantic_item_id": semantic_item_id, "classification": classification})

    conn.commit()
    print_json_fn(
        {
            "ok": True,
            "provider": provider,
            "contract_version": contract_version,
            "provider_run_id": run_id,
            "provider_run_node_id": run_node_id,
            "provider_artifact_id": artifact_id,
            "provider_artifact_node_id": artifact_node_id,
            "artifact": captured,
            "entity_maps": entity_maps,
            "facts": facts,
            "warnings": warning_nodes,
        }
    )
    return 0


def register_provider(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handlers: Mapping[str, ProviderHandler],
) -> None:
    """Register the provider command family while cli.py keeps global flags and dispatch."""
    _validate_handlers(handlers)

    provider = subparsers.add_parser("provider")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)
    provider_contract = provider_sub.add_parser("contract")
    provider_contract.set_defaults(func=handlers["contract"])
    provider_import = provider_sub.add_parser("import-json")
    provider_import.add_argument("--path", required=True)
    provider_import.add_argument("--endpoint")
    provider_import.add_argument("--source-node")
    provider_import.add_argument("--provider")
    provider_import.add_argument("--label")
    provider_import.set_defaults(func=handlers["import_json"])


__all__ = [
    "PROVIDER_CONTRACT_VERSION",
    "DEFAULT_IMPACT_SOURCE",
    "PREFERRED_IMPACT_PROVIDER",
    "PREFERRED_IMPACT_SKILL",
    "GITNEXUS_INDEX_PATH",
    "PROVIDER_HANDLER_KEYS",
    "build_provider_handlers",
    "cmd_provider_contract",
    "cmd_provider_import_json",
    "impact_provider_contract",
    "impact_metadata",
    "gitnexus_command",
    "gitnexus_provider_detail",
    "load_provider_structured_json",
    "provider_closure_evidence_boundary",
    "provider_lookup_entity_map",
    "provider_payload_items",
    "register_provider",
    "upsert_provider_entity_map",
]
