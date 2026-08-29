from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?",
            (name,),
        ).fetchone()
    )


def endpoint_agcp_doctor_findings(
    conn: sqlite3.Connection,
    endpoint_id: str,
    *,
    endpoint_agcp_predicate_rows: Callable[[sqlite3.Connection, str], list[sqlite3.Row]],
    predicate_link_rows: Callable[[sqlite3.Connection, list[str] | None], list[sqlite3.Row]],
    endpoint_source_nondowngrade_audit: Callable[[sqlite3.Connection, str], dict[str, Any]],
    row_to_dict: Callable[[sqlite3.Row | dict[str, Any] | None], dict[str, Any] | None],
) -> dict[str, Any]:
    predicate_rows = endpoint_agcp_predicate_rows(conn, endpoint_id)
    predicate_ids = [str(row["id"]) for row in predicate_rows]
    link_rows = predicate_link_rows(conn, predicate_ids) if predicate_ids else []
    linked_predicates = {str(row["predicate_id"]) for row in link_rows}
    unmapped_predicates = [row_to_dict(row) for row in predicate_rows if str(row["id"]) not in linked_predicates]
    contracted_tables_absent = [
        table
        for table in (
            "source_promises",
            "hard_predicates",
            "task_predicate_links",
            "evidence_predicate_coverage",
            "review_results",
            "work_chains",
        )
        if not _table_exists(conn, table)
    ]
    closed_link_rows = []
    if not any(table in contracted_tables_absent for table in ("source_promises", "hard_predicates", "task_predicate_links")):
        closed_link_rows = conn.execute(
            """
            SELECT tpl.task_id, tpl.check_id, tpl.predicate_id, ac.closed_by_node_id
            FROM task_predicate_links tpl
            JOIN acceptance_checks ac ON ac.id = tpl.check_id
            JOIN tasks t ON t.id = tpl.task_id
            JOIN hard_predicates hp ON hp.id = tpl.predicate_id
            JOIN source_promises sp ON sp.id = hp.source_promise_id
            WHERE sp.endpoint_id = ?
              AND hp.lifecycle = 'active'
              AND ac.closed_by_node_id IS NOT NULL
            ORDER BY tpl.task_id ASC, tpl.check_id ASC, tpl.predicate_id ASC
            """,
            (endpoint_id,),
        ).fetchall()
    missing_closed_coverage = []
    for row in closed_link_rows:
        if not _table_exists(conn, "evidence_predicate_coverage"):
            missing_closed_coverage.append(row_to_dict(row))
            continue
        coverage = conn.execute(
            """
            SELECT 1 FROM evidence_predicate_coverage
            WHERE evidence_node_id = ?
              AND check_id = ?
              AND predicate_id = ?
              AND result = 'pass'
            LIMIT 1
            """,
            (row["closed_by_node_id"], row["check_id"], row["predicate_id"]),
        ).fetchone()
        if not coverage:
            missing_closed_coverage.append(row_to_dict(row))
    review_rows = []
    if _table_exists(conn, "review_results"):
        if _table_exists(conn, "work_chains"):
            review_rows = conn.execute(
                """
                SELECT rr.*, wc.name AS work_chain_name
                FROM review_results rr
                LEFT JOIN work_chains wc ON wc.id = rr.work_chain_id
                WHERE rr.endpoint_id = ?
                  AND rr.result IN ('reject', 'partial', 'needs_user_decision')
                ORDER BY rr.created_at DESC, rr.id DESC
                """,
                (endpoint_id,),
            ).fetchall()
        else:
            review_rows = conn.execute(
                """
                SELECT rr.*, NULL AS work_chain_name
                FROM review_results rr
                WHERE rr.endpoint_id = ?
                  AND rr.result IN ('reject', 'partial', 'needs_user_decision')
                ORDER BY rr.created_at DESC, rr.id DESC
                """,
                (endpoint_id,),
            ).fetchall()
    source_audit = endpoint_source_nondowngrade_audit(conn, endpoint_id)
    return {
        "contracted_tables_absent": contracted_tables_absent,
        "contracted_tables_status": "absent_expected" if contracted_tables_absent else "legacy_present_pending_contraction",
        "active_predicate_count": len(predicate_rows),
        "task_predicate_link_count": len(link_rows),
        "unmapped_predicates": unmapped_predicates,
        "closed_checks_missing_predicate_coverage": missing_closed_coverage,
        "non_accepting_reviews": [row_to_dict(row) for row in review_rows],
        "source_non_downgrade_findings": source_audit["findings"],
        "source_non_downgrade_matrix": source_audit["source_promise_matrix"],
    }


__all__ = ["endpoint_agcp_doctor_findings"]
