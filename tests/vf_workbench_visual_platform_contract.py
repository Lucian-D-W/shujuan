from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.cli import render_workbench_html
from shujuan.commands.graph import build_workbench_overlay


def extract_payload(html: str) -> dict[str, object]:
    marker = '<script id="projection-payload" type="application/json">'
    raw = html.split(marker, 1)[1].split("</script>", 1)[0]
    return json.loads(raw)


def projection_item(node_id: str, kind: str, label: str, lane: str, state: str, color: str, review: str | None = None) -> dict[str, object]:
    return {
        "kind": kind,
        "kind_label_zh": {"task": "任务", "acceptance_check": "验收检查", "evidence": "证据", "audit_finding": "审计发现"}.get(kind, kind),
        "id": node_id,
        "node_id": node_id,
        "label": label,
        "summary": label,
        "visible_chain": [{"id": node_id, "type": kind, "label": label}],
        "visible_edges": [],
        "hidden_source_count": 0,
        "hidden_source_edge_classes": [],
        "detail_ref": f"graph detail --node {node_id}",
        "lane_role": lane,
        "lifecycle_state": state,
        "filter_metadata": {
            "text": f"{kind} {label}",
            "lane_role": lane,
            "lane_lifecycle": state,
            "review_result": review,
            "ownership": "assigned",
            "node_type": kind,
            "edge_types": [],
            "active_state": "active",
            "closeout_gate": "blocking" if state == "blocked" else "warning",
            "has_hidden_sources": False,
            "has_detail_ref": True,
            "has_source_preview": False,
        },
        "visual": {"attention": True, "color": color, "shape": "info-card"},
        "raw": {},
    }


def assert_visual_platform_contract() -> None:
    items = [
        projection_item("node_task", "task", "Worker active task", "worker_lane", "open", "#38bdf8"),
        projection_item("node_blocker", "acceptance_check", "Active blocker", "controller_lane", "blocked", "#ef4444"),
        projection_item("node_evidence", "evidence", "Verified evidence", "controller_lane", "verified", "#22c55e"),
        projection_item("node_review", "audit_finding", "Partial review", "reviewer_lane", "active", "#f97316", "partial"),
    ]
    views = {"attention": {"items": items, "item_count": len(items), "broken_visible_chain_count": 0}}
    overlay = build_workbench_overlay(
        sqlite3.connect(":memory:"),
        endpoint_id=None,
        views=views,
        requested_view="attention",
        include_history=False,
        include_consumed=False,
        limit=25,
    )
    payload = {"endpoint": "visual-contract", "views": views, "overlay": overlay, "detail_payloads": {}, "workbench": {"db_write_path": False}}
    html = render_workbench_html(payload)
    exported = extract_payload(html)
    contract = exported["overlay"]["visual_feature_contract"]
    palette = contract["highlight"]["palette"]
    if contract["background"] != "black_or_near_black" or contract["node_shape"] != "readable_box_or_card":
        raise AssertionError(f"visual feature contract does not describe the rebuilt platform: {contract}")
    expected_slots = [
        "selected_route_reference_example",
        "active_blocker",
        "verified_or_evidence_linked",
        "open_or_worker_active",
        "returned_imported_or_waiting_controller",
        "review_accept",
        "review_reject",
        "review_unclear",
        "review_partial",
        "controller_lane",
        "worker_lane",
        "reviewer_lane",
        "research_lane",
        "writer_lane",
        "provider_lane",
        "summary_only_or_non_active",
    ]
    colors = {palette.get(slot) for slot in expected_slots}
    if None in colors or len(colors) < 5:
        raise AssertionError(f"semantic slots collapsed or disappeared: {palette}")
    required_html = [
        "data-node-rendering=\"box-card\"",
        "data-visual-feature-schema=\"wb_lane_visual_feature_boundary.v1\"",
        "selected_route_reference_example",
        "active_blocker",
        "verified_or_evidence_linked",
        "open_or_worker_active",
        "returned_imported_or_waiting_controller",
        "semantic-active-blocker",
        "semantic-verified-or-evidence-linked",
        "semantic-open-or-worker-active",
        "function semanticColorForItem(item)",
        "function laneColorForItem(item)",
        "function statusBadge(label, color, kind = 'status')",
        "status-badge",
        "data-status-kind",
        "data-semantic-color",
        "data-lane-color",
        "card-lane-swatch",
        "style=\"--step-color:",
        "style=\"--route-color:",
        "data-legend-group=\"${escapeText(groupName)}\"",
        "semanticPalette.controller_lane",
        "semanticPalette.worker_lane",
        "semanticPalette.reviewer_lane",
        "semanticPalette.research_lane",
        "semanticPalette.writer_lane",
        "semanticPalette.provider_lane",
        'data-i18n="flows"',
        'data-i18n="steps"',
        'data-i18n="rawJson"',
        "const DISPLAY_TEXT",
    ]
    missing = [text for text in required_html if text not in html]
    if missing:
        raise AssertionError(f"workbench HTML missed visual-platform predicates: {missing}")
    forbidden = ["type: 'circle'", "labelPlacement: 'bottom'", "force-directed primary"]
    if any(text in html for text in forbidden):
        raise AssertionError("workbench platform still advertises dot/force-primary rendering")
    forbidden_yellow_only = [
        "frameParams.set('refresh', String(Date.now()))",
        "const signature = JSON.stringify([payload.generated_at",
    ]
    leaked = [text for text in forbidden_yellow_only if text in html]
    if leaked:
        raise AssertionError(f"workbench live shell retained volatile/yellow-only refresh patterns: {leaked}")


