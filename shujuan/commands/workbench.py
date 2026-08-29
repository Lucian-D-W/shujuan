from __future__ import annotations

import argparse
import html
import json
import shutil
import sqlite3
import time
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..schema_roles import schema_visibility_policy


WorkbenchHandler = Callable[[argparse.Namespace], int]
WORKBENCH_HANDLER_KEYS = ("export", "serve")
WORKBENCH_PROJECTION_MODES = ("active", "history", "evidence", "all")
WORKBENCH_DEPENDENCY_KEYS = (
    "connect",
    "resolve_endpoint_identifier",
    "graph_projection_payload",
    "graph_detail_payload",
    "print_json",
    "relpath",
)

connect: Callable[[Path], sqlite3.Connection] | None = None
resolve_endpoint_identifier: Callable[[sqlite3.Connection, Path, str], str] | None = None
graph_projection_payload: Callable[..., dict[str, Any]] | None = None
graph_detail_payload: Callable[..., dict[str, Any]] | None = None
print_json: Callable[[Any], None] | None = None
relpath: Callable[[Path, Path], str] | None = None


def _validate_handlers(handlers: Mapping[str, WorkbenchHandler]) -> None:
    missing = [key for key in WORKBENCH_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"workbench command boundary is missing: {', '.join(missing)}")


def _workbench_dependencies(deps: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in WORKBENCH_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"workbench handler boundary is missing: {', '.join(missing)}")
    return {key: deps[key] for key in WORKBENCH_DEPENDENCY_KEYS}


def _require_dependency(name: str) -> Any:
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"workbench command dependency is not configured: {name}")
    return value


def build_workbench_handlers(deps: Mapping[str, Any]) -> dict[str, WorkbenchHandler]:
    """Build workbench handlers from cli.py-owned graph/runtime helpers without importing cli.py."""
    globals().update(_workbench_dependencies(deps))
    return {"export": cmd_workbench_export, "serve": cmd_workbench_serve}


def register_workbench(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, WorkbenchHandler]) -> None:
    _validate_handlers(handlers)
    workbench = subparsers.add_parser("workbench")
    workbench_sub = workbench.add_subparsers(dest="workbench_command", required=True)
    workbench_export = workbench_sub.add_parser("export")
    workbench_export.add_argument("--endpoint", required=True)
    workbench_export.add_argument("--path", default=Path(".shujuan") / "exports" / "workbench.html")
    workbench_export.add_argument("--format", choices=["html", "json"], default="html")
    workbench_export.add_argument("--view", default="all", choices=["attention", "execution", "discussions", "audit", "full", "all"])
    workbench_export.add_argument("--mode", choices=WORKBENCH_PROJECTION_MODES, help="Use DB-backed workbench mode views instead of the legacy graph view set.")
    workbench_export.add_argument("--include-consumed", action="store_true")
    workbench_export.add_argument("--include-history", action="store_true")
    workbench_export.add_argument("--limit", type=int, default=50)
    workbench_export.add_argument("--layout", default="endpoint_radial_chain")
    workbench_export.set_defaults(func=handlers["export"])
    workbench_serve = workbench_sub.add_parser("serve")
    workbench_serve.add_argument("--endpoint", required=True)
    workbench_serve.add_argument("--host", default="127.0.0.1")
    workbench_serve.add_argument("--port", type=int, default=8765)
    workbench_serve.add_argument("--mode", default="active", choices=WORKBENCH_PROJECTION_MODES)
    workbench_serve.add_argument("--include-consumed", action="store_true")
    workbench_serve.add_argument("--limit", type=int, default=50)
    workbench_serve.add_argument("--layout", default="endpoint_radial_chain")
    workbench_serve.add_argument("--poll-seconds", type=float, default=2.0)
    workbench_serve.set_defaults(func=handlers["serve"])


def attach_workbench_details(conn: sqlite3.Connection, repo: Path, payload: dict[str, Any], *, limit: int = 25) -> dict[str, Any]:
    graph_detail_payload_fn = _require_dependency("graph_detail_payload")
    node_ids: list[str] = []
    for view_payload in payload.get("views", {}).values():
        for item in view_payload.get("items", []):
            node_id = item.get("node_id")
            if node_id and node_id not in node_ids:
                node_ids.append(str(node_id))
    payload["detail_payloads"] = {
        node_id: graph_detail_payload_fn(conn, node_id, repo=repo, limit=limit)
        for node_id in node_ids[:limit]
    }
    return payload


def ensure_workbench_g6_asset(repo: Path, out_path: Path) -> dict[str, Any]:
    relpath_fn = _require_dependency("relpath")
    source_candidates = [
        repo / "node_modules" / "@antv" / "g6" / "dist" / "g6.min.js",
        Path(__file__).resolve().parents[2] / "node_modules" / "@antv" / "g6" / "dist" / "g6.min.js",
    ]
    source = next((candidate for candidate in source_candidates if candidate.exists()), source_candidates[0])
    target = out_path.parent / "g6.min.js"
    if not source.exists():
        return {
            "bundled": False,
            "script_src": "https://unpkg.com/@antv/g6@5.1.1/dist/g6.min.js",
            "warning": "node_modules/@antv/g6/dist/g6.min.js was not found; HTML points at the pinned CDN fallback.",
        }
    if not target.exists() or source.stat().st_mtime_ns != target.stat().st_mtime_ns or source.stat().st_size != target.stat().st_size:
        shutil.copy2(source, target)
    return {
        "bundled": True,
        "script_src": target.name,
        "asset_path": relpath_fn(target, repo),
        "package": "@antv/g6",
        "version": "5.1.1",
    }


