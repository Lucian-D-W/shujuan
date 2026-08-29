from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "v6_p1_worker_hunk_manifest_2026-05-21.md"

REQUIRED_FIELDS = {
    "worker_id",
    "lane",
    "task_id",
    "check_id",
    "path",
    "hunk_id",
    "hunk_header",
    "range",
    "hash",
    "claimed_owner",
    "ownership_status",
    "pre_existing_dirty",
    "lane_note",
}

REQUIRED_LANES = {
    "owned",
    "pre_existing_dirty",
    "deleted_obsolete",
    "not_owned_observed",
    "out_of_scope",
    "path_level_fallback",
}


def table_rows(markdown: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current_header: list[str] | None = None
    for line in markdown.splitlines():
        if not line.startswith("|"):
            current_header = None
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if cells and set(cells) == {"---"}:
            continue
        if REQUIRED_FIELDS <= set(cells):
            current_header = cells
            continue
        if current_header and len(cells) == len(current_header):
            rows.append(dict(zip(current_header, cells)))
    return rows


def main() -> int:
    text = MANIFEST.read_text(encoding="utf-8")
    for field in REQUIRED_FIELDS:
        if f"`{field}`" not in text and re.search(rf"\|\s*{re.escape(field)}\s*\|", text) is None:
            raise AssertionError(f"manifest is missing required ownership field: {field}")
    for phrase in (
        "Path-level fallback rules:",
        "A path-level fallback does not claim ownership of pre-existing dirty hunks",
        "Deleted obsolete, not-owned observed, and out-of-scope rows must stay in their own lanes",
    ):
        if phrase not in text:
            raise AssertionError(f"manifest is missing explicit fallback/lane rule: {phrase}")

    rows = table_rows(text)
    if not rows:
        raise AssertionError("manifest has no ownership rows with the required field set")
    lanes = {row["lane"] for row in rows}
    missing_lanes = REQUIRED_LANES - lanes
    if missing_lanes:
        raise AssertionError(f"manifest collapsed or omitted lane(s): {sorted(missing_lanes)}")
    statuses = {row["ownership_status"] for row in rows}
    if not REQUIRED_LANES <= statuses:
        raise AssertionError(f"ownership_status values do not distinguish all lanes: {sorted(statuses)}")

    def rows_for(lane: str) -> list[dict[str, str]]:
        return [row for row in rows if row["lane"] == lane]

    if not any(row["task_id"] == "task_e4de9ecaacfb4480" and row["check_id"] == "check_55b1aeb2322747dc" for row in rows_for("owned")):
        raise AssertionError("owned lane does not include the worker hunk manifest artifact check")
    if not any(row["check_id"] == "check_23949c0f1dce4a9b" for row in rows_for("owned")):
        raise AssertionError("owned lane does not include the focused manifest test check")
    if not all(row["pre_existing_dirty"] == "yes" for row in rows_for("pre_existing_dirty")):
        raise AssertionError("pre-existing dirty lane must mark pre_existing_dirty=yes")
    if not all(row["path"] == "None" for row in rows_for("deleted_obsolete")):
        raise AssertionError("deleted obsolete lane must remain explicit and separate when no deletion occurred")
    if any(row["claimed_owner"] == "goodall-v6-p1-hardening-worker" for row in rows_for("not_owned_observed")):
        raise AssertionError("not-owned observed lane incorrectly claims worker ownership")
    if any(row["claimed_owner"] == "goodall-v6-p1-hardening-worker" for row in rows_for("out_of_scope")):
        raise AssertionError("out-of-scope lane incorrectly claims worker ownership")
    if not all(row["hunk_id"].startswith("path_fallback:") for row in rows_for("path_level_fallback")):
        raise AssertionError("path-level fallback rows must use explicit path_fallback hunk ids")
    if not all(row["pre_existing_dirty"] == "yes" for row in rows_for("path_level_fallback")):
        raise AssertionError("path-level fallback rows must preserve the pre-existing dirty marker")

    print(json.dumps({"ok": True, "worker_hunk_manifest_contract": "passed", "lanes": sorted(lanes)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
