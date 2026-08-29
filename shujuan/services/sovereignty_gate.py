from __future__ import annotations

import re
from typing import Any

_NORMALIZE_RE = re.compile(r"[\s，。,.!?！？、；;：:'\"`“”‘’（）()\[\]{}<>《》/\\|_-]+")

NO_GOVERNANCE_MARKERS = [
    "no governance",
    "without governance",
    "without shujuan",
    "outside shujuan",
    "do not use governance",
    "don't use governance",
    "do not use shujuan",
    "don't use shujuan",
    "no shujuan",
    "do not save",
    "don't save",
    "do not record",
    "don't record",
    "do not capture",
    "don't capture",
    "no record",
    "no capture",
    "outside the process",
    "off the books",
    "private note",
    "no log",
    "in my vault",
    "my vault",
    "不走 shujuan",
    "不用 shujuan",
    "不要使用 shujuan",
    "不要用 shujuan",
    "别用 shujuan",
    "不要走 shujuan",
    "不走书卷",
    "不用书卷",
    "不要使用书卷",
    "不要用书卷",
    "不要走书卷",
    "别用书卷",
    "不走流程",
    "不用流程",
    "不要走流程",
    "不进流程",
    "不纳入流程",
    "不要治理",
    "不用治理",
    "不做治理",
    "不治理",
    "不要落库",
    "不落库",
    "不写库",
    "不要记录",
    "不记录",
    "不要保存",
    "不保存",
    "不要捕获",
    "不捕获",
]

_NO_GOVERNANCE_ONTOLOGY_PATTERNS = [
    re.compile(r"(?i)\bdo\s+not\s+use\s+governance\b"),
    re.compile(r"(?i)\bdon'?t\s+use\s+governance\b"),
    re.compile(r"(?i)\bdon'?t\s+(?:save|record|capture)\s+(?:this\s+)?(?:task|request|run)\b"),
    re.compile(r"(?i)\bdo\s+not\s+(?:save|record|capture)\s+(?:this\s+)?(?:task|request|run)\b"),
    re.compile(r"(?i)\bdo\s+not\s+put\s+(?:this(?:\s+(?:task|request|run))?|task|request|run|it)\s+into\s+the\s+process\b"),
    re.compile(r"(?i)\boff\s+the\s+books\b"),
    re.compile(r"(?i)\bprivate\s+note\b"),
    re.compile(r"(?i)\bno\s+log\b"),
    re.compile(r"(?i)\b(?:in\s+)?my\s+vault\b"),
    re.compile(r"(?:请|麻烦|这次|本次)?(?:不用|不做|不要使用|不要用)治理"),
    re.compile(r"(?:请|麻烦|这次|本次)?(?:不要|别|不)(?:把这次任务)?(?:纳入治理|进入流程)"),
    re.compile(r"(?:请|麻烦|这次|本次)?(?:不要|别|不)(?:记录|保存)(?:这次任务|本次任务|这个任务)?"),
    re.compile(r"(?:请|麻烦|这次|本次)?(?:不要|别|不)(?:把这次任务)?落库"),
    re.compile(r"(?:请|麻烦|这次|本次)?(?:不要|别|不)留痕"),
]

_DIRECTIVE_HINTS = [
    "direct answer",
    "answer directly",
    "just answer",
    "answer without governance",
    "outside shujuan",
    "outside the process",
    "off the books",
    "private note",
    "no log",
    "my vault",
    "do not record this task",
    "don't record this task",
    "do not record this request",
    "do not record this run",
    "off the books",
    "private note",
    "no log",
    "my vault",
    "直接回答",
    "直接告诉我",
    "直接说",
    "只回答",
    "别记录这次任务",
    "不要记录这次任务",
    "不要记录本次任务",
    "这次不要记录",
    "本次不要记录",
    "这次不记录",
    "本次不记录",
    "这次不要保存",
    "本次不要保存",
    "这次不用治理",
    "本次不用治理",
    "这次不做治理",
    "本次不做治理",
    "这次不要落库",
    "本次不要落库",
    "这次不要留痕",
    "本次不要留痕",
    "不要落库这次任务",
    "不要把这次任务",
]

_TOPIC_HINTS = [
    "why",
    "summary",
    "summarize",
    "explain",
    "discussion",
    "talk about",
    "quote",
    "phrase",
    "wording",
    "mean",
    "means",
    "meaning",
    "what does",
    "为什么",
    "总结",
    "解释",
    "讨论",
    "聊聊",
    "措辞",
    "这句话",
    "是什么意思",
    "什么意思",
    "含义",
]

_POLITE_PREFIXES = [
    "请",
    "麻烦",
    "烦请",
    "这次",
    "本次",
    "当前",
    "这个任务",
    "这轮",
    "本轮",
    "你",
    "请你",
    "麻烦你",
    "帮我",
]

_DIRECTIVE_CONTEXT_HINTS = [
    "this task",
    "task",
    "answer",
    "just answer",
    "help me",
    "look at",
    "check",
    "review",
    "handle",
    "process",
    "fix",
    "continue",
    "acceptance",
    "处理",
    "执行",
    "继续",
    "做",
    "回答",
    "只回答",
    "告诉",
    "看看",
    "检查",
    "修复",
    "验收",
    "任务",
    "问题",
]


def _compact(value: str) -> str:
    return _NORMALIZE_RE.sub("", value.lower())


def _regex_hits(text: str, patterns: list[re.Pattern[str]]) -> list[str]:
    hits = [match.group(0) for pattern in patterns for match in pattern.finditer(text)]
    return list(dict.fromkeys(hits))


