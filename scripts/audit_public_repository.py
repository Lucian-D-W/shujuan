from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


MAX_PUBLIC_BLOB_BYTES = 5 * 1024 * 1024
SAFE_AUTHOR_PATTERNS = (
    re.compile(r"^[^@]+@users\.noreply\.github\.com$", re.IGNORECASE),
    re.compile(r"^[^@]+@[^@]+\.invalid$", re.IGNORECASE),
)
FORBIDDEN_PATH_PATTERNS = (
    ("private_runtime_path", re.compile(r"(^|/)\.shujuan(/|$)", re.IGNORECASE)),
    ("provider_index_path", re.compile(r"(^|/)(?:\.gitnexus|\.ai|\.codegraph)(/|$)", re.IGNORECASE)),
    ("generated_skill_copy", re.compile(r"(^|/)(?:\.agents|\.claude)/skills/gitnexus[^/]*(/|$)", re.IGNORECASE)),
    ("removed_integration_path", re.compile(r"zhanggong", re.IGNORECASE)),
    ("generated_output_path", re.compile(r"(^|/)(?:build|dist|release|[^/]+\.egg-info)(/|$)", re.IGNORECASE)),
    ("credential_file_path", re.compile(r"(^|/)(?:\.env(?:\..*)?|credentials\.json|secrets?\.[^/]+)$", re.IGNORECASE)),
    ("private_key_file_path", re.compile(r"\.(?:pem|key|p12|pfx|jks)$", re.IGNORECASE)),
    ("database_or_dump_path", re.compile(r"\.(?:sqlite3?|db|dump|bak|sql\.gz)$", re.IGNORECASE)),
    ("archive_path", re.compile(r"\.(?:7z|rar|tar|tgz|zip)$", re.IGNORECASE)),
)
SECRET_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")),
    ("github_token", re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("openai_key", re.compile(rb"(?<![A-Za-z0-9_])sk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_-]{32,}")),
    ("anthropic_key", re.compile(rb"sk-ant-[A-Za-z0-9_-]{24,}")),
    ("aws_access_key", re.compile(rb"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("google_api_key", re.compile(rb"AIza[0-9A-Za-z_-]{35}")),
    ("slack_token", re.compile(rb"xox[baprs]-[0-9A-Za-z-]{20,}")),
    ("stripe_live_key", re.compile(rb"(?:sk|rk)_live_[0-9A-Za-z]{16,}")),
    ("gitlab_token", re.compile(rb"glpat-[0-9A-Za-z_-]{20,}")),
    ("npm_token", re.compile(rb"npm_[0-9A-Za-z]{36,}")),
    ("pypi_token", re.compile(rb"pypi-AgEIcH[A-Za-z0-9_-]{40,}")),
    ("huggingface_token", re.compile(rb"hf_[0-9A-Za-z]{30,}")),
)
EMBEDDED_CREDENTIAL_PATTERN = re.compile(
    rb"(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|https?)://[^\s/:@]+:([^\s/@]+)@",
    re.IGNORECASE,
)
PLACEHOLDER_PASSWORDS = {
    b"pass",
    b"password",
    b"secret",
    b"test",
    b"example",
    b"redacted",
    b"masked",
    b"changeme",
    b"bad",
    b"postgres",
    b"***",
}


def _git(root: Path, *args: str, text: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[Any]:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        text=text,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        stderr = completed.stderr if text else completed.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(stderr.strip() or f"git {' '.join(args)} failed")
    return completed


def _finding(rule: str, path: str, *, line: int | None = None, detail: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"rule": rule, "path": path}
    if line is not None:
        payload["line"] = line
    if detail:
        payload["detail"] = detail
    return payload


def _line_number(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def _allowed_email(email: str, explicit: set[str]) -> bool:
    normalized = email.strip().lower()
    return normalized in explicit or any(pattern.fullmatch(normalized) for pattern in SAFE_AUTHOR_PATTERNS)


def _reachable_tree_entries(root: Path, ref: str) -> tuple[list[str], list[dict[str, Any]]]:
    commits = [line for line in _git(root, "rev-list", ref).stdout.splitlines() if line]
    entries: list[dict[str, Any]] = []
    for commit in commits:
        raw = _git(root, "-c", "core.quotepath=false", "ls-tree", "-r", "-z", "-l", commit, text=False).stdout
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            metadata, path_bytes = entry.split(b"\t", 1)
            mode, object_type, oid, size_bytes = metadata.split(b" ", 3)
            entries.append(
                {
                    "commit": commit,
                    "mode": mode.decode("ascii"),
                    "object_type": object_type.decode("ascii"),
                    "oid": oid.decode("ascii"),
                    "size": None if size_bytes == b"-" else int(size_bytes),
                    "path": path_bytes.decode("utf-8", errors="replace"),
                }
            )
    return commits, entries


def _scan_content(data: bytes, path: str, personal_values: list[bytes]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rule, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(data):
            findings.append(_finding(rule, path, line=_line_number(data, match.start())))
    for match in EMBEDDED_CREDENTIAL_PATTERN.finditer(data):
        password = match.group(1).strip().lower()
        if password not in PLACEHOLDER_PASSWORDS and not password.startswith((b"${", b"{", b"<", b"%")):
            findings.append(_finding("embedded_url_credential", path, line=_line_number(data, match.start())))
    lowered = data.lower()
    for value in personal_values:
        if not value:
            continue
        offset = lowered.find(value.lower())
        if offset >= 0:
            findings.append(_finding("personal_identity_value", path, line=_line_number(data, offset)))
    return findings


def audit_repository(
    root: Path,
    *,
    ref: str = "main",
    require_no_remotes: bool = False,
    require_clean: bool = False,
    allowed_author_emails: set[str] | None = None,
    max_blob_bytes: int = MAX_PUBLIC_BLOB_BYTES,
) -> dict[str, Any]:
    root = root.resolve()
    explicit_emails = {item.strip().lower() for item in (allowed_author_emails or set()) if item.strip()}
    commit = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
    findings: list[dict[str, Any]] = []

    remotes = [line for line in _git(root, "remote").stdout.splitlines() if line.strip()]
    if require_no_remotes and remotes:
        findings.append(_finding("remote_present", "<git-config>", detail=f"count={len(remotes)}"))
    dirty_entries = [line for line in _git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines() if line]
    if require_clean and dirty_entries:
        findings.append(_finding("worktree_not_clean", "<worktree>", detail=f"count={len(dirty_entries)}"))

    commits, tree_entries = _reachable_tree_entries(root, ref)
    blobs_by_oid: dict[str, dict[str, Any]] = {}
    historical_paths: set[str] = set()
    for entry in tree_entries:
        mode = str(entry["mode"])
        path = PurePosixPath(str(entry["path"])).as_posix()
        historical_paths.add(path)
        if mode == "120000":
            findings.append(_finding("symlink", path))
        elif mode == "160000":
            findings.append(_finding("gitlink", path))
        for rule, pattern in FORBIDDEN_PATH_PATTERNS:
            if pattern.search(path):
                findings.append(_finding(rule, path))
        if entry["object_type"] == "blob":
            blob = blobs_by_oid.setdefault(
                str(entry["oid"]),
                {"oid": str(entry["oid"]), "size": int(entry["size"] or 0), "paths": set()},
            )
            blob["paths"].add(path)

    author_lines = _git(root, "log", ref, "--format=%H%x09%an%x09%ae%x09%cn%x09%ce").stdout.splitlines()
    unsafe_author_count = 0
    for line in author_lines:
        fields = line.split("\t")
        if len(fields) < 5:
            findings.append(_finding("unparseable_commit_identity", "<commit-metadata>"))
            continue
        for email in (fields[2], fields[4]):
            if not _allowed_email(email, explicit_emails):
                unsafe_author_count += 1
                findings.append(_finding("non_public_commit_email", "<commit-metadata>"))

    personal_texts = [os.environ.get("USERPROFILE", ""), os.environ.get("HOME", "")]
    global_email = subprocess.run(
        ["git", "config", "--global", "user.email"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout.strip()
    if global_email and not _allowed_email(global_email, explicit_emails):
        personal_texts.append(global_email)
    personal_variants = {
        variant
        for item in personal_texts
        if item
        for variant in (item, item.replace("\\", "/"), item.replace("/", "\\"))
        if variant
    }
    personal_values = [item.encode("utf-8") for item in sorted(personal_variants)]

    scanned_bytes = 0
    max_seen = 0
    for oid, blob in blobs_by_oid.items():
        size = int(blob["size"])
        max_seen = max(max_seen, size)
        if size > max_blob_bytes:
            for path in sorted(blob["paths"]):
                findings.append(_finding("oversized_blob", path, detail=f"bytes={size}"))
            continue
        data = _git(root, "cat-file", "blob", oid, text=False).stdout
        scanned_bytes += len(data)
        for path in sorted(blob["paths"]):
            findings.extend(_scan_content(data, path, personal_values))

    commit_messages = _git(root, "log", ref, "--format=%B%x00", text=False).stdout
    findings.extend(_scan_content(commit_messages, "<commit-messages>", personal_values))

    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for finding in findings:
        key = (finding.get("rule"), finding.get("path"), finding.get("line"), finding.get("detail"))
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    unique.sort(key=lambda item: (str(item.get("path")), str(item.get("rule")), int(item.get("line") or 0)))
    return {
        "ok": not unique,
        "schema": "shujuan.publication_privacy_gate.v1",
        "root": str(root),
        "ref": ref,
        "commit": commit,
        "commit_count": len(commits),
        "reachable_blob_count": len(blobs_by_oid),
        "historical_path_count": len(historical_paths),
        "scanned_bytes": scanned_bytes,
        "max_blob_bytes": max_seen,
        "remote_count": len(remotes),
        "dirty_entry_count": len(dirty_entries),
        "unsafe_commit_identity_count": unsafe_author_count,
        "findings_count": len(unique),
        "findings": unique,
        "matched_values_redacted": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit exactly the Git objects reachable from a public ref without printing matched secret values.")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--ref", default="main")
    parser.add_argument("--require-no-remotes", action="store_true")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--allow-author-email", action="append", default=[])
    parser.add_argument("--max-blob-bytes", type=int, default=MAX_PUBLIC_BLOB_BYTES)
    args = parser.parse_args()
    try:
        payload = audit_repository(
            args.repo,
            ref=args.ref,
            require_no_remotes=args.require_no_remotes,
            require_clean=args.require_clean,
            allowed_author_emails=set(args.allow_author_email),
            max_blob_bytes=max(1, args.max_blob_bytes),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {
            "ok": False,
            "schema": "shujuan.publication_privacy_gate.v1",
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "matched_values_redacted": True,
        }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
