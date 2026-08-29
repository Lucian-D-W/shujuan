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


def workbench_payload() -> dict[str, object]:
    items: list[dict[str, object]] = [
        {
            "kind": "task",
            "kind_label_zh": "任务",
            "id": "task_bilingual",
            "node_id": "node_task_bilingual",
            "label": "Implement bilingual workbench mode",
            "summary": "Main route task.",
            "visible_chain": [
                {"id": "node_scope_bilingual", "type": "scope_contract", "label": "Scope"},
                {"id": "node_task_bilingual", "type": "task", "label": "Bilingual task"},
            ],
            "visible_edges": [
                {
                    "id": "edge_scope_task_bilingual",
                    "from_node_id": "node_scope_bilingual",
                    "to_node_id": "node_task_bilingual",
                    "type": "DECOMPOSES_TO",
                    "style": "solid",
                }
            ],
            "hidden_source_count": 0,
            "hidden_source_edge_classes": [],
            "detail_ref": "graph detail --node node_task_bilingual",
            "lane_role": "worker_lane",
            "lifecycle_state": "open",
            "filter_metadata": {
                "text": "task bilingual workbench mode",
                "lane_role": "worker_lane",
                "lane_role_label_zh": "实施车道",
                "lane_lifecycle": "open",
                "lane_lifecycle_label_zh": "打开",
                "ownership": "assigned",
                "node_type": "task",
                "node_type_label_zh": "任务",
                "edge_types": ["DECOMPOSES_TO"],
                "active_state": "active",
                "closeout_gate": "blocking",
                "has_hidden_sources": False,
                "has_detail_ref": True,
                "has_source_preview": False,
            },
            "visual": {"attention": True, "color": "#38bdf8", "shape": "info-card"},
            "raw": {},
        },
        {
            "kind": "acceptance_check",
            "kind_label_zh": "验收检查",
            "id": "check_bilingual",
            "node_id": "node_check_bilingual",
            "label": "Bilingual display check",
            "summary": "Check canonical values stay stable.",
            "visible_chain": [{"id": "node_check_bilingual", "type": "acceptance_check", "label": "Check"}],
            "visible_edges": [],
            "hidden_source_count": 0,
            "hidden_source_edge_classes": [],
            "detail_ref": "graph detail --node node_check_bilingual",
            "lane_role": "controller_lane",
            "lifecycle_state": "open",
            "filter_metadata": {
                "text": "acceptance check bilingual display",
                "lane_role": "controller_lane",
                "lane_lifecycle": "open",
                "ownership": "assigned",
                "node_type": "acceptance_check",
                "node_type_label_zh": "验收检查",
                "edge_types": [],
                "active_state": "active",
                "closeout_gate": "blocking",
                "has_hidden_sources": False,
                "has_detail_ref": True,
                "has_source_preview": False,
            },
            "visual": {"attention": True, "color": "#ef4444", "shape": "info-card"},
            "raw": {},
        },
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
    return {
        "endpoint": "bilingual-mode",
        "views": views,
        "overlay": overlay,
        "detail_payloads": {},
        "workbench": {"db_write_path": False},
    }


def assert_bilingual_display_mode() -> None:
    html = render_workbench_html(workbench_payload())
    exported = extract_payload(html)
    serialized = json.dumps(exported, ensure_ascii=False, sort_keys=True)

    required_markers = [
        'id="language-select"',
        "let displayLanguage",
        "window.__shujuanDisplayLanguage",
        "const DISPLAY_TEXT",
        "const CANONICAL_DISPLAY",
        "function localizedEntry(entry, category)",
        "function canonicalText(value, category)",
        "function routeLabel(route)",
        "function renderLanguageSurface()",
        "chip.textContent = edgeChipLabel(edge);",
        "chip.setAttribute('data-edge-type'",
    ]
    missing = [marker for marker in required_markers if marker not in html]
    if missing:
        raise AssertionError(f"bilingual display-mode markers missing: {missing}")

    forbidden_mixed_ui = [
        "Search / 搜索",
        "All / 全部",
        "Flows / 路线",
        "Steps / 步骤",
        "Detail / 详情",
        "Legend / 图例",
        "Raw JSON / 调试",
        "Endpoint / 方向",
        "Task / 任务",
        "DECOMP / 拆分为",
        "${compact} /",
        "label_en || option.value} /",
        "label_zh || route.id} /",
        "kind || '')} /",
    ]
    present = [marker for marker in forbidden_mixed_ui if marker in html]
    if present:
        raise AssertionError(f"main UI still contains hard-coded bilingual labels: {present}")

    canonical_values = ["DECOMPOSES_TO", "acceptance_check", "worker_lane", "blocking", "node_task_bilingual"]
    missing_canonical = [value for value in canonical_values if value not in serialized]
    if missing_canonical:
        raise AssertionError(f"projection canonical values were not preserved: {missing_canonical}")


def assert_product_cleanup_markers() -> None:
    html = render_workbench_html(workbench_payload())
    required_cleanup = [
        "const suppressDerivedContext = selectedRouteId !== 'all_route' && routeNodes.size > 0;",
        "const renderedNodes = suppressDerivedContext ? Array.from(nodes.values()).filter((node) => !node.data.derived) : Array.from(nodes.values());",
        ".map-card.route-dim { opacity: 0.32;",
        ".map-card.derived-card.route-dim { opacity: 0.18;",
        ".lane-rail { position: absolute; top: 48px; bottom: 28px;",
        "background: transparent;",
    ]
    missing = [marker for marker in required_cleanup if marker not in html]
    if missing:
        raise AssertionError(f"product cleanup markers missing: {missing}")


def main() -> int:
    assert_bilingual_display_mode()
    assert_product_cleanup_markers()
    print(json.dumps({"ok": True, "vf_workbench_bilingual_mode_regressions": "passed"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
