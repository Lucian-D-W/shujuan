from __future__ import annotations

import argparse
import ast
import html
import json
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any


OVERLAY_SCHEMA_VERSION = "workbench_lane_overlay.v1"
VISUAL_FEATURE_SCHEMA_VERSION = "wb_lane_visual_feature_boundary.v1"
WORKBENCH_PROJECTION_MODES = {"active", "history", "evidence", "all"}

SEMANTIC_HIGHLIGHT_PALETTE = {
    "selected_route_reference_example": "#facc15",
    "active_blocker": "#ef4444",
    "verified_or_evidence_linked": "#22c55e",
    "open_or_worker_active": "#38bdf8",
    "returned_imported_or_waiting_controller": "#f59e0b",
    "review_accept": "#22c55e",
    "review_reject": "#ef4444",
    "review_unclear": "#a78bfa",
    "review_partial": "#f97316",
    "controller_lane": "#f472b6",
    "worker_lane": "#38bdf8",
    "reviewer_lane": "#a78bfa",
    "research_lane": "#14b8a6",
    "writer_lane": "#eab308",
    "provider_lane": "#94a3b8",
    "summary_only_or_non_active": "#64748b",
}

LABEL_ZH = {
    "acceptance_check": "验收检查",
    "active": "活跃",
    "agent_run": "执行运行",
    "all": "全部",
    "active_mode": "活跃模式",
    "ambiguous": "权属不明",
    "APPLIES_TO": "适用于",
    "artifact": "产物",
    "artifact_preview": "产物预览",
    "assigned": "已分配",
    "attention": "注意",
    "attention_route": "当前注意路线",
    "audit": "审计",
    "audit_finding": "审计发现",
    "blocked": "阻塞",
    "blocking": "阻塞",
    "BLOCKS": "阻塞",
    "change_set": "改动集",
    "child_chain": "子链",
    "claimed_hunk": "已认领片段",
    "closed": "已关闭",
    "closed_by_controller": "主控已关闭",
    "CLOSES": "关闭",
    "closeout_ready": "关闭就绪",
    "controller": "主控",
    "controller_lane": "主控车道",
    "deferred": "已延期",
    "DECOMPOSES_TO": "拆分为",
    "DERIVED_FROM": "来源于",
    "detail_ref": "详情引用",
    "diff_hunk": "代码片段",
    "discussion": "讨论",
    "discussion_segment": "讨论片段",
    "discussions": "讨论",
    "edge": "边",
    "evidence": "证据",
    "evidence_mode": "证据模式",
    "evidence_route": "证据路线",
    "EXECUTES": "执行",
    "execution": "执行",
    "execution_route": "实施路线",
    "filter_metadata": "筛选元数据",
    "full": "完整",
    "HAS_IMPACT_ARTIFACT": "有影响产物",
    "HAS_IMPACT_FACT": "有影响事实",
    "hidden_sources": "隐藏来源",
    "history_mode": "历史模式",
    "imported": "已导入",
    "lane_role": "车道角色",
    "non_active": "非活跃",
    "open": "打开",
    "ownership_route": "权属路线",
    "packet": "委托包",
    "packeted": "已发包",
    "pre_existing_dirty": "预存脏路径",
    "PRODUCES": "产生",
    "provider": "提供方",
    "provider_lane": "提供方车道",
    "raw_preview": "原始预览",
    "ready": "就绪",
    "rejected": "复核拒绝",
    "researcher": "调研",
    "research_lane": "调研车道",
    "resolved": "已解决",
    "returned": "已返回",
    "review": "复核",
    "review_result": "复核结果",
    "review_route": "复核路线",
    "reviewed": "已复核",
    "reviewer": "复核",
    "reviewer_lane": "复核车道",
    "scope_contract": "范围契约",
    "source_preview": "来源预览",
    "summary_only": "仅摘要",
    "task": "任务",
    "test_result": "测试结果",
    "touched": "已触碰",
    "unclear": "不清楚",
    "unresolved_question": "未决问题",
    "VALIDATED_BY": "被验证",
    "VALIDATES": "验证",
    "verified": "已验证",
    "warning": "警告",
    "work_note": "工作记录",
    "worker": "实施",
    "worker_lane": "实施车道",
    "writer": "写作",
    "writer_lane": "写作车道",
}

ROUTE_PRESETS = [
    ("attention_route", "Attention Route", "当前注意路线", "attention"),
    ("execution_route", "Implementation Route", "实施路线", "execution"),
    ("review_route", "Review Route", "复核路线", "audit"),
    ("evidence_route", "Evidence Route", "证据路线", "audit"),
    ("ownership_route", "Ownership Route", "权属路线", "full"),
    ("blocked_route", "Blocked Route", "阻塞路线", "attention"),
    ("all_route", "All", "全部", "all"),
]

LANE_ROLE_BY_KIND = {
    "agent_run": "worker_lane",
    "artifact": "worker_lane",
    "audit_finding": "reviewer_lane",
    "change_set": "worker_lane",
    "diff_hunk": "worker_lane",
    "discussion_segment": "research_lane",
    "evidence": "controller_lane",
    "review_result": "reviewer_lane",
    "task": "worker_lane",
    "acceptance_check": "controller_lane",
    "unresolved_question": "controller_lane",
    "work_note": "writer_lane",
}

BLOCKING_KINDS = {"task", "acceptance_check", "audit_finding", "unresolved_question", "child_chain"}
NON_ACTIVE_STATES = {"closed", "closed_by_controller", "resolved", "deferred", "product_backlog", "summary_only", "consumed", "superseded", "invalidated"}


def _configure(deps: Mapping[str, Any]) -> None:
    globals().update(deps)


def label_zh(value: Any) -> str:
    text = str(value or "")
    return LABEL_ZH.get(text, text)


def legend_entry(value: str, label_en: str | None = None, **metadata: Any) -> dict[str, Any]:
    return {"value": value, "label_en": label_en or value, "label_zh": label_zh(value), **metadata}


def overlay_legend() -> dict[str, list[dict[str, Any]]]:
    return {
        "node_types": [
            legend_entry("endpoint", "endpoint"),
            legend_entry("scope_contract", "scope_contract"),
            legend_entry("task", "task"),
            legend_entry("acceptance_check", "acceptance_check"),
            legend_entry("evidence", "evidence"),
            legend_entry("artifact", "artifact"),
            legend_entry("change_set", "change_set"),
            legend_entry("diff_hunk", "diff_hunk"),
            legend_entry("audit_finding", "audit_finding"),
            legend_entry("work_note", "work_note"),
            legend_entry("delegation_lane", "delegation_lane"),
            legend_entry("delegation_packet", "delegation_packet"),
            legend_entry("review_result", "review_result"),
            legend_entry("ownership_snapshot", "ownership_snapshot"),
            legend_entry("discussion_segment", "discussion_segment"),
            legend_entry("unresolved_question", "unresolved_question"),
        ],
        "edge_types": [
            legend_entry("DERIVED_FROM"),
            legend_entry("APPLIES_TO"),
            legend_entry("DECOMPOSES_TO"),
            legend_entry("EXECUTES"),
            legend_entry("PRODUCES"),
            legend_entry("VALIDATES"),
            legend_entry("VALIDATED_BY"),
            legend_entry("CLOSES"),
            legend_entry("BLOCKS"),
            legend_entry("HAS_IMPACT_FACT"),
            legend_entry("HAS_IMPACT_ARTIFACT"),
        ],
        "lane_roles": [
            legend_entry("controller_lane", "controller"),
            legend_entry("worker_lane", "worker"),
            legend_entry("reviewer_lane", "reviewer"),
            legend_entry("research_lane", "researcher"),
            legend_entry("writer_lane", "writer"),
            legend_entry("provider_lane", "provider"),
        ],
        "states": [
            legend_entry("open"),
            legend_entry("packeted"),
            legend_entry("returned"),
            legend_entry("imported"),
            legend_entry("verified"),
            legend_entry("reviewed"),
            legend_entry("closed_by_controller"),
            legend_entry("rejected"),
            legend_entry("unclear"),
            legend_entry("blocked"),
            legend_entry("summary_only"),
            legend_entry("active"),
            legend_entry("deferred"),
            legend_entry("resolved"),
        ],
        "route_steps": [
            legend_entry("selected_step", "selected route step"),
            legend_entry("route_node", "selected route node"),
            legend_entry("route_edge", "selected route edge"),
        ],
        "diagnostics": [
            legend_entry("raw_item_count", "raw item count"),
            legend_entry("visible_item_count", "visible item count"),
            legend_entry("overlay_node_count", "overlay node count"),
            legend_entry("overlay_edge_count", "overlay edge count"),
            legend_entry("route_count", "route count"),
        ],
        "filters": [
            legend_entry("text", "text search"),
            legend_entry("view", "view"),
            legend_entry("lane_role", "lane role"),
            legend_entry("lane_lifecycle", "lane lifecycle"),
            legend_entry("review_result", "review result"),
            legend_entry("ownership", "ownership"),
            legend_entry("node_type", "node type"),
            legend_entry("edge_type", "edge type"),
            legend_entry("evidence_type", "evidence type"),
            legend_entry("active_state", "active state"),
            legend_entry("closeout_gate", "closeout gate"),
            legend_entry("hidden_sources", "hidden sources"),
            legend_entry("detail_ref", "detail ref"),
            legend_entry("source_preview", "source preview"),
        ],
    }