def _matches(text: str, markers: list[str]) -> list[str]:
    lowered = text.lower()
    compact = _compact(text)
    hits: list[str] = []
    for marker in markers:
        normalized = marker.lower()
        compact_marker = _compact(marker)
        if normalized in lowered or (compact_marker and compact_marker in compact):
            hits.append(marker)
    return list(dict.fromkeys(hits))


def _contains_any(compact_text: str, hints: list[str]) -> bool:
    return any(_compact(hint) in compact_text for hint in hints if _compact(hint))


def _starts_any(compact_text: str, hints: list[str]) -> bool:
    return any(compact_text.startswith(_compact(hint)) for hint in hints if _compact(hint))


def _strip_compact_prefixes(value: str, prefixes: list[str]) -> str:
    remaining = value
    compact_prefixes = sorted({_compact(prefix) for prefix in prefixes}, key=len, reverse=True)
    changed = True
    while remaining and changed:
        changed = False
        for prefix in compact_prefixes:
            if prefix and remaining.startswith(prefix):
                remaining = remaining[len(prefix) :]
                changed = True
                break
    return remaining


def _has_directive_hint(text: str) -> bool:
    compact = _compact(text)
    return _contains_any(compact, _DIRECTIVE_HINTS)


def _has_topic_hint(text: str) -> bool:
    compact = _compact(text)
    return _contains_any(compact, _TOPIC_HINTS)


def _marker_has_directive_context(text: str, marker: str) -> bool:
    compact_text = _compact(text)
    compact_marker = _compact(marker)
    if not compact_marker:
        return False
    search_start = 0
    while True:
        index = compact_text.find(compact_marker, search_start)
        if index < 0:
            return False
        raw_prefix = compact_text[:index]
        prefix = _strip_compact_prefixes(raw_prefix, _POLITE_PREFIXES)
        suffix = compact_text[index + len(compact_marker) :]
        if _starts_any(suffix, _TOPIC_HINTS) or (_contains_any(prefix, _TOPIC_HINTS) and not _contains_any(prefix, _DIRECTIVE_HINTS)):
            search_start = index + len(compact_marker)
            continue
        if not prefix:
            return True
        if _contains_any(prefix, _DIRECTIVE_CONTEXT_HINTS):
            return True
        if _starts_any(suffix, _DIRECTIVE_CONTEXT_HINTS):
            return True
        search_start = index + len(compact_marker)


def is_no_governance_topic_only(text: str) -> bool:
    marker_hits = list(dict.fromkeys([*_matches(text, NO_GOVERNANCE_MARKERS), *_regex_hits(text, _NO_GOVERNANCE_ONTOLOGY_PATTERNS)]))
    if not marker_hits:
        return False
    if _has_directive_hint(text):
        return False
    contextual = any(_marker_has_directive_context(text, marker) for marker in marker_hits)
    return _has_topic_hint(text) and not contextual


def explicit_no_governance_hits(text: str) -> list[str]:
    marker_hits = list(dict.fromkeys([*_matches(text, NO_GOVERNANCE_MARKERS), *_regex_hits(text, _NO_GOVERNANCE_ONTOLOGY_PATTERNS)]))
    if not marker_hits:
        return []
    if _has_directive_hint(text):
        return marker_hits
    contextual = any(_marker_has_directive_context(text, marker) for marker in marker_hits)
    if contextual:
        return marker_hits
    if is_no_governance_topic_only(text):
        return []
    compact = _compact(text)
    stripped = _strip_compact_prefixes(compact, _POLITE_PREFIXES)
    for marker in NO_GOVERNANCE_MARKERS:
        compact_marker = _compact(marker)
        if not compact_marker or not stripped.startswith(compact_marker):
            continue
        suffix = stripped[len(compact_marker) :]
        if _starts_any(suffix, _TOPIC_HINTS):
            return []
        return marker_hits
    return []


def explicit_no_governance_reasons(text: str) -> list[str]:
    return [f"explicit_no_governance:{marker}" for marker in explicit_no_governance_hits(text)]


def sovereignty_gate(text: str) -> dict[str, Any]:
    hits = explicit_no_governance_hits(text)
    return {
        "hit": bool(hits),
        "reasons": [f"explicit_no_governance:{marker}" for marker in hits],
        "evidence_phrases": hits,
        "relation_decision": {
            "relation_type": "no_governance_exit" if hits else "independent_root",
            "decision_type": "no_write" if hits else "append",
            "route_hint": "No Governance" if hits else None,
            "mode_hint": "no_governance" if hits else None,
        },
    }


def no_governance_payload(
    *,
    command: str,
    content: str,
    reasons: list[str] | None = None,
    contract: dict[str, Any] | None = None,
    workflow: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "read_only": True,
        "mode": "no_governance",
        "recommended_mode": "no_governance",
        "recommended_route": "No Governance",
        "command": command,
        "db_writes": 0,
        "capture_claim": False,
        "context": None,
        "current_handle": None,
        "stop_writes": True,
        "exit_brake": {
            "read_only": True,
            "no_governance": True,
            "friction_brake": True,
            "stop_writes": True,
            "reason": "explicit_no_governance",
        },
        "contract": contract,
        "reasons": reasons or explicit_no_governance_reasons(content),
        "user_goal": " ".join(content.strip().split())[:220],
        "note": "No Governance returned before connecting to or mutating the shujuan DB.",
    }
    if workflow:
        payload["workflow"] = workflow
    return payload


__all__ = [
    "NO_GOVERNANCE_MARKERS",
    "explicit_no_governance_hits",
    "explicit_no_governance_reasons",
    "is_no_governance_topic_only",
    "no_governance_payload",
    "sovereignty_gate",
]
