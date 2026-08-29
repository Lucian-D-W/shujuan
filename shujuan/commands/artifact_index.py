from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


ArtifactIndexHandler = Callable[[argparse.Namespace], int]
ARTIFACT_INDEX_HANDLER_KEYS = ("refresh", "verify")
ARTIFACT_INDEX_DEPENDENCY_KEYS = ("ensure_layout", "print_json", "append_trace_event", "json_error_payload")


def _configure(deps: Mapping[str, Any]) -> None:
    missing = [key for key in ARTIFACT_INDEX_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"artifact index command boundary is missing: {', '.join(missing)}")
    globals().update({key: deps[key] for key in ARTIFACT_INDEX_DEPENDENCY_KEYS})


def _normalize_path(repo: Path, raw: str) -> str:
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    return str(path.resolve().relative_to(repo.resolve())).replace("\\", "/")


def _bucket_from_name(name: str) -> str:
    lowered = name.lower()
    if "mapping" in lowered or "import" in lowered:
        return "db_mapping"
    if any(token in lowered for token in ("reviewer", "review", "packet", "return")):
        return "review_material"
    if any(token in lowered for token in ("acceptance", "verify", "suite", "change_set", "evidence")):
        return "evidence"
    if any(token in lowered for token in ("old_", "superseded", "obsolete")):
        return "superseded"
    return "authoritative"


def _kind_from_name(name: str) -> str:
    lowered = name.lower()
    if lowered == "task_chain.json":
        return "task_chain"
    if "mapping" in lowered:
        return "db_mapping"
    if "packet" in lowered:
        return "review_packet"
    if "return" in lowered:
        return "review_return"
    if "change_set" in lowered:
        return "change_set_summary"
    if "verify" in lowered or "suite" in lowered:
        return "verification_artifact"
    if "prompt" in lowered or "handoff" in lowered:
        return "controller_material"
    return "artifact"


def _materiality(bucket: str) -> str:
    return "evidence" if bucket == "evidence" else "material"


def _entry(
    *,
    repo: Path,
    raw: str,
    bucket: str,
    basis: str,
    current_reference: str | None,
    supersede_reason: str | None = None,
) -> dict[str, Any]:
    path = _normalize_path(repo, raw)
    name = Path(path).name
    current = path == current_reference
    canonical = bucket in {"authoritative", "db_mapping"} and current
    related_to: list[dict[str, str]] = []
    if bucket == "db_mapping" and current_reference:
        related_to.append({"kind": "maps_import_of", "path": current_reference})
    if bucket == "evidence" and current_reference:
        related_to.append({"kind": "supports_current_artifact", "path": current_reference})
    if bucket == "review_material" and current_reference:
        related_to.append({"kind": "context_for_current_artifact", "path": current_reference})
    if bucket == "superseded" and current_reference:
        related_to.append({"kind": "superseded_by", "path": current_reference})
    return {
        "path": path,
        "bucket": bucket,
        "kind": _kind_from_name(name),
        "basis": basis,
        "materiality": _materiality(bucket),
        "current_status": "current" if current else ("superseded" if bucket == "superseded" else "supporting"),
        "canonical_status": "canonical" if canonical else ("superseded" if bucket == "superseded" else "supporting"),
        "reason": supersede_reason if bucket == "superseded" else None,
        "related_to": related_to,
    }