def visual_feature_contract() -> dict[str, Any]:
    return {
        "schema": VISUAL_FEATURE_SCHEMA_VERSION,
        "background": "black_or_near_black",
        "node_shape": "readable_box_or_card",
        "node_text": ["title", "type", "lane_role", "state", "owner_or_closeout_gate_when_available"],
        "layout": "reference_inspired_lane_or_architecture_flow",
        "highlight": {
            "palette": SEMANTIC_HIGHLIGHT_PALETTE,
            "not_hardcoded_single_color": True,
            "applies_to": ["node_border", "edge_stroke", "step_chip", "status_badge"],
        },
        "right_panel": ["flow_selector", "numbered_steps", "details_when_selected", "debug_json"],
        "fallback": {
            "nonblank": True,
            "diagnostics": ["raw_item_count", "visible_item_count", "overlay_node_count", "overlay_edge_count", "active_filters"],
        },
    }


def item_lane_role(kind: str, raw: dict[str, Any]) -> str:
    role = raw.get("role") or raw.get("lane_role") or raw.get("reviewer_role")
    if role:
        role_text = str(role).replace("_agent", "").replace("reviewer", "reviewer")
        if not role_text.endswith("_lane"):
            role_text = f"{role_text}_lane"
        return role_text
    return LANE_ROLE_BY_KIND.get(kind, "controller_lane")


def item_lifecycle_state(kind: str, item: dict[str, Any], raw: dict[str, Any], visual: dict[str, Any]) -> str:
    for key in ("lifecycle", "current_state", "status", "reviewer_state", "result"):
        if raw.get(key):
            value = str(raw[key])
            if value == "needs_user_decision":
                return "blocked"
            return value
    if raw.get("closed_by_node_id") or raw.get("closed_at"):
        return "closed_by_controller"
    if kind in {"acceptance_check", "task"}:
        return "open"
    return str(visual.get("state") or kind)


def item_closeout_gate(kind: str, state: str) -> str:
    if kind in BLOCKING_KINDS and state not in NON_ACTIVE_STATES:
        return "blocking"
    if state in {"verified", "reviewed", "closed_by_controller", "closed", "resolved"}:
        return "ready"
    return "warning"


def item_filter_metadata(kind: str, item: dict[str, Any], raw: dict[str, Any], visual: dict[str, Any]) -> dict[str, Any]:
    state = item_lifecycle_state(kind, item, raw, visual)
    lane_role = item_lane_role(kind, raw)
    edge_types = sorted({str(edge.get("type")) for edge in item.get("visible_edges") or [] if edge.get("type")})
    evidence_type = raw.get("expected_evidence_type") or raw.get("record_type")
    active_state = "non_active" if state in NON_ACTIVE_STATES else "active"
    source_preview_available = bool(raw.get("preview") or raw.get("artifact_preview") or item.get("hidden_source_count"))
    return {
        "text": " ".join(str(part or "") for part in [kind, item.get("label"), item.get("summary"), item.get("id"), item.get("node_id")]).strip(),
        "view": None,
        "lane_role": lane_role,
        "lane_role_label_zh": label_zh(lane_role),
        "lane_lifecycle": state,
        "lane_lifecycle_label_zh": label_zh(state),
        "review_result": raw.get("result") or raw.get("reviewer_state"),
        "ownership": raw.get("ownership") or raw.get("snapshot_kind") or ("assigned" if kind in {"task", "acceptance_check"} else None),
        "node_type": kind,
        "node_type_label_zh": label_zh(kind),
        "edge_types": edge_types,
        "edge_type_labels_zh": {edge_type: label_zh(edge_type) for edge_type in edge_types},
        "evidence_type": evidence_type,
        "active_state": active_state,
        "active_state_label_zh": label_zh(active_state),
        "closeout_gate": item_closeout_gate(kind, state),
        "closeout_gate_label_zh": label_zh(item_closeout_gate(kind, state)),
        "has_hidden_sources": bool(item.get("hidden_source_count")),
        "has_detail_ref": bool(item.get("detail_ref")),
        "has_source_preview": source_preview_available,
    }

def visible_node(conn: sqlite3.Connection, node_id: str | None) -> dict[str, Any] | None:
    if not node_id:
        return None
    row = conn.execute("SELECT id, type, label, summary FROM nodes WHERE id = ?", (node_id,)).fetchone()
    return row_to_dict(row)

def hidden_source_edges(conn: sqlite3.Connection, node_id: str | None, visible_ids: set[str]) -> list[dict[str, Any]]:
    if not node_id:
        return []
    rows = conn.execute(
        f"""
        SELECT id, type, to_node_id
        FROM edges
        WHERE from_node_id = ?
          AND type IN ({','.join('?' for _ in FOLDED_SOURCE_EDGE_TYPES)})
        """,
        (node_id, *sorted(FOLDED_SOURCE_EDGE_TYPES)),
    ).fetchall()
    return [row_to_dict(row) for row in rows if str(row["to_node_id"]) not in visible_ids]

def hidden_source_count(conn: sqlite3.Connection, node_id: str | None, visible_ids: set[str]) -> int:
    return len(hidden_source_edges(conn, node_id, visible_ids))

