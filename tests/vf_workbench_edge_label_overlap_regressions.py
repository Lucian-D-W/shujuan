from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.cli import render_workbench_html
from shujuan.commands.graph import build_workbench_overlay


EDGE_TYPES = ["DECOMPOSES_TO", "VALIDATES", "VALIDATED_BY", "APPLIES_TO", "CLOSES", "BLOCKS"]
CARD_WIDTH = 176
CARD_HEIGHT = 82
CARD_GAP_X = 48
CARD_GAP_Y = 24
COLUMN_COUNT = 5


def dense_projection_items(count: int = 18) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for index in range(count):
        edge_type = EDGE_TYPES[index % len(EDGE_TYPES)]
        node_id = f"task_{index:02d}"
        items.append(
            {
                "kind": "task",
                "kind_label_zh": "任务",
                "id": f"task_item_{index:02d}",
                "node_id": node_id,
                "label": f"Dense route task {index:02d}",
                "summary": f"Dense route task {index:02d}",
                "visible_chain": [
                    {"id": "scope_root", "type": "scope_contract", "label": "Scope"},
                    {"id": node_id, "type": "task", "label": f"Task {index:02d}"},
                ],
                "visible_edges": [
                    {
                        "id": f"edge_{index:02d}",
                        "from_node_id": "scope_root",
                        "to_node_id": node_id,
                        "type": edge_type,
                        "style": "solid",
                    }
                ],
                "hidden_source_count": 0,
                "hidden_source_edge_classes": [],
                "detail_ref": f"graph detail --node {node_id}",
                "lane_role": "worker_lane",
                "lifecycle_state": "open",
                "filter_metadata": {
                    "text": f"task Dense route task {index:02d}",
                    "lane_role": "worker_lane",
                    "lane_lifecycle": "open",
                    "ownership": "assigned",
                    "node_type": "task",
                    "edge_types": [edge_type],
                    "active_state": "active",
                    "closeout_gate": "blocking",
                    "has_hidden_sources": False,
                    "has_detail_ref": True,
                    "has_source_preview": False,
                },
                "visual": {"attention": True, "color": "#38bdf8", "shape": "info-card"},
                "raw": {},
            }
        )
    return items


def extract_payload(html: str) -> dict[str, object]:
    marker = '<script id="projection-payload" type="application/json">'
    raw = html.split(marker, 1)[1].split("</script>", 1)[0]
    return json.loads(raw)


def route_order_nodes(route: dict[str, object]) -> list[str]:
    return [str(node_id) for node_id in route.get("node_ids") or []]


def dense_layout_rectangles(route_node_ids: list[str], width: int = 900) -> list[dict[str, float]]:
    side_pad = CARD_WIDTH / 2 + 28
    content_width = max(width, side_pad * 2 + CARD_WIDTH + (COLUMN_COUNT - 1) * (CARD_WIDTH + CARD_GAP_X))
    pitch = max(360, content_width - side_pad * 2 - CARD_WIDTH) / max(1, COLUMN_COUNT - 1)
    buckets: dict[int, list[str]] = {}
    max_order = max(1, len(route_node_ids) - 1)
    for route_order, node_id in enumerate(route_node_ids):
        column = round((route_order / max_order) * (COLUMN_COUNT - 1))
        buckets.setdefault(column, []).append(node_id)
    rectangles: list[dict[str, float]] = []
    for column, node_ids in buckets.items():
        col_x = round(side_pad + min(COLUMN_COUNT - 1, max(0, column)) * pitch)
        left = max(12, min(content_width - CARD_WIDTH - 12, col_x - CARD_WIDTH / 2))
        for slot, node_id in enumerate(node_ids):
            top = 86 + slot * (CARD_HEIGHT + CARD_GAP_Y)
            rectangles.append({"id": node_id, "left": left, "top": top, "right": left + CARD_WIDTH, "bottom": top + CARD_HEIGHT})
    return rectangles


def rectangles_overlap(left: dict[str, float], right: dict[str, float]) -> bool:
    return not (
        left["right"] <= right["left"]
        or right["right"] <= left["left"]
        or left["bottom"] <= right["top"]
        or right["bottom"] <= left["top"]
    )


def assert_route_edge_labels_and_dense_positions() -> None:
    views = {"attention": {"items": dense_projection_items(), "item_count": 18, "broken_visible_chain_count": 0}}
    overlay = build_workbench_overlay(
        sqlite3.connect(":memory:"),
        endpoint_id=None,
        views=views,
        requested_view="attention",
        include_history=False,
        include_consumed=False,
        limit=50,
    )
    payload = {"endpoint": "dense-route", "views": views, "overlay": overlay, "detail_payloads": {}, "workbench": {"db_write_path": False}}
    html = render_workbench_html(payload)
    exported = extract_payload(html)
    route = next(route for route in exported["overlay"]["flows"] if route["id"] == "attention_route")
    route_edges = [edge for edge in exported["overlay"]["edges"] if edge["id"] in set(route["edge_ids"])]
    exposed_types = {edge["type"] for edge in route_edges}
    if not set(EDGE_TYPES).issubset(exposed_types):
        raise AssertionError(f"route overlay lost edge type semantics: {route_edges}")

    required_label_markers = [
        "function edgeChipLabel(edge)",
        "const edgeType = (edge.data || {}).type || (edge.data || {}).label || 'EDGE';",
        "chip.textContent = edgeChipLabel(edge);",
        "chip.setAttribute('data-edge-type'",
        "DECOMPOSES_TO: 'DECOMP'",
    ]
    missing = [marker for marker in required_label_markers if marker not in html]
    if missing:
        raise AssertionError(f"route edge label derivation markers missing: {missing}")
    forbidden_numeric_chip_markers = [
        "routeEdgeOrder",
        "chip.textContent = String(",
    ]
    present = [marker for marker in forbidden_numeric_chip_markers if marker in html]
    if present:
        raise AssertionError(f"route edge chips can still render bare numeric labels: {present}")

    required_overlap_markers = [
        "const columnBuckets = new Map();",
        "const cardGapY = 24;",
        "const contentWidth = Math.max(size.width",
        "const contentHeight = Math.max(size.height",
        "overlayLayer.style.height = `${contentHeight}px`;",
        "window.__shujuanRouteMapLayout",
    ]
    missing = [marker for marker in required_overlap_markers if marker not in html]
    if missing:
        raise AssertionError(f"collision-aware route map markers missing: {missing}")

    rectangles = dense_layout_rectangles(route_order_nodes(route))
    overlaps = [
        (left["id"], right["id"])
        for index, left in enumerate(rectangles)
        for right in rectangles[index + 1 :]
        if rectangles_overlap(left, right)
    ]
    if overlaps:
        raise AssertionError(f"dense route card positions overlap: {overlaps[:8]}")


def main() -> int:
    assert_route_edge_labels_and_dense_positions()
    print(json.dumps({"ok": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
