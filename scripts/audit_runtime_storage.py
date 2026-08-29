from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shujuan.store import connect_runtime
from scripts.audit_public_repository import EMBEDDED_CREDENTIAL_PATTERN, PLACEHOLDER_PASSWORDS, SECRET_PATTERNS


AUDITED_BUCKETS = ("patches", "artifacts", "exports")
CANONICAL_REFERENCE_COLUMNS = {
    "evidence_records": ("ref",),
    "projection_snapshots": ("payload_ref",),
    "provider_artifacts": ("path", "capture_ref"),
    "run_snapshots": ("patch_ref",),
}
PATH_PATTERN = re.compile(
    r"(?i)(?:[a-z]:/[^\s\"'<>]*?)?\.shujuan/(?:patches|artifacts|exports)/[^\s\"'<>]+"
)
FILE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.-]+\.(?:patch|md|json|html|png|txt|ps1|py|sql|csv|zip|sha256)")
TRAILING_PATH_PUNCTUATION = ".,;:)]}"


def normalize_runtime_reference(value: str) -> str | None:
    normalized = value.replace("\\", "/").rstrip(TRAILING_PATH_PUNCTUATION)
    marker = normalized.lower().find(".shujuan/")
    if marker < 0:
        return None
    return normalized[marker:]


def text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (dict, list, tuple)):
        return [json.dumps(value, ensure_ascii=False, sort_keys=True)]
    return [str(value)]


def database_reference_tokens(
    repo: Path,
) -> tuple[set[str], set[str], int, Counter[str], list[dict[str, Any]]]:
    references: set[str] = set()
    basenames: set[str] = set()
    scanned_values = 0
    secret_counts: Counter[str] = Counter()
    secret_locations: set[tuple[str, str, str]] = set()
    conn = connect_runtime(repo, read_only_filesystem=True)
    try:
        columns = conn.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND data_type IN ('text', 'character varying', 'character', 'json', 'jsonb')
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()
        for column in columns:
            table_name = str(column["table_name"])
            column_name = str(column["column_name"])
            quoted_table = '"' + table_name.replace('"', '""') + '"'
            quoted_column = '"' + column_name.replace('"', '""') + '"'
            rows = conn.execute(
                f"SELECT {quoted_column} AS value FROM {quoted_table} WHERE {quoted_column} IS NOT NULL"
            ).fetchall()
            for row in rows:
                for text in text_values(row["value"]):
                    scanned_values += 1
                    normalized_text = text.replace("\\", "/")
                    for match in PATH_PATTERN.finditer(normalized_text):
                        normalized = normalize_runtime_reference(match.group(0))
                        if normalized:
                            references.add(normalized)
                    basenames.update(FILE_TOKEN_PATTERN.findall(normalized_text))
                    data = text.encode("utf-8", errors="replace")
                    for rule, pattern in SECRET_PATTERNS:
                        count = sum(1 for _ in pattern.finditer(data))
                        if count:
                            secret_counts[rule] += count
                            secret_locations.add((rule, table_name, column_name))
                    for match in EMBEDDED_CREDENTIAL_PATTERN.finditer(data):
                        password = match.group(1).strip().lower()
                        if password not in PLACEHOLDER_PASSWORDS and not password.startswith((b"${", b"{", b"<", b"%")):
                            secret_counts["embedded_url_credential"] += 1
                            secret_locations.add(("embedded_url_credential", table_name, column_name))
    finally:
        conn.close()
    locations = [
        {"rule": rule, "table": table, "column": column}
        for rule, table, column in sorted(secret_locations)
    ]
    return references, basenames, scanned_values, secret_counts, locations