def assert_compact_feature_artifact() -> None:
    artifact_path = ROOT / "docs" / "workbench_lane_visual_features_2026-05-23.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("schema") != "wb_lane_visual_feature_boundary.v1":
        raise AssertionError(f"feature artifact must expose top-level schema: {artifact_path}")
    forbidden_full_export_keys = {"detail_payloads", "views", "nodes", "edges", "overlay", "projection_metadata", "noise_controls", "workbench"}
    present = forbidden_full_export_keys.intersection(artifact)
    if present:
        raise AssertionError(f"compact feature artifact regressed into a full export payload: {sorted(present)}")
    serialized = json.dumps(artifact, ensure_ascii=False)
    forbidden_payload_markers = ["visible_chain", "visible_edges", "detail_ref_sources", "projection_payload"]
    leaked = [marker for marker in forbidden_payload_markers if marker in serialized]
    if leaked:
        raise AssertionError(f"compact feature artifact leaked full export markers: {leaked}")
    required_sections = {"reference", "background", "layout", "node_features", "edge_features", "right_panel", "filters", "highlight", "export_contract"}
    if not required_sections.issubset(artifact):
        raise AssertionError(f"feature artifact missing sections: {sorted(required_sections.difference(artifact))}")
    palette = artifact["highlight"]["semantic_palette"]
    expected_slots = {
        "selected_route_reference_example",
        "active_blocker",
        "verified_or_evidence_linked",
        "open_or_worker_active",
        "returned_imported_or_waiting_controller",
        "review_accept",
        "review_reject",
        "review_unclear",
        "review_partial",
    }
    if not expected_slots.issubset(palette):
        raise AssertionError(f"feature artifact missing semantic slots: {sorted(expected_slots.difference(palette))}")
    if len({palette[slot] for slot in expected_slots}) < 5:
        raise AssertionError(f"feature artifact collapsed highlight palette toward yellow-only: {palette}")
    if artifact_path.stat().st_size > 25000:
        raise AssertionError(f"feature artifact should stay compact, got {artifact_path.stat().st_size} bytes")


def main() -> int:
    assert_visual_platform_contract()
    assert_compact_feature_artifact()
    print(json.dumps({"ok": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
