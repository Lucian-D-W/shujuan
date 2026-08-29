from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_runtime_storage import normalize_runtime_reference, text_values


def main() -> int:
    cases = {
        ".shujuan\\patches\\run.patch": ".shujuan/patches/run.patch",
        "E:/repo/.shujuan/artifacts/result.json)": ".shujuan/artifacts/result.json",
        "not-a-runtime-reference": None,
    }
    for value, expected in cases.items():
        actual = normalize_runtime_reference(value)
        if actual != expected:
            raise AssertionError(f"reference normalization mismatch: {value!r} -> {actual!r}")
    if text_values({"path": ".shujuan/exports/report.md"}) != ['{"path": ".shujuan/exports/report.md"}']:
        raise AssertionError("structured database values were not normalized deterministically")
    print("runtime storage audit helpers: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
