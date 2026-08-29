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


def assert_workbench_html_has_nonblank_contract() -> None:
    items = [
        {
            "kind": "task",
            "kind_label_zh": "任务",
            "id": "task_vf14",
            "node_id": "node_task",
            "label": "Remediate VF-14 blank canvas",
            "summary": "Projection item with chain and edge data.",
            "visible_chain": [
                {"id": "node_scope", "type": "scope_contract", "label": "Scope"},
                {"id": "node_task", "type": "task", "label": "Task"},
            ],
            "visible_edges": [
                {
                    "id": "edge_scope_task",
                    "from_node_id": "node_scope",
                    "to_node_id": "node_task",
                    "type": "DECOMPOSES_TO",
                    "style": "solid",
                }
            ],
            "hidden_source_count": 1,
            "hidden_source_edge_classes": ["DERIVED_FROM"],
            "detail_ref": "graph detail --node node_task",
            "lane_role": "worker_lane",
            "lifecycle_state": "open",
            "filter_metadata": {
                "text": "task Remediate VF-14 blank canvas",
                "lane_role": "worker_lane",
                "lane_role_label_zh": "实施车道",
                "lane_lifecycle": "open",
                "lane_lifecycle_label_zh": "打开",
                "ownership": "assigned",
                "node_type": "task",
                "node_type_label_zh": "任务",
                "edge_types": ["DECOMPOSES_TO"],
                "evidence_type": "test_result",
                "active_state": "active",
                "closeout_gate": "blocking",
                "has_hidden_sources": True,
                "has_detail_ref": True,
                "has_source_preview": True,
            },
            "visual": {"attention": True, "color": "#f59e0b"},
            "raw": {},
        },
        {
            "kind": "audit_finding",
            "kind_label_zh": "审计发现",
            "id": "finding_vf14",
            "node_id": "node_finding",
            "label": "Fallback-only item",
            "summary": "No visible_chain should still produce a fallback node.",
            "visible_chain": [],
            "visible_edges": [],
            "hidden_source_count": 0,
            "hidden_source_edge_classes": [],
            "detail_ref": "graph detail --node node_finding",
            "lane_role": "reviewer_lane",
            "lifecycle_state": "active",
            "filter_metadata": {
                "text": "audit finding fallback-only item",
                "lane_role": "reviewer_lane",
                "lane_role_label_zh": "复核车道",
                "lane_lifecycle": "active",
                "lane_lifecycle_label_zh": "活跃",
                "review_result": "partial",
                "ownership": "ambiguous",
                "node_type": "audit_finding",
                "node_type_label_zh": "审计发现",
                "edge_types": [],
                "active_state": "active",
                "closeout_gate": "blocking",
                "has_hidden_sources": False,
                "has_detail_ref": True,
                "has_source_preview": False,
            },
            "visual": {"attention": True, "color": "#fb7185"},
            "raw": {},
        },
    ]
    views = {
        "attention": {
            "items": items,
            "item_count": 2,
            "broken_visible_chain_count": 0,
            "layout": {"algorithm": "endpoint_radial_chain"},
        }
    }
    overlay = build_workbench_overlay(
        sqlite3.connect(":memory:"),
        endpoint_id=None,
        views=views,
        requested_view="all",
        include_history=True,
        include_consumed=False,
        limit=50,
    )
    payload = {
        "endpoint": "vf14",
        "views": views,
        "overlay": overlay,
        "nodes": [{"id": "node_explicit", "type": "evidence", "label": "Explicit node"}],
        "edges": [{"id": "edge_explicit", "source": "node_task", "target": "node_explicit", "type": "VALIDATED_BY"}],
        "detail_payloads": {},
        "workbench": {"db_write_path": False},
    }
    html = render_workbench_html(payload, layout="endpoint_radial_chain", g6_script_src="g6.min.js")
    exported = extract_payload(html)
    attention = exported["views"]["attention"]["items"]
    if not attention or not attention[0]["visible_chain"] or not attention[0]["visible_edges"]:
        raise AssertionError(f"exported payload lost graph data: {exported}")
    exported_overlay = exported["overlay"]
    if exported_overlay["schema_version"] != "workbench_lane_overlay.v1":
        raise AssertionError(f"overlay schema version missing: {exported_overlay}")
    if exported_overlay["legend"]["node_types"][2]["value"] != "task" or exported_overlay["legend"]["node_types"][2]["label_zh"] != "任务":
        raise AssertionError(f"bilingual node legend missing canonical task value: {exported_overlay['legend']['node_types']}")
    attention_route = next((route for route in exported_overlay["flows"] if route["id"] == "attention_route"), None)
    if not attention_route or attention_route["label_en"] != "Attention Route" or attention_route["label_zh"] != "当前注意路线" or not attention_route["steps"]:
        raise AssertionError(f"attention route did not include bilingual numbered steps: {exported_overlay['flows']}")
    diagnostics = exported_overlay["diagnostics"]
    if diagnostics["raw_item_count"] != 2 or diagnostics["visible_item_count"] != 2 or diagnostics["overlay_node_count"] < 2:
        raise AssertionError(f"overlay diagnostics lost counts: {diagnostics}")
    feature_contract = exported_overlay["visual_feature_contract"]
    palette = feature_contract["highlight"]["palette"]
    semantic_slots = {
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
    if feature_contract["schema"] != "wb_lane_visual_feature_boundary.v1" or not semantic_slots.issubset(palette):
        raise AssertionError(f"feature contract did not preserve semantic multi-color slots: {feature_contract}")
    if len({palette[slot] for slot in semantic_slots}) < 5:
        raise AssertionError(f"semantic palette collapsed toward a single highlight color: {palette}")
    required = [
        "#graph-mount { position: relative; height: 100%; min-height: 520px;",
        "function registerNode(node, item, visual)",
        "if (!chain.length && item.node_id)",
        "payload.nodes || explicitGraph.nodes",
        "function renderGraphFallback(data, title, detail)",
        "role=\"status\"",
        "No graph nodes",
        "G6 render failed",
        "window.__shujuanGraphData = data",
        "window.__shujuanGraphRenderError",
        "resolveG6GraphConstructor()",
        "graphMount.dataset.g6Width",
        "graphMount.dataset.g6Height",
        "data-g6-error",
        "data-g6-canvas-count",
        "layout: layoutOptions(requestedLayout, data, size)",
        "id=\"route-filter\"",
        "id=\"reset-filters\"",
        "id=\"lane-filter\"",
        "id=\"closeout-filter\"",
        "id=\"source-filter\"",
        "route-node-glow",
        "route-edge-glow",
        "data-node-rendering",
        "data-node-shape",
        "nodeShape: 'info-card'",
        "type: 'rect'",
        "cardTitle",
        "cardType",
        "cardLane",
        "cardRole",
        "labelPlacement: 'center'",
        "className = `map-card",
        "step-number",
        'data-i18n="rawJson"',
        "当前注意路线",
        "Attention Route",
        "${escapeText(t('rawCount'))}=${diagnostics.raw_item_count",
        "${escapeText(t('visibleCount'))}=${data.nodes.length}",
        "data-visual-feature-schema=\"wb_lane_visual_feature_boundary.v1\"",
        "semanticPalette",
        "selected_route_reference_example",
        "active_blocker",
        "verified_or_evidence_linked",
        "open_or_worker_active",
        "returned_imported_or_waiting_controller",
        "review_unclear",
    ]
    missing = [text for text in required if text not in html]
    if missing:
        raise AssertionError(f"workbench HTML missing blank-canvas safeguards: {missing}")
    forbidden_rendering = ["type: 'circle'", "labelPlacement: 'bottom'"]
    if any(text in html for text in forbidden_rendering):
        raise AssertionError("workbench route map still advertises circle/dot node rendering")
    forbidden = ["fetch(", "XMLHttpRequest", "method=\"post\"", "action="]
    exposed = [text for text in forbidden if text in html]
    if exposed:
        raise AssertionError(f"workbench HTML exposed write/runtime fetch path: {exposed}")


def main() -> int:
    assert_workbench_html_has_nonblank_contract()
    print(json.dumps({"ok": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
