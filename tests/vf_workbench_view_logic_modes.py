import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.commands.graph import build_workbench_overlay
from shujuan.commands.workbench import render_workbench_html


def projection_item(
    node_id: str,
    kind: str,
    label: str,
    *,
    active_state: str = "active",
    lifecycle: str = "open",
    lane: str = "worker_lane",
    closeout_gate: str = "",
    ownership: str = "assigned",
    evidence_type: str = "",
    edge_type: str = "DECOMPOSES_TO",
) -> dict[str, object]:
    edge_id = f"edge_{node_id}"
    return {
        "kind": kind,
        "kind_label_zh": kind,
        "id": f"item_{node_id}",
        "node_id": node_id,
        "label": label,
        "summary": label,
        "visible_chain": [
            {"id": "endpoint_node", "type": "endpoint", "label": "Endpoint"},
            {"id": node_id, "type": kind, "label": label},
        ],
        "visible_edges": [
            {
                "id": edge_id,
                "from_node_id": "endpoint_node",
                "to_node_id": node_id,
                "type": edge_type,
                "style": "solid",
            }
        ],
        "hidden_source_count": 0,
        "hidden_source_edge_classes": [],
        "detail_ref": f"graph detail --node {node_id}",
        "lane_role": lane,
        "lifecycle_state": lifecycle,
        "filter_metadata": {
            "text": label,
            "lane_role": lane,
            "lane_lifecycle": lifecycle,
            "ownership": ownership,
            "node_type": kind,
            "edge_types": [edge_type],
            "evidence_type": evidence_type,
            "active_state": active_state,
            "closeout_gate": closeout_gate,
            "has_hidden_sources": False,
            "has_detail_ref": True,
            "has_source_preview": False,
        },
        "visual": {"attention": active_state == "active", "color": "#38bdf8"},
        "raw": {},
    }


def overlay_for_mode(mode: str, views: dict[str, dict[str, object]]) -> dict[str, object]:
    return build_workbench_overlay(
        sqlite3.connect(":memory:"),
        endpoint_id=None,
        views=views,
        requested_view=mode,
        include_history=mode in {"history", "all"},
        include_consumed=False,
        limit=100,
    )


def mode_views() -> dict[str, dict[str, object]]:
    active_items = [
        projection_item("node_task", "task", "Active implementation task"),
        projection_item("node_check", "acceptance_check", "Blocking active check", closeout_gate="blocking"),
        projection_item("node_review", "audit_finding", "Active review finding", lane="reviewer_lane"),
        projection_item("node_unresolved", "unresolved_question", "Active unresolved question", lane="controller_lane"),
    ]
    history_items = [
        projection_item("node_history_task", "task", "Deferred historical task", active_state="non_active", lifecycle="deferred"),
        projection_item("node_closed_check", "acceptance_check", "Closed historical check", active_state="non_active", lifecycle="closed_by_controller"),
    ]
    evidence_items = [
        projection_item("node_evidence", "evidence", "Validation artifact", active_state="non_active", lifecycle="verified", lane="controller_lane", evidence_type="artifact", edge_type="VALIDATES"),
        projection_item("node_change", "change_set", "Change set evidence", active_state="non_active", lifecycle="closed", lane="worker_lane", evidence_type="change_set", edge_type="VALIDATED_BY"),
    ]
    combined: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in [*active_items, *history_items, *evidence_items]:
        key = str(item["node_id"])
        if key not in seen:
            seen.add(key)
            combined.append(item)
    return {
        "active": {"items": active_items, "item_count": len(active_items), "broken_visible_chain_count": 0},
        "history": {"items": history_items, "item_count": len(history_items), "broken_visible_chain_count": 0},
        "evidence": {"items": evidence_items, "item_count": len(evidence_items), "broken_visible_chain_count": 0},
        "all": {"items": combined, "item_count": len(combined), "broken_visible_chain_count": 0},
    }


def route_by_id(overlay: dict[str, object], route_id: str) -> dict[str, object]:
    for route in overlay["flows"]:
        if route["id"] == route_id:
            return route
    raise AssertionError(f"missing route {route_id}: {overlay['flows']}")


