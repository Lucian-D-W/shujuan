from __future__ import annotations

from typing import Any

EVIDENCE_NODE_TYPES = {"change_set", "test_result", "artifact", "user_confirmation"}

EXPECTED_EVIDENCE_TYPE_MAP = {
    "diff": {"change_set"},
    "change_set": {"change_set"},
    "test": {"test_result"},
    "test_result": {"test_result"},
    "artifact": {"artifact"},
    "file": {"artifact"},
    "doc_update": {"artifact", "change_set"},
    "user_confirmation": {"user_confirmation"},
    "confirmation": {"user_confirmation"},
}

PREDICATE_COVERAGE_REQUIRED_FIELDS = {
    "check_id",
    "predicate_id",
    "assertion",
    "result",
    "not_covered",
    "reason",
}
PREDICATE_COVERAGE_PASS_RESULTS = {"pass", "passed", "ok", "covered", "success", "succeeded"}
OVERRIDE_INACTIVE_STATES = {"deferred", "product_backlog", "backlog", "invalidated", "superseded", "replaced", "revoked"}
OVERRIDE_REVOKE_TERMS = {"revoke", "revoked", "supersede", "superseded", "replace", "replaced", "unclear", "ambiguous"}
OVERRIDE_ACCEPT_TERMS = {"accept", "accepted", "acknowledge", "acknowledged", "allow", "allowed", "approve", "approved"}
OVERRIDE_RISK_TERMS = {"risk", "mismatch", "missing coverage", "predicate coverage", "evidence type", "override"}
OVERRIDE_SCOPE_TERMS = {"scope", "scoped", "endpoint", "check", "acceptance", "task"}


def expected_evidence_allowed(expected: str | None) -> set[str]:
    if not expected:
        return set(EVIDENCE_NODE_TYPES)
    normalized = str(expected).strip().lower().replace("-", "_")
    return EXPECTED_EVIDENCE_TYPE_MAP.get(normalized, {normalized})


def normalize_predicate_coverage_matrix_rows(raw_rows: Any, *, source: str) -> list[dict[str, Any]]:
    if not isinstance(raw_rows, list):
        raise SystemExit(f"predicate_coverage_matrix in {source} must be a JSON array")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(raw_rows, start=1):
        if not isinstance(row, dict):
            raise SystemExit(f"predicate_coverage_matrix row {index} in {source} must be an object")
        missing = sorted(PREDICATE_COVERAGE_REQUIRED_FIELDS - set(row.keys()))
        if missing:
            raise SystemExit(f"predicate_coverage_matrix row {index} in {source} is missing required field(s): {', '.join(missing)}")
        if not isinstance(row.get("not_covered"), bool):
            raise SystemExit(f"predicate_coverage_matrix row {index} in {source} must set not_covered to true or false")
        normalized = {
            "check_id": str(row.get("check_id") or "").strip(),
            "predicate_id": str(row.get("predicate_id") or "").strip(),
            "assertion": str(row.get("assertion") or "").strip(),
            "result": str(row.get("result") or "").strip(),
            "not_covered": bool(row.get("not_covered")),
            "reason": "" if row.get("reason") is None else str(row.get("reason")).strip(),
        }
        for field in ("check_id", "predicate_id", "assertion", "result"):
            if not normalized[field]:
                raise SystemExit(f"predicate_coverage_matrix row {index} in {source} has empty {field}")
        if (normalized["not_covered"] or normalized["result"].lower() not in PREDICATE_COVERAGE_PASS_RESULTS) and not normalized["reason"]:
            raise SystemExit(f"predicate_coverage_matrix row {index} in {source} is not covered or not passing but has no reason")
        rows.append(normalized)
    return rows


def predicate_coverage_row_passed(row: dict[str, Any]) -> bool:
    return row["not_covered"] is False and str(row["result"]).strip().lower() in PREDICATE_COVERAGE_PASS_RESULTS


def normalized_coverage_result(row: dict[str, Any]) -> str:
    if row.get("not_covered") is True:
        return "not_covered"
    result = str(row.get("result") or "").strip().lower()
    if result in PREDICATE_COVERAGE_PASS_RESULTS:
        return "pass"
    if result in {"partial", "partially_covered"}:
        return "partial"
    if result in {"not_covered", "not-covered", "missing"}:
        return "not_covered"
    return "fail"


def override_reason_accepts_risk_within_scope(reason: str | None) -> bool:
    text = str(reason or "").strip().lower()
    if not text:
        return False
    if any(term in text for term in OVERRIDE_REVOKE_TERMS):
        return False
    accepts = any(term in text for term in OVERRIDE_ACCEPT_TERMS)
    names_risk = any(term in text for term in OVERRIDE_RISK_TERMS)
    scopes = any(term in text for term in OVERRIDE_SCOPE_TERMS)
    return accepts and names_risk and scopes


def effective_override_interpretation(
    *,
    warning_node_id: str | None,
    kind: str,
    current_state: str | None,
    override_reason: str | None,
) -> dict[str, Any]:
    state = str(current_state or "active").strip().lower().replace("-", "_")
    reason = str(override_reason or "").strip()
    if state == "resolved":
        effective = override_reason_accepts_risk_within_scope(reason)
        reason_code = "resolved_reason_accepts_scoped_risk" if effective else "resolved_reason_does_not_accept_scoped_risk"
    elif state in OVERRIDE_INACTIVE_STATES:
        effective = False
        reason_code = f"{state}_override_warning_not_effective"
    else:
        effective = True
        reason_code = "active_override_warning"
    return {
        "effective": effective,
        "kind": kind,
        "warning_node_id": warning_node_id,
        "current_state": state,
        "override_reason": reason,
        "reason_code": reason_code,
        "lightweight_v7_p0": True,
    }


__all__ = [
    "EVIDENCE_NODE_TYPES",
    "EXPECTED_EVIDENCE_TYPE_MAP",
    "PREDICATE_COVERAGE_PASS_RESULTS",
    "PREDICATE_COVERAGE_REQUIRED_FIELDS",
    "expected_evidence_allowed",
    "effective_override_interpretation",
    "normalize_predicate_coverage_matrix_rows",
    "normalized_coverage_result",
    "override_reason_accepts_risk_within_scope",
    "predicate_coverage_row_passed",
]
