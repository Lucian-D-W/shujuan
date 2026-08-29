from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _load_payload() -> dict[str, object] | None:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _sanitize_prompt(prompt: str) -> str:
    return prompt.replace("\x00", " ")


def _repo_root() -> Path | None:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists() or (candidate / "AGENTS.md").exists() or (candidate / ".codex" / "hooks.json").exists():
            return candidate
    return None


def main() -> int:
    payload = _load_payload()
    if payload is None:
        return 0
    prompt = payload.get("prompt") or payload.get("content") or ""
    if not isinstance(prompt, str):
        return 0
    prompt = _sanitize_prompt(prompt)
    if not prompt.strip():
        return 0
    root = _repo_root()
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix="shujuan-intent-", suffix=".txt") as handle:
            handle.write(prompt)
            intent_path = handle.name
        try:
            command = [sys.executable, "-m", "shujuan"]
            if root is not None:
                command.extend(["--repo", str(root)])
            command.extend(["route", "guard", "--pure", "--intent-file", intent_path])
            completed = subprocess.run(
                command,
                cwd=str(root) if root is not None else None,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        finally:
            try:
                os.unlink(intent_path)
            except OSError:
                pass
    except OSError:
        return 0
    if completed.returncode not in {0, 1}:
        return 0
    try:
        route = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return 0
    context = {
        "shujuan_method_hint": {
            "route": route.get("recommended_route"),
            "skill": route.get("recommended_skill"),
            "method_version": route.get("method_version"),
            "read_only": True,
            "db_writes": 0,
            "non_authoritative": True,
        }
    }
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": json.dumps(context, ensure_ascii=False),
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