def canonical_reference_status(repo: Path) -> dict[str, dict[str, int]]:
    status: dict[str, dict[str, int]] = {}
    conn = connect_runtime(repo, read_only_filesystem=True)
    try:
        for table_name, columns in CANONICAL_REFERENCE_COLUMNS.items():
            for column_name in columns:
                rows = conn.execute(
                    f'SELECT "{column_name}" AS value FROM "{table_name}" WHERE "{column_name}" IS NOT NULL'
                ).fetchall()
                existing = 0
                missing = 0
                skipped = 0
                for row in rows:
                    value = str(row["value"]).strip()
                    normalized = normalize_runtime_reference(value)
                    if normalized:
                        candidate = repo / normalized
                    elif table_name == "provider_artifacts":
                        raw_candidate = Path(value)
                        candidate = raw_candidate if raw_candidate.is_absolute() else repo / raw_candidate
                    else:
                        skipped += 1
                        continue
                    if candidate.is_file():
                        existing += 1
                    else:
                        missing += 1
                status[f"{table_name}.{column_name}"] = {
                    "row_count": len(rows),
                    "existing_file_count": existing,
                    "missing_file_count": missing,
                    "non_file_value_count": skipped,
                }
    finally:
        conn.close()
    return status


def audit_runtime_storage(repo: Path) -> dict[str, Any]:
    runtime_root = repo / ".shujuan"
    references, referenced_basenames, scanned_values, secret_counts, secret_locations = database_reference_tokens(repo)
    canonical_status = canonical_reference_status(repo)
    existing_by_ref: dict[str, Path] = {}
    files_by_bucket: dict[str, list[Path]] = {}
    for bucket in AUDITED_BUCKETS:
        files = sorted(path for path in (runtime_root / bucket).rglob("*") if path.is_file())
        files_by_bucket[bucket] = files
        for path in files:
            existing_by_ref[path.relative_to(repo).as_posix()] = path

    referenced_paths: set[str] = set()
    for relative_path, path in existing_by_ref.items():
        if relative_path in references or path.name in referenced_basenames:
            referenced_paths.add(relative_path)

    missing_references = sorted(
        reference
        for reference in references
        if reference.startswith(tuple(f".shujuan/{bucket}/" for bucket in AUDITED_BUCKETS))
        and reference not in existing_by_ref
    )

    buckets: dict[str, Any] = {}
    unreferenced_paths: list[str] = []
    for bucket, files in files_by_bucket.items():
        referenced = [path for path in files if path.relative_to(repo).as_posix() in referenced_paths]
        unreferenced = [path for path in files if path.relative_to(repo).as_posix() not in referenced_paths]
        unreferenced_paths.extend(path.relative_to(repo).as_posix() for path in unreferenced)
        buckets[bucket] = {
            "file_count": len(files),
            "size_bytes": sum(path.stat().st_size for path in files),
            "referenced_count": len(referenced),
            "referenced_size_bytes": sum(path.stat().st_size for path in referenced),
            "unreferenced_count": len(unreferenced),
            "unreferenced_size_bytes": sum(path.stat().st_size for path in unreferenced),
        }

    return {
        "ok": not secret_counts,
        "read_only": True,
        "matched_values_redacted": True,
        "repo": str(repo),
        "scanned_database_values": scanned_values,
        "database_secret_signal_count": sum(secret_counts.values()),
        "database_secret_signal_rules": dict(sorted(secret_counts.items())),
        "database_secret_signal_locations": secret_locations,
        "database_reference_count": len(references),
        "referenced_basename_count": len(referenced_basenames),
        "missing_reference_count": len(missing_references),
        "missing_references": missing_references,
        "canonical_references": canonical_status,
        "canonical_missing_file_count": sum(
            item["missing_file_count"] for item in canonical_status.values()
        ),
        "buckets": buckets,
        "unreferenced_paths": sorted(unreferenced_paths),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only audit of private shujuan runtime file references.")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--plan-out", type=Path, help="Optional private JSON path for a quarantine plan.")
    args = parser.parse_args()
    result = audit_runtime_storage(args.repo.resolve())
    plan_out = args.plan_out
    if plan_out is not None:
        plan_out = plan_out.resolve()
        runtime_root = (args.repo.resolve() / ".shujuan").resolve()
        if not plan_out.is_relative_to(runtime_root):
            raise SystemExit("--plan-out must remain inside the private .shujuan runtime")
        plan_out.parent.mkdir(parents=True, exist_ok=True)
        plan_out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    printable = {
        key: value
        for key, value in result.items()
        if key not in {"missing_references", "unreferenced_paths"}
    }
    printable["unreferenced_path_count"] = len(result["unreferenced_paths"])
    if plan_out is not None:
        printable["plan_out"] = str(plan_out)
    print(json.dumps(printable, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