def visual_metadata(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    base = dict(VIEW_VISUALS.get(kind, {"color": SEMANTIC_HIGHLIGHT_PALETTE["summary_only_or_non_active"], "shape": "rect", "state": kind}))
    status = item.get("status") or item.get("current_state")
    if status:
        base["state"] = str(status)
    result = item.get("result") or item.get("reviewer_state")
    closeout_gate = item.get("closeout_gate")
    if status in {"consumed", "resolved"}:
        base["color"] = SEMANTIC_HIGHLIGHT_PALETTE["summary_only_or_non_active"]
    elif status in {"superseded", "invalidated"}:
        base["color"] = SEMANTIC_HIGHLIGHT_PALETTE["summary_only_or_non_active"]
    elif status in {"verified", "reviewed", "closed_by_controller", "closed"}:
        base["color"] = SEMANTIC_HIGHLIGHT_PALETTE["verified_or_evidence_linked"]
    elif status in {"returned", "imported", "packeted"}:
        base["color"] = SEMANTIC_HIGHLIGHT_PALETTE["returned_imported_or_waiting_controller"]
    elif status in {"blocked", "needs_user_decision"} or closeout_gate == "blocking":
        base["color"] = SEMANTIC_HIGHLIGHT_PALETTE["active_blocker"]
    elif result == "accept":
        base["color"] = SEMANTIC_HIGHLIGHT_PALETTE["review_accept"]
    elif result == "reject":
        base["color"] = SEMANTIC_HIGHLIGHT_PALETTE["review_reject"]
    elif result == "unclear":
        base["color"] = SEMANTIC_HIGHLIGHT_PALETTE["review_unclear"]
    elif result == "partial":
        base["color"] = SEMANTIC_HIGHLIGHT_PALETTE["review_partial"]
    elif status in {"unreviewed", "active"}:
        base["attention"] = True
    base["shape"] = "info-card"
    return base

def edge_metadata_for_chain(conn: sqlite3.Connection, visible_chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for left, right in zip(visible_chain, visible_chain[1:]):
        row = conn.execute(
            """
            SELECT *
            FROM edges
            WHERE (from_node_id = ? AND to_node_id = ?)
               OR (from_node_id = ? AND to_node_id = ?)
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (left["id"], right["id"], right["id"], left["id"]),
        ).fetchone()
        if not row:
            continue
        edge_type = str(row["type"])
        style = "dashed" if edge_type.startswith("PROVIDER") or edge_type in {"HAS_IMPACT_FACT", "HAS_IMPACT_ARTIFACT"} else "solid"
        edges.append(
            {
                "id": row["id"],
                "from_node_id": row["from_node_id"],
                "to_node_id": row["to_node_id"],
                "type": edge_type,
                "style": style,
                "confidence": row["confidence"] if row["confidence"] is not None else 1.0,
            }
        )
    return edges

def projection_item(
    conn: sqlite3.Connection,
    *,
    node_id: str | None,
    item: dict[str, Any],
    kind: str,
    visible_chain_ids: list[str],
) -> dict[str, Any]:
    visible_chain = []
    seen_chain_ids: set[str] = set()
    for visible_id in visible_chain_ids:
        node = visible_node(conn, visible_id)
        if node and str(node["id"]) not in seen_chain_ids:
            visible_chain.append(node)
            seen_chain_ids.add(str(node["id"]))
    visible_ids = {str(node["id"]) for node in visible_chain}
    if node_id and str(node_id) not in visible_ids:
        node = visible_node(conn, node_id)
        if node:
            visible_chain.insert(0, node)
            visible_ids.add(str(node["id"]))
    hidden_edges = hidden_source_edges(conn, node_id, visible_ids)
    raw = dict(item)
    visual = visual_metadata(kind, raw)
    filter_metadata = item_filter_metadata(kind, {"visible_edges": edge_metadata_for_chain(conn, visible_chain), "hidden_source_count": len(hidden_edges), "detail_ref": f"graph detail --node {node_id}" if node_id else None, **raw}, raw, visual)
    return {
        "kind": kind,
        "kind_label_zh": label_zh(kind),
        "id": item.get("id") or node_id,
        "node_id": node_id,
        "label": item.get("label") or item.get("title") or item.get("task_body") or item.get("check_body") or item.get("summary"),
        "summary": item.get("summary") or item.get("task_body") or item.get("check_body"),
        "visible_chain": visible_chain,
        "visible_edges": edge_metadata_for_chain(conn, visible_chain),
        "hidden_source_count": len(hidden_edges),
        "hidden_source_edge_classes": sorted({str(edge["type"]) for edge in hidden_edges}),
        "detail_ref": f"graph detail --node {node_id}" if node_id else None,
        "lane_role": filter_metadata["lane_role"],
        "lane_role_label_zh": filter_metadata["lane_role_label_zh"],
        "lifecycle_state": filter_metadata["lane_lifecycle"],
        "lifecycle_state_label_zh": filter_metadata["lane_lifecycle_label_zh"],
        "filter_metadata": filter_metadata,
        "visual": visual,
        "raw": raw,
    }

def chain_has_broken_adjacency(item: dict[str, Any]) -> bool:
    chain = item.get("visible_chain") or []
    if len(chain) < 2:
        return False
    edge_pairs = {
        frozenset((str(edge.get("from_node_id")), str(edge.get("to_node_id"))))
        for edge in item.get("visible_edges") or []
    }
    for left, right in zip(chain, chain[1:]):
        if frozenset((str(left.get("id")), str(right.get("id")))) not in edge_pairs:
            return True
    return False

def broken_visible_chain_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if item.get("node_id") and (not item["visible_chain"] or chain_has_broken_adjacency(item))]


def _unique_sorted(values: set[Any]) -> list[Any]:
    return sorted(value for value in values if value not in {None, ""})


def _route_step(item: dict[str, Any], index: int) -> dict[str, Any]:
    node_ids = [str(node.get("id")) for node in item.get("visible_chain") or [] if node.get("id")]
    if not node_ids and item.get("node_id"):
        node_ids = [str(item["node_id"])]
    edge_ids = [str(edge.get("id")) for edge in item.get("visible_edges") or [] if edge.get("id")]
    filters = item.get("filter_metadata") or {}
    return {
        "index": index,
        "node_id": item.get("node_id"),
        "label": item.get("label") or item.get("id") or item.get("node_id"),
        "kind": item.get("kind"),
        "kind_label_zh": item.get("kind_label_zh") or label_zh(item.get("kind")),
        "lane_role": filters.get("lane_role") or item.get("lane_role"),
        "lane_role_label_zh": filters.get("lane_role_label_zh") or item.get("lane_role_label_zh"),
        "state": filters.get("lane_lifecycle") or item.get("lifecycle_state"),
        "state_label_zh": filters.get("lane_lifecycle_label_zh") or item.get("lifecycle_state_label_zh"),
        "route_node_ids": node_ids,
        "route_edge_ids": edge_ids,
        "detail_ref": item.get("detail_ref"),
    }


def _flow_items(route_id: str, view_name: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    mode_views_present = any(name in views for name in ("active", "history", "evidence"))
    if route_id == "all_route":
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for payload in views.values():
            for item in payload.get("items") or []:
                key = str(item.get("node_id") or item.get("id"))
                if key not in seen:
                    merged.append(item)
                    seen.add(key)
        return merged
    if mode_views_present:
        if route_id == "evidence_route":
            items = list((views.get("evidence") or {}).get("items") or [])
        elif route_id in {"attention_route", "execution_route", "review_route", "blocked_route"}:
            items = list((views.get("active") or {}).get("items") or [])
        elif route_id == "ownership_route":
            items = _flow_items("all_route", "all", views)
        else:
            items = []
    else:
        items = list((views.get(view_name) or {}).get("items") or [])
    if route_id == "evidence_route":
        return [item for item in items if item.get("kind") in {"evidence", "acceptance_check", "change_set", "artifact", "diff_hunk"} or (item.get("filter_metadata") or {}).get("evidence_type")]
    if route_id == "review_route":
        return [item for item in items if item.get("kind") in {"audit_finding", "review_result", "acceptance_check", "unresolved_question"}]
    if route_id == "ownership_route":
        return [item for item in items if (item.get("filter_metadata") or {}).get("ownership") or item.get("kind") in {"task", "agent_run", "change_set"}]
    if route_id == "blocked_route":
        return [item for item in items if (item.get("filter_metadata") or {}).get("closeout_gate") == "blocking"]
    return items


def _workbench_mode_defaults(views: dict[str, Any], requested_view: str) -> dict[str, Any]:
    mode_views_present = any(name in views for name in ("active", "history", "evidence"))
    normalized_view = (requested_view or "").lower().replace("-", "_")
    if not mode_views_present:
        return {"view": normalized_view or "attention", "route": "attention_route", "active_only": True}
    if normalized_view == "all" or "all" in views:
        return {"view": "all", "route": "all_route", "active_only": False}
    if normalized_view == "evidence" or ("evidence" in views and "active" not in views):
        return {"view": "evidence", "route": "evidence_route", "active_only": False}
    if normalized_view == "history" or ("history" in views and "active" not in views):
        return {"view": "history", "route": "all_route", "active_only": False}
    return {"view": "active" if "active" in views else normalized_view, "route": "attention_route", "active_only": True}


def _route_effective_source_view(route_id: str, source_view: str, views: dict[str, Any]) -> str:
    mode_views_present = any(name in views for name in ("active", "history", "evidence"))
    if not mode_views_present:
        return source_view
    if route_id == "evidence_route":
        return "evidence"
    if route_id in {"attention_route", "execution_route", "review_route", "blocked_route"}:
        return "active"
    if route_id in {"ownership_route", "all_route"}:
        return "all"
    return source_view


def _overlay_lanes(conn: sqlite3.Connection, endpoint_id: str | None) -> list[dict[str, Any]]:
    if not endpoint_id:
        return []
    try:
        rows = conn.execute(
            """
            SELECT dl.id, dl.lane_name, dl.role, dl.lifecycle, dl.task_id, dl.check_id,
                   dl.controller_agent, dl.delegated_agent, dl.created_at, dl.updated_at,
                   COUNT(dp.id) AS packet_count
            FROM delegation_lanes dl
            LEFT JOIN delegation_packets dp ON dp.lane_id = dl.id
            WHERE dl.endpoint_id = ?
            GROUP BY dl.id, dl.lane_name, dl.role, dl.lifecycle, dl.task_id, dl.check_id,
                     dl.controller_agent, dl.delegated_agent, dl.created_at, dl.updated_at
            ORDER BY dl.updated_at DESC, dl.id DESC
            """,
            (endpoint_id,),
        ).fetchall()
    except Exception:
        return []
    lanes = []
    for row in rows:
        lane = row_to_dict(row)
        role = str(lane.get("role") or "")
        lane_role = f"{role}_lane" if role and not role.endswith("_lane") else role
        lanes.append(
            {
                **lane,
                "lane_role": lane_role,
                "lane_role_label_zh": label_zh(lane_role),
                "lifecycle_label_zh": label_zh(lane.get("lifecycle")),
            }
        )
    return lanes


def build_workbench_overlay(
    conn: sqlite3.Connection,
    *,
    endpoint_id: str | None,
    views: dict[str, Any],
    requested_view: str,
    include_history: bool,
    include_consumed: bool,
    limit: int,
) -> dict[str, Any]:
    raw_item_count = sum(int(view.get("item_count", len(view.get("items") or []))) for view in views.values())
    mode_defaults = _workbench_mode_defaults(views, requested_view)
    default_flow_id = str(mode_defaults["route"])
    default_active_only = bool(mode_defaults["active_only"])
    unique_items: dict[str, dict[str, Any]] = {}
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    filter_values: dict[str, set[Any]] = {
        "view": set(views.keys()),
        "lane_role": set(),
        "lane_lifecycle": set(),
        "review_result": set(),
        "ownership": set(),
        "node_type": set(),
        "edge_type": set(),
        "evidence_type": set(),
        "active_state": set(),
        "closeout_gate": set(),
        "hidden_sources": {True, False},
        "detail_ref": {True, False},
        "source_preview": {True, False},
    }
    for view_name, view_payload in views.items():
        for item in view_payload.get("items") or []:
            key = str(item.get("node_id") or item.get("id"))
            if key not in unique_items:
                unique_items[key] = item
            filters = item.setdefault("filter_metadata", {})
            filters["view"] = view_name
            for filter_name in ("lane_role", "lane_lifecycle", "review_result", "ownership", "node_type", "evidence_type", "active_state", "closeout_gate"):
                if filters.get(filter_name):
                    filter_values[filter_name].add(filters[filter_name])
            for edge_type in filters.get("edge_types") or []:
                filter_values["edge_type"].add(edge_type)
            for bool_name in ("has_hidden_sources", "has_detail_ref", "has_source_preview"):
                filter_values[bool_name.replace("has_", "")].add(bool(filters.get(bool_name)))
            for chain_node in item.get("visible_chain") or []:
                if chain_node.get("id"):
                    nodes[str(chain_node["id"])] = {
                        **chain_node,
                        "kind_label_zh": label_zh(chain_node.get("type")),
                        "route_candidate": True,
                    }
            if item.get("node_id") and str(item["node_id"]) not in nodes:
                nodes[str(item["node_id"])] = {
                    "id": item["node_id"],
                    "type": item.get("kind") or "projection_item",
                    "label": item.get("label") or item.get("id"),
                    "summary": item.get("summary"),
                    "kind_label_zh": label_zh(item.get("kind")),
                    "route_candidate": True,
                }
            for edge in item.get("visible_edges") or []:
                edge_id = str(edge.get("id") or f"{edge.get('from_node_id')}->{edge.get('to_node_id')}")
                edges[edge_id] = {
                    **edge,
                    "id": edge_id,
                    "type_label_zh": label_zh(edge.get("type")),
                    "route_candidate": True,
                }
    flows = []
    for route_id, label_en, label_zh_text, source_view in ROUTE_PRESETS:
        items = _flow_items(route_id, source_view, views)
        effective_source_view = _route_effective_source_view(route_id, source_view, views)
        step_payloads = [_route_step(item, index) for index, item in enumerate(items, start=1)]
        route_node_ids: list[str] = []
        route_edge_ids: list[str] = []
        for step in step_payloads:
            route_node_ids.extend(step["route_node_ids"])
            route_edge_ids.extend(step["route_edge_ids"])
        flows.append(
            {
                "id": route_id,
                "label_en": label_en,
                "label_zh": label_zh_text,
                "is_default": route_id == default_flow_id,
                "source_view": effective_source_view,
                "legacy_source_view": source_view,
                "node_ids": list(dict.fromkeys(route_node_ids)),
                "edge_ids": list(dict.fromkeys(route_edge_ids)),
                "count_scope": "route_visible_nodes_edges",
                "empty_state": {
                    "is_empty": not route_node_ids,
                    "title_en": "No route nodes",
                    "title_zh": "没有路线节点",
                    "detail_en": "This route has no DB-derived nodes in the current projection mode and filters.",
                    "detail_zh": "当前投影模式和筛选条件下，这条路线没有数据库派生节点。",
                },
                "steps": step_payloads,
                "filters": {"view": effective_source_view, "active_only": route_id in {"attention_route", "blocked_route"}},
            }
        )
    available_filters = [
        {
            "id": name,
            "label_en": name.replace("_", " "),
            "label_zh": label_zh(name),
            "options": [
                {"value": value, "label_en": str(value), "label_zh": label_zh(value)}
                for value in _unique_sorted(values)
            ],
        }
        for name, values in filter_values.items()
    ]
    visible_item_count = len(unique_items)
    return {
        "schema_version": OVERLAY_SCHEMA_VERSION,
        "legend": overlay_legend(),
        "flows": flows,
        "default_flow_id": default_flow_id,
        "lanes": _overlay_lanes(conn, endpoint_id),
        "filters": {
            "available": available_filters,
            "active": {
                "view": mode_defaults["view"],
                "active_only": default_active_only,
                "text": "",
                "include_history": include_history,
                "include_consumed": include_consumed,
                "limit": limit,
            },
        },
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "visual_feature_contract": visual_feature_contract(),
        "semantic_highlight_palette": SEMANTIC_HIGHLIGHT_PALETTE,
        "diagnostics": {
            "raw_item_count": raw_item_count,
            "visible_item_count": visible_item_count,
            "overlay_node_count": len(nodes),
            "overlay_edge_count": len(edges),
            "route_count": len(flows),
            "per_view_counts": {name: len(view.get("items") or []) for name, view in views.items()},
            "count_scopes": {
                "raw_item_count": "projection views before browser filters",
                "visible_item_count": "unique projection items before browser filters",
                "overlay_node_count": "deduplicated visible-chain nodes before browser filters",
                "overlay_edge_count": "deduplicated visible-chain edges before browser filters",
                "per_view_counts": "projection items per requested mode/view",
                "route_counts": "route-visible nodes and edges before browser filters",
            },
            "render_errors": [],
            "filter_state": {"view": mode_defaults["view"], "active_only_default": default_active_only, "search_default": ""},
            "blank_state": {
                "title_en": "No visible graph nodes",
                "title_zh": "没有可见图节点",
                "recovery_en": "Reset filters or switch to All.",
                "recovery_zh": "重置筛选或切换到全部。",
                "reasons": ["no_db_facts", "empty_route", "active_only_suppression", "search_filter_suppression", "filter_suppression", "render_failure"],
            },
        },
    }

def task_chain_ids(conn: sqlite3.Connection, task_node_id: str | None) -> list[str | None]:
    if not task_node_id:
        return []
    row = conn.execute(
        """
        SELECT sc.node_id AS scope_node_id
        FROM tasks t
        LEFT JOIN scope_contracts sc ON sc.id = t.contract_id
        WHERE t.node_id = ?
        """,
        (task_node_id,),
    ).fetchone()
    return [row["scope_node_id"] if row else None, task_node_id]

def check_chain_ids(conn: sqlite3.Connection, check_node_id: str | None, task_id: str | None) -> list[str | None]:
    if not check_node_id:
        return []
    row = conn.execute(
        """
        SELECT t.node_id AS task_node_id, sc.node_id AS scope_node_id
        FROM tasks t
        LEFT JOIN scope_contracts sc ON sc.id = t.contract_id
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()
    if not row:
        return [check_node_id]
    return [row["scope_node_id"], row["task_node_id"], check_node_id]

def graph_projection_payload(
    conn: sqlite3.Connection,
    endpoint_name: str,
    view: str,
    *,
    mode: str | None = None,
    include_consumed: bool = False,
    include_history: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    status = endpoint_status_payload(conn, endpoint_name)
    endpoint_node_id = status["endpoint"]["node_id"]
    requested = view.lower().replace("-", "_")
    requested_mode = (mode or "").lower().replace("-", "_") or None
    if requested_mode and requested_mode not in WORKBENCH_PROJECTION_MODES:
        raise SystemExit("workbench projection mode must be one of: active, history, evidence, all")
    if requested_mode:
        requested_views = ["active", "history", "evidence"] if requested_mode == "all" else [requested_mode]
    elif requested in {"all", "full"}:
        views = ["attention", "execution", "discussions", "audit", "full"]
    else:
        views = [requested]
    payload_views: dict[str, Any] = {}

    def add_view_payload(current_view: str, items: list[dict[str, Any]], *, rank_by: str) -> None:
        items = items[:limit]
        broken = broken_visible_chain_items(items)
        payload_views[current_view] = {
            "items": items,
            "item_count": len(items),
            "broken_visible_chain_count": len(broken),
            "broken_visible_chain_items": [{"node_id": item.get("node_id"), "detail_ref": item.get("detail_ref")} for item in broken],
            "layout": {"algorithm": "endpoint_radial_chain", "rank_by": rank_by},
        }

    if requested_mode:
        mode_items: dict[str, list[dict[str, Any]]] = {"active": [], "history": [], "evidence": []}
        for task in status.get("current_tasks") or []:
            mode_items["active"].append(projection_item(conn, node_id=task.get("node_id"), item=task, kind="task", visible_chain_ids=task_chain_ids(conn, task.get("node_id"))))
        for check in status.get("open_checks") or []:
            mode_items["active"].append(
                projection_item(
                    conn,
                    node_id=check.get("node_id"),
                    item=check,
                    kind="acceptance_check",
                    visible_chain_ids=check_chain_ids(conn, check.get("node_id"), check.get("task_id")),
                )
            )
        for unresolved in status.get("unresolved") or []:
            mode_items["active"].append(projection_item(conn, node_id=unresolved.get("id"), item=unresolved, kind="unresolved_question", visible_chain_ids=[unresolved.get("id"), endpoint_node_id]))
        for finding in status.get("recent_audit_findings") or []:
            mode_items["active"].append(projection_item(conn, node_id=finding.get("id"), item=finding, kind="audit_finding", visible_chain_ids=[finding.get("id"), endpoint_node_id]))
        for note in status.get("recent_work_notes") or []:
            mode_items["active"].append(projection_item(conn, node_id=note.get("id"), item=note, kind="work_note", visible_chain_ids=[note.get("id"), endpoint_node_id]))
        for discussion in status.get("recent_discussions") or []:
            state = discussion.get("status")
            item = {**discussion, "current_state": state}
            target = "active" if state in {"unreviewed", "extracted"} else "history"
            if target == "history" and state in {"consumed", "superseded"} and not include_consumed:
                continue
            mode_items[target].append(projection_item(conn, node_id=discussion.get("node_id"), item=item, kind="discussion_segment", visible_chain_ids=[discussion.get("node_id"), endpoint_node_id]))
        for child in status.get("chain_children") or []:
            if child["active_obligation_count"]:
                mode_items["active"].append(
                    projection_item(
                        conn,
                        node_id=child.get("endpoint_node_id"),
                        item={"id": child.get("endpoint"), "label": child.get("endpoint"), "summary": child.get("description"), **child},
                        kind="child_chain",
                        visible_chain_ids=[endpoint_node_id, child.get("endpoint_node_id")],
                    )
                )
        for task in status.get("deferred_tasks") or []:
            mode_items["history"].append(projection_item(conn, node_id=task.get("node_id"), item={**task, "current_state": "deferred"}, kind="task", visible_chain_ids=task_chain_ids(conn, task.get("node_id"))))
        for check in status.get("deferred_checks") or []:
            mode_items["history"].append(
                projection_item(
                    conn,
                    node_id=check.get("node_id"),
                    item={**check, "current_state": "deferred"},
                    kind="acceptance_check",
                    visible_chain_ids=check_chain_ids(conn, check.get("node_id"), check.get("task_id")),
                )
            )
        for check in status.get("closed_checks") or []:
            check_item = projection_item(
                conn,
                node_id=check.get("node_id"),
                item={**check, "current_state": "closed_by_controller"},
                kind="acceptance_check",
                visible_chain_ids=check_chain_ids(conn, check.get("node_id"), check.get("task_id")),
            )
            mode_items["history"].append(check_item)
            mode_items["evidence"].append(check_item)
        for item in (status.get("semantic_projection") or {}).get("inactive", []):
            mode_items["history"].append(projection_item(conn, node_id=item.get("node_id"), item=item, kind=item.get("item_type") or "historical", visible_chain_ids=[item.get("node_id"), endpoint_node_id]))
        for evidence in status.get("evidence") or []:
            mode_items["evidence"].append(projection_item(conn, node_id=evidence.get("id"), item=evidence, kind=evidence.get("type") or "evidence", visible_chain_ids=[evidence.get("id")]))

        mode_palette = {
            "active": SEMANTIC_HIGHLIGHT_PALETTE["open_or_worker_active"],
            "history": SEMANTIC_HIGHLIGHT_PALETTE["summary_only_or_non_active"],
            "evidence": SEMANTIC_HIGHLIGHT_PALETTE["verified_or_evidence_linked"],
        }
        for mode_name, items in mode_items.items():
            for item in items:
                filters = item.setdefault("filter_metadata", {})
                filters["projection_mode"] = mode_name
                filters["projection_mode_label_zh"] = label_zh(f"{mode_name}_mode")
                item["projection_mode"] = mode_name
                item["projection_mode_label_zh"] = filters["projection_mode_label_zh"]
                item.setdefault("visual", {})["mode_color"] = mode_palette[mode_name]
        combined_items: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for mode_name in ("active", "history", "evidence"):
            if mode_name in requested_views:
                add_view_payload(mode_name, mode_items[mode_name], rank_by=f"{mode_name}_then_recency")
            if requested_mode == "all":
                for item in mode_items[mode_name]:
                    key = str(item.get("node_id") or item.get("id"))
                    if key not in seen_keys:
                        combined_items.append(item)
                        seen_keys.add(key)
        if requested_mode == "all":
            add_view_payload("all", combined_items, rank_by="mode_then_recency")
        views = list(payload_views.keys())
    else:
        for current_view in views:
            items: list[dict[str, Any]] = []
            if current_view == "attention":
                for task in status.get("current_tasks") or []:
                    items.append(projection_item(conn, node_id=task.get("node_id"), item=task, kind="task", visible_chain_ids=task_chain_ids(conn, task.get("node_id"))))
                for check in status.get("open_checks") or []:
                    items.append(
                        projection_item(
                            conn,
                            node_id=check.get("node_id"),
                            item=check,
                            kind="acceptance_check",
                            visible_chain_ids=check_chain_ids(conn, check.get("node_id"), check.get("task_id")),
                        )
                    )
                for unresolved in status.get("unresolved") or []:
                    items.append(projection_item(conn, node_id=unresolved.get("id"), item=unresolved, kind="unresolved_question", visible_chain_ids=[unresolved.get("id"), endpoint_node_id]))
                for discussion in status.get("recent_discussions") or []:
                    if discussion.get("status") in {"unreviewed", "extracted"}:
                        items.append(projection_item(conn, node_id=discussion.get("node_id"), item=discussion, kind="discussion_segment", visible_chain_ids=[discussion.get("node_id"), endpoint_node_id]))
                for child in status.get("chain_children") or []:
                    items.append(
                        projection_item(
                            conn,
                            node_id=child.get("endpoint_node_id"),
                            item={"id": child.get("endpoint"), "label": child.get("endpoint"), "summary": child.get("description"), **child},
                            kind="child_chain",
                            visible_chain_ids=[endpoint_node_id, child.get("endpoint_node_id")],
                        )
                    )
            elif current_view == "execution":
                rows = conn.execute(
                    """
                    SELECT ar.id, ar.node_id, ar.agent_name, ar.model_name, ar.started_at, ar.ended_at, ar.final_report,
                           cs.id AS change_set_id, cs.node_id AS change_set_node_id, cs.summary AS change_summary
                    FROM agent_runs ar
                    JOIN edges e ON e.from_node_id = ar.node_id
                    LEFT JOIN change_sets cs ON cs.run_id = ar.id
                    WHERE e.type = 'APPLIES_TO' AND e.to_node_id = ?
                    ORDER BY ar.started_at DESC
                    LIMIT 30
                    """,
                    (endpoint_node_id,),
                ).fetchall()
                for row in rows:
                    item = row_to_dict(row)
                    chain = [item.get("node_id"), endpoint_node_id]
                    items.append(projection_item(conn, node_id=item.get("node_id"), item=item, kind="agent_run", visible_chain_ids=chain))
            elif current_view == "discussions":
                for discussion in status.get("recent_discussions") or []:
                    if discussion.get("status") in {"consumed", "superseded"} and not include_consumed:
                        continue
                    items.append(projection_item(conn, node_id=discussion.get("node_id"), item=discussion, kind="discussion_segment", visible_chain_ids=[discussion.get("node_id"), endpoint_node_id]))
            elif current_view == "audit":
                for finding in status.get("recent_audit_findings") or []:
                    items.append(projection_item(conn, node_id=finding.get("id"), item=finding, kind="audit_finding", visible_chain_ids=[finding.get("id"), endpoint_node_id]))
                for evidence in status.get("evidence") or []:
                    items.append(projection_item(conn, node_id=evidence.get("id"), item=evidence, kind="evidence", visible_chain_ids=[evidence.get("id")]))
                if include_history:
                    for item in (status.get("semantic_projection") or {}).get("inactive", []):
                        items.append(projection_item(conn, node_id=item.get("node_id"), item=item, kind=item.get("item_type") or "historical", visible_chain_ids=[item.get("node_id"), endpoint_node_id]))
            elif current_view == "full":
                rows = conn.execute(
                    """
                    SELECT n.*
                    FROM nodes n
                    JOIN edges e ON e.from_node_id = n.id OR e.to_node_id = n.id
                    WHERE e.from_node_id = ? OR e.to_node_id = ?
                    ORDER BY n.updated_at DESC, n.id DESC
                    LIMIT ?
                    """,
                    (endpoint_node_id, endpoint_node_id, limit),
                ).fetchall()
                for row in rows:
                    item = row_to_dict(row)
                    items.append(projection_item(conn, node_id=item.get("id"), item=item, kind=item.get("type") or "node", visible_chain_ids=[item.get("id"), endpoint_node_id]))
            else:
                raise SystemExit("graph projection --view must be one of: attention, execution, discussions, audit, full, all")
            add_view_payload(current_view, items, rank_by="attention_then_recency")
    overlay = build_workbench_overlay(
        conn,
        endpoint_id=status["endpoint"].get("id"),
        views=payload_views,
        requested_view=requested_mode or requested,
        include_history=include_history,
        include_consumed=include_consumed,
        limit=limit,
    )
    mode_counts = {name: int(view_payload.get("item_count", 0)) for name, view_payload in payload_views.items()}
    return {
        "ok": True,
        "endpoint": endpoint_name,
        "view": requested,
        "mode": requested_mode,
        "projection_modes": list(WORKBENCH_PROJECTION_MODES),
        "mode_counts": mode_counts if requested_mode else {},
        "read_only": True,
        "generated_at": now_iso(),
        "projection_metadata": {
            "source_kind": "projection_payload",
            "views": views,
            "mode": requested_mode,
            "mode_counts": mode_counts if requested_mode else {},
            "endpoint_node_id": endpoint_node_id,
            "event_anchor_node_id": endpoint_node_id,
            "snapshot_capable": True,
            "folded_edge_classes": sorted(FOLDED_SOURCE_EDGE_TYPES),
            "detail_ref_sources": ["discussion_segment", "evidence", "change_set", "diff_hunk"],
        },
        "noise_controls": {
            "include_consumed": include_consumed,
            "include_history": include_history,
            "limit": limit,
            "raw_sources_folded": True,
        },
        "views": payload_views,
        "overlay": overlay,
        "projection_contract": "Visible payloads fold source detail behind detail_ref and hidden_source_count; projection is not closure evidence.",
    }

def save_projection_snapshot(
    conn: sqlite3.Connection,
    repo: Path,
    *,
    endpoint_name: str,
    projection_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    endpoint = query_endpoint(conn, endpoint_name)
    snapshot_id = new_id("projection_snapshot")
    payload_text = json.dumps(payload, indent=2, sort_keys=True)
    out_dir = repo / ".shujuan" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload_ref = out_dir / f"{snapshot_id}_{projection_type}.json"
    payload_ref.write_text(payload_text, encoding="utf-8")
    payload_hash = sha256_text(payload_text)
    conn.execute(
        """
        INSERT INTO projection_snapshots
          (id, projection_type, endpoint_id, generated_from_event_id, generated_at, payload_hash, payload_ref, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            projection_type,
            endpoint["id"],
            endpoint["node_id"],
            now_iso(),
            payload_hash,
            relpath(payload_ref, repo),
            json_dumps({"read_only_payload": True, "view": payload.get("view")}),
        ),
    )
    return {"snapshot_id": snapshot_id, "payload_ref": relpath(payload_ref, repo), "payload_hash": payload_hash}

def cmd_graph_projection(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    endpoint_name = resolve_endpoint_identifier(conn, repo, args.endpoint)
    payload = graph_projection_payload(
        conn,
        endpoint_name,
        args.view,
        include_consumed=args.include_consumed,
        include_history=args.include_history,
        limit=args.limit,
    )
    if args.save_snapshot:
        payload["snapshot"] = save_projection_snapshot(conn, repo, endpoint_name=endpoint_name, projection_type=args.view, payload=payload)
        conn.commit()
    print_json(payload)
    return 0

def bounded_preview(value: str | None, *, limit: int = 4000) -> dict[str, Any] | None:
    if value is None:
        return None
    text = str(value)
    return {"text": text[:limit], "truncated": len(text) > limit, "chars": len(text), "limit": limit}

def repo_ref_preview(repo: Path | None, ref: str | None, *, limit: int = 4000) -> dict[str, Any] | None:
    if repo is None or not ref:
        return None
    path = Path(str(ref))
    if not path.is_absolute():
        path = repo / path
    try:
        resolved = path.resolve()
        repo_resolved = repo.resolve()
        if repo_resolved not in resolved.parents and resolved != repo_resolved:
            return {"ref": str(ref), "error": "ref outside repo", "limit": limit}
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ref": str(ref), "error": str(exc), "limit": limit}
    preview = bounded_preview(text, limit=limit) or {"text": "", "truncated": False, "chars": 0, "limit": limit}
    preview["ref"] = str(ref)
    return preview

def graph_detail_payload(conn: sqlite3.Connection, node_id: str, *, repo: Path | None = None, limit: int = 50, preview_limit: int = 4000) -> dict[str, Any]:
    node = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if not node:
        raise SystemExit(f"node not found: {node_id}")
    outgoing = conn.execute(
        """
        SELECT e.*, n.type AS to_type, n.label AS to_label, n.summary AS to_summary
        FROM edges e
        JOIN nodes n ON n.id = e.to_node_id
        WHERE e.from_node_id = ?
        ORDER BY e.created_at DESC
        LIMIT ?
        """,
        (node_id, limit),
    ).fetchall()
    incoming = conn.execute(
        """
        SELECT e.*, n.type AS from_type, n.label AS from_label, n.summary AS from_summary
        FROM edges e
        JOIN nodes n ON n.id = e.from_node_id
        WHERE e.to_node_id = ?
        ORDER BY e.created_at DESC
        LIMIT ?
        """,
        (node_id, limit),
    ).fetchall()
    discussion = None
    if node["type"] == "discussion_segment":
        segment = conn.execute("SELECT * FROM discussion_segments WHERE node_id = ?", (node_id,)).fetchone()
        if segment:
            lifecycle = conn.execute(
                "SELECT * FROM discussion_lifecycle_events WHERE segment_id = ? ORDER BY created_at ASC, id ASC",
                (segment["id"],),
            ).fetchall()
            discussion = {
                "segment": row_to_dict(segment),
                "messages": [row_to_dict(row) for row in discussion_messages(conn, segment["id"])],
                "lifecycle_events": [row_to_dict(row) for row in lifecycle],
            }
    evidence_records = conn.execute(
        "SELECT * FROM evidence_records WHERE evidence_node_id = ? ORDER BY created_at ASC, id ASC",
        (node_id,),
    ).fetchall()
    evidence_payload = []
    for row in evidence_records:
        record = row_to_dict(row)
        record["preview"] = repo_ref_preview(repo, record.get("ref"), limit=preview_limit)
        evidence_payload.append(record)
    change_set = None
    if node["type"] == "change_set":
        row = conn.execute("SELECT * FROM change_sets WHERE node_id = ?", (node_id,)).fetchone()
        if row:
            files = conn.execute(
                "SELECT * FROM diff_files WHERE change_set_id = ? ORDER BY path_new, path_old, id LIMIT ?",
                (row["id"], limit),
            ).fetchall()
            hunks = conn.execute(
                """
                SELECT dh.*, df.path_old, df.path_new, df.change_type
                FROM diff_hunks dh
                JOIN diff_files df ON df.id = dh.diff_file_id
                WHERE df.change_set_id = ?
                ORDER BY df.path_new, df.path_old, dh.new_start, dh.id
                LIMIT ?
                """,
                (row["id"], limit),
            ).fetchall()
            change_set = {
                "record": row_to_dict(row),
                "files": [row_to_dict(item) for item in files],
                "diff_hunks": [
                    {
                        **row_to_dict(item),
                        "old_text_preview": bounded_preview(item["old_text"], limit=preview_limit),
                        "new_text_preview": bounded_preview(item["new_text"], limit=preview_limit),
                        "context_text_preview": bounded_preview(item["context_text"], limit=preview_limit),
                    }
                    for item in hunks
                ],
                "patch_preview": repo_ref_preview(repo, props_dict(node).get("patch_ref"), limit=preview_limit),
            }
    diff_hunk = None
    if node["type"] == "diff_hunk":
        row = conn.execute(
            """
            SELECT dh.*, df.path_old, df.path_new, df.change_type, df.change_set_id
            FROM diff_hunks dh
            JOIN diff_files df ON df.id = dh.diff_file_id
            WHERE dh.node_id = ?
            """,
            (node_id,),
        ).fetchone()
        if row:
            diff_hunk = {
                **row_to_dict(row),
                "old_text_preview": bounded_preview(row["old_text"], limit=preview_limit),
                "new_text_preview": bounded_preview(row["new_text"], limit=preview_limit),
                "context_text_preview": bounded_preview(row["context_text"], limit=preview_limit),
            }
    artifact_preview = None
    props = props_dict(node)
    if node["type"] in {"artifact", "test_result"}:
        artifact_preview = repo_ref_preview(repo, props.get("path") or props.get("capture_ref"), limit=preview_limit)
    visible_ids = {node_id}
    return {
        "ok": True,
        "read_only": True,
        "node": row_to_dict(node),
        "visual": visual_metadata(str(node["type"]), row_to_dict(node) or {}),
        "incoming": [row_to_dict(row) for row in incoming],
        "outgoing": [row_to_dict(row) for row in outgoing],
        "discussion": discussion,
        "evidence_records": evidence_payload,
        "change_set": change_set,
        "diff_hunk": diff_hunk,
        "artifact_preview": artifact_preview,
        "hidden_source_count": hidden_source_count(conn, node_id, visible_ids),
        "hidden_source_edge_classes": sorted({str(edge["type"]) for edge in hidden_source_edges(conn, node_id, visible_ids)}),
        "detail_contract": "Detail payload expands folded sources for supervision only; it does not mutate DB state.",
    }

def cmd_graph_detail(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    print_json(graph_detail_payload(conn, args.node, repo=repo, limit=args.limit))
    return 0

def cmd_graph_show(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    node = conn.execute("SELECT * FROM nodes WHERE id = ?", (args.node,)).fetchone()
    if not node:
        raise SystemExit(f"node not found: {args.node}")
    outgoing = conn.execute(
        """
        SELECT e.*, n.type AS to_type, n.label AS to_label
        FROM edges e
        JOIN nodes n ON n.id = e.to_node_id
        WHERE e.from_node_id = ?
        ORDER BY e.created_at DESC
        LIMIT ?
        """,
        (args.node, args.limit),
    ).fetchall()
    incoming = conn.execute(
        """
        SELECT e.*, n.type AS from_type, n.label AS from_label
        FROM edges e
        JOIN nodes n ON n.id = e.from_node_id
        WHERE e.to_node_id = ?
        ORDER BY e.created_at DESC
        LIMIT ?
        """,
        (args.node, args.limit),
    ).fetchall()
    print_json(
        {
            "node": row_to_dict(node),
            "outgoing": [row_to_dict(row) for row in outgoing],
            "incoming": [row_to_dict(row) for row in incoming],
        }
    )
    return 0

def cmd_graph_edges(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    clauses = []
    params: list[Any] = []
    if args.from_node:
        clauses.append("e.from_node_id = ?")
        params.append(args.from_node)
    if args.to_node:
        clauses.append("e.to_node_id = ?")
        params.append(args.to_node)
    if args.type:
        clauses.append("e.type = ?")
        params.append(args.type)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"""
        SELECT e.*, nf.type AS from_type, nf.label AS from_label,
               nt.type AS to_type, nt.label AS to_label
        FROM edges e
        JOIN nodes nf ON nf.id = e.from_node_id
        JOIN nodes nt ON nt.id = e.to_node_id
        {where}
        ORDER BY e.created_at DESC
        LIMIT ?
        """,
        (*params, args.limit),
    ).fetchall()
    print_json({"edges": [row_to_dict(row) for row in rows]})
    return 0

def cmd_graph_link(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    edge_type = args.type.upper()
    if edge_type not in GRAPH_LINK_EDGE_TYPES:
        allowed = ", ".join(sorted(GRAPH_LINK_EDGE_TYPES))
        raise SystemExit(f"graph link --type must be one of: {allowed}")
    require_node(conn, args.from_node, "from node")
    require_node(conn, args.to_node, "to node")
    if args.evidence_node:
        require_node(conn, args.evidence_node, "edge evidence node")
    edge_id = create_edge(
        conn,
        args.from_node,
        edge_type,
        args.to_node,
        reason=args.reason,
        evidence_node_id=args.evidence_node,
        created_by="agent",
        props={"created_by_cli": "graph link"},
    )
    refresh = maybe_refresh_endpoint(conn, args.refresh_endpoint, args.from_node) if args.refresh_endpoint else None
    conn.commit()
    print_json(
        {
            "ok": True,
            "edge_id": edge_id,
            "from_node_id": args.from_node,
            "type": edge_type,
            "to_node_id": args.to_node,
            "endpoint_refresh": refresh,
        }
    )
    return 0

def cmd_graph_resolve(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    require_node(conn, args.node, "resolved target node")
    require_node(conn, args.source_node, "resolution source node")
    endpoint = query_endpoint(conn, args.endpoint) if args.endpoint else None
    target_node_ids = list(args.applies_to or [])
    if endpoint:
        target_node_ids.append(str(endpoint["node_id"]))
        if endpoint["root_node_id"]:
            target_node_ids.append(str(endpoint["root_node_id"]))
    target_node_ids = list(dict.fromkeys(target_node_ids))
    if not target_node_ids:
        raise SystemExit("graph resolve requires --endpoint or at least one --applies-to target")
    for target_node_id in target_node_ids:
        require_node(conn, target_node_id, "resolution applies-to target")
    body = read_arg_or_stdin(args.body)
    node_id = create_node(
        conn,
        "work_note",
        args.label or "resolution note",
        body[:240],
        {"kind": "resolution", "body": body, "endpoint": args.endpoint},
    )
    semantic_item_id = register_semantic_item(
        conn,
        node_id,
        "work_note",
        state=PRODUCT_BACKLOG_STATE,
        source_node=args.source_node,
        event_type="created",
        reason="Resolution note recorded.",
        props={"kind": "resolution", "body": body, "endpoint": args.endpoint},
    )
    source_edges = link_source_nodes(conn, node_id, [args.source_node], reason="Resolution note derived from source evidence node.")
    applies_edges = [
        create_edge(conn, node_id, "APPLIES_TO", target_node_id, reason="Resolution note applies to endpoint, root, or target node.", created_by="agent")
        for target_node_id in target_node_ids
    ]
    edge_id = create_edge(
        conn,
        node_id,
        "RESOLVES",
        args.node,
        reason=args.reason or "Resolution note resolves older semantic node.",
        created_by="agent",
    )
    transition_semantic_item(
        conn,
        args.node,
        state="resolved",
        event_type="resolved",
        source_node=node_id,
        reason=args.reason or "Resolution note resolves older semantic node.",
    )
    refresh = maybe_refresh_endpoint(conn, args.endpoint, node_id) if args.endpoint else None
    conn.commit()
    print_json(
        {
            "ok": True,
            "node_id": node_id,
            "semantic_item_id": semantic_item_id,
            "source_edges": source_edges,
            "applies_edges": applies_edges,
            "resolves_edge_id": edge_id,
            "resolved_node_id": args.node,
            "endpoint_refresh": refresh,
        }
    )
    return 0

def compact_snippet(text: str, limit: int = 260) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"

def semantic_candidate_types(text: str, heading: str | None = None) -> list[dict[str, Any]]:
    haystack = f"{heading or ''}\n{text}".lower()
    matches: list[dict[str, Any]] = []
    for node_type, markers in SEMANTIC_CANDIDATE_RULES:
        hit = [marker for marker in markers if marker.lower() in haystack]
        if hit:
            matches.append({"type": node_type, "reasons": hit[:3]})
    return matches

def cmd_graph_candidates(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    if bool(args.from_document) == bool(args.from_session):
        raise SystemExit("pass exactly one of --from-document or --from-session")
    requested_types = set(args.type or [])
    candidates: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    if args.from_document:
        document_id = args.from_document
        if document_id == "latest":
            latest = conn.execute("SELECT id FROM source_documents ORDER BY imported_at DESC, id DESC LIMIT 1").fetchone()
            if not latest:
                raise SystemExit("no source documents found")
            document_id = latest["id"]
        rows = conn.execute(
            """
            SELECT ds.id, ds.node_id, ds.section_index, ds.heading, ds.body, ds.content_hash,
                   sd.title AS document_title
            FROM document_sections ds
            JOIN source_documents sd ON sd.id = ds.document_id
            WHERE ds.document_id = ?
            ORDER BY ds.section_index
            """,
            (document_id,),
        ).fetchall()
        sources = [
            {
                "source_kind": "document_section",
                "source_id": row["id"],
                "source_node_id": row["node_id"],
                "heading": row["heading"],
                "body": row["body"],
                "content_hash": row["content_hash"],
            }
            for row in rows
        ]
    else:
        rows = conn.execute(
            """
            SELECT id, node_id, actor, content, content_hash, turn_index
            FROM messages
            WHERE session_id = ?
            ORDER BY turn_index
            """,
            (args.from_session,),
        ).fetchall()
        sources = [
            {
                "source_kind": "message",
                "source_id": row["id"],
                "source_node_id": row["node_id"],
                "heading": f"{row['actor']} turn {row['turn_index']}",
                "body": row["content"],
                "content_hash": row["content_hash"],
            }
            for row in rows
        ]
    for source in sources:
        for match in semantic_candidate_types(source["body"], source.get("heading")):
            if requested_types and match["type"] not in requested_types:
                continue
            label_hint = source.get("heading") or compact_snippet(source["body"], 80)
            candidate_id = sha256_text(
                f"{source['source_kind']}:{source['source_id']}:{source['content_hash']}:{match['type']}"
            )[:16]
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "suggested_type": match["type"],
                    "label_hint": compact_snippet(str(label_hint), 100),
                    "summary_hint": compact_snippet(source["body"]),
                    "source_kind": source["source_kind"],
                    "source_id": source["source_id"],
                    "source_node_id": source["source_node_id"],
                    "reasons": match["reasons"],
                    "create_command": (
                        "graph extract "
                        + (
                            f"--from-section {source['source_id']} "
                            if source["source_kind"] == "document_section"
                            else f"--from-session {args.from_session} --from-message {source['source_id']} "
                        )
                        + f"--type {match['type']} --label <confirmed-label> --summary <confirmed-summary>"
                    ),
                }
            )
            if len(candidates) >= args.limit:
                break
        if len(candidates) >= args.limit:
            break
    print_json(
        {
            "ok": True,
            "created": [],
            "candidates": candidates,
            "note": "Candidates are deterministic hints only. Confirm with graph extract so every semantic node has a DERIVED_FROM source edge.",
        }
    )
    return 0

def first_source_node_id(source_rows: list[dict[str, str]]) -> str | None:
    return source_rows[0]["node_id"] if source_rows else None

def create_task_row_for_node(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    body: str,
    contract_id: str | None,
    parent_task_id: str | None,
    optional: bool,
    created_from_node_id: str | None,
) -> str:
    if contract_id:
        contract = conn.execute("SELECT id, node_id FROM scope_contracts WHERE id = ?", (contract_id,)).fetchone()
        if not contract:
            raise SystemExit(f"scope contract not found: {contract_id}")
    else:
        contract = None
    if parent_task_id:
        parent = conn.execute("SELECT id FROM tasks WHERE id = ?", (parent_task_id,)).fetchone()
        if not parent:
            raise SystemExit(f"parent task not found: {parent_task_id}")
    task_id = new_id("task")
    conn.execute(
        """
        INSERT INTO tasks
          (id, node_id, contract_id, parent_task_id, task_body, is_mandatory, created_from_node_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, node_id, contract_id, parent_task_id, body, 0 if optional else 1, created_from_node_id),
    )
    if contract:
        create_edge(conn, contract["node_id"], "DECOMPOSES_TO", node_id, reason="Scope contract decomposes to extracted task.")
    return task_id

def create_acceptance_row_for_node(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    task_id: str,
    body: str,
    expected_evidence_type: str | None,
) -> str:
    task = conn.execute("SELECT id, node_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        raise SystemExit(f"task not found: {task_id}")
    check_id = new_id("check")
    conn.execute(
        """
        INSERT INTO acceptance_checks
          (id, node_id, task_id, check_body, expected_evidence_type)
        VALUES (?, ?, ?, ?, ?)
        """,
        (check_id, node_id, task_id, body, expected_evidence_type),
    )
    create_edge(conn, task["node_id"], "DECOMPOSES_TO", node_id, reason="Extracted task has acceptance check.")
    return check_id

def create_scope_contract_row_for_node(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    body: str,
    source_node_id: str | None,
    non_downgrade_rules: str | None,
) -> str:
    contract_id = new_id("contract")
    conn.execute(
        """
        INSERT INTO scope_contracts
          (id, node_id, source_node_id, body, non_downgrade_rules, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (contract_id, node_id, source_node_id, body, non_downgrade_rules, now_iso()),
    )
    return contract_id

def cmd_graph_extract(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    conn = connect(repo)
    if not args.from_session and not args.from_section and not args.from_discussion:
        raise SystemExit("pass --from-session, --from-discussion, or at least one --from-section")
    messages = []
    sections = []
    discussion_sources = []
    if args.from_session:
        messages = conn.execute(
            """
            SELECT id, node_id, actor, content, turn_index
            FROM messages
            WHERE session_id = ?
            ORDER BY turn_index
            """,
            (args.from_session,),
        ).fetchall()
        if not messages:
            raise SystemExit(f"session has no messages or does not exist: {args.from_session}")
    if args.from_section:
        placeholders = ",".join("?" for _ in args.from_section)
        sections = conn.execute(
            f"""
            SELECT id, node_id, heading, body, section_index
            FROM document_sections
            WHERE id IN ({placeholders})
            ORDER BY section_index
            """,
            tuple(args.from_section),
        ).fetchall()
        found = {row["id"] for row in sections}
        missing = [section_id for section_id in args.from_section if section_id not in found]
        if missing:
            raise SystemExit(f"document sections not found: {', '.join(missing)}")
    if args.from_discussion:
        for segment_id in args.from_discussion:
            segment = resolve_discussion_segment(conn, segment_id)
            discussion_sources.append(segment)
    if not args.type:
        print_json(
            {
                "ok": True,
                "created": [],
                "candidate_messages": [row_to_dict(row) for row in messages],
                "candidate_sections": [row_to_dict(row) for row in sections],
                "candidate_discussions": [row_to_dict(row) for row in discussion_sources],
                "note": "No semantic nodes were created. Pass --type and --label with explicit message/section sources for manual extraction.",
            }
        )
        return 0
    if not args.label:
        raise SystemExit("--label is required when --type is provided")
    if args.type == "acceptance_check" and not args.task:
        raise SystemExit("graph extract --type acceptance_check requires --task to avoid orphan acceptance_check nodes")
    source_message_ids = args.from_message or ([messages[-1]["id"]] if messages and not sections else [])
    source_rows = []
    for message_id in source_message_ids:
        match = next((row for row in messages if row["id"] == message_id), None)
        if not match:
            raise SystemExit(f"message {message_id} is not in session {args.from_session}")
        source_rows.append({"id": match["id"], "node_id": match["node_id"], "kind": "message"})
    for section in sections:
        source_rows.append({"id": section["id"], "node_id": section["node_id"], "kind": "document_section"})
    for segment in discussion_sources:
        source_rows.append({"id": segment["id"], "node_id": segment["node_id"], "kind": "discussion_segment"})
    if args.type in {"scope_contract", "task", "acceptance_check"} and not source_rows:
        raise SystemExit(f"graph extract --type {args.type} requires at least one source message, discussion, or section")
    node_id = create_node(
        conn,
        args.type,
        args.label,
        args.summary,
        {
            "extracted_from_session": args.from_session,
            "extracted_from_sections": args.from_section,
            "extracted_from_discussions": args.from_discussion,
            "manual": True,
        },
    )
    semantic_item_id = register_semantic_item(
        conn,
        node_id,
        args.type,
        state="active",
        source_node=first_source_node_id(source_rows),
        scope_node=node_id,
        event_type="created",
        reason=args.reason or "Manual graph extraction from source evidence.",
        props={"manual": True},
    )
    edge_ids = []
    for row in source_rows:
        edge_ids.append(
            create_edge(
                conn,
                node_id,
                "DERIVED_FROM",
                row["node_id"],
                reason=args.reason or "Manual graph extraction from transcript message.",
                created_by="agent",
            )
        )
    body = args.summary or args.label
    structured: dict[str, Any] = {"created": False}
    if args.type == "scope_contract":
        contract_id = create_scope_contract_row_for_node(
            conn,
            node_id=node_id,
            body=body,
            source_node_id=first_source_node_id(source_rows),
            non_downgrade_rules=args.non_downgrade_rules,
        )
        structured = {"created": True, "scope_contract_id": contract_id, "contract_id": contract_id}
    elif args.type == "task":
        task_id = create_task_row_for_node(
            conn,
            node_id=node_id,
            body=body,
            contract_id=args.contract,
            parent_task_id=args.parent,
            optional=args.optional,
            created_from_node_id=first_source_node_id(source_rows),
        )
        structured = {"created": True, "task_id": task_id}
    elif args.type == "acceptance_check":
        check_id = create_acceptance_row_for_node(
            conn,
            node_id=node_id,
            task_id=args.task,
            body=body,
            expected_evidence_type=args.expected_evidence_type,
        )
        structured = {"created": True, "acceptance_check_id": check_id}
    conn.commit()
    print_json(
        {
            "ok": True,
            "node_id": node_id,
            "semantic_item_id": semantic_item_id,
            "edge_ids": edge_ids,
            "source_messages": source_message_ids,
            "source_sections": args.from_section,
            "source_discussions": args.from_discussion,
            "structured": structured,
            **{key: value for key, value in structured.items() if key.endswith("_id")},
        }
    )
    return 0

def build_graph_handlers(deps: Mapping[str, Any]) -> dict[str, Any]:
    _configure(deps)
    return {
        "candidates": cmd_graph_candidates,
        "extract": cmd_graph_extract,
        "show": cmd_graph_show,
        "edges": cmd_graph_edges,
        "projection": cmd_graph_projection,
        "detail": cmd_graph_detail,
        "link": cmd_graph_link,
        "resolve": cmd_graph_resolve,
    }


def register_graph(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, Any]) -> None:
    graph = subparsers.add_parser("graph")
    graph_sub = graph.add_subparsers(dest="graph_command", required=True)
    graph_candidates = graph_sub.add_parser("candidates")
    graph_candidates.add_argument("--from-document")
    graph_candidates.add_argument("--from-session")
    graph_candidates.add_argument("--type", action="append", default=[])
    graph_candidates.add_argument("--limit", type=int, default=50)
    graph_candidates.set_defaults(func=handlers["candidates"])
    graph_extract = graph_sub.add_parser("extract")
    graph_extract.add_argument("--from-session")
    graph_extract.add_argument("--from-message", action="append", default=[])
    graph_extract.add_argument("--from-section", action="append", default=[])
    graph_extract.add_argument("--from-discussion", action="append", default=[])
    graph_extract.add_argument("--type")
    graph_extract.add_argument("--label")
    graph_extract.add_argument("--summary")
    graph_extract.add_argument("--reason")
    graph_extract.add_argument("--contract", help="When --type task, create the task row under this scope contract.")
    graph_extract.add_argument("--parent", help="When --type task, set parent task id.")
    graph_extract.add_argument("--optional", action="store_true", help="When --type task, mark extracted task optional.")
    graph_extract.add_argument("--task", help="When --type acceptance_check, create the check row under this task id.")
    graph_extract.add_argument("--expected-evidence-type")
    graph_extract.add_argument("--non-downgrade-rules", help="When --type scope_contract, store non-downgrade rules.")
    graph_extract.set_defaults(func=handlers["extract"])
    graph_show = graph_sub.add_parser("show")
    graph_show.add_argument("--node", required=True)
    graph_show.add_argument("--limit", type=int, default=20)
    graph_show.set_defaults(func=handlers["show"])
    graph_edges = graph_sub.add_parser("edges")
    graph_edges.add_argument("--from-node")
    graph_edges.add_argument("--to-node")
    graph_edges.add_argument("--type")
    graph_edges.add_argument("--limit", type=int, default=50)
    graph_edges.set_defaults(func=handlers["edges"])
    graph_projection = graph_sub.add_parser("projection")
    graph_projection.add_argument("--endpoint", required=True)
    graph_projection.add_argument("--view", default="all", choices=["attention", "execution", "discussions", "audit", "full", "all"])
    graph_projection.add_argument("--include-consumed", action="store_true")
    graph_projection.add_argument("--include-history", action="store_true")
    graph_projection.add_argument("--limit", type=int, default=50)
    graph_projection.add_argument("--save-snapshot", action="store_true")
    graph_projection.set_defaults(func=handlers["projection"])
    graph_detail = graph_sub.add_parser("detail")
    graph_detail.add_argument("--node", required=True)
    graph_detail.add_argument("--limit", type=int, default=50)
    graph_detail.set_defaults(func=handlers["detail"])
    graph_link = graph_sub.add_parser("link")
    graph_link.add_argument("--from-node", required=True)
    graph_link.add_argument("--to-node", required=True)
    graph_link.add_argument("--type", required=True)
    graph_link.add_argument("--reason", required=True)
    graph_link.add_argument("--evidence-node")
    graph_link.add_argument("--refresh-endpoint")
    graph_link.set_defaults(func=handlers["link"])
    graph_resolve = graph_sub.add_parser("resolve")
    graph_resolve.add_argument("--node", required=True)
    graph_resolve.add_argument("--source-node", required=True)
    graph_resolve.add_argument("--body", required=True)
    graph_resolve.add_argument("--endpoint")
    graph_resolve.add_argument("--applies-to", action="append", default=[])
    graph_resolve.add_argument("--label")
    graph_resolve.add_argument("--reason")
    graph_resolve.set_defaults(func=handlers["resolve"])