def _discover_entries(repo: Path, artifact_root: Path) -> dict[str, list[str]]:
    buckets = {key: [] for key in ("authoritative", "review_material", "db_mapping", "evidence", "superseded")}
    for path in sorted(artifact_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {"INDEX.md", "INDEX.json"}:
            continue
        buckets[_bucket_from_name(path.name)].append(str(path))
    return buckets


def _current_reference(repo: Path, current: list[str], discovered: dict[str, list[str]]) -> str | None:
    if current:
        return _normalize_path(repo, current[0])
    for candidate in discovered.get("authoritative") or []:
        return _normalize_path(repo, candidate)
    return None


def _unique_raw_paths(repo: Path, values: list[str]) -> list[str]:
    unique: dict[str, str] = {}
    for value in values:
        unique.setdefault(_normalize_path(repo, value), value)
    return list(unique.values())


def _render_index_markdown(payload: dict[str, Any]) -> str:
    lines = [f"# Artifact Index: {payload['endpoint']}", ""]
    lines.append(f"- Schema: {payload['schema_version']}")
    lines.append(f"- Write allowed: {str(payload['write_allowed']).lower()}")
    lines.append("")
    for heading, key in (
        ("Authoritative", "authoritative"),
        ("Review Material", "review_material"),
        ("DB Mapping", "db_mapping"),
        ("Evidence", "evidence"),
        ("Superseded", "superseded"),
    ):
        lines.append(f"## {heading}")
        items = payload.get(key) or []
        if not items:
            lines.append("- none")
            lines.append("")
            continue
        for item in items:
            relation = ""
            if item.get("related_to"):
                first = item["related_to"][0]
                relation = f"; {first['kind']}={first['path']}"
            lines.append(
                f"- {item['path']} (kind={item['kind']}; current_status={item['current_status']}; canonical_status={item['canonical_status']}; basis={item['basis']}{relation})"
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _verify_payload(payload: dict[str, Any], *, repo: Path, allow_missing_planned: bool = False) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if payload.get("schema_version") != "shujuan.artifact_index.v2":
        violations.append({"code": "schema_version_mismatch"})
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        violations.append({"code": "missing_entries"})
        return violations
    required = {"path", "bucket", "kind", "basis", "materiality", "current_status", "canonical_status", "related_to"}
    entry_paths = {str(entry.get("path")) for entry in entries if isinstance(entry, dict) and entry.get("path")}
    canonical_authoritative = []
    for entry in entries:
        if not isinstance(entry, dict):
            violations.append({"code": "invalid_entry"})
            continue
        missing = sorted(required - set(entry))
        if missing:
            violations.append({"code": "missing_entry_fields", "path": entry.get("path"), "fields": missing})
            continue
        path = str(entry["path"])
        bucket = str(entry["bucket"])
        canonical_status = str(entry["canonical_status"])
        if bucket == "authoritative" and canonical_status == "canonical":
            canonical_authoritative.append(path)
        if not allow_missing_planned and not (repo / path).exists():
            violations.append({"code": "missing_artifact_file", "path": path, "bucket": bucket, "basis": entry.get("basis")})
        if bucket != "evidence" and entry.get("materiality") == "evidence":
            violations.append({"code": "non_evidence_bucket_marked_evidence", "path": path, "bucket": bucket})
        if bucket == "review_material" and entry.get("kind") in {"verification_artifact", "change_set_summary"}:
            violations.append({"code": "review_material_misclassified_as_evidence", "path": path, "kind": entry.get("kind")})
        for relation in entry.get("related_to") or []:
            if not isinstance(relation, dict):
                violations.append({"code": "invalid_related_to", "path": path})
                continue
            related_path = relation.get("path")
            if related_path and str(related_path) not in entry_paths:
                violations.append({"code": "related_to_missing_entry", "path": path, "related_path": related_path})
    if len(canonical_authoritative) != 1:
        violations.append({"code": "canonical_authoritative_count", "count": len(canonical_authoritative), "paths": canonical_authoritative})
    return violations


def build_artifact_index_handlers(deps: Mapping[str, Any]) -> dict[str, ArtifactIndexHandler]:
    _configure(deps)

    def refresh(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        artifact_root = ensure_layout(repo) / "artifacts" / args.endpoint
        artifact_root.mkdir(parents=True, exist_ok=True)
        discovered = _discover_entries(repo, artifact_root) if args.discover else {key: [] for key in ("authoritative", "review_material", "db_mapping", "evidence", "superseded")}
        current_reference = _current_reference(repo, args.current, discovered)
        raw_buckets = {
            "authoritative": [*discovered["authoritative"], *args.current],
            "review_material": [*discovered["review_material"], *args.review_material],
            "db_mapping": [*discovered["db_mapping"], *args.mapping],
            "evidence": [*discovered["evidence"], *args.evidence],
            "superseded": [*discovered["superseded"], *args.supersede],
        }
        payload = {
            "ok": True,
            "schema_version": "shujuan.artifact_index.v2",
            "endpoint": args.endpoint,
            "read_only": False,
            "write_allowed": False,
            "index_path": str((artifact_root / "INDEX.json").relative_to(repo)),
            "authoritative": [
                _entry(repo=repo, raw=value, bucket="authoritative", basis="explicit" if value in args.current else "discovered", current_reference=current_reference)
                for value in _unique_raw_paths(repo, raw_buckets["authoritative"])
            ],
            "review_material": [
                _entry(repo=repo, raw=value, bucket="review_material", basis="explicit" if value in args.review_material else "discovered", current_reference=current_reference)
                for value in _unique_raw_paths(repo, raw_buckets["review_material"])
            ],
            "db_mapping": [
                _entry(repo=repo, raw=value, bucket="db_mapping", basis="explicit" if value in args.mapping else "discovered", current_reference=current_reference)
                for value in _unique_raw_paths(repo, raw_buckets["db_mapping"])
            ],
            "evidence": [
                _entry(repo=repo, raw=value, bucket="evidence", basis="explicit" if value in args.evidence else "discovered", current_reference=current_reference)
                for value in _unique_raw_paths(repo, raw_buckets["evidence"])
            ],
            "superseded": [
                _entry(
                    repo=repo,
                    raw=value,
                    bucket="superseded",
                    basis="explicit" if value in args.supersede else "discovered",
                    current_reference=current_reference,
                    supersede_reason=args.supersede_reason or "superseded",
                )
                for value in _unique_raw_paths(repo, raw_buckets["superseded"])
            ],
        }
        payload["entries"] = [
            *payload["authoritative"],
            *payload["review_material"],
            *payload["db_mapping"],
            *payload["evidence"],
            *payload["superseded"],
        ]
        (artifact_root / "INDEX.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        (artifact_root / "INDEX.md").write_text(_render_index_markdown(payload), encoding="utf-8")
        append_trace_event(
            repo,
            event_type="artifact_index_refresh",
            endpoint=args.endpoint,
            read_only=False,
            status="written",
            details={"counts": {key: len(payload[key]) for key in ("authoritative", "review_material", "db_mapping", "evidence", "superseded")}},
        )
        print_json(payload)
        return 0

    def verify(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        artifact_root = ensure_layout(repo) / "artifacts" / args.endpoint
        index_path = artifact_root / "INDEX.json"
        if not index_path.exists():
            print_json(json_error_payload("missing_artifact_index", f"artifact index is missing for endpoint {args.endpoint}", read_only=True))
            return 1
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        violations = _verify_payload(payload if isinstance(payload, dict) else {}, repo=repo, allow_missing_planned=bool(args.allow_missing_planned))
        result = {
            "ok": not violations,
            "read_only": True,
            "endpoint": args.endpoint,
            "index_path": str(index_path.relative_to(repo)),
            "violations": violations,
        }
        print_json(result)
        return 0 if result["ok"] or args.allow_fail else 1

    return {"refresh": refresh, "verify": verify}


def register_artifact_index(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, ArtifactIndexHandler]) -> None:
    missing = [key for key in ARTIFACT_INDEX_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"artifact index command boundary is missing: {', '.join(missing)}")
    artifact = subparsers.add_parser("artifact")
    artifact_sub = artifact.add_subparsers(dest="artifact_command", required=True)
    index = artifact_sub.add_parser("index")
    index_sub = index.add_subparsers(dest="artifact_index_command", required=True)
    refresh = index_sub.add_parser("refresh")
    refresh.add_argument("--endpoint", required=True)
    refresh.add_argument("--current", action="append", default=[])
    refresh.add_argument("--mapping", action="append", default=[])
    refresh.add_argument("--review-material", action="append", default=[])
    refresh.add_argument("--supersede", action="append", default=[])
    refresh.add_argument("--supersede-reason")
    refresh.add_argument("--evidence", action="append", default=[])
    refresh.add_argument("--discover", action="store_true")
    refresh.set_defaults(func=handlers["refresh"])
    verify = index_sub.add_parser("verify")
    verify.add_argument("--endpoint", required=True)
    verify.add_argument("--allow-fail", action="store_true")
    verify.add_argument("--allow-missing-planned", action="store_true")
    verify.set_defaults(func=handlers["verify"])


__all__ = ["ARTIFACT_INDEX_HANDLER_KEYS", "build_artifact_index_handlers", "register_artifact_index"]