def render_workbench_html(payload: dict[str, Any], *, layout: str = "endpoint_radial_chain", g6_script_src: str = "g6.min.js") -> str:
    pretty = json.dumps(payload, indent=2, sort_keys=True)
    endpoint = html.escape(str(payload.get("endpoint") or "endpoint"))
    data = html.escape(pretty)
    script_data = pretty.replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>shujuan workbench: {endpoint}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Arial, sans-serif; background: #101418; color: #e5e7eb; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; background: #101418; }}
    header {{ padding: 16px 22px; border-bottom: 1px solid #374151; background: #171b22; }}
    h1 {{ font-size: 20px; margin: 0 0 6px; font-weight: 700; }}
    p {{ margin: 0; color: #cbd5e1; }}
    main {{ display: grid; grid-template-columns: minmax(320px, 1fr) 360px; height: calc(100vh - 74px); min-height: 520px; overflow: hidden; }}
    #graph-mount {{ position: relative; height: 100%; min-height: 520px; background: #0b0f14; overflow: hidden; }}
    aside {{ border-left: 1px solid #374151; background: #171b22; display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; }}
    .toolbar {{ display: flex; gap: 8px; align-items: center; padding: 12px; border-bottom: 1px solid #374151; flex-wrap: wrap; }}
    .toolbar input, .toolbar select {{ min-height: 34px; border-radius: 6px; border: 1px solid #4b5563; background: #0f1720; color: #f8fafc; padding: 7px 9px; }}
    .toolbar label {{ display: inline-flex; gap: 6px; align-items: center; font-size: 13px; color: #d1d5db; }}
    button {{ min-height: 34px; border-radius: 6px; border: 1px solid #4b5563; background: #243244; color: #f8fafc; padding: 7px 10px; cursor: pointer; }}
    button[aria-pressed="true"] {{ background: #14532d; border-color: #22c55e; }}
    .panel {{ padding: 14px; overflow: auto; border-bottom: 1px solid #374151; }}
    .panel h2 {{ margin: 0 0 10px; font-size: 15px; }}
    .summary {{ color: #d1d5db; font-size: 13px; line-height: 1.45; }}
    .source-list {{ margin: 8px 0 0; padding-left: 18px; color: #cbd5e1; }}
    code {{ background: #0f1720; color: #bfdbfe; padding: 2px 5px; border-radius: 4px; }}
    pre {{ overflow: auto; white-space: pre-wrap; background: #090d12; color: #f8fafc; padding: 12px; border-radius: 6px; border: 1px solid #334155; font-size: 12px; }}
    .drawer {{ min-height: 170px; }}
    #item-list {{ flex: 0 0 240px; }}
    #detail-panel {{ flex: 0 0 180px; }}
    #source-drawer, #diff-preview {{ flex: 0 0 170px; }}
    #projection-json {{ max-height: 280px; }}
    .muted {{ color: #94a3b8; }}
    .badge {{ display: inline-block; margin: 2px 5px 2px 0; padding: 2px 6px; border-radius: 999px; background: #263241; color: #dbeafe; font-size: 12px; }}
    .graph-status {{ position: absolute; inset: 18px; display: flex; align-items: center; justify-content: center; border: 1px dashed #475569; background: rgba(15, 23, 32, 0.86); color: #dbeafe; text-align: center; padding: 24px; }}
    .graph-status h2 {{ margin: 0 0 8px; font-size: 16px; }}
    .graph-status p {{ margin: 5px 0; font-size: 13px; }}
    .fallback-nodes {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 12px; }}
    .fallback-node {{ max-width: 180px; border: 1px solid #475569; border-left: 4px solid var(--node-color, #60a5fa); border-radius: 6px; padding: 7px 9px; background: #111827; color: #f8fafc; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    @media (max-width: 900px) {{ main {{ grid-template-columns: 1fr; height: auto; overflow: visible; }} aside {{ border-left: 0; border-top: 1px solid #374151; max-height: 70vh; }} #graph-mount {{ height: 58vh; min-height: 360px; }} }}
  </style>
</head>
<body>
  <header><h1>shujuan workbench: {endpoint}</h1><p>Read-only projection export. It does not mutate governance state, exposes no write/action endpoints, and renders projection payloads through AntV G6. Layout: {html.escape(layout)}.</p></header>
  <main>
    <div id="graph-mount" data-g6-rendered="pending" data-g6-node-count="0" data-g6-edge-count="0" data-g6-canvas-count="0" data-g6-error="" aria-label="AntV G6 projection canvas"></div>
    <aside>
      <div class="toolbar" aria-label="Projection controls">
        <select id="view-filter" title="View filter"></select>
        <input id="search-input" type="search" placeholder="Search graph" aria-label="Search graph">
        <label><input id="attention-only" type="checkbox" checked> Attention</label>
        <button id="source-toggle" type="button" aria-pressed="false">Source</button>
        <button id="diff-toggle" type="button" aria-pressed="false">Diff</button>
      </div>
      <section class="panel" id="item-list"><h2>Items</h2></section>
      <section class="panel" id="detail-panel"><h2>Detail</h2><p class="muted">Select a graph item to inspect detail_ref, folded source classes, and raw preview.</p></section>
      <section class="panel drawer" id="source-drawer"><h2>Source Drawer</h2><p class="muted">Source previews stay embedded in this read-only artifact.</p></section>
      <section class="panel drawer" id="diff-preview"><h2>Artifact/Diff Preview</h2><p class="muted">Change set and diff hunk previews appear here when selected.</p></section>
      <section class="panel"><h2>Projection JSON</h2><pre id="projection-json">{data}</pre></section>
    </aside>
  </main>
  <script id="projection-payload" type="application/json">{script_data}</script>
  <script src="{html.escape(g6_script_src)}"></script>
  <script>
    const payload = JSON.parse(document.getElementById('projection-payload').textContent);
    const details = payload.detail_payloads || {{}};
    const viewFilter = document.getElementById('view-filter');
    const searchInput = document.getElementById('search-input');
    const attentionOnly = document.getElementById('attention-only');
    const itemList = document.getElementById('item-list');
    const detailPanel = document.getElementById('detail-panel');
    const sourceDrawer = document.getElementById('source-drawer');
    const diffPreview = document.getElementById('diff-preview');
    const sourceToggle = document.getElementById('source-toggle');
    const diffToggle = document.getElementById('diff-toggle');
    const graphMount = document.getElementById('graph-mount');
    const viewNames = Object.keys(payload.views || {{}});
    const defaultView = viewNames.includes('attention') ? 'attention' : (viewNames[0] || 'full');
    let selectedView = defaultView;
    let selectedNodeId = null;
    let graph = null;

    viewNames.forEach((name) => {{
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      option.selected = name === defaultView;
      viewFilter.appendChild(option);
    }});

    function escapeText(value) {{
      return String(value ?? '').replace(/[&<>"']/g, (ch) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    }}

    function shortLabel(value, max = 16) {{
      const text = String(value ?? '');
      return text.length > max ? `${{text.slice(0, max - 1)}}...` : text;
    }}

    function resolveG6GraphConstructor() {{
      return (window.G6 || {{}}).Graph || null;
    }}

    function destroyGraph() {{
      if (graph && !graph.destroyed) graph.destroy();
      graph = null;
    }}

    function allItems() {{
      const views = payload.views || {{}};
      const source = selectedView === 'all' ? Object.values(views).flatMap((view) => view.items || []) : ((views[selectedView] || {{}}).items || []);
      const query = searchInput.value.trim().toLowerCase();
      return source.filter((item) => {{
        if (attentionOnly.checked && !(item.visual || {{}}).attention && item.kind !== 'task' && item.kind !== 'acceptance_check' && item.kind !== 'child_chain') return false;
        if (!query) return true;
        return JSON.stringify([item.kind, item.label, item.summary, item.id, item.node_id]).toLowerCase().includes(query);
      }});
    }}

    function graphData() {{
      const items = allItems();
      const nodes = new Map();
      const edges = new Map();
      function registerNode(node, item, visual) {{
        if (!node || !node.id || nodes.has(node.id)) return;
        const itemVisual = visual || item.visual || {{}};
        nodes.set(node.id, {{
          id: node.id,
          type: 'rect',
          data: {{
            item,
            node,
            label: shortLabel(node.label || node.summary || node.id),
            color: itemVisual.color || '#60a5fa',
            stroke: item.node_id === node.id ? '#f8fafc' : '#94a3b8',
            size: item.node_id === node.id ? [190, 86] : [170, 76]
          }}
        }});
      }}
      function registerEdge(edge, item) {{
        if (!edge || !edge.from_node_id || !edge.to_node_id) return;
        const edgeId = edge.id || `${{edge.from_node_id}}->${{edge.to_node_id}}`;
        registerNode({{ id: edge.from_node_id, type: 'edge_endpoint', label: edge.from_node_id }}, item, {{ color: '#64748b' }});
        registerNode({{ id: edge.to_node_id, type: 'edge_endpoint', label: edge.to_node_id }}, item, {{ color: '#64748b' }});
        edges.set(edgeId, {{
          id: edgeId,
          source: edge.from_node_id,
          target: edge.to_node_id,
          data: {{ ...edge, label: edge.type || 'edge' }}
        }});
      }}
      items.forEach((item) => {{
        const chain = item.visible_chain || [];
        chain.forEach((node) => registerNode(node, item));
        if (!chain.length && item.node_id) {{
          registerNode({{ id: item.node_id, type: item.kind || 'projection_item', label: item.label || item.id || item.node_id, summary: item.summary || '' }}, item);
        }}
        (item.visible_edges || []).forEach((edge) => registerEdge(edge, item));
      }});
      const explicitGraph = payload.graph || payload.data || {{}};
      (payload.nodes || explicitGraph.nodes || []).forEach((node) => {{
        registerNode(node, {{ kind: node.type || 'node', node_id: node.id, label: node.label || node.id, visual: node.visual || {{}} }});
      }});
      (payload.edges || explicitGraph.edges || []).forEach((edge) => {{
        registerEdge({{ id: edge.id, from_node_id: edge.from_node_id || edge.source, to_node_id: edge.to_node_id || edge.target, type: edge.type || edge.label, style: edge.style }}, {{ kind: 'edge', node_id: edge.source || edge.from_node_id, visual: {{ color: '#64748b' }} }});
      }});
      return {{ nodes: Array.from(nodes.values()), edges: Array.from(edges.values()) }};
    }}

    function graphSize() {{
      const rect = graphMount.getBoundingClientRect();
      const size = {{
        width: Math.max(640, Math.floor(rect.width || 900)),
        height: Math.max(480, Math.floor(rect.height || 700))
      }};
      graphMount.dataset.g6Width = String(size.width);
      graphMount.dataset.g6Height = String(size.height);
      return size;
    }}

    const requestedLayout = {json.dumps(layout)};

    function layoutOptions(name, data, size) {{
      const nodeCount = Math.max(data.nodes.length, 1);
      const cols = Math.max(2, Math.ceil(Math.sqrt(nodeCount)));
      const rows = Math.max(2, Math.ceil(nodeCount / cols));
      const normalized = String(name || '').replace(/-/g, '_').toLowerCase();
      const common = {{
        preventOverlap: true,
        nodeSize: 48
      }};
      if (normalized === 'endpoint_radial_chain' || normalized === 'radial') {{
        return {{
          ...common,
          type: 'radial',
          unitRadius: Math.max(70, Math.min(size.width, size.height) / Math.max(3, Math.ceil(Math.sqrt(nodeCount)))),
          linkDistance: 90,
          nodeSpacing: 18
        }};
      }}
      if (normalized === 'force') {{
        return {{
          ...common,
          type: 'force',
          linkDistance: 110,
          nodeStrength: -180,
          edgeStrength: 0.6
        }};
      }}
      if (normalized === 'circular') {{
        return {{
          ...common,
          type: 'circular',
          radius: Math.max(160, Math.min(size.width, size.height) / 2.8)
        }};
      }}
      return {{
        ...common,
        type: 'grid',
        begin: [50, 60],
        cols,
        rows,
        width: Math.max(320, size.width - 120),
        height: Math.max(300, size.height - 140),
        condense: true
      }};
    }}

    function renderGraphFallback(data, title, detail) {{
      destroyGraph();
      graphMount.dataset.g6Rendered = title === 'No graph nodes' ? 'empty' : 'error';
      graphMount.dataset.g6NodeCount = String(data.nodes.length);
      graphMount.dataset.g6EdgeCount = String(data.edges.length);
      graphMount.dataset.g6CanvasCount = '0';
      if (detail) graphMount.dataset.g6Error = detail;
      const chips = data.nodes.slice(0, 12).map((node) => `<span class="fallback-node" style="--node-color:${{escapeText((node.data || {{}}).color || '#60a5fa')}}">${{escapeText((node.data || {{}}).label || node.id)}}</span>`).join('');
      graphMount.innerHTML = `<div class="graph-status" role="status"><div><h2>${{escapeText(title)}}</h2><p>${{escapeText(detail || 'The export is readable, but the G6 canvas did not produce a mounted graph.')}}</p><p>nodes=${{data.nodes.length}} edges=${{data.edges.length}} layout=${{escapeText(requestedLayout)}}</p>${{chips ? `<div class="fallback-nodes">${{chips}}</div>` : ''}}</div></div>`;
    }}

    async function renderGraph() {{
      const data = graphData();
      const size = graphSize();
      window.__shujuanGraphData = data;
      window.__shujuanGraphRenderError = null;
      if (!data.nodes.length) {{
        renderGraphFallback(data, 'No graph nodes', 'No visible graph nodes match the selected view, search, and attention filter.');
        renderItemList();
        return;
      }}
      const options = {{
        container: graphMount,
        width: size.width,
        height: size.height,
        data,
        autoFit: 'center',
        layout: layoutOptions(requestedLayout, data, size),
        node: {{
          style: {{
            size: (d) => d.data.size,
            fill: (d) => d.data.color,
            stroke: (d) => d.data.stroke,
            lineWidth: 2,
            label: true,
            labelText: (d) => d.data.label,
            labelFill: '#f8fafc',
            labelFontSize: 11,
            labelPlacement: 'center',
            labelBackground: true,
            labelBackgroundFill: '#111827',
            labelBackgroundStroke: '#334155',
            labelBackgroundRadius: 4,
            labelPadding: [2, 4]
          }}
        }},
        edge: {{
          style: {{
            stroke: (d) => d.data.style === 'dashed' ? '#38bdf8' : '#94a3b8',
            lineWidth: 2,
            lineDash: (d) => d.data.style === 'dashed' ? [5, 5] : undefined,
            endArrow: true
          }}
        }},
        behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'click-select']
      }};
      try {{
        graphMount.dataset.g6Rendered = 'pending';
        graphMount.dataset.g6NodeCount = String(data.nodes.length);
        graphMount.dataset.g6EdgeCount = String(data.edges.length);
        const GraphCtor = resolveG6GraphConstructor();
        if (!GraphCtor) throw new Error('AntV G6 global did not expose Graph; check the bundled or CDN script.');
        if (!graph) {{
          graphMount.innerHTML = '';
          // Equivalent to new G6.Graph(options) when the UMD global is available.
          graph = new GraphCtor(options);
        }}
        else graph.setOptions(options);
        window.__shujuanGraph = graph;
        await graph.render();
        graphMount.dataset.g6Rendered = 'true';
        graphMount.dataset.g6CanvasCount = String(graphMount.querySelectorAll('canvas, svg').length);
        renderItemList();
      }} catch (error) {{
        const message = String(error && error.message ? error.message : error);
        window.__shujuanGraphRenderError = message;
        graphMount.dataset.g6Rendered = 'error';
        graphMount.dataset.g6Error = message;
        console.error(error);
        renderGraphFallback(data, 'G6 render failed', message);
        renderItemList();
      }}
    }}

    function renderItemList() {{
      const buttons = allItems().map((item) => `<button type="button" data-node-id="${{escapeText(item.node_id || '')}}">${{escapeText(item.kind)}}: ${{escapeText(item.label || item.id || item.node_id || '')}}</button>`).join(' ');
      itemList.innerHTML = `<h2>Items</h2>${{buttons || '<p class="muted">No visible items match the current filters.</p>'}}`;
      itemList.querySelectorAll('button[data-node-id]').forEach((button) => {{
        button.addEventListener('click', () => renderDetail(button.getAttribute('data-node-id')));
      }});
    }}

    function selectedDetail() {{
      if (!selectedNodeId) return null;
      return details[selectedNodeId] || null;
    }}

    function renderDetail(nodeId) {{
      selectedNodeId = nodeId;
      const detail = selectedDetail();
      if (!detail) {{
        detailPanel.innerHTML = '<h2>Detail</h2><p class="muted">No embedded detail payload for this selection.</p>';
        return;
      }}
      const classes = (detail.hidden_source_edge_classes || []).map((name) => `<span class="badge">${{escapeText(name)}}</span>`).join('');
      detailPanel.innerHTML = `<h2>Detail</h2><p><code>${{escapeText(detail.node.id)}}</code> ${{escapeText(detail.node.type)}} </p><p class="summary">${{escapeText(detail.node.summary || detail.node.label || '')}}</p><p>${{classes || '<span class="muted">No folded source classes</span>'}}</p><p class="muted">${{escapeText(detail.detail_contract)}}</p>`;
      renderSourceDrawer();
      renderDiffPreview();
    }}

    function renderSourceDrawer() {{
      const detail = selectedDetail();
      if (!detail || sourceToggle.getAttribute('aria-pressed') !== 'true') return;
      const records = (detail.evidence_records || []).map((record) => `<li>${{escapeText(record.record_type)}} <code>${{escapeText(record.ref || '')}}</code><pre>${{escapeText((record.preview || {{}}).text || '')}}</pre></li>`).join('');
      const messages = (((detail.discussion || {{}}).messages) || []).map((msg) => `<li>${{escapeText(msg.actor)}}: ${{escapeText(msg.content)}}</li>`).join('');
      sourceDrawer.innerHTML = `<h2>Source Drawer</h2><ul class="source-list">${{records || messages || '<li>No raw source preview embedded.</li>'}}</ul>`;
    }}

    function renderDiffPreview() {{
      const detail = selectedDetail();
      if (!detail || diffToggle.getAttribute('aria-pressed') !== 'true') return;
      const hunks = ((detail.change_set || {{}}).diff_hunks || []);
      const hunk = detail.diff_hunk ? [detail.diff_hunk] : hunks;
      const body = hunk.map((item) => `<p><code>${{escapeText(item.path_new || item.path_old || item.hunk_header)}}</code></p><pre>${{escapeText(((item.new_text_preview || item.context_text_preview || {{}}).text) || '')}}</pre>`).join('');
      const patch = (((detail.change_set || {{}}).patch_preview || {{}}).text) || '';
      diffPreview.innerHTML = `<h2>Artifact/Diff Preview</h2>${{body || (patch ? `<pre>${{escapeText(patch)}}</pre>` : '<p class="muted">No change set or diff hunk preview embedded.</p>')}}`;
    }}

    viewFilter.addEventListener('change', () => {{ selectedView = viewFilter.value; renderGraph(); }});
    searchInput.addEventListener('input', renderGraph);
    attentionOnly.addEventListener('change', renderGraph);
    sourceToggle.addEventListener('click', () => {{ sourceToggle.setAttribute('aria-pressed', String(sourceToggle.getAttribute('aria-pressed') !== 'true')); renderSourceDrawer(); }});
    diffToggle.addEventListener('click', () => {{ diffToggle.setAttribute('aria-pressed', String(diffToggle.getAttribute('aria-pressed') !== 'true')); renderDiffPreview(); }});
    window.addEventListener('resize', () => renderGraph());
    window.addEventListener('beforeunload', () => {{ if (graph && !graph.destroyed) graph.destroy(); }});
    renderGraph();
    const first = graphData().nodes[0];
    if (first) renderDetail(first.id);
  </script>
</body>
</html>
"""


def _render_workbench_html_v2(payload: dict[str, Any], *, layout: str = "endpoint_radial_chain", g6_script_src: str = "g6.min.js") -> str:
    pretty = json.dumps(payload, indent=2, sort_keys=True)
    endpoint = html.escape(str(payload.get("endpoint") or "endpoint"))
    data = html.escape(pretty)
    script_data = pretty.replace("</", "<\\/")
    template = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>shujuan workbench: __ENDPOINT__</title>
  <style>
    :root { color-scheme: dark; font-family: Arial, sans-serif; background: #050607; color: #e5e7eb; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #050607; }
    header { min-height: 78px; padding: 10px 20px 8px; border-bottom: 1px solid #1f2933; background: #08090d; }
    h1 { font-size: 20px; margin: 0 0 6px; font-weight: 700; }
    p { margin: 0; color: #b8c0cc; }
    .top-legend { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; align-items: center; }
    .top-legend span { display: inline-flex; align-items: center; gap: 5px; color: #9ca3af; font-size: 11px; }
    .top-legend i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; background: var(--swatch, #94a3b8); box-shadow: 0 0 10px color-mix(in srgb, var(--swatch, #94a3b8) 45%, transparent); }
    main { display: grid; grid-template-columns: minmax(520px, 1fr) 360px; height: calc(100vh - 78px); min-height: 540px; overflow: hidden; background: #050607; }
    #graph-mount { position: relative; height: 100%; min-height: 520px; background: #05080a; overflow: auto; }
    aside { border-left: 1px solid #2a313b; background: #12161c; display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; }
    .toolbar { display: flex; gap: 8px; align-items: center; padding: 12px; border-bottom: 1px solid #2a313b; flex-wrap: wrap; }
    .toolbar input, .toolbar select, .filter-grid select { min-height: 34px; border-radius: 6px; border: 1px solid #475569; background: #0b1118; color: #f8fafc; padding: 7px 9px; min-width: 0; }
    .toolbar label, .filter-grid label { display: grid; gap: 4px; font-size: 12px; color: #d1d5db; }
    .language-field { min-width: 104px; }
    button { min-height: 34px; border-radius: 6px; border: 1px solid #48515e; background: #18202a; color: #f8fafc; padding: 7px 10px; cursor: pointer; }
    button[aria-pressed="true"], .route-button[aria-pressed="true"], .step-button[aria-pressed="true"] { background: #3b2f0a; border-color: #fbbf24; box-shadow: 0 0 0 1px rgba(251, 191, 36, 0.45), 0 0 18px rgba(245, 158, 11, 0.24); }
    .filter-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 0 12px 12px; border-bottom: 1px solid #2a313b; }
    .panel { padding: 14px; overflow: auto; border-bottom: 1px solid #2a313b; }
    .panel h2 { margin: 0 0 10px; font-size: 15px; }
    .summary { color: #d1d5db; font-size: 13px; line-height: 1.45; }
    .source-list { margin: 8px 0 0; padding-left: 18px; color: #cbd5e1; }
    code { background: #080d13; color: #bfdbfe; padding: 2px 5px; border-radius: 4px; }
    pre { overflow: auto; white-space: pre-wrap; background: #06090d; color: #f8fafc; padding: 12px; border-radius: 6px; border: 1px solid #334155; font-size: 12px; }
    .drawer { min-height: 120px; }
    #route-panel { flex: 0 0 auto; }
    #step-panel { flex: 0 0 220px; }
    #detail-panel { flex: 0 0 210px; }
    #source-drawer, #diff-preview, #legend-panel, #debug-panel { flex: 0 0 150px; }
    #projection-json { max-height: 280px; }
    .muted { color: #94a3b8; }
    .badge { display: inline-block; margin: 2px 5px 2px 0; padding: 2px 6px; border-radius: 999px; background: #263241; color: #dbeafe; font-size: 12px; border: 1px solid transparent; }
    .status-badge { border-color: var(--badge-color, #60a5fa); background: color-mix(in srgb, var(--badge-color, #60a5fa) 20%, #111827); color: #f8fafc; }
    .route-grid, .step-list { display: grid; gap: 7px; }
    .route-button { width: 100%; text-align: left; border-left: 3px solid var(--route-color, #64748b); background: #0c131b; }
    .route-button strong, .step-title { display: block; font-size: 13px; color: #f8fafc; }
    .route-button span, .step-meta { display: block; margin-top: 2px; color: #cbd5e1; font-size: 12px; }
    .step-button { width: 100%; display: grid; grid-template-columns: 26px 1fr; gap: 8px; align-items: start; text-align: left; background: #0b141d; border-left: 3px solid var(--step-color, #38bdf8); }
    .step-number { display: inline-flex; width: 22px; height: 22px; border-radius: 999px; align-items: center; justify-content: center; background: var(--step-color, #38bdf8); color: #050607; font-weight: 700; font-size: 12px; }
    .legend-grid { display: flex; flex-wrap: wrap; gap: 6px; }
    .legend-chip { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--legend-color, #334155); background: color-mix(in srgb, var(--legend-color, #64748b) 15%, #0b141d); border-radius: 6px; padding: 5px 7px; font-size: 12px; }
    .legend-chip i { width: 8px; height: 8px; border-radius: 2px; background: var(--legend-color, #64748b); display: inline-block; }
    .route-node-glow, .map-card.route-selected { outline: 2px solid #fbbf24; box-shadow: 0 0 0 1px rgba(251, 191, 36, 0.6), 0 0 22px rgba(245, 158, 11, 0.55); }
    .route-edge-glow { color: #facc15; text-shadow: 0 0 10px rgba(245, 158, 11, 0.65); }
    .semantic-active-blocker { border-color: #ef4444; }
    .semantic-verified-or-evidence-linked { border-color: #22c55e; }
    .semantic-open-or-worker-active { border-color: #38bdf8; }
    .semantic-returned-imported-or-waiting-controller { border-color: #f59e0b; }
    .semantic-review-unclear { border-color: #a78bfa; }
    .semantic-review-partial { border-color: #f97316; }
    #graph-mount canvas { opacity: 0.08; }
    #graph-mount > svg:not(.card-edge-layer) { opacity: 0.08; }
    .card-overlay { position: relative; min-width: 100%; min-height: 100%; pointer-events: none; z-index: 20; }
    .card-edge-layer { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; pointer-events: none; }
    .lane-rail { position: absolute; top: 48px; bottom: 28px; border-left: 2px solid var(--lane-color, rgba(148, 163, 184, 0.2)); border-right: 1px solid rgba(148, 163, 184, 0.035); background: transparent; pointer-events: none; opacity: 0.58; }
    .lane-rail.route-lane { background: rgba(251, 191, 36, 0.025); border-color: rgba(251, 191, 36, 0.16); }
    .lane-label { position: absolute; top: 18px; transform: translateX(-50%); color: var(--lane-color, #64748b); font-size: 11px; text-align: center; letter-spacing: 0; white-space: nowrap; pointer-events: none; }
    .row-label { position: absolute; left: 18px; color: #475569; font-size: 11px; border-left: 2px solid rgba(244, 114, 182, 0.5); padding-left: 8px; pointer-events: none; }
    .map-card { position: absolute; box-sizing: border-box; width: 176px; height: 82px; border: 1px solid var(--node-color, #60a5fa); border-left: 4px solid var(--node-color, #60a5fa); border-radius: 7px; background: rgba(14, 18, 24, 0.96); color: #f8fafc; padding: 8px 10px; overflow: hidden; pointer-events: auto; text-align: left; box-shadow: inset 0 -2px 0 var(--lane-color, #64748b); }
    .map-card.route-dim { opacity: 0.32; filter: saturate(0.35) contrast(0.9); background: rgba(8, 11, 15, 0.68); border-color: #26303b; box-shadow: none; }
    .map-card.route-dim .card-type, .map-card.route-dim .card-state, .map-card.route-dim .card-role { color: #64748b; }
    .map-card.derived-card { border-style: dashed; background: rgba(8, 11, 15, 0.7); color: #94a3b8; }
    .map-card.derived-card.route-dim { opacity: 0.18; }
    .map-card.step-selected { border-color: #fde68a; box-shadow: 0 0 0 2px rgba(253, 230, 138, 0.65), 0 0 26px rgba(245, 158, 11, 0.58); }
    .card-title { font-size: 13px; font-weight: 700; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .card-type, .card-state, .card-role { margin-top: 5px; font-size: 11px; color: #cbd5e1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .card-meta-line { display: flex; align-items: center; gap: 5px; min-width: 0; }
    .card-lane-swatch { width: 8px; height: 8px; border-radius: 2px; background: var(--lane-color, #64748b); flex: 0 0 auto; }
    .edge-chip { position: absolute; min-width: 44px; max-width: 132px; height: 22px; padding: 0 8px; border-radius: 7px; display: inline-flex; align-items: center; justify-content: center; background: var(--edge-color, #facc15); color: #111827; font-size: 10px; line-height: 1; font-weight: 800; box-shadow: 0 0 12px color-mix(in srgb, var(--edge-color, #facc15) 70%, transparent); pointer-events: none; z-index: 4; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transform: translate(-50%, -50%); }
    .graph-status { position: absolute; inset: 18px; display: flex; align-items: center; justify-content: center; border: 1px dashed #475569; background: rgba(8, 12, 18, 0.88); color: #dbeafe; text-align: center; padding: 24px; }
    .graph-status h2 { margin: 0 0 8px; font-size: 16px; }
    .graph-status p { margin: 5px 0; font-size: 13px; }
    .fallback-nodes { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 12px; }
    .fallback-node { width: 190px; min-height: 72px; border: 1px solid var(--node-color, #60a5fa); border-left: 4px solid var(--node-color, #60a5fa); border-radius: 7px; padding: 8px 9px; background: #111827; color: #f8fafc; overflow: hidden; text-align: left; box-shadow: inset 0 -2px 0 var(--lane-color, #64748b); }
    .fallback-node.route-selected { border-color: #fbbf24; box-shadow: 0 0 14px rgba(245, 158, 11, 0.34); }
    details summary { cursor: pointer; color: #e5e7eb; font-weight: 700; }
    @media (max-width: 900px) { header { min-height: 92px; } main { grid-template-columns: 1fr; height: auto; overflow: visible; } aside { border-left: 0; border-top: 1px solid #374151; max-height: 70vh; } #graph-mount { height: 62vh; min-height: 420px; } }
  </style>
</head>
<body>
  <header>
    <h1>shujuan workbench: __ENDPOINT__</h1>
    <p><span data-i18n="readOnlyProjection">只读投影导出</span> <span data-i18n="routeOverlay">路线覆盖</span>: <code id="overlay-version">pending</code>. <span data-i18n="layoutLabel">布局</span>: __LAYOUT__.</p>
    <div class="top-legend" id="top-legend" aria-label="Workbench legend" data-i18n-aria-label="workbenchLegend"></div>
  </header>
  <main>
    <div id="graph-mount" data-visual-feature-schema="wb_lane_visual_feature_boundary.v1" data-node-rendering="box-card" data-g6-rendered="pending" data-g6-node-count="0" data-g6-edge-count="0" data-g6-canvas-count="0" data-g6-error="" aria-label="AntV G6 projection canvas" data-i18n-aria-label="graphCanvas"></div>
    <aside>
      <div class="toolbar" aria-label="Projection controls" data-i18n-aria-label="projectionControls">
        <label class="language-field"><span data-i18n="displayLanguage">显示语言</span><select id="language-select" title="Display language" aria-label="Display language" data-i18n-title="displayLanguage" data-i18n-aria-label="displayLanguage"><option value="zh">中文</option><option value="en">English</option></select></label>
        <select id="route-filter" title="Route filter" aria-label="Route filter" data-i18n-title="routeFilter" data-i18n-aria-label="routeFilter"></select>
        <select id="view-filter" title="View filter" data-i18n-title="viewFilter" data-i18n-aria-label="viewFilter"></select>
        <input id="search-input" type="search" placeholder="搜索" aria-label="Search graph" data-i18n-placeholder="searchPlaceholder" data-i18n-aria-label="searchGraph">
        <label><span data-i18n="activeOnly">仅活跃</span><input id="attention-only" type="checkbox" checked></label>
        <button id="reset-filters" type="button" data-i18n="reset">重置</button>
      </div>
      <div class="filter-grid" aria-label="Composable filters" data-i18n-aria-label="composableFilters">
        <label><span data-i18n="lane">车道</span><select id="lane-filter"></select></label><label><span data-i18n="state">状态</span><select id="state-filter"></select></label>
        <label><span data-i18n="node">节点</span><select id="node-type-filter"></select></label><label><span data-i18n="edge">边</span><select id="edge-type-filter"></select></label>
        <label><span data-i18n="evidence">证据</span><select id="evidence-filter"></select></label><label><span data-i18n="review">复核</span><select id="review-filter"></select></label>
        <label><span data-i18n="owner">负责人</span><select id="ownership-filter"></select></label><label><span data-i18n="closeout">收口</span><select id="closeout-filter"></select></label>
        <label><span data-i18n="source">来源</span><select id="source-filter"></select></label>
      </div>
      <section class="panel" id="route-panel"><h2 data-i18n="flows">路线</h2><div class="route-grid" id="route-list"></div></section>
      <section class="panel" id="step-panel"><h2 data-i18n="steps">步骤</h2><div id="step-list" class="step-list"></div></section>
      <section class="panel" id="detail-panel"><h2 data-i18n="detail">详情</h2><p class="muted" data-i18n="selectNodeOrStep">请选择节点或路线步骤。</p></section>
      <section class="panel drawer" id="source-drawer"><h2 data-i18n="sourceDrawer">来源抽屉</h2><p class="muted" data-i18n="sourceDrawerEmpty">来源预览保留在这个只读产物中。</p></section>
      <section class="panel drawer" id="diff-preview"><h2 data-i18n="artifactDiffPreview">产物和差异预览</h2><p class="muted" data-i18n="artifactDiffPreviewEmpty">选择后这里会显示 change set 和 diff hunk 预览。</p></section>
      <section class="panel" id="legend-panel"><h2 data-i18n="legend">图例</h2><div id="legend-list" class="legend-grid"></div></section>
      <section class="panel" id="debug-panel"><details><summary data-i18n="rawJson">原始 JSON</summary><pre id="projection-json">__DATA__</pre></details></section>
    </aside>
  </main>
  <script id="projection-payload" type="application/json">__SCRIPT_DATA__</script>
  <script src="__G6_SCRIPT_SRC__"></script>
  <script>
    const payload = JSON.parse(document.getElementById('projection-payload').textContent);
    const overlay = payload.overlay || { schema_version: 'workbench_lane_overlay.fallback', legend: {}, flows: [], filters: { available: [], active: {} }, diagnostics: { raw_item_count: 0, visible_item_count: 0, overlay_node_count: 0, overlay_edge_count: 0, route_count: 0, render_errors: [] } };
    const visualContract = overlay.visual_feature_contract || (payload.workbench || {}).visual_feature_contract || {};
    const semanticPalette = {
      selected_route_reference_example: '#facc15',
      active_blocker: '#ef4444',
      verified_or_evidence_linked: '#22c55e',
      open_or_worker_active: '#38bdf8',
      returned_imported_or_waiting_controller: '#f59e0b',
      review_accept: '#22c55e',
      review_reject: '#ef4444',
      review_unclear: '#a78bfa',
      review_partial: '#f97316',
      controller_lane: '#f472b6',
      worker_lane: '#38bdf8',
      reviewer_lane: '#a78bfa',
      research_lane: '#14b8a6',
      writer_lane: '#eab308',
      provider_lane: '#94a3b8',
      summary_only_or_non_active: '#64748b',
      ...(overlay.semantic_highlight_palette || {}),
      ...((visualContract.highlight && visualContract.highlight.palette) || {})
    };
    const details = payload.detail_payloads || {};
    const languageSelect = document.getElementById('language-select');
    const routeFilter = document.getElementById('route-filter');
    const viewFilter = document.getElementById('view-filter');
    const searchInput = document.getElementById('search-input');
    const attentionOnly = document.getElementById('attention-only');
    const resetFilters = document.getElementById('reset-filters');
    const routeList = document.getElementById('route-list');
    const stepList = document.getElementById('step-list');
    const detailPanel = document.getElementById('detail-panel');
    const sourceDrawer = document.getElementById('source-drawer');
    const diffPreview = document.getElementById('diff-preview');
    const legendList = document.getElementById('legend-list');
    const topLegend = document.getElementById('top-legend');
    const graphMount = document.getElementById('graph-mount');
    const viewNames = Object.keys(payload.views || {});
    const workbenchDefaults = payload.workbench || {};
    const overlayDefaultFilters = ((overlay.filters || {}).active || {});
    const defaultViewCandidate = workbenchDefaults.default_view || overlayDefaultFilters.view;
    const defaultView = viewNames.includes(defaultViewCandidate) ? defaultViewCandidate : (viewNames.includes('attention') ? 'attention' : (viewNames[0] || 'full'));
    const defaultRouteId = workbenchDefaults.default_route || overlay.default_flow_id || 'attention_route';
    const defaultActiveOnly = Boolean(workbenchDefaults.default_active_only ?? overlayDefaultFilters.active_only ?? true);
    const filterControls = {
      lane_role: document.getElementById('lane-filter'), lane_lifecycle: document.getElementById('state-filter'),
      node_type: document.getElementById('node-type-filter'), edge_type: document.getElementById('edge-type-filter'),
      evidence_type: document.getElementById('evidence-filter'), review_result: document.getElementById('review-filter'),
      ownership: document.getElementById('ownership-filter'), closeout_gate: document.getElementById('closeout-filter'),
      source: document.getElementById('source-filter')
    };
    let selectedView = defaultView;
    let selectedRouteId = defaultRouteId;
    let selectedNodeId = null;
    let selectedStepIndex = null;
    let graph = null;
    let displayLanguage = ((payload.workbench || {}).display_language || 'zh') === 'en' ? 'en' : 'zh';
    attentionOnly.checked = defaultActiveOnly;
    document.getElementById('overlay-version').textContent = overlay.schema_version || 'none';

    function escapeText(value) { return String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }
    function shortLabel(value, max = 24) { const text = String(value ?? ''); return text.length > max ? `${text.slice(0, max - 1)}...` : text; }
    const DISPLAY_TEXT = {
      zh: {
        readOnlyProjection: '只读投影导出。', routeOverlay: '路线覆盖', layoutLabel: '布局', displayLanguage: '显示语言',
        workbenchLegend: '工作台图例', graphCanvas: 'AntV G6 投影画布', projectionControls: '投影控制', composableFilters: '组合过滤器', routeFilter: '路线过滤器', viewFilter: '视图过滤器', searchGraph: '搜索图',
        searchPlaceholder: '搜索', activeOnly: '仅活跃', reset: '重置',
        lane: '车道', state: '状态', node: '节点', edge: '边', evidence: '证据', review: '复核', owner: '负责人', closeout: '收口', source: '来源',
        flows: '路线', steps: '步骤', detail: '详情', legend: '图例', rawJson: '原始 JSON',
        sourceDrawer: '来源抽屉', sourceDrawerEmpty: '来源预览保留在这个只读产物中。',
        artifactDiffPreview: '产物和差异预览', artifactDiffPreviewEmpty: '选择后这里会显示 change set 和 diff hunk 预览。',
        selectNodeOrStep: '请选择节点或路线步骤。', noEmbeddedDetail: '这个选择没有嵌入详情载荷。',
        noFoldedSourceClasses: '没有折叠来源类别', noRawSourcePreview: '没有嵌入原始来源预览。',
        noChangeSetPreview: '没有 change set 或 diff hunk 预览。', noNumberedSteps: '没有匹配当前路线和过滤器的编号步骤。',
        selectedRoute: '选中路线', activeBlocker: '活跃阻塞', verifiedEvidence: '已验证/有证据', openWorkerActive: '打开/实施中',
        returnedWaitingController: '已返回/等待控制器', reviewUnclear: '复核不明确',
        noGraphNodes: '没有图节点', noGraphNodesDetail: '没有可见图节点匹配当前路线、过滤器、搜索和仅活跃设置。',
        g6ReadableFallback: '导出可读，但 G6 画布没有生成已挂载图。', g6RenderFailed: 'G6 渲染失败',
        filters: '过滤器', resetFilters: '重置过滤器', resetRecovery: '重置过滤器或切换到全部。',
        routeEdge: '路线边', nodes: '节点', edges: '边',
        activeRoute: '当前路线', context: '上下文', evidenceReview: '证据和复核',
        projectionPayload: '投影载荷', rawCount: '原始', visibleCount: '可见', contextRoute: '上下文路线',
        nonRouteContext: '非路线上下文', available: '可用', filterMetadata: '过滤元数据',
        routeCountScope: '路线可见节点/边', emptyRoute: '空路线', emptyRouteDetail: '当前模式和筛选下，这条路线没有数据库派生节点。',
        noDbFacts: '没有数据库事实', noDbFactsDetail: '当前投影没有从数据库读到可渲染事实。',
        activeOnlySuppression: '仅活跃筛选隐藏了节点', activeOnlySuppressionDetail: '当前视图有非活跃或证据/历史节点；关闭仅活跃或重置可显示。',
        searchFilterSuppression: '搜索隐藏了节点', searchFilterSuppressionDetail: '当前搜索词隐藏了原本可见的图节点。',
        filterSuppression: '筛选隐藏了节点', filterSuppressionDetail: '当前组合筛选隐藏了原本可见的图节点。',
      },
      en: {
        readOnlyProjection: 'Read-only projection export.', routeOverlay: 'Route overlay', layoutLabel: 'Layout', displayLanguage: 'Display language',
        workbenchLegend: 'Workbench legend', graphCanvas: 'AntV G6 projection canvas', projectionControls: 'Projection controls', composableFilters: 'Composable filters', routeFilter: 'Route filter', viewFilter: 'View filter', searchGraph: 'Search graph',
        searchPlaceholder: 'Search', activeOnly: 'Active', reset: 'Reset',
        lane: 'Lane', state: 'State', node: 'Node', edge: 'Edge', evidence: 'Evidence', review: 'Review', owner: 'Owner', closeout: 'Closeout', source: 'Source',
        flows: 'Flows', steps: 'Steps', detail: 'Detail', legend: 'Legend', rawJson: 'Raw JSON',
        sourceDrawer: 'Source Drawer', sourceDrawerEmpty: 'Source previews stay embedded in this read-only artifact.',
        artifactDiffPreview: 'Artifact/Diff Preview', artifactDiffPreviewEmpty: 'Change set and diff hunk previews appear here when selected.',
        selectNodeOrStep: 'Select a node or route step.', noEmbeddedDetail: 'No embedded detail payload for this selection.',
        noFoldedSourceClasses: 'No folded source classes', noRawSourcePreview: 'No raw source preview embedded.',
        noChangeSetPreview: 'No change set or diff hunk preview embedded.', noNumberedSteps: 'No numbered steps match this route and filters.',
        selectedRoute: 'Selected route', activeBlocker: 'Active blocker', verifiedEvidence: 'Verified/evidence', openWorkerActive: 'Open/worker-active',
        returnedWaitingController: 'Returned/waiting controller', reviewUnclear: 'Review unclear',
        noGraphNodes: 'No graph nodes', noGraphNodesDetail: 'No visible graph nodes match the selected route, filters, search, and active-only setting.',
        g6ReadableFallback: 'The export is readable, but the G6 canvas did not produce a mounted graph.', g6RenderFailed: 'G6 render failed',
        filters: 'filters', resetFilters: 'Reset filters', resetRecovery: 'Reset filters or switch to All.',
        routeEdge: 'Route edge', nodes: 'nodes', edges: 'edges',
        activeRoute: 'Active route', context: 'Context', evidenceReview: 'Evidence + review',
        projectionPayload: 'Projection payload', rawCount: 'raw', visibleCount: 'visible', contextRoute: 'context route',
        nonRouteContext: 'non-route context', available: 'available', filterMetadata: 'filter metadata',
        routeCountScope: 'route visible nodes/edges', emptyRoute: 'empty route', emptyRouteDetail: 'This route has no DB-derived nodes in the current mode and filters.',
        noDbFacts: 'No DB facts', noDbFactsDetail: 'The current projection did not read any renderable facts from the database.',
        activeOnlySuppression: 'Active-only hid nodes', activeOnlySuppressionDetail: 'This view has non-active history or evidence nodes; turn off Active or reset to show them.',
        searchFilterSuppression: 'Search hid nodes', searchFilterSuppressionDetail: 'The current search term hid graph nodes that otherwise match.',
        filterSuppression: 'Filters hid nodes', filterSuppressionDetail: 'The current filter combination hid graph nodes that otherwise match.',
      }
    };
    const CANONICAL_DISPLAY = {
      zh: {
        all: '全部', hidden: '隐藏来源', detail: '详情引用', preview: '来源预览',
        attention: '注意', execution: '执行', discussions: '讨论', audit: '审计', full: '完整', all_view: '全部视图',
        task: '任务', acceptance_check: '验收检查', evidence: '证据', audit_finding: '审计发现', scope_contract: '范围契约', workbench_context: '工作台上下文', edge_endpoint: '边端点', node: '节点', source: '来源',
        worker_lane: '实施车道', controller_lane: '控制车道', reviewer_lane: '复核车道',
        open: '打开', active: '活跃', blocked: '阻塞', verified: '已验证', closed: '已关闭', warning: '警告', blocking: '阻塞',
        assigned: '已分配', ambiguous: '不明确',
        DECOMPOSES_TO: '拆分为', VALIDATES: '验证', VALIDATED_BY: '由此验证', APPLIES_TO: '适用于', CLOSES: '关闭', BLOCKS: '阻塞', HAS_IMPACT_FACT: '影响事实', HAS_IMPACT_ARTIFACT: '影响产物',
        endpoint: '方向', scope: '范围', work: '实施', review: '复核证据', closeout: '收口',
      },
      en: {
        all: 'All', hidden: 'Hidden sources', detail: 'Detail ref', preview: 'Source preview',
        attention: 'Attention', execution: 'Execution', discussions: 'Discussions', audit: 'Audit', full: 'Full', all_view: 'All views',
        task: 'Task', acceptance_check: 'Acceptance check', evidence: 'Evidence', audit_finding: 'Audit finding', scope_contract: 'Scope contract', workbench_context: 'Workbench context', edge_endpoint: 'Edge endpoint', node: 'Node', source: 'Source',
        worker_lane: 'Worker lane', controller_lane: 'Controller lane', reviewer_lane: 'Reviewer lane',
        open: 'Open', active: 'Active', blocked: 'Blocked', verified: 'Verified', closed: 'Closed', warning: 'Warning', blocking: 'Blocking',
        assigned: 'Assigned', ambiguous: 'Ambiguous',
        DECOMPOSES_TO: 'DECOMP', VALIDATES: 'VALIDATES', VALIDATED_BY: 'VAL_BY', APPLIES_TO: 'APPLIES', CLOSES: 'CLOSES', BLOCKS: 'BLOCKS', HAS_IMPACT_FACT: 'IMPACT', HAS_IMPACT_ARTIFACT: 'IMPACT',
        endpoint: 'Endpoint', scope: 'Scope', work: 'Work lane', review: 'Review + evidence', closeout: 'Closeout',
      }
    };
    const ALL_FILTER_LABEL_KEYS = {
      lane_role: { zh: '全部车道', en: 'All lanes' },
      lane_lifecycle: { zh: '全部状态', en: 'All states' },
      node_type: { zh: '全部节点', en: 'All nodes' },
      edge_type: { zh: '全部边', en: 'All edges' },
      evidence_type: { zh: '全部证据', en: 'All evidence' },
      review_result: { zh: '全部复核', en: 'All reviews' },
      ownership: { zh: '全部负责人', en: 'All owners' },
      closeout_gate: { zh: '全部收口门', en: 'All gates' },
    };
    function t(key) { return (DISPLAY_TEXT[displayLanguage] || DISPLAY_TEXT.zh)[key] || DISPLAY_TEXT.en[key] || key; }
    function canonicalText(value, category) {
      const key = String(value || '');
      if (!key) return '';
      const table = CANONICAL_DISPLAY[displayLanguage] || CANONICAL_DISPLAY.zh;
      return table[key] || table[`${category || ''}:${key}`] || (displayLanguage === 'en' ? key.replace(/_/g, ' ') : key);
    }
    function localizedEntry(entry, category) {
      if (!entry) return '';
      const value = entry.value ?? entry.id ?? '';
      if (displayLanguage === 'zh') return entry.label_zh || canonicalText(value, category) || entry.label_en || String(value);
      return entry.label_en || canonicalText(value, category) || entry.label_zh || String(value);
    }
    function localizedPair(enValue, zhValue, canonicalValue, category) {
      if (displayLanguage === 'zh') return zhValue || canonicalText(canonicalValue || enValue, category) || enValue || '';
      return enValue || canonicalText(canonicalValue || zhValue, category) || zhValue || '';
    }
    function setStaticLanguage() {
      document.documentElement.lang = displayLanguage === 'zh' ? 'zh-CN' : 'en';
      document.querySelectorAll('[data-i18n]').forEach((node) => { node.textContent = t(node.getAttribute('data-i18n')); });
      document.querySelectorAll('[data-i18n-placeholder]').forEach((node) => { node.setAttribute('placeholder', t(node.getAttribute('data-i18n-placeholder'))); });
      document.querySelectorAll('[data-i18n-title]').forEach((node) => { node.setAttribute('title', t(node.getAttribute('data-i18n-title'))); });
      document.querySelectorAll('[data-i18n-aria-label]').forEach((node) => { node.setAttribute('aria-label', t(node.getAttribute('data-i18n-aria-label'))); });
      if (languageSelect) languageSelect.value = displayLanguage;
    }
    function renderViewControls() {
      viewFilter.innerHTML = '';
      viewNames.forEach((name) => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = canonicalText(name === 'all' ? 'all_view' : name, 'view');
        option.selected = name === selectedView;
        viewFilter.appendChild(option);
      });
    }
    function cardFields(node, item) {
      const filters = item.filter_metadata || {};
      const title = node.label || item.label || item.summary || item.id || node.id;
      const typeLine = localizedPair(item.kind || node.type || 'node', item.kind_label_zh || filters.node_type_label_zh || '', item.kind || node.type || 'node', 'node_type');
      const lane = canonicalText(filters.lane_role || item.lane_role || '', 'lane_role');
      const state = canonicalText(filters.lane_lifecycle || item.lifecycle_state || '', 'lane_lifecycle');
      const gate = canonicalText(filters.closeout_gate || '', 'closeout_gate');
      const owner = canonicalText(filters.ownership || '', 'ownership');
      return {
        title: shortLabel(title, 30),
        typeLine,
        laneLine: [lane, state].filter(Boolean).join(' · '),
        roleLine: [gate, owner].filter(Boolean).join(' · '),
      };
    }
    function cardLabel(fields) {
      return [fields.title, fields.typeLine, fields.laneLine, fields.roleLine].filter(Boolean).join('\n');
    }
    function resolveG6GraphConstructor() { return (window.G6 || {}).Graph || null; }
    function destroyGraph() { if (graph && !graph.destroyed) graph.destroy(); graph = null; }
    function filterOptions(id) { const found = ((overlay.filters || {}).available || []).find((item) => item.id === id); return found ? (found.options || []) : []; }
    function fillSelect(select, id) {
      const previous = select.value;
      select.innerHTML = '';
      const all = document.createElement('option');
      all.value = '';
      all.textContent = (ALL_FILTER_LABEL_KEYS[id] || {})[displayLanguage] || canonicalText('all');
      select.appendChild(all);
      filterOptions(id).forEach((option) => {
        const node = document.createElement('option');
        node.value = String(option.value);
        node.textContent = localizedEntry(option, id);
        select.appendChild(node);
      });
      select.value = Array.from(select.options).some((option) => option.value === previous) ? previous : '';
    }
    function renderFilterControls() {
      fillSelect(filterControls.lane_role, 'lane_role');
      fillSelect(filterControls.lane_lifecycle, 'lane_lifecycle');
      fillSelect(filterControls.node_type, 'node_type');
      fillSelect(filterControls.edge_type, 'edge_type');
      fillSelect(filterControls.evidence_type, 'evidence_type');
      fillSelect(filterControls.review_result, 'review_result');
      fillSelect(filterControls.ownership, 'ownership');
      fillSelect(filterControls.closeout_gate, 'closeout_gate');
      const previous = filterControls.source.value;
      filterControls.source.innerHTML = [
        ['', displayLanguage === 'zh' ? '全部来源' : 'All sources'],
        ['hidden', canonicalText('hidden', 'source')],
        ['detail', canonicalText('detail', 'source')],
        ['preview', canonicalText('preview', 'source')],
      ].map(([value, label]) => `<option value="${escapeText(value)}">${escapeText(label)}</option>`).join('');
      filterControls.source.value = Array.from(filterControls.source.options).some((option) => option.value === previous) ? previous : '';
    }

    function allProjectionItems() { const views = payload.views || {}; return selectedView === 'all' ? Object.values(views).flatMap((view) => view.items || []) : ((views[selectedView] || {}).items || []); }
    function routeById(routeId = selectedRouteId) { return (overlay.flows || []).find((route) => route.id === routeId) || (overlay.flows || [])[0] || { id: 'attention_route', label_en: 'Attention Route', label_zh: '当前注意路线', node_ids: [], edge_ids: [], steps: [] }; }
    function routeLabel(route) { return localizedPair(route.label_en || route.id, route.label_zh || route.id, route.id, 'route'); }
    function selectedRouteNodeIds() { return new Set((routeById().node_ids || []).map(String)); }
    function selectedRouteEdgeIds() { return new Set((routeById().edge_ids || []).map(String)); }
    function routeOrderMap() { const map = new Map(); (routeById().node_ids || []).map(String).forEach((id, index) => map.set(id, index)); return map; }
    function edgeLegendLabel(edgeType) {
      const edgeTypes = (((overlay.legend || {}).edge_types) || []);
      const found = edgeTypes.find((entry) => String(entry.value) === String(edgeType));
      return found ? localizedEntry(found, 'edge_type') : '';
    }
    function compactEdgeType(edgeType) {
      const text = String(edgeType || 'EDGE');
      const aliases = { DECOMPOSES_TO: 'DECOMP', VALIDATED_BY: 'VAL_BY', HAS_IMPACT_FACT: 'IMPACT', HAS_IMPACT_ARTIFACT: 'IMPACT' };
      return aliases[text] || text.replace(/_/g, ' ');
    }
    function edgeChipLabel(edge) {
      const edgeType = (edge.data || {}).type || (edge.data || {}).label || 'EDGE';
      if (displayLanguage === 'zh') return (edge.data || {}).type_label_zh || edgeLegendLabel(edgeType) || canonicalText(edgeType, 'edge_type');
      return compactEdgeType(edgeType);
    }
    function semanticSlotForState(stateValue, gateValue, reviewValue, activeValue, kindValue) {
      const state = String(stateValue || '').toLowerCase();
      const gate = String(gateValue || '').toLowerCase();
      const review = String(reviewValue || '').toLowerCase();
      const active = String(activeValue || '').toLowerCase();
      const kind = String(kindValue || '').toLowerCase();
      if (gate === 'blocking' || state === 'blocked' || state === 'needs_user_decision') return 'active_blocker';
      if (review === 'accept') return 'review_accept';
      if (review === 'reject') return 'review_reject';
      if (review === 'unclear') return 'review_unclear';
      if (review === 'partial') return 'review_partial';
      if (['verified', 'reviewed', 'closed_by_controller', 'closed', 'resolved'].includes(state) || kind === 'evidence') return 'verified_or_evidence_linked';
      if (['returned', 'imported', 'packeted'].includes(state)) return 'returned_imported_or_waiting_controller';
      if (active === 'non_active' || ['deferred', 'summary_only', 'consumed', 'superseded', 'invalidated'].includes(state)) return 'summary_only_or_non_active';
      return 'open_or_worker_active';
    }
    function semanticColorForItem(item) {
      const filters = (item || {}).filter_metadata || {};
      const visual = (item || {}).visual || {};
      const slot = semanticSlotForState(filters.lane_lifecycle || item.lifecycle_state || visual.state, filters.closeout_gate, filters.review_result, filters.active_state, item.kind);
      return semanticPalette[slot] || semanticPalette.open_or_worker_active || '#38bdf8';
    }
    function laneColor(laneValue) {
      const lane = String(laneValue || '').toLowerCase();
      if (semanticPalette[lane]) return semanticPalette[lane];
      if (lane.includes('controller')) return semanticPalette.controller_lane || '#f472b6';
      if (lane.includes('worker')) return semanticPalette.worker_lane || '#38bdf8';
      if (lane.includes('review')) return semanticPalette.reviewer_lane || '#a78bfa';
      if (lane.includes('research')) return semanticPalette.research_lane || '#14b8a6';
      if (lane.includes('writer')) return semanticPalette.writer_lane || '#eab308';
      if (lane.includes('provider')) return semanticPalette.provider_lane || '#94a3b8';
      return semanticPalette.summary_only_or_non_active || '#64748b';
    }
    function laneColorForItem(item) {
      const filters = (item || {}).filter_metadata || {};
      return laneColor(filters.lane_role || item.lane_role || '');
    }
    function statusBadge(label, color, kind = 'status') {
      if (!label) return '';
      return `<span class="badge status-badge" data-status-kind="${escapeText(kind)}" style="--badge-color:${escapeText(color)}">${escapeText(label)}</span>`;
    }
    function stepColor(step) {
      return semanticPalette[semanticSlotForState(step.state, step.closeout_gate, step.review_result, '', step.kind)] || semanticPalette.open_or_worker_active || '#38bdf8';
    }
    function legendColor(entry, groupName) {
      const value = String((entry || {}).value || '');
      if (groupName === 'lane_roles') return laneColor(value);
      if (groupName === 'states') return semanticPalette[semanticSlotForState(value, value === 'blocked' ? 'blocking' : '', '', value === 'summary_only' ? 'non_active' : '', '')] || '#64748b';
      if (groupName === 'edge_types') return semanticPalette.open_or_worker_active || '#38bdf8';
      if (groupName === 'route_steps') return semanticPalette.selected_route_reference_example || '#facc15';
      return semanticPalette.summary_only_or_non_active || '#64748b';
    }
    function routeColor(route) {
      const id = String((route || {}).id || '');
      if (id.includes('blocked')) return semanticPalette.active_blocker || '#ef4444';
      if (id.includes('evidence')) return semanticPalette.verified_or_evidence_linked || '#22c55e';
      if (id.includes('execution')) return semanticPalette.open_or_worker_active || '#38bdf8';
      if (id.includes('review')) return semanticPalette.review_unclear || '#a78bfa';
      if (id.includes('ownership')) return semanticPalette.returned_imported_or_waiting_controller || '#f59e0b';
      return semanticPalette.summary_only_or_non_active || '#64748b';
    }
    function itemMatchesFilter(item, options = {}) {
      const filters = item.filter_metadata || {};
      if (!options.ignoreActiveOnly && attentionOnly.checked && filters.active_state === 'non_active' && filters.closeout_gate !== 'blocking') return false;
      if (!options.ignoreControls) {
        if (filterControls.lane_role.value && filters.lane_role !== filterControls.lane_role.value) return false;
        if (filterControls.lane_lifecycle.value && filters.lane_lifecycle !== filterControls.lane_lifecycle.value) return false;
        if (filterControls.node_type.value && filters.node_type !== filterControls.node_type.value) return false;
        if (filterControls.edge_type.value && !(filters.edge_types || []).includes(filterControls.edge_type.value)) return false;
        if (filterControls.evidence_type.value && filters.evidence_type !== filterControls.evidence_type.value) return false;
        if (filterControls.review_result.value && filters.review_result !== filterControls.review_result.value) return false;
        if (filterControls.ownership.value && filters.ownership !== filterControls.ownership.value) return false;
        if (filterControls.closeout_gate.value && filters.closeout_gate !== filterControls.closeout_gate.value) return false;
        if (filterControls.source.value === 'hidden' && !filters.has_hidden_sources) return false;
        if (filterControls.source.value === 'detail' && !filters.has_detail_ref) return false;
        if (filterControls.source.value === 'preview' && !filters.has_source_preview) return false;
      }
      const query = searchInput.value.trim().toLowerCase();
      if (!options.ignoreSearch && query && !JSON.stringify([filters.text, item.kind, item.label, item.summary, item.id, item.node_id]).toLowerCase().includes(query)) return false;
      return true;
    }
    function allItems() { return allProjectionItems().filter((item) => itemMatchesFilter(item)); }
    function activeFilterSummary() {
      const active = [`route=${selectedRouteId}`, `view=${selectedView}`];
      if (searchInput.value.trim()) active.push(`search=${searchInput.value.trim()}`);
      if (attentionOnly.checked) active.push('active_only=true');
      Object.entries(filterControls).forEach(([name, control]) => { if (control.value) active.push(`${name}=${control.value}`); });
      return active;
    }

    function graphData() {
      const items = allItems();
      const nodes = new Map();
      const edges = new Map();
      const routeNodes = selectedRouteNodeIds();
      const routeEdges = selectedRouteEdgeIds();
      const routeOrder = routeOrderMap();
      const selectedRouteIsEmpty = selectedRouteId !== 'all_route' && routeNodes.size === 0;
      if (selectedRouteIsEmpty) return { nodes: [], edges: [], emptyRoute: true, blankReason: 'empty_route' };
      const suppressDerivedContext = selectedRouteId !== 'all_route' && routeNodes.size > 0;
      function registerNode(node, item, visual) {
        if (!node || !node.id || nodes.has(node.id)) return;
        const itemVisual = visual || item.visual || {};
        const isRoute = routeNodes.has(String(node.id));
        const fields = cardFields(node, item);
        const selectedStep = selectedStepIndex && (routeById().steps || []).find((step) => step.index === selectedStepIndex && (step.route_node_ids || []).map(String).includes(String(node.id)));
        const semanticColor = itemVisual.color || semanticColorForItem(item);
        const itemLaneColor = laneColorForItem(item);
        nodes.set(node.id, { id: node.id, type: 'rect', data: { item, node, label: cardLabel(fields), cardTitle: fields.title, cardType: fields.typeLine, cardLane: fields.laneLine, cardRole: fields.roleLine, color: semanticColor, laneColor: itemLaneColor, stroke: semanticColor, size: [176, 82], nodeShape: 'info-card', routeSelected: isRoute, stepSelected: Boolean(selectedStep), routeOrder: routeOrder.has(String(node.id)) ? routeOrder.get(String(node.id)) : null, derived: Boolean(item.derived_visual) } });
      }
      function addDerivedNode(id, title, typeLine, laneLine, roleLine, color, columnHint) {
        if (nodes.has(id)) return;
        const item = {
          kind: 'workbench_context',
          kind_label_zh: '工作台上下文',
          node_id: id,
          label: title,
          summary: [typeLine, laneLine, roleLine].filter(Boolean).join(' · '),
          derived_visual: true,
          filter_metadata: { active_state: 'non_active', lane_role: laneLine || '', node_type: 'workbench_context', text: `${title} ${typeLine} ${laneLine} ${roleLine}` },
          visual: { color }
        };
        nodes.set(id, { id, type: 'rect', data: { item, node: { id, type: 'workbench_context', label: title }, label: [title, typeLine, laneLine, roleLine].filter(Boolean).join('\n'), cardTitle: shortLabel(title, 30), cardType: typeLine, cardLane: laneLine, cardRole: roleLine, color, laneColor: color, stroke: color, size: [176, 82], nodeShape: 'info-card', routeSelected: false, stepSelected: false, routeOrder: null, derived: true, columnHint } });
      }
      function registerEdge(edge, item) {
        if (!edge || !edge.from_node_id || !edge.to_node_id) return;
        const edgeId = edge.id || `${edge.from_node_id}->${edge.to_node_id}`;
        registerNode({ id: edge.from_node_id, type: 'edge_endpoint', label: edge.from_node_id }, item, { color: '#64748b' });
        registerNode({ id: edge.to_node_id, type: 'edge_endpoint', label: edge.to_node_id }, item, { color: '#64748b' });
        edges.set(edgeId, { id: edgeId, source: edge.from_node_id, target: edge.to_node_id, data: { ...edge, label: edge.type || 'edge', color: semanticColorForItem(item), routeSelected: routeEdges.has(String(edgeId)) } });
      }
      items.forEach((item) => {
        const chain = item.visible_chain || [];
        chain.forEach((node) => registerNode(node, item));
        if (!chain.length && item.node_id) registerNode({ id: item.node_id, type: item.kind || 'projection_item', label: item.label || item.id || item.node_id, summary: item.summary || '' }, item);
        (item.visible_edges || []).forEach((edge) => registerEdge(edge, item));
      });
      const explicitGraph = payload.graph || payload.data || {};
      (payload.nodes || explicitGraph.nodes || []).forEach((node) => registerNode(node, { kind: node.type || 'node', node_id: node.id, label: node.label || node.id, visual: node.visual || {} }));
      (payload.edges || explicitGraph.edges || []).forEach((edge) => registerEdge({ id: edge.id, from_node_id: edge.from_node_id || edge.source, to_node_id: edge.to_node_id || edge.target, type: edge.type || edge.label, style: edge.style }, { kind: 'edge', node_id: edge.source || edge.from_node_id, visual: { color: '#64748b' } }));
      if (nodes.size > 0 && nodes.size < 10) {
        const diagnostics = overlay.diagnostics || {};
        const lanes = filterOptions('lane_role').map((option) => option.value).filter(Boolean);
        const gates = filterOptions('closeout_gate').map((option) => option.value).filter(Boolean);
        const nodeTypes = filterOptions('node_type').map((option) => option.value).filter(Boolean);
        addDerivedNode('derived_projection_payload', t('projectionPayload'), canonicalText('source', 'node_type'), `${t('rawCount')} ${diagnostics.raw_item_count || 0}`, `${t('visibleCount')} ${diagnostics.visible_item_count || 0}`, semanticPalette.open_or_worker_active || '#38bdf8', 0);
        lanes.slice(0, 3).forEach((lane, index) => addDerivedNode(`derived_lane_${lane}`, canonicalText(lane, 'lane_role'), canonicalText('worker_lane', 'lane_role'), canonicalText(lane, 'lane_role'), t('contextRoute'), [semanticPalette.worker_lane || '#38bdf8', semanticPalette.returned_imported_or_waiting_controller || '#f59e0b', semanticPalette.reviewer_lane || '#a78bfa'][index] || '#64748b', index + 1));
        nodeTypes.slice(0, 3).forEach((type, index) => addDerivedNode(`derived_type_${type}`, canonicalText(type, 'node_type'), canonicalText('node', 'node_type'), t('filterMetadata'), t('available'), [semanticPalette.controller_lane || '#f472b6', semanticPalette.verified_or_evidence_linked || '#22c55e', semanticPalette.review_unclear || '#a78bfa'][index] || '#64748b', index + 1));
        gates.slice(0, 2).forEach((gate, index) => addDerivedNode(`derived_gate_${gate}`, `${canonicalText(gate, 'closeout_gate')} ${canonicalText('closeout', 'column')}`, canonicalText('closeout', 'column'), canonicalText(gate, 'closeout_gate'), t('nonRouteContext'), gate === 'blocking' ? (semanticPalette.active_blocker || '#ef4444') : (semanticPalette.verified_or_evidence_linked || '#22c55e'), 3 + index));
      }
      const renderedNodes = suppressDerivedContext ? Array.from(nodes.values()).filter((node) => !node.data.derived) : Array.from(nodes.values());
      let blankReason = '';
      if (!renderedNodes.length) {
        const diagnostics = overlay.diagnostics || {};
        const projectionItems = allProjectionItems();
        const withoutActiveOnly = projectionItems.filter((item) => itemMatchesFilter(item, { ignoreActiveOnly: true }));
        const withoutSearch = projectionItems.filter((item) => itemMatchesFilter(item, { ignoreSearch: true }));
        if (!(diagnostics.raw_item_count || projectionItems.length)) blankReason = 'no_db_facts';
        else if (attentionOnly.checked && withoutActiveOnly.length > 0) blankReason = 'active_only_suppression';
        else if (searchInput.value.trim() && withoutSearch.length > 0) blankReason = 'search_filter_suppression';
        else if (projectionItems.length > 0) blankReason = 'filter_suppression';
      }
      return { nodes: renderedNodes, edges: Array.from(edges.values()), blankReason };
    }
    function graphSize() { const rect = graphMount.getBoundingClientRect(); const size = { width: Math.max(640, Math.floor(rect.width || 900)), height: Math.max(480, Math.floor(rect.height || 700)) }; graphMount.dataset.g6Width = String(size.width); graphMount.dataset.g6Height = String(size.height); return size; }
    const requestedLayout = __REQUESTED_LAYOUT__;
    function layoutOptions(name, data, size) {
      const nodeCount = Math.max(data.nodes.length, 1); const cols = Math.max(2, Math.ceil(Math.sqrt(nodeCount))); const rows = Math.max(2, Math.ceil(nodeCount / cols)); const normalized = String(name || '').replace(/-/g, '_').toLowerCase(); const common = { preventOverlap: true, nodeSize: 48 };
      if (normalized === 'endpoint_radial_chain' || normalized === 'radial') return { ...common, type: 'radial', unitRadius: Math.max(70, Math.min(size.width, size.height) / Math.max(3, Math.ceil(Math.sqrt(nodeCount)))), linkDistance: 90, nodeSpacing: 18 };
      if (normalized === 'force') return { ...common, type: 'force', linkDistance: 110, nodeStrength: -180, edgeStrength: 0.6 };
      if (normalized === 'circular') return { ...common, type: 'circular', radius: Math.max(160, Math.min(size.width, size.height) / 2.8) };
      return { ...common, type: 'grid', begin: [50, 60], cols, rows, width: Math.max(320, size.width - 120), height: Math.max(300, size.height - 140), condense: true };
    }
    function renderGraphFallback(data, title, detail) {
      destroyGraph();
      graphMount.dataset.g6Rendered = (title === t('noGraphNodes') || title === t('emptyRoute')) ? 'empty' : 'error';
      graphMount.dataset.g6NodeCount = String(data.nodes.length); graphMount.dataset.g6EdgeCount = String(data.edges.length); graphMount.dataset.g6CanvasCount = '0'; graphMount.dataset.semanticPalette = Object.keys(semanticPalette).join(','); if (detail) graphMount.dataset.g6Error = detail;
      const chips = data.nodes.slice(0, 12).map((node) => `<span class="fallback-node${(node.data || {}).routeSelected ? ' route-selected' : ''}" data-node-shape="info-card" style="--node-color:${escapeText((node.data || {}).color || '#60a5fa')};--lane-color:${escapeText((node.data || {}).laneColor || '#64748b')}"><span class="card-title">${escapeText((node.data || {}).cardTitle || node.id)}</span><span class="card-type">${escapeText((node.data || {}).cardType || '')}</span><span class="card-state">${escapeText((node.data || {}).cardLane || '')}</span><span class="card-role">${escapeText((node.data || {}).cardRole || '')}</span></span>`).join('');
      const diagnostics = overlay.diagnostics || {}; const activeFilters = activeFilterSummary().join(', ') || canonicalText('all');
      graphMount.innerHTML = `<div class="graph-status" role="status"><div><h2>${escapeText(title)}</h2><p>${escapeText(detail || t('g6ReadableFallback'))}</p><p>${escapeText(t('rawCount'))}=${diagnostics.raw_item_count || 0} ${escapeText(t('visibleCount'))}=${data.nodes.length} overlay=${diagnostics.overlay_node_count || 0}/${diagnostics.overlay_edge_count || 0} ${escapeText(t('layoutLabel'))}=${escapeText(requestedLayout)}</p><p>${escapeText(t('filters'))}: ${escapeText(activeFilters)}</p>${chips ? `<div class="fallback-nodes">${chips}</div>` : ''}<div><button type="button" onclick="window.__shujuanResetFilters && window.__shujuanResetFilters()">${escapeText(t('resetFilters'))}</button> <span class="muted">${escapeText((displayLanguage === 'zh' ? (diagnostics.blank_state || {}).recovery_zh : (diagnostics.blank_state || {}).recovery_en) || t('resetRecovery'))}</span></div></div></div>`;
    }
    function blankStateCopy(data) {
      const reason = data.emptyRoute ? 'empty_route' : (data.blankReason || 'filter_suppression');
      const copies = {
        no_db_facts: [t('noDbFacts'), t('noDbFactsDetail')],
        empty_route: [t('emptyRoute'), t('emptyRouteDetail')],
        active_only_suppression: [t('activeOnlySuppression'), t('activeOnlySuppressionDetail')],
        search_filter_suppression: [t('searchFilterSuppression'), t('searchFilterSuppressionDetail')],
        filter_suppression: [t('filterSuppression'), t('filterSuppressionDetail')],
      };
      return copies[reason] || [t('noGraphNodes'), t('noGraphNodesDetail')];
    }
    function renderCardOverlay(data, size) {
      const old = graphMount.querySelector('.card-overlay');
      if (old) old.remove();
      const overlayLayer = document.createElement('div');
      overlayLayer.className = 'card-overlay';
      overlayLayer.setAttribute('data-node-rendering', 'box-card');
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'card-edge-layer');
      overlayLayer.appendChild(svg);
      const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
      defs.innerHTML = '<marker id="route-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L8,3 z" fill="#facc15"></path></marker><marker id="dim-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L7,3 z" fill="#475569"></path></marker>';
      svg.appendChild(defs);
      const cardWidth = 176;
      const cardHeight = 82;
      const columnLabels = [
        { id: 'endpoint', label: canonicalText('endpoint', 'column'), color: semanticPalette.provider_lane || '#94a3b8' },
        { id: 'scope', label: canonicalText('scope', 'column'), color: semanticPalette.controller_lane || '#f472b6' },
        { id: 'work', label: canonicalText('work', 'column'), color: semanticPalette.worker_lane || '#38bdf8' },
        { id: 'review', label: canonicalText('review', 'column'), color: semanticPalette.reviewer_lane || '#a78bfa' },
        { id: 'closeout', label: canonicalText('closeout', 'column'), color: semanticPalette.verified_or_evidence_linked || '#22c55e' },
      ];
      const cardGapX = 48;
      const cardGapY = 24;
      const sidePad = cardWidth / 2 + 28;
      const contentWidth = Math.max(size.width, sidePad * 2 + cardWidth + (columnLabels.length - 1) * (cardWidth + cardGapX));
      const usable = Math.max(360, contentWidth - sidePad * 2 - cardWidth);
      const pitch = usable / Math.max(1, columnLabels.length - 1);
      const routeIds = (routeById().node_ids || []).map(String);
      const routeOrder = routeOrderMap();
      const routeColumns = new Set();
      const positions = new Map();
      function colX(col) { return Math.round(sidePad + Math.min(columnLabels.length - 1, Math.max(0, col)) * pitch); }
      function columnForNode(node) {
        if (node.data.routeOrder !== null && node.data.routeOrder !== undefined) {
          const maxOrder = Math.max(1, routeIds.length - 1);
          return Math.round((node.data.routeOrder / maxOrder) * (columnLabels.length - 1));
        }
        if (node.data.columnHint !== undefined && node.data.columnHint !== null) return Number(node.data.columnHint);
        const filters = ((node.data.item || {}).filter_metadata || {});
        const kind = String((node.data.item || {}).kind || (node.data.node || {}).type || '').toLowerCase();
        const lane = String(filters.lane_role || '').toLowerCase();
        if (kind.includes('endpoint')) return 0;
        if (kind.includes('scope')) return 1;
        if (kind.includes('task') || lane.includes('worker')) return 2;
        if (kind.includes('review') || kind.includes('audit') || lane.includes('review')) return 3;
        if (kind.includes('evidence') || kind.includes('artifact') || kind.includes('change')) return 3;
        if (kind.includes('check') || filters.closeout_gate) return 4;
        return 2;
      }
      const sortedNodes = [...data.nodes].sort((left, right) => {
        const leftOrder = left.data.routeOrder ?? 999;
        const rightOrder = right.data.routeOrder ?? 999;
        if (leftOrder !== rightOrder) return leftOrder - rightOrder;
        if (Boolean(left.data.derived) !== Boolean(right.data.derived)) return left.data.derived ? 1 : -1;
        return String(left.id).localeCompare(String(right.id));
      });
      const columnBuckets = new Map();
      sortedNodes.forEach((node) => {
        const col = Math.max(0, Math.min(columnLabels.length - 1, columnForNode(node)));
        if (node.data.routeSelected) routeColumns.add(col);
        if (!columnBuckets.has(col)) columnBuckets.set(col, []);
        columnBuckets.get(col).push(node);
      });
      const maxColumnRows = Math.max(1, ...Array.from(columnBuckets.values()).map((bucket) => bucket.length));
      const topPad = 86;
      const cardStepY = cardHeight + cardGapY;
      const contentHeight = Math.max(size.height, topPad + maxColumnRows * cardStepY + 48);
      overlayLayer.style.width = `${contentWidth}px`;
      overlayLayer.style.height = `${contentHeight}px`;
      svg.setAttribute('width', String(contentWidth));
      svg.setAttribute('height', String(contentHeight));
      svg.setAttribute('viewBox', `0 0 ${contentWidth} ${contentHeight}`);
      columnLabels.forEach((column, index) => {
        const left = Math.max(0, colX(index) - cardWidth / 2 - 14);
        const rail = document.createElement('div');
        rail.className = `lane-rail${routeColumns.has(index) ? ' route-lane' : ''}`;
        rail.style.left = `${left}px`;
        rail.style.width = `${Math.min(cardWidth + 28, contentWidth - left)}px`;
        rail.style.setProperty('--lane-color', column.color);
        overlayLayer.appendChild(rail);
        const label = document.createElement('div');
        label.className = 'lane-label';
        label.style.left = `${colX(index)}px`;
        label.style.setProperty('--lane-color', column.color);
        label.textContent = column.label;
        overlayLayer.appendChild(label);
      });
      [
        { text: t('activeRoute'), y: 56 },
        { text: t('context'), y: Math.max(56, topPad - 28) },
        { text: t('evidenceReview'), y: Math.max(56, contentHeight - 64) },
      ].forEach((row) => {
        const label = document.createElement('div');
        label.className = 'row-label';
        label.style.top = `${Math.max(54, row.y)}px`;
        label.textContent = row.text;
        overlayLayer.appendChild(label);
      });
      columnBuckets.forEach((bucket, col) => {
        bucket.forEach((node, slot) => {
          const left = Math.max(12, Math.min(contentWidth - cardWidth - 12, colX(col) - cardWidth / 2));
          const top = topPad + slot * cardStepY;
          positions.set(node.id, { x: left + cardWidth / 2, y: top + cardHeight / 2, left, top, col, row: slot });
        });
      });
      function edgePath(from, to) {
        const startX = from.x + (to.x >= from.x ? cardWidth / 2 - 6 : -cardWidth / 2 + 6);
        const startY = from.y;
        const endX = to.x + (to.x >= from.x ? -cardWidth / 2 + 6 : cardWidth / 2 - 6);
        const endY = to.y;
        const bend = Math.max(42, Math.abs(endX - startX) * 0.35);
        return `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`;
      }
      data.edges.forEach((edge) => {
        const from = positions.get(edge.source);
        const to = positions.get(edge.target);
        if (!from || !to) return;
        const isRoute = edge.data.routeSelected;
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', edgePath(from, to));
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', isRoute ? (semanticPalette.selected_route_reference_example || '#facc15') : (edge.data.color || semanticPalette.summary_only_or_non_active || '#64748b'));
        path.setAttribute('stroke-width', isRoute ? '3.5' : '1.5');
        path.setAttribute('stroke-linecap', 'round');
        path.setAttribute('opacity', isRoute ? '0.96' : '0.36');
        path.setAttribute('marker-end', isRoute ? 'url(#route-arrow)' : 'url(#dim-arrow)');
        if (isRoute) path.setAttribute('filter', 'drop-shadow(0 0 8px rgba(245,158,11,0.95))');
        svg.appendChild(path);
        if (isRoute) {
          const chip = document.createElement('span');
          chip.className = 'edge-chip';
          chip.textContent = edgeChipLabel(edge);
          chip.setAttribute('data-edge-type', String((edge.data || {}).type || 'EDGE'));
          chip.setAttribute('aria-label', `${t('routeEdge')} ${edgeChipLabel(edge)}`);
          chip.style.setProperty('--edge-color', semanticPalette.selected_route_reference_example || '#facc15');
          chip.style.left = `${Math.round((from.x + to.x) / 2)}px`;
          chip.style.top = `${Math.round((from.y + to.y) / 2)}px`;
          overlayLayer.appendChild(chip);
        }
      });
      routeColumns.forEach((col) => {
        const rail = overlayLayer.querySelectorAll('.lane-rail')[col];
        if (rail) rail.classList.add('route-lane');
      });
      sortedNodes.forEach((node) => {
        const position = positions.get(node.id);
        if (!position) return;
        const card = document.createElement('button');
        card.type = 'button';
        const hasRoute = routeIds.length > 0 && selectedRouteId !== 'all_route';
        card.className = `map-card${node.data.routeSelected ? ' route-selected' : ''}${node.data.stepSelected ? ' step-selected' : ''}${hasRoute && !node.data.routeSelected ? ' route-dim' : ''}${node.data.derived ? ' derived-card' : ''}`;
        card.setAttribute('data-node-id', node.id);
        card.setAttribute('data-node-shape', node.data.nodeShape || 'info-card');
        card.setAttribute('aria-label', `${node.data.cardTitle || node.id} ${node.data.cardType || ''}`);
        card.style.left = `${position.left}px`;
        card.style.top = `${position.top}px`;
        card.style.setProperty('--node-color', node.data.color || '#60a5fa');
        card.style.setProperty('--lane-color', node.data.laneColor || '#64748b');
        card.setAttribute('data-semantic-color', node.data.color || '#60a5fa');
        card.setAttribute('data-lane-color', node.data.laneColor || '#64748b');
        card.innerHTML = `<div class="card-title">${escapeText(node.data.cardTitle || node.id)}</div><div class="card-type">${escapeText(node.data.cardType || '')}</div><div class="card-state card-meta-line"><span class="card-lane-swatch" aria-hidden="true"></span><span>${escapeText(node.data.cardLane || '')}</span></div><div class="card-role">${escapeText(node.data.cardRole || '')}</div>`;
        card.addEventListener('click', () => renderDetail(node.id));
        overlayLayer.appendChild(card);
      });
      window.__shujuanRouteMapLayout = { cardWidth, cardHeight, cardGapX, cardGapY, contentWidth, contentHeight, positions: Array.from(positions.entries()).map(([id, pos]) => ({ id, ...pos })) };
      graphMount.appendChild(overlayLayer);
    }
    async function renderGraph() {
      const data = graphData(); const size = graphSize(); window.__shujuanGraphData = data; window.__shujuanGraphRenderError = null; renderRouteList(); renderStepList();
      if (!data.nodes.length) { const blank = blankStateCopy(data); renderGraphFallback(data, blank[0], blank[1]); renderRouteList(); renderStepList(); return; }
      try {
        destroyGraph(); graphMount.innerHTML = ''; graphMount.dataset.g6Rendered = 'dom-primary'; graphMount.dataset.g6NodeCount = String(data.nodes.length); graphMount.dataset.g6EdgeCount = String(data.edges.length); graphMount.dataset.g6CanvasCount = '1';
        renderCardOverlay(data, size); renderRouteList(); renderStepList(); return;
      } catch (domError) {
        window.__shujuanGraphRenderError = String(domError && domError.message ? domError.message : domError);
        console.error(domError);
      }
      const options = {
        container: graphMount, width: size.width, height: size.height, data, autoFit: 'center', layout: layoutOptions(requestedLayout, data, size),
        node: { style: { size: (d) => d.data.size, radius: 7, fill: '#111827', stroke: (d) => d.data.stroke, lineWidth: (d) => d.data.routeSelected ? 3 : 2, label: true, labelText: (d) => d.data.label, labelFill: '#f8fafc', labelFontSize: 10, labelPlacement: 'center', labelTextAlign: 'center', labelTextBaseline: 'middle', labelBackground: false, shadowColor: (d) => d.data.routeSelected ? '#f59e0b' : 'transparent', shadowBlur: (d) => d.data.routeSelected ? 18 : 0 } },
        edge: { style: { stroke: (d) => d.data.routeSelected ? (semanticPalette.selected_route_reference_example || '#facc15') : (d.data.color || (d.data.style === 'dashed' ? (semanticPalette.open_or_worker_active || '#38bdf8') : (semanticPalette.summary_only_or_non_active || '#94a3b8'))), lineWidth: (d) => d.data.routeSelected ? 4 : 2, lineDash: (d) => d.data.style === 'dashed' ? [5, 5] : undefined, endArrow: true, shadowColor: (d) => d.data.routeSelected ? '#f59e0b' : 'transparent', shadowBlur: (d) => d.data.routeSelected ? 12 : 0 } },
        behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'click-select']
      };
      try {
        graphMount.dataset.g6Rendered = 'pending'; graphMount.dataset.g6NodeCount = String(data.nodes.length); graphMount.dataset.g6EdgeCount = String(data.edges.length);
        const GraphCtor = resolveG6GraphConstructor(); if (!GraphCtor) throw new Error('AntV G6 global did not expose Graph; check the bundled or CDN script.');
        if (!graph) { graphMount.innerHTML = ''; /* Equivalent to new G6.Graph(options) when the UMD global is available. */ graph = new GraphCtor(options); } else graph.setOptions(options);
        window.__shujuanGraph = graph; await graph.render();
        if (graph && graph.on) graph.on('node:click', (event) => { const id = event && (event.target && event.target.id || event.item && event.item.id || event.id); if (id) renderDetail(id); });
        graphMount.dataset.g6Rendered = 'true'; graphMount.dataset.g6CanvasCount = String(graphMount.querySelectorAll('canvas, svg').length); renderCardOverlay(data, size); renderRouteList(); renderStepList();
      } catch (error) {
        const message = String(error && error.message ? error.message : error);
        window.__shujuanGraphRenderError = message; graphMount.dataset.g6Error = message; console.error(error);
        try {
          destroyGraph(); graphMount.innerHTML = ''; graphMount.dataset.g6Rendered = 'dom-fallback'; graphMount.dataset.g6NodeCount = String(data.nodes.length); graphMount.dataset.g6EdgeCount = String(data.edges.length); graphMount.dataset.g6CanvasCount = '1';
          renderCardOverlay(data, size); renderRouteList(); renderStepList();
        } catch (fallbackError) {
          renderGraphFallback(data, t('g6RenderFailed'), `${message}; DOM route-map fallback failed: ${String(fallbackError && fallbackError.message ? fallbackError.message : fallbackError)}`);
          renderRouteList(); renderStepList();
        }
      }
    }
    function selectedDetail() { if (!selectedNodeId) return null; return details[selectedNodeId] || null; }
    function findItemByNode(nodeId) { return allProjectionItems().find((item) => item.node_id === nodeId || (item.visible_chain || []).some((node) => node.id === nodeId)) || null; }
    function renderDetail(nodeId) {
      selectedNodeId = nodeId; const detail = selectedDetail(); const item = findItemByNode(nodeId); const filters = (item || {}).filter_metadata || {};
      const itemColor = item ? semanticColorForItem(item) : (semanticPalette.summary_only_or_non_active || '#64748b');
      const itemLaneColor = item ? laneColorForItem(item) : (semanticPalette.summary_only_or_non_active || '#64748b');
      const itemBadges = [
        statusBadge(canonicalText(filters.lane_role || '', 'lane_role'), itemLaneColor, 'lane_role'),
        statusBadge(canonicalText(filters.lane_lifecycle || '', 'lane_lifecycle'), itemColor, 'lane_lifecycle'),
        statusBadge(canonicalText(filters.closeout_gate || '', 'closeout_gate'), itemColor, 'closeout_gate'),
      ].join('');
      if (!detail) { detailPanel.innerHTML = item ? `<h2>${escapeText(t('detail'))}</h2><p><code>${escapeText(nodeId)}</code> ${escapeText(localizedPair(item.kind || '', item.kind_label_zh || '', item.kind || '', 'node_type'))}</p><p class="summary">${escapeText(item.summary || item.label || '')}</p><p>${itemBadges}</p><p class="muted">${escapeText(item.detail_ref || 'No detail_ref embedded.')}</p>` : `<h2>${escapeText(t('detail'))}</h2><p class="muted">${escapeText(t('noEmbeddedDetail'))}</p>`; return; }
      const classes = (detail.hidden_source_edge_classes || []).map((name) => statusBadge(name, itemColor, 'hidden_source')).join('');
      const records = (detail.evidence_records || []).map((record) => `<li>${escapeText(record.record_type)} <code>${escapeText(record.ref || '')}</code><pre>${escapeText((record.preview || {}).text || '')}</pre></li>`).join('');
      const messages = (((detail.discussion || {}).messages) || []).map((msg) => `<li>${escapeText(msg.actor)}: ${escapeText(msg.content)}</li>`).join('');
      const hunks = ((detail.change_set || {}).diff_hunks || []); const hunk = detail.diff_hunk ? [detail.diff_hunk] : hunks;
      const diffBody = hunk.map((entry) => `<p><code>${escapeText(entry.path_new || entry.path_old || entry.hunk_header)}</code></p><pre>${escapeText(((entry.new_text_preview || entry.context_text_preview || {}).text) || '')}</pre>`).join('');
      const patch = (((detail.change_set || {}).patch_preview || {}).text) || '';
      detailPanel.innerHTML = `<h2>${escapeText(t('detail'))}</h2><p><code>${escapeText(detail.node.id)}</code> ${escapeText(localizedPair(detail.node.type || '', (item || {}).kind_label_zh || '', detail.node.type || '', 'node_type'))}</p><p class="summary">${escapeText(detail.node.summary || detail.node.label || '')}</p><p>${classes || `<span class="muted">${escapeText(t('noFoldedSourceClasses'))}</span>`}</p><p>${itemBadges}</p><p class="muted">${escapeText(detail.detail_contract)}</p>`;
      sourceDrawer.innerHTML = `<h2>${escapeText(t('sourceDrawer'))}</h2><ul class="source-list">${records || messages || `<li>${escapeText(t('noRawSourcePreview'))}</li>`}</ul>`;
      diffPreview.innerHTML = `<h2>${escapeText(t('artifactDiffPreview'))}</h2>${diffBody || (patch ? `<pre>${escapeText(patch)}</pre>` : `<p class="muted">${escapeText(t('noChangeSetPreview'))}</p>`)}`;
    }
    function renderRouteControls() { routeFilter.innerHTML = ''; (overlay.flows || []).forEach((route) => { const option = document.createElement('option'); option.value = route.id; option.textContent = routeLabel(route); option.selected = route.id === selectedRouteId; routeFilter.appendChild(option); }); }
    function renderRouteList() {
      routeList.innerHTML = (overlay.flows || []).map((route) => { const isEmpty = !(route.node_ids || []).length; return `<button type="button" class="route-button" data-route-id="${escapeText(route.id)}" data-empty-route="${isEmpty}" aria-disabled="${isEmpty}" aria-pressed="${route.id === selectedRouteId}" style="--route-color:${escapeText(route.id === selectedRouteId ? (semanticPalette.selected_route_reference_example || '#facc15') : routeColor(route))}"><strong>${escapeText(routeLabel(route))}</strong><span>${escapeText(t('routeCountScope'))}: ${escapeText(t('nodes'))}=${(route.node_ids || []).length} ${escapeText(t('edges'))}=${(route.edge_ids || []).length}${isEmpty ? ` · ${escapeText(t('emptyRoute'))}` : ''}</span></button>`; }).join('');
      routeList.querySelectorAll('button[data-route-id]').forEach((button) => button.addEventListener('click', () => { selectedRouteId = button.getAttribute('data-route-id'); routeFilter.value = selectedRouteId; selectedStepIndex = null; renderGraph(); }));
    }
    function renderStepList() {
      const route = routeById(); const steps = route.steps || [];
      stepList.innerHTML = steps.length ? steps.map((step) => `<button type="button" class="step-button" data-step-index="${step.index}" data-node-id="${escapeText(step.node_id || '')}" aria-pressed="${step.index === selectedStepIndex}" style="--step-color:${escapeText(step.index === selectedStepIndex ? (semanticPalette.selected_route_reference_example || '#facc15') : stepColor(step))}"><span class="step-number">${step.index}</span><span><span class="step-title">${escapeText(step.label || step.node_id || '')}</span><span class="step-meta">${escapeText(localizedPair(step.kind || '', step.kind_label_zh || '', step.kind || '', 'node_type'))} · ${escapeText(canonicalText(step.state || '', 'lane_lifecycle'))}</span></span></button>`).join('') : `<p class="muted">${escapeText(t('noNumberedSteps'))}</p>`;
      stepList.querySelectorAll('button[data-step-index]').forEach((button) => button.addEventListener('click', () => { selectedStepIndex = Number(button.getAttribute('data-step-index')); const nodeId = button.getAttribute('data-node-id'); if (nodeId) renderDetail(nodeId); renderGraph(); }));
    }
    function renderLegend() {
      const legend = overlay.legend || {};
      const groups = ['node_types', 'edge_types', 'lane_roles', 'states'];
      const fixed = groups.flatMap((groupName) => (legend[groupName] || []).slice(0, 16).map((entry) => ({ entry, groupName }))).slice(0, 64);
      legendList.innerHTML = fixed.map(({ entry, groupName }) => `<span class="legend-chip" data-legend-group="${escapeText(groupName)}" style="--legend-color:${escapeText(legendColor(entry, groupName))}"><i aria-hidden="true"></i><code>${escapeText(entry.value)}</code> ${escapeText(localizedEntry(entry, 'legend'))}</span>`).join('');
    }
    function renderTopLegend() {
      const items = [
        [semanticPalette.selected_route_reference_example || '#facc15', t('selectedRoute')],
        [semanticPalette.active_blocker || '#ef4444', t('activeBlocker')],
        [semanticPalette.verified_or_evidence_linked || '#22c55e', t('verifiedEvidence')],
        [semanticPalette.open_or_worker_active || '#38bdf8', t('openWorkerActive')],
        [semanticPalette.returned_imported_or_waiting_controller || '#f59e0b', t('returnedWaitingController')],
        [semanticPalette.review_unclear || '#a78bfa', t('reviewUnclear')],
        [semanticPalette.controller_lane || '#f472b6', canonicalText('controller_lane', 'lane_role')],
        [semanticPalette.worker_lane || '#38bdf8', canonicalText('worker_lane', 'lane_role')],
        [semanticPalette.reviewer_lane || '#a78bfa', canonicalText('reviewer_lane', 'lane_role')],
        [semanticPalette.research_lane || '#14b8a6', canonicalText('research_lane', 'lane_role')],
        [semanticPalette.writer_lane || '#eab308', canonicalText('writer_lane', 'lane_role')],
        [semanticPalette.provider_lane || '#94a3b8', canonicalText('provider_lane', 'lane_role')],
      ];
      topLegend.innerHTML = items.map(([color, label]) => `<span><i style="--swatch:${color}"></i>${escapeText(label)}</span>`).join('');
    }
    function resetAllFilters() { selectedRouteId = defaultRouteId; routeFilter.value = selectedRouteId; selectedView = defaultView; viewFilter.value = defaultView; searchInput.value = ''; attentionOnly.checked = defaultActiveOnly; selectedStepIndex = null; Object.values(filterControls).forEach((control) => { control.value = ''; }); renderGraph(); }
    function renderLanguageSurface() {
      setStaticLanguage();
      renderViewControls();
      renderFilterControls();
      renderRouteControls();
      renderRouteList();
      renderStepList();
      renderLegend();
      renderTopLegend();
      if (selectedNodeId) renderDetail(selectedNodeId);
    }
    window.__shujuanResetFilters = resetAllFilters;
    languageSelect.addEventListener('change', () => { displayLanguage = languageSelect.value === 'en' ? 'en' : 'zh'; window.__shujuanDisplayLanguage = displayLanguage; renderLanguageSurface(); renderGraph(); });
    routeFilter.addEventListener('change', () => { selectedRouteId = routeFilter.value; selectedStepIndex = null; renderGraph(); });
    viewFilter.addEventListener('change', () => { selectedView = viewFilter.value; renderGraph(); });
    searchInput.addEventListener('input', renderGraph); attentionOnly.addEventListener('change', renderGraph); resetFilters.addEventListener('click', resetAllFilters);
    Object.values(filterControls).forEach((control) => control.addEventListener('change', renderGraph));
    window.addEventListener('resize', () => renderGraph()); window.addEventListener('beforeunload', () => { if (graph && !graph.destroyed) graph.destroy(); });
    window.__shujuanDisplayLanguage = displayLanguage; renderLanguageSurface(); renderGraph();
    const first = graphData().nodes[0]; if (first) renderDetail(first.id);
  </script>
</body>
</html>
"""
    return (
        template
        .replace("__ENDPOINT__", endpoint)
        .replace("__LAYOUT__", html.escape(layout))
        .replace("__DATA__", data)
        .replace("__SCRIPT_DATA__", script_data)
        .replace("__G6_SCRIPT_SRC__", html.escape(g6_script_src))
        .replace("__REQUESTED_LAYOUT__", json.dumps(layout))
    )


render_workbench_html = _render_workbench_html_v2


def build_workbench_payload(
    conn: sqlite3.Connection,
    repo: Path,
    endpoint_name: str,
    *,
    view: str = "all",
    mode: str | None = None,
    include_consumed: bool = False,
    include_history: bool = False,
    limit: int = 50,
    g6_asset: dict[str, Any] | None = None,
    live_service: dict[str, Any] | None = None,
) -> dict[str, Any]:
    graph_projection_payload_fn = _require_dependency("graph_projection_payload")
    payload = graph_projection_payload_fn(
        conn,
        endpoint_name,
        view,
        mode=mode,
        include_consumed=include_consumed,
        include_history=include_history or bool(mode in {"history", "all"}),
        limit=limit,
    )
    payload = attach_workbench_details(conn, repo, payload)
    overlay = payload.get("overlay") or {}
    overlay_filters = (overlay.get("filters") or {}).get("active") or {}
    if mode is None:
        default_view = "attention"
        default_route = "attention_route"
        default_active_only = True
    else:
        default_view = str(overlay_filters.get("view") or mode)
        default_route = str(overlay.get("default_flow_id") or "attention_route")
        default_active_only = bool(overlay_filters.get("active_only", mode == "active"))
    visibility_policy = schema_visibility_policy()
    advanced_visibility = visibility_policy.pop("advanced_schema_visibility", [])
    include_advanced_schema_visibility = bool(mode in {"history", "all"} or include_history)
    visibility_policy["advanced_material_omitted"] = not include_advanced_schema_visibility
    visibility_policy["advanced_visibility_opt_in"] = "Use workbench history/all mode or schema roles --advanced for dormant, delegated, provider, discussion, and contracted history details."
    if include_advanced_schema_visibility:
        visibility_policy["advanced_schema_visibility"] = advanced_visibility
    payload["workbench"] = {
        "engine": "AntV G6",
        "package": "@antv/g6",
        "package_version": "5.1.1",
        "g6_asset": g6_asset or {"bundled": False},
        "default_view": default_view,
        "default_route": default_route,
        "default_active_only": default_active_only,
        "projection_mode": mode,
        "projection_modes": list(WORKBENCH_PROJECTION_MODES),
        "mode_counts": payload.get("mode_counts") or {},
        "visual_feature_contract": (payload.get("overlay") or {}).get("visual_feature_contract"),
        "semantic_highlight_palette": (payload.get("overlay") or {}).get("semantic_highlight_palette"),
        "default_surface_contract": {
            "first_screen": "current_governance_objects",
            "visible_object_classes": visibility_policy["default_visible_objects"],
            "advanced_opt_in_roles": visibility_policy["default_hidden_roles"],
            "support_opt_in_roles": visibility_policy["support_hidden_roles"],
            "contracted_history_policy": "hidden_from_default_layer_until_explicit_history_or_advanced_request",
            "closure_boundary": "read_only_projection_material_not_closure_evidence",
        },
        "read_only_controls": [
            "route_filter",
            "view_filter",
            "search",
            "active_only",
            "reset_filters",
            "lane_role_filter",
            "lifecycle_filter",
            "review_filter",
            "ownership_filter",
            "node_edge_filters",
            "evidence_filter",
            "closeout_source_filters",
            "numbered_steps",
            "detail_panel",
            "source_drawer",
            "artifact_diff_preview",
            "raw_json_debug",
        ],
        "db_write_path": False,
        "live_service": live_service or {"enabled": False},
    }
    payload["schema_visibility"] = visibility_policy
    return payload


def render_workbench_live_shell(
    *,
    endpoint: str,
    mode: str,
    limit: int,
    layout: str,
    poll_seconds: float,
    include_consumed: bool,
) -> str:
    endpoint_html = html.escape(endpoint)
    mode_options = "\n".join(
        f'<option value="{html.escape(name)}"{" selected" if name == mode else ""}>{html.escape(name)}</option>'
        for name in WORKBENCH_PROJECTION_MODES
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>shujuan live workbench: {endpoint_html}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Arial, sans-serif; background: #050607; color: #e5e7eb; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; background: #050607; }}
    header {{ min-height: 70px; padding: 10px 18px; border-bottom: 1px solid #1f2933; background: #08090d; display: grid; gap: 8px; }}
    h1 {{ margin: 0; font-size: 18px; }}
    .controls {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    select, input, button {{ min-height: 32px; border-radius: 6px; border: 1px solid #475569; background: #0b1118; color: #f8fafc; padding: 6px 9px; }}
    button {{ cursor: pointer; background: #18202a; }}
    code, .badge {{ background: #111827; border: 1px solid #263241; border-radius: 6px; padding: 3px 6px; }}
    .status {{ color: #b8c0cc; font-size: 12px; display: flex; gap: 8px; flex-wrap: wrap; }}
    iframe {{ display: block; width: 100%; height: calc(100vh - 102px); border: 0; background: #050607; }}
    @media (max-width: 900px) {{ iframe {{ height: calc(100vh - 148px); }} }}
  </style>
</head>
<body>
  <header>
    <div class="controls">
      <h1>shujuan live workbench: {endpoint_html}</h1>
      <label>mode <select id="mode-select">{mode_options}</select></label>
      <label>limit <input id="limit-input" type="number" min="1" max="500" value="{int(limit)}"></label>
      <button id="refresh-button" type="button">Refresh</button>
      <span class="badge">read-only</span>
      <span class="badge">poll {poll_seconds:g}s</span>
    </div>
    <div class="status" id="live-status">Waiting for DB-backed projection...</div>
  </header>
  <iframe id="workbench-frame" title="DB-backed shujuan workbench"></iframe>
  <script>
    const endpoint = {json.dumps(endpoint)};
    const layout = {json.dumps(layout)};
    const includeConsumed = {json.dumps(bool(include_consumed))};
    const pollMs = Math.max(500, Number({json.dumps(poll_seconds)}) * 1000);
    const modeSelect = document.getElementById('mode-select');
    const limitInput = document.getElementById('limit-input');
    const statusNode = document.getElementById('live-status');
    const frame = document.getElementById('workbench-frame');
    let lastProjectionHash = '';
    let manualFrameRefreshToken = 0;
    let refreshTimer = null;
    function query() {{
      const params = new URLSearchParams({{ mode: modeSelect.value, limit: limitInput.value, include_consumed: includeConsumed ? '1' : '0' }});
      return params;
    }}
    function countsText(payload) {{
      const counts = payload.mode_counts || {{}};
      return Object.keys(counts).sort().map((name) => `${{name}}=${{counts[name]}}`).join(' ');
    }}
    function stableProjectionSignature(payload) {{
      const withoutVolatileFields = JSON.parse(JSON.stringify(payload || {{}}));
      delete withoutVolatileFields.generated_at;
      return JSON.stringify({{
        layout,
        mode: modeSelect.value,
        limit: limitInput.value,
        include_consumed: includeConsumed,
        projection: withoutVolatileFields
      }});
    }}
    function stableHash(text) {{
      let hash = 2166136261;
      for (let index = 0; index < text.length; index += 1) {{
        hash ^= text.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }}
      return (hash >>> 0).toString(16);
    }}
    async function refreshProjection(forceFrame = false) {{
      const params = query();
      const response = await fetch(`/api/projection?${{params.toString()}}`, {{ cache: 'no-store' }});
      if (!response.ok) throw new Error(await response.text());
      const payload = await response.json();
      const signature = stableProjectionSignature(payload);
      statusNode.textContent = `mode=${{payload.mode || modeSelect.value}} view=${{payload.view}} generated=${{payload.generated_at}} ${{countsText(payload)}}`;
      window.__shujuanLiveProjection = payload;
      if (forceFrame || signature !== lastProjectionHash) {{
        lastProjectionHash = signature;
        const frameParams = query();
        frameParams.set('layout', layout);
        frameParams.set('projection_signature', stableHash(signature));
        if (forceFrame) frameParams.set('manual_refresh', String(++manualFrameRefreshToken));
        frame.src = `/frame?${{frameParams.toString()}}`;
      }}
    }}
    async function refreshWithStatus(forceFrame = false) {{
      try {{
        await refreshProjection(forceFrame);
      }} catch (error) {{
        statusNode.textContent = `live projection error: ${{error && error.message ? error.message : error}}`;
      }}
    }}
    function schedule() {{
      if (refreshTimer) clearInterval(refreshTimer);
      refreshTimer = setInterval(refreshWithStatus, pollMs);
    }}
    document.getElementById('refresh-button').addEventListener('click', () => refreshWithStatus(true));
    modeSelect.addEventListener('change', () => {{ lastProjectionHash = ''; refreshWithStatus(true); }});
    limitInput.addEventListener('change', () => {{ lastProjectionHash = ''; refreshWithStatus(true); }});
    refreshWithStatus();
    schedule();
  </script>
</body>
</html>
"""


def _close_quietly(conn: Any) -> None:
    close = getattr(conn, "close", None)
    if close:
        close()


def _parse_bool_flag(values: dict[str, list[str]], name: str, default: bool = False) -> bool:
    raw = (values.get(name) or [str(int(default))])[0].lower()
    return raw in {"1", "true", "yes", "on"}


def _parse_projection_mode(values: dict[str, list[str]], default: str) -> str:
    mode = (values.get("mode") or [default])[0]
    return mode if mode in WORKBENCH_PROJECTION_MODES else default


def _parse_projection_limit(values: dict[str, list[str]], default: int) -> int:
    limit_text = (values.get("limit") or [str(default)])[0]
    try:
        return max(1, min(500, int(limit_text)))
    except ValueError:
        return default


def _workbench_g6_source(repo: Path) -> Path | None:
    candidates = [
        repo / "node_modules" / "@antv" / "g6" / "dist" / "g6.min.js",
        Path(__file__).resolve().parents[2] / "node_modules" / "@antv" / "g6" / "dist" / "g6.min.js",
    ]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def cmd_workbench_export(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    connect_fn = _require_dependency("connect")
    resolve_endpoint_identifier_fn = _require_dependency("resolve_endpoint_identifier")
    print_json_fn = _require_dependency("print_json")
    relpath_fn = _require_dependency("relpath")
    conn = connect_fn(repo)
    endpoint_name = resolve_endpoint_identifier_fn(conn, repo, args.endpoint)
    out_path = Path(args.path)
    if not out_path.is_absolute():
        out_path = repo / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    g6_asset = ensure_workbench_g6_asset(repo, out_path) if args.format == "html" else {"bundled": False}
    payload = build_workbench_payload(
        conn,
        repo,
        endpoint_name,
        view=args.view,
        mode=args.mode,
        include_consumed=args.include_consumed,
        include_history=args.include_history,
        limit=args.limit,
        g6_asset=g6_asset,
    )
    if args.format == "json":
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    else:
        out_path.write_text(render_workbench_html(payload, layout=args.layout, g6_script_src=str(g6_asset.get("script_src") or "g6.min.js")), encoding="utf-8")
    print_json_fn(
        {
            "ok": True,
            "endpoint": endpoint_name,
            "path": relpath_fn(out_path, repo),
            "read_only": True,
            "view": args.view,
            "mode": args.mode,
            "format": args.format,
            "g6": g6_asset,
            "db_write_path": False,
        }
    )
    return 0


def _workbench_service_handler(args: argparse.Namespace, endpoint_name: str, g6_asset_path: Path | None) -> type[BaseHTTPRequestHandler]:
    repo = args.repo.resolve()
    connect_fn = _require_dependency("connect")

    class WorkbenchLiveHandler(BaseHTTPRequestHandler):
        server_version = "shujuan-workbench/1.0"

        def log_message(self, format: str, *values: Any) -> None:  # noqa: A003
            return

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _projection_payload(self, query: dict[str, list[str]]) -> dict[str, Any]:
            mode = _parse_projection_mode(query, args.mode)
            limit = _parse_projection_limit(query, args.limit)
            include_consumed = _parse_bool_flag(query, "include_consumed", bool(args.include_consumed))
            conn = connect_fn(repo)
            try:
                return build_workbench_payload(
                    conn,
                    repo,
                    endpoint_name,
                    view="all",
                    mode=mode,
                    include_consumed=include_consumed,
                    include_history=mode in {"history", "all"},
                    limit=limit,
                    g6_asset={"bundled": bool(g6_asset_path), "script_src": "/assets/g6.min.js" if g6_asset_path else "https://unpkg.com/@antv/g6@5.1.1/dist/g6.min.js"},
                    live_service={"enabled": True, "refresh": "poll", "poll_seconds": args.poll_seconds, "projection_endpoint": "/api/projection"},
                )
            finally:
                _close_quietly(conn)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path in {"/", "/workbench"}:
                    mode = _parse_projection_mode(query, args.mode)
                    limit = _parse_projection_limit(query, args.limit)
                    include_consumed = _parse_bool_flag(query, "include_consumed", bool(args.include_consumed))
                    layout = (query.get("layout") or [args.layout])[0]
                    body = render_workbench_live_shell(
                        endpoint=endpoint_name,
                        mode=mode,
                        limit=limit,
                        layout=layout,
                        poll_seconds=args.poll_seconds,
                        include_consumed=include_consumed,
                    ).encode("utf-8")
                    self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                    return
                if parsed.path == "/api/projection":
                    payload = self._projection_payload(query)
                    self._send(HTTPStatus.OK, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"), "application/json; charset=utf-8")
                    return
                if parsed.path == "/frame":
                    payload = self._projection_payload(query)
                    layout = (query.get("layout") or [args.layout])[0]
                    script_src = str((payload.get("workbench") or {}).get("g6_asset", {}).get("script_src") or "/assets/g6.min.js")
                    body = render_workbench_html(payload, layout=layout, g6_script_src=script_src).encode("utf-8")
                    self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                    return
                if parsed.path == "/assets/g6.min.js" and g6_asset_path and g6_asset_path.exists():
                    self._send(HTTPStatus.OK, g6_asset_path.read_bytes(), "text/javascript; charset=utf-8")
                    return
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")
            except Exception as exc:
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc).encode("utf-8", errors="replace"), "text/plain; charset=utf-8")

    return WorkbenchLiveHandler


def cmd_workbench_serve(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    connect_fn = _require_dependency("connect")
    resolve_endpoint_identifier_fn = _require_dependency("resolve_endpoint_identifier")
    print_json_fn = _require_dependency("print_json")
    conn = connect_fn(repo)
    try:
        endpoint_name = resolve_endpoint_identifier_fn(conn, repo, args.endpoint)
    finally:
        _close_quietly(conn)
    handler = _workbench_service_handler(args, endpoint_name, _workbench_g6_source(repo))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    host, port = server.server_address[:2]
    print_json_fn(
        {
            "ok": True,
            "endpoint": endpoint_name,
            "url": f"http://{host}:{port}/workbench",
            "projection_endpoint": f"http://{host}:{port}/api/projection",
            "read_only": True,
            "mode": args.mode,
            "projection_modes": list(WORKBENCH_PROJECTION_MODES),
            "db_write_path": False,
            "live_refresh": "poll",
            "poll_seconds": args.poll_seconds,
        }
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    time.sleep(0)
    return 0




__all__ = [
    "WORKBENCH_HANDLER_KEYS",
    "WORKBENCH_PROJECTION_MODES",
    "attach_workbench_details",
    "build_workbench_payload",
    "build_workbench_handlers",
    "cmd_workbench_export",
    "cmd_workbench_serve",
    "ensure_workbench_g6_asset",
    "register_workbench",
    "render_workbench_live_shell",
    "render_workbench_html",
]
