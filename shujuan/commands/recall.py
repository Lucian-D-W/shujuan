from __future__ import annotations

import argparse
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _configure(deps: Mapping[str, Any]) -> None:
    globals().update(deps)


def _terms(query: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9_.:-]+|[\u4e00-\u9fff]{2,}", query.lower())
    return list(dict.fromkeys(item for item in raw if len(item) >= 2))[:12]


def _like(term: str) -> str:
    return f"%{term}%"


def _row_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _add_candidate(candidates: dict[str, dict[str, Any]], *, node_id: str, kind: str, label: str | None, summary: str | None, score: int, why: list[str], detail_ref: str, next_probe: str | None = None) -> None:
    key = f"{kind}:{node_id}"
    existing = candidates.get(key)
    if existing:
        existing["score"] += score
        existing["why_selected"] = list(dict.fromkeys([*existing["why_selected"], *why]))
        return
    candidates[key] = {
        "node_id": node_id,
        "kind": kind,
        "label": label,
        "summary": summary,
        "score": score,
        "why_selected": why,
        "detail_ref": detail_ref,
        "next_probe": next_probe,
    }


def recall_frontier_payload(conn: sqlite3.Connection, *, query: str, endpoint: str | None = None, top: int = 12) -> dict[str, Any]:
    terms = _terms(query)
    candidates: dict[str, dict[str, Any]] = {}
    endpoint_row = None
    endpoint_targets: list[str] = []
    if endpoint:
        endpoint_row = conn.execute("SELECT * FROM endpoints WHERE name = ? AND archived_at IS NULL", (endpoint,)).fetchone()
        if endpoint_row:
            endpoint_targets.append(str(endpoint_row["node_id"]))
            if endpoint_row["root_node_id"]:
                endpoint_targets.append(str(endpoint_row["root_node_id"]))
            _add_candidate(
                candidates,
                node_id=str(endpoint_row["node_id"]),
                kind="endpoint",
                label=str(endpoint_row["name"]),
                summary=endpoint_row["description"],
                score=50,
                why=["endpoint_candidate"],
                detail_ref=f"endpoint brief {endpoint_row['name']}",
                next_probe=f"python -m shujuan endpoint brief {endpoint_row['name']} --markdown",
            )

    for term in terms:
        pattern = _like(term)
        for row in conn.execute(
            """
            SELECT id, type, label, summary
            FROM nodes
            WHERE lower(COALESCE(label, '') || ' ' || COALESCE(summary, '') || ' ' || COALESCE(props, '')) LIKE ?
            ORDER BY created_at DESC
            LIMIT 40
            """,
            (pattern,),
        ).fetchall():
            node_id = str(row["id"])
            score = 40 if row["type"] in {"task", "acceptance_check", "source_item"} else 25
            _add_candidate(
                candidates,
                node_id=node_id,
                kind=str(row["type"]),
                label=row["label"],
                summary=row["summary"],
                score=score,
                why=[f"lexical_hit:{term}"],
                detail_ref=f"graph detail --node {node_id}",
                next_probe=f"python -m shujuan graph detail --node {node_id}",
            )
        for row in conn.execute(
            """
            SELECT ds.node_id, ds.heading, substr(ds.body, 1, 240) AS summary, sd.title
            FROM document_sections ds
            JOIN source_documents sd ON sd.id = ds.document_id
            WHERE lower(COALESCE(ds.heading, '') || ' ' || ds.body || ' ' || sd.title) LIKE ?
            ORDER BY ds.section_index ASC
            LIMIT 30
            """,
            (pattern,),
        ).fetchall():
            node_id = str(row["node_id"])
            _add_candidate(
                candidates,
                node_id=node_id,
                kind="source_section",
                label=row["heading"] or row["title"],
                summary=row["summary"],
                score=35,
                why=[f"source_section_hit:{term}"],
                detail_ref=f"graph detail --node {node_id}",
                next_probe=f"python -m shujuan graph detail --node {node_id}",
            )
        for row in conn.execute(
            """
            SELECT co.node_id, co.path, co.qualified_name, co.symbol_name, co.type
            FROM code_objects co
            WHERE lower(COALESCE(co.path, '') || ' ' || COALESCE(co.qualified_name, '') || ' ' || COALESCE(co.symbol_name, '')) LIKE ?
              AND co.archived_at IS NULL
            ORDER BY co.path ASC, COALESCE(co.start_line, 0) ASC
            LIMIT 25
            """,
            (pattern,),
        ).fetchall():
            node_id = str(row["node_id"])
            label = row["qualified_name"] or row["symbol_name"] or row["path"]
            _add_candidate(
                candidates,
                node_id=node_id,
                kind="code_object",
                label=label,
                summary=row["path"],
                score=25,
                why=[f"code_why_hit:{term}"],
                detail_ref=f"why --path {row['path']}",
                next_probe=f"python -m shujuan why --path {row['path']}",
            )

    seed_node_ids = [item["node_id"] for item in candidates.values()][:40]
    if endpoint_targets:
        seed_node_ids.extend(endpoint_targets)
    seed_node_ids = list(dict.fromkeys(seed_node_ids))
    if seed_node_ids:
        placeholders = ",".join("?" for _ in seed_node_ids)
        for row in conn.execute(
            f"""
            SELECT e.type AS edge_type, e.from_node_id, e.to_node_id,
                   n.id, n.type, n.label, n.summary
            FROM edges e
            JOIN nodes n ON n.id = CASE WHEN e.from_node_id IN ({placeholders}) THEN e.to_node_id ELSE e.from_node_id END
            WHERE e.from_node_id IN ({placeholders}) OR e.to_node_id IN ({placeholders})
            ORDER BY e.created_at DESC
            LIMIT 80
            """,
            [*seed_node_ids, *seed_node_ids, *seed_node_ids],
        ).fetchall():
            edge_type = str(row["edge_type"])
            score = 30 if edge_type in {"DERIVED_FROM", "APPLIES_TO", "SUPERSEDES", "RESOLVES"} else 18
            node_id = str(row["id"])
            _add_candidate(
                candidates,
                node_id=node_id,
                kind=f"edge_neighbor:{edge_type}",
                label=row["label"],
                summary=row["summary"],
                score=score,
                why=[f"graph_neighbor:{edge_type}"],
                detail_ref=f"graph detail --node {node_id}",
                next_probe=f"python -m shujuan graph detail --node {node_id}",
            )

    ranked = sorted(candidates.values(), key=lambda item: (-int(item["score"]), str(item["kind"]), str(item.get("label") or "")))[: max(1, top)]
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    unsearched = [
        "embedding/vector runtime intentionally not searched",
        "frontier is bounded to lexical hits plus one-hop graph expansion",
    ]
    if not endpoint:
        unsearched.append("endpoint-scoped active obligations; pass --endpoint to anchor them")
    return {
        "ok": True,
        "read_only": True,
        "db_writes": 0,
        "filesystem_writes": 0,
        "query": query,
        "endpoint": endpoint,
        "terms": terms,
        "frontier": ranked,
        "claim_ledger_seed": [],
        "unsearched_frontier": unsearched,
        "stop_rule": "stop after bounded lexical plus one-hop graph candidates; ask for endpoint or narrower query before widening",
    }


