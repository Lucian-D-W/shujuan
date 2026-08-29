from __future__ import annotations

import argparse
import json
import shlex
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCAL_FILE_SUFFIXES = (
    ".bat",
    ".cmd",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _split_command(command: str) -> tuple[list[str], str | None]:
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError as exc:
        return [], f"command could not be parsed: {exc}"
    cleaned = [token.strip("\"'") for token in tokens if token.strip("\"'")]
    if not cleaned:
        return [], "command is empty"
    return cleaned, None


def _looks_like_local_file(token: str) -> bool:
    if not token or token.startswith("-") or "://" in token:
        return False
    if token.startswith("/") and token.count("/") == 1 and "\\" not in token:
        return False
    lowered = token.lower()
    return any(sep in token for sep in ("/", "\\")) or lowered.endswith(LOCAL_FILE_SUFFIXES)


def _path_arg_tokens(tokens: list[str]) -> list[str]:
    path_tokens: list[str] = []
    for token in tokens[1:]:
        candidate = token.split("=", 1)[1] if token.startswith("-") and "=" in token else token
        if _looks_like_local_file(candidate):
            path_tokens.append(candidate)
    return path_tokens


def _resolve_repo_file(token: str) -> Path:
    path = Path(token)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _validate_replay_command(check_key: str, command: Any) -> list[dict[str, str]]:
    if not isinstance(command, str):
        return [{"check_key": check_key, "command": str(command), "reason": "replay.command must be a string"}]

    failures: list[dict[str, str]] = []
    tokens, parse_error = _split_command(command)
    if parse_error:
        return [{"check_key": check_key, "command": command, "reason": parse_error}]

    executable = tokens[0]
    executable_path = _resolve_repo_file(executable) if _looks_like_local_file(executable) else None
    if executable_path is not None:
        if not executable_path.exists():
            failures.append(
                {
                    "check_key": check_key,
                    "command": command,
                    "token": executable,
                    "reason": "command executable path is missing",
                }
            )
    elif shutil.which(executable) is None:
        failures.append(
            {
                "check_key": check_key,
                "command": command,
                "token": executable,
                "reason": "command executable was not found on PATH",
            }
        )

    for token in _path_arg_tokens(tokens):
        target = _resolve_repo_file(token)
        if not _is_relative_to(target, ROOT):
            failures.append(
                {
                    "check_key": check_key,
                    "command": command,
                    "token": token,
                    "reason": "replay command file reference is outside the repository",
                }
            )
            continue
        if not target.exists():
            failures.append(
                {
                    "check_key": check_key,
                    "command": command,
                    "token": token,
                    "reason": "replay command file reference is missing",
                }
            )
    return failures


def verify_coverage(path: Path, *, strict_p0: bool) -> dict[str, Any]:
    payload = _load(path)
    rows = payload.get("checks")
    if not isinstance(rows, list):
        raise SystemExit("coverage JSON must contain checks[]")
    missing_refs: list[dict[str, str]] = []
    command_failures: list[dict[str, str]] = []
    strict_failures: list[dict[str, str]] = []
    allowed_status = {"observed_pass", "reported_pass_unreplayable", "not_tested"}
    validated_commands = 0
    for row in rows:
        if not isinstance(row, dict):
            raise SystemExit("coverage checks[] entries must be objects")
        key = str(row.get("check_key") or "")
        phase = str(row.get("phase") or "")
        status = str(row.get("evidence_status") or "")
        if status not in allowed_status:
            raise SystemExit(f"{key} has invalid evidence_status: {status}")
        replay = row.get("replay") or {}
        if not isinstance(replay, dict):
            raise SystemExit(f"{key} replay must be an object")
        command = replay.get("command")
        if command:
            validated_commands += 1
            command_failures.extend(_validate_replay_command(key, command))
        refs = replay.get("references") or []
        for ref in refs:
            if not isinstance(ref, str):
                missing_refs.append({"check_key": key, "reference": repr(ref), "reason": "replay reference must be a string"})
                continue
            target = _resolve_repo_file(ref)
            if not _is_relative_to(target, ROOT):
                missing_refs.append({"check_key": key, "reference": ref, "reason": "replay reference is outside the repository"})
                continue
            if not target.exists():
                missing_refs.append({"check_key": key, "reference": ref, "reason": "replay reference is missing"})
        if strict_p0 and phase == "P0" and status == "observed_pass" and not command:
            strict_failures.append({"check_key": key, "reason": "P0 observed pass is missing replay.command"})
        if strict_p0 and phase == "P0" and status == "reported_pass_unreplayable":
            strict_failures.append({"check_key": key, "reason": "P0 check is not replayable"})
    ok = not missing_refs and not command_failures and not strict_failures
    return {
        "ok": ok,
        "coverage_file": str(path),
        "strict_p0": strict_p0,
        "check_count": len(rows),
        "validated_command_count": validated_commands,
        "missing_references": missing_refs,
        "command_failures": command_failures,
        "strict_failures": strict_failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify v11.2 replay evidence coverage metadata.")
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--strict-p0", action="store_true")
    args = parser.parse_args()
    result = verify_coverage((ROOT / args.coverage).resolve(), strict_p0=args.strict_p0)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