def assert_mode_defaults_and_routes() -> None:
    views = mode_views()
    expectations = {
        "active": ("active", "attention_route", True),
        "history": ("history", "all_route", False),
        "evidence": ("evidence", "evidence_route", False),
        "all": ("all", "all_route", False),
    }
    for mode, (view, route, active_only) in expectations.items():
        selected_views = views if mode == "all" else {mode: views[mode]}
        overlay = overlay_for_mode(mode, selected_views)
        filters = overlay["filters"]["active"]
        diagnostics = overlay["diagnostics"]
        if overlay["default_flow_id"] != route or filters["view"] != view or filters["active_only"] is not active_only:
            raise AssertionError(f"{mode} defaults drifted: route={overlay['default_flow_id']} filters={filters}")
        if diagnostics["filter_state"]["active_only_default"] is not active_only:
            raise AssertionError(f"{mode} diagnostics contradicted active-only default: {diagnostics}")
        if route_by_id(overlay, route)["empty_state"]["is_empty"]:
            raise AssertionError(f"{mode} default route was empty: {route_by_id(overlay, route)}")

    all_overlay = overlay_for_mode("all", views)
    expected_nonempty = {
        "attention_route",
        "execution_route",
        "review_route",
        "evidence_route",
        "ownership_route",
        "blocked_route",
        "all_route",
    }
    empty = [route_id for route_id in expected_nonempty if not route_by_id(all_overlay, route_id)["node_ids"]]
    if empty:
        raise AssertionError(f"all-mode route presets were unexpectedly empty: {empty}")
    if route_by_id(all_overlay, "evidence_route")["source_view"] != "evidence":
        raise AssertionError(f"evidence route source was mislabeled: {route_by_id(all_overlay, 'evidence_route')}")
    if route_by_id(all_overlay, "all_route")["count_scope"] != "route_visible_nodes_edges":
        raise AssertionError(f"all route omitted route count scope: {route_by_id(all_overlay, 'all_route')}")


def assert_frame_defaults_and_blank_state_markers() -> None:
    views = mode_views()
    overlay = overlay_for_mode("history", {"history": views["history"]})
    payload = {
        "endpoint": "view-logic",
        "mode": "history",
        "view": "all",
        "mode_counts": {"history": 2},
        "views": {"history": views["history"]},
        "overlay": overlay,
        "detail_payloads": {},
        "workbench": {
            "db_write_path": False,
            "default_view": "history",
            "default_route": "all_route",
            "default_active_only": False,
            "mode_counts": {"history": 2},
        },
    }
    html = render_workbench_html(payload)
    exported = json.loads(html.split('<script id="projection-payload" type="application/json">', 1)[1].split("</script>", 1)[0])
    if exported["workbench"]["default_active_only"] is not False or exported["workbench"]["default_route"] != "all_route":
        raise AssertionError(f"history payload lost mode defaults: {exported['workbench']}")
    required_markers = [
        "const defaultViewCandidate = workbenchDefaults.default_view || overlayDefaultFilters.view;",
        "const defaultRouteId = workbenchDefaults.default_route || overlay.default_flow_id || 'attention_route';",
        "const defaultActiveOnly = Boolean(workbenchDefaults.default_active_only ?? overlayDefaultFilters.active_only ?? true);",
        "attentionOnly.checked = defaultActiveOnly;",
        "selectedRouteId = defaultRouteId;",
        "blankReason = 'active_only_suppression'",
        "search_filter_suppression",
        "filter_suppression",
        "no_db_facts",
        "function blankStateCopy(data)",
        "node.data.routeOrder / maxOrder",
        "window.__shujuanRouteMapLayout",
    ]
    missing = [marker for marker in required_markers if marker not in html]
    if missing:
        raise AssertionError(f"frame omitted mode/reset/blank/layout markers: {missing}")


def main() -> int:
    assert_mode_defaults_and_routes()
    assert_frame_defaults_and_blank_state_markers()
    print(json.dumps({"ok": True, "vf_workbench_view_logic_modes": "passed"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