def render_recall_frontier_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Recall Frontier: {payload['query']}",
        "",
        f"- Read-only: {'yes' if payload.get('read_only') else 'no'}",
        f"- Endpoint: {payload.get('endpoint') or '(unbound)'}",
        "",
        "## Candidates",
    ]
    for item in payload.get("frontier") or []:
        label = item.get("label") or item.get("node_id")
        why = ", ".join(item.get("why_selected") or [])
        lines.append(f"- {item['rank']}. {item['kind']} `{label}` score={item['score']} ({why})")
    lines.extend(["", "## Unsearched Frontier"])
    for item in payload.get("unsearched_frontier") or []:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def build_recall_handlers(deps: Mapping[str, Any]) -> dict[str, Any]:
    _configure(deps)

    def frontier(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        conn = connect_read_only(repo)
        try:
            payload = recall_frontier_payload(conn, query=args.query, endpoint=args.endpoint, top=args.top)
        finally:
            conn.close()
        if args.markdown:
            print_text(render_recall_frontier_markdown(payload), end="")
        else:
            print_json(payload)
        return 0

    return {"frontier": frontier}


def register_recall(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, Any]) -> None:
    recall = subparsers.add_parser("recall")
    recall_sub = recall.add_subparsers(dest="recall_command", required=True)
    frontier = recall_sub.add_parser("frontier", help="Read-only deterministic hybrid recall frontier.")
    frontier.add_argument("--query", required=True)
    frontier.add_argument("--endpoint")
    frontier.add_argument("--top", type=int, default=12)
    frontier.add_argument("--markdown", action="store_true")
    frontier.set_defaults(func=handlers["frontier"])
