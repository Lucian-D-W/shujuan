from __future__ import annotations

import json
import re
import sys
from typing import Any


def _load_payload() -> dict[str, object] | None:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _command_fragments(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        joined = " ".join(str(item) for item in value)
        fragments = [joined]
        for item in value:
            fragments.extend(_command_fragments(item))
        return fragments
    if isinstance(value, dict):
        fragments: list[str] = []
        for key in ("tool_input", "command", "cmd", "args", "argv", "input"):
            if key in value:
                fragments.extend(_command_fragments(value[key]))
        return fragments
    return []


def main() -> int:
    payload = _load_payload()
    if payload is None:
        return 0
    fragments = _command_fragments(payload)
    fragments.append(json.dumps(payload, ensure_ascii=False))
    risky = [
        r"evidence\s+.*--close-check",
        r"evidence\s+(?:close|closeout)\b",
        r"exec\s+stop",
        r"endpoint\s+refresh",
        r"endpoint\s+doctor\s+.*--strict-closeout(?!.*--read-only)",
    ]
    if any(re.search(pattern, command, re.IGNORECASE) for pattern in risky for command in fragments):
        reason = "Shujuan closeout/governance write requires controller authority and matching evidence. This hook is advisory; CLI gates remain authoritative."
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": reason,
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    },
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
