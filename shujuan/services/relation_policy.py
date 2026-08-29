from __future__ import annotations

import re
from typing import Any

from .sovereignty_gate import (
    explicit_no_governance_hits as _sovereignty_explicit_no_governance_hits,
    is_no_governance_topic_only as _sovereignty_topic_only,
)

_NORMALIZE_RE = re.compile(r"[\s，。,.!?！？、；;：:'\"`“”‘’（）()\[\]{}<>《》/\\|_-]+")
_VERSION_TOKEN_RE = re.compile(r"(?i)\bv\s*\d+\b")
_SUCCESSOR_SCOPE_PATTERNS = [
    re.compile(r"(?i)\bsuccessor\s+(?:patch\s+)?scope\b"),
    re.compile(r"(?i)\b(?:start|open|create)\s+a?\s*successor\b"),
    re.compile(r"(?i)\bpatch\s+scope\s+to\s+v\s*\d+(?:\.\d+)*\b"),
    re.compile(r"(?i)\bsuccessor\s+(?:patch\s+)?scope\s+to\s+v\s*\d+(?:\.\d+)*\b"),
    re.compile(r"(?i)\bbased on\s+v\s*\d+\b"),
    re.compile(r"(?i)\bafter\s+v\s*\d+\b"),
    re.compile(r"(?i)\bupgrade to\s+v\s*\d+\b"),
    re.compile(r"(?i)\bmove to\s+v\s*\d+\b"),
    re.compile(r"(?i)\bnext version\b"),
    re.compile(r"(?i)\bnext\s+step\b"),
    re.compile(r"基于\s*v\s*\d+"),
    re.compile(r"承接\s*v\s*\d+"),
    re.compile(r"推进\s*v\s*\d+"),
    re.compile(r"升级到\s*v\s*\d+"),
    re.compile(r"在\s*v\s*\d+\s*基础上"),
    re.compile(r"v\s*\d+\s*之后"),
]
_NON_VERSION_LINEAGE_PATTERNS = [
    re.compile(r"(?i)\bfollow[\s-]?up\b"),
    re.compile(r"(?i)\bleftover\b"),
    re.compile(r"(?i)\bremaining\b"),
    re.compile(r"(?i)\bcarry\s+over\b"),
    re.compile(r"(?i)\bcarried\s+over\b"),
    re.compile(r"上一版"),
    re.compile(r"前一版"),
    re.compile(r"上一次"),
    re.compile(r"之前的报告"),
    re.compile(r"前序"),
    re.compile(r"遗留"),
    re.compile(r"剩余"),
    re.compile(r"承接"),
    re.compile(r"后续"),
    re.compile(r"延续"),
]
_NEXT_SCOPE_HINT_PATTERNS = [
    re.compile(r"(?i)\bnext\s+version\b"),
    re.compile(r"下一版"),
    re.compile(r"下一个版本"),
]
_NO_GOVERNANCE_ONTOLOGY_PATTERNS = [
    re.compile(r"(?i)\bdo\s+not\s+use\s+governance\b"),
    re.compile(r"(?i)\bdon'?t\s+(?:save|record|capture)\s+(?:this\s+)?(?:task|request|run)\b"),
    re.compile(r"(?i)\bdo\s+not\s+put\s+(?:this(?:\s+(?:task|request|run))?|task|request|run|it)\s+into\s+the\s+process\b"),
    re.compile(r"(?:请|麻烦|这次|本次)?(?:不用|不做|不要使用|不要用)治理"),
    re.compile(r"(?:请|麻烦|这次|本次)?(?:不要|别|不)(?:把这次任务)?(?:纳入治理|进入流程)"),
    re.compile(r"(?:请|麻烦|这次|本次)?(?:不要|别|不)(?:记录|保存)(?:这次任务|本次任务|这个任务)?"),
    re.compile(r"(?:请|麻烦|这次|本次)?(?:不要|别|不)(?:把这次任务)?落库"),
    re.compile(r"(?:请|麻烦|这次|本次)?(?:不要|别|不)留痕"),
]
_INDEPENDENT_REVIEW_PATTERNS = [
    re.compile(r"(?i)\bindependent(?:ly)?\s+review\b"),
    re.compile(r"(?i)\breview\s+independently\b"),
    re.compile(r"(?i)\breview\s+separately\b"),
    re.compile(r"(?i)\bplease\s+review\s+this\s+fix\b"),
    re.compile(r"(?i)\breview\s+if\s+this\s+passes\b"),
    re.compile(r"(?i)\bseparately\s+check\s+if\s+this\s+fix\s+passes\b"),
    re.compile(r"(?i)\bcheck\s+if\s+this\s+fix\s+passes\b"),
    re.compile(r"(?i)\bdoes\s+this\s+pass(?:\s+acceptance)?\b"),
    re.compile(r"(?i)\breview\s+whether\s+this\s+can\s+close\b"),
    re.compile(r"(?i)\b(?:does|is)\s+this\s+evidence\s+(?:look\s+)?sufficient\s+for\s+closure\b"),
    re.compile(r"(?i)\bevidence\s+(?:look\s+)?sufficient\s+for\s+closure\b"),
    re.compile(r"帮我看看.*验收"),
    re.compile(r"能不能过验收"),
    re.compile(r"验收一下"),
    re.compile(r"是否可以验收"),
    re.compile(r"通过了吗"),
    re.compile(r"看一下是否通过"),
]
_NEGATED_REVIEW_PATTERNS = [
    re.compile(r"(?:不需要|无需|不用|不要|不做)(?:独立|单独)?(?:审查|检查|复核)"),
    re.compile(r"不需要审查"),
    re.compile(r"无需审查"),
    re.compile(r"不用审查"),
    re.compile(r"不要审查"),
    re.compile(r"不审查"),
    re.compile(r"(?i)不需要\s*reviewer"),
    re.compile(r"(?i)无需\s*reviewer"),
    re.compile(r"(?i)不要\s*reviewer"),
    re.compile(r"(?i)\bno\s+review\b"),
    re.compile(r"(?i)\bwithout\s+review\b"),
    re.compile(r"(?i)\bdo\s+not\s+review\b"),
    re.compile(r"(?i)\bdon't\s+review\b"),
    re.compile(r"(?i)\bskip\s+review\b"),
    re.compile(r"(?i)\bskip\s+reviewer\b"),
    re.compile(r"(?i)\bskip\s+the\s+reviewer\b"),
]
_STATE_TARGET_PATTERNS = [
    re.compile(r"(?i)\b(?:this|that|the)\s+(?:task|check|issue|problem|endpoint|fix)\b"),
    re.compile(r"这个(?:任务|检查|问题|端点|修复)"),
    re.compile(r"该(?:任务|检查|问题|端点|修复)"),
    re.compile(r"当前(?:任务|检查|问题|端点|修复)"),
]
_STATE_TARGET_ID_PATTERNS = [
    re.compile(r"(?i)(?<![a-z0-9_])T\d+(?![a-z0-9_])"),
    re.compile(r"(?i)(?<![a-z0-9_])C\d+(?![a-z0-9_])"),
    re.compile(r"(?i)(?<![a-z0-9_])task[-_][a-z0-9_]+(?![a-z0-9_])"),
    re.compile(r"(?i)(?<![a-z0-9_])check[-_][a-z0-9_]+(?![a-z0-9_])"),
    re.compile(r"(?i)(?<![a-z0-9_])node_[a-z0-9_]+(?![a-z0-9_])"),
]
_STATE_CHANGE_PATTERNS = {
    "delete": [
        re.compile(r"(?i)\b(?:delete|remove|drop|erase)\b"),
        re.compile(r"删除"),
        re.compile(r"移除"),
        re.compile(r"去掉"),
    ],
    "promote": [
        re.compile(r"(?i)\bpromote\b"),
        re.compile(r"(?i)\bactivate\b"),
        re.compile(r"(?i)\bpull\s+into\s+scope\b"),
        re.compile(r"提级"),
        re.compile(r"提升.*优先级"),
        re.compile(r"激活"),
        re.compile(r"转为当前"),
    ],
    "defer": [
        re.compile(r"(?i)\bdefer\b"),
        re.compile(r"(?i)\bbacklog\b"),
        re.compile(r"(?i)\bpostpone\b"),
        re.compile(r"推迟"),
        re.compile(r"延期"),
        re.compile(r"暂缓"),
        re.compile(r"放入\s*backlog"),
    ],
    "escalate": [
        re.compile(r"(?i)\bescalate\b"),
        re.compile(r"(?i)\bcontroller\s+decision\b"),
        re.compile(r"(?i)\bneeds\s+decision\b"),
        re.compile(r"升级处理"),
        re.compile(r"需要裁决"),
        re.compile(r"升级给\s*controller"),
    ],
}
_NO_GOVERNANCE_DIRECTIVE_HINTS = [
    "direct answer",
    "answer directly",
    "just answer",
    "answer without governance",
    "outside shujuan",
    "outside the process",
    "off the books",
    "private note",
    "no log",
    "in my vault",
    "my vault",
    "直接回答",
    "直接告诉我",
    "直接说",
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
_NO_GOVERNANCE_TOPIC_HINTS = [
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
_NO_GOVERNANCE_POLITE_PREFIXES = [
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
_NO_GOVERNANCE_DIRECTIVE_CONTEXT_HINTS = [
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
    "acceptance",
    "处理",
    "执行",
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


def _matches(text: str, markers: list[str]) -> list[str]:
    lowered = text.lower()
    compact = _compact(text)
    hits: list[str] = []
    for marker in markers:
        normalized = marker.lower()
        if normalized in lowered or _compact(marker) in compact:
            hits.append(marker)
    return list(dict.fromkeys(hits))


def _any_phrase(text: str, markers: list[str]) -> bool:
    lowered = text.lower()
    compact = _compact(text)
    return any(marker.lower() in lowered or _compact(marker) in compact for marker in markers)


def _successor_scope_hits(text: str) -> list[str]:
    hits = [match.group(0) for pattern in _SUCCESSOR_SCOPE_PATTERNS for match in pattern.finditer(text)]
    return list(dict.fromkeys(hits))


def _regex_hits(text: str, patterns: list[re.Pattern[str]]) -> list[str]:
    hits = [match.group(0) for pattern in patterns for match in pattern.finditer(text)]
    return list(dict.fromkeys(hits))


def _non_version_lineage_hits(text: str) -> list[str]:
    return _regex_hits(text, _NON_VERSION_LINEAGE_PATTERNS)


def _next_scope_hint_hits(text: str) -> list[str]:
    return _regex_hits(text, _NEXT_SCOPE_HINT_PATTERNS)


def _no_governance_ontology_hits(text: str) -> list[str]:
    return _regex_hits(text, _NO_GOVERNANCE_ONTOLOGY_PATTERNS)


def _independent_review_hits(text: str) -> list[str]:
    return list(dict.fromkeys([*_matches(text, INDEPENDENT_REVIEW_MARKERS), *_regex_hits(text, _INDEPENDENT_REVIEW_PATTERNS)]))


def _negated_review_hits(text: str) -> list[str]:
    return _regex_hits(text, _NEGATED_REVIEW_PATTERNS)


def _state_target_bound(text: str) -> bool:
    return bool(_regex_hits(text, _STATE_TARGET_PATTERNS) or _regex_hits(text, _STATE_TARGET_ID_PATTERNS))


def _state_target_hint(text: str) -> str | None:
    explicit_ids = _regex_hits(text, _STATE_TARGET_ID_PATTERNS)
    if explicit_ids:
        return explicit_ids[0]
    targets = _regex_hits(text, _STATE_TARGET_PATTERNS)
    return targets[0] if targets else None


def _state_change_decision(text: str) -> tuple[str | None, list[str]]:
    for decision_type, patterns in _STATE_CHANGE_PATTERNS.items():
        hits = _regex_hits(text, patterns)
        if hits:
            return decision_type, hits
    return None, []


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


def _marker_has_directive_context(text: str, marker: str) -> bool:
    compact_text = _compact(text)
    compact_marker = _compact(marker)
    if not compact_marker:
        return False
    compact_contexts = sorted({_compact(hint) for hint in _NO_GOVERNANCE_DIRECTIVE_CONTEXT_HINTS}, key=len, reverse=True)
    search_start = 0
    while True:
        index = compact_text.find(compact_marker, search_start)
        if index < 0:
            return False
        prefix = _strip_compact_prefixes(compact_text[:index], _NO_GOVERNANCE_POLITE_PREFIXES)
        suffix = compact_text[index + len(compact_marker) :]
        compact_topics = sorted({_compact(hint) for hint in _NO_GOVERNANCE_TOPIC_HINTS}, key=len, reverse=True)
        if any(suffix.startswith(hint) for hint in compact_topics):
            search_start = index + len(compact_marker)
            continue
        if not prefix:
            return True
        if any(suffix.startswith(hint) for hint in compact_contexts):
            return True
        search_start = index + len(compact_marker)


def _explicit_no_governance_hits(text: str) -> list[str]:
    sovereignty_hits = _sovereignty_explicit_no_governance_hits(text)
    if sovereignty_hits:
        return sovereignty_hits
    if _sovereignty_topic_only(text):
        return []
    marker_hits = list(dict.fromkeys([*_matches(text, NO_GOVERNANCE_MARKERS), *_no_governance_ontology_hits(text)]))
    if not marker_hits:
        return []
    direct = _any_phrase(text, _NO_GOVERNANCE_DIRECTIVE_HINTS)
    contextual = any(_marker_has_directive_context(text, marker) for marker in marker_hits)
    topic_only = _any_phrase(text, _NO_GOVERNANCE_TOPIC_HINTS) and not direct and not contextual
    if direct:
        return marker_hits
    compact = _compact(text)
    stripped = _strip_compact_prefixes(compact, _NO_GOVERNANCE_POLITE_PREFIXES)
    compact_topics = sorted({_compact(hint) for hint in _NO_GOVERNANCE_TOPIC_HINTS}, key=len, reverse=True)
    for marker in NO_GOVERNANCE_MARKERS:
        compact_marker = _compact(marker)
        if not compact_marker or not stripped.startswith(compact_marker):
            continue
        suffix = stripped[len(compact_marker) :]
        if any(suffix.startswith(topic) for topic in compact_topics):
            return []
        return marker_hits
    if contextual:
        return marker_hits
    if topic_only:
        return []
    return []


def explicit_no_governance_hits(text: str) -> list[str]:
    return _explicit_no_governance_hits(text)


NO_GOVERNANCE_MARKERS = [
    "no governance",
    "without governance",
    "without shujuan",
    "outside shujuan",
    "do not use shujuan",
    "don't use shujuan",
    "no shujuan",
    "do not save",
    "do not record",
    "don't record",
    "do not capture",
    "don't capture",
    "no record",
    "no capture",
    "outside the process",
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

INDEPENDENT_REVIEW_MARKERS = [
    "independent review",
    "review independently",
    "review separately",
    "standalone review",
    "separate review",
    "single review pass",
    "独立审查",
    "独立检查",
    "单独审查",
    "单独检查",
    "单独验收",
    "复核",
]

CONTINUATION_MARKERS = [
    "continue",
    "take over",
    "takeover",
    "resume",
    "recover",
    "pick up",
    "previous",
    "recent",
    "earlier",
    "last hour",
    "just created",
    "the task from before",
    "where were we",
    "接手",
    "接管",
    "继续",
    "恢复",
    "续上",
    "刚才",
    "前一个小时",
    "上个任务",
    "上一个任务",
    "前一个任务",
    "之前建立",
    "刚建立",
    "相关任务",
    "继承",
    "后续",
]

SUCCESSOR_SCOPE_MARKERS = [
    "next version",
    "successor scope",
    "successor patch scope",
]

REVISION_MARKERS = [
    "revise",
    "revision",
    "contradict",
    "correction",
    "reopen",
    "supersede",
    "replace",
    "rebuild",
    "recreate",
    "redo",
    "old task is wrong",
    "previous task is wrong",
    "修正",
    "反驳",
    "推翻",
    "替换",
    "取代",
    "替代",
    "重建",
    "重做",
    "重新建",
    "旧任务不对",
    "前一个任务不对",
    "上一个任务不对",
    "刚才那个任务不对",
    "重检",
]

FORK_MARKERS = [
    "fork",
    "variant",
    "alternative",
    "alternate path",
    "parallel track",
    "parallel option",
    "separate option",
    "另一版",
    "变体",
    "备选",
    "平行方案",
    "平行推进",
    "并行方案",
    "另一路",
    "另一条线",
    "分叉方案",
]

DELETE_MARKERS = ["delete", "remove", "drop", "erase", "删除", "去掉", "移除"]
PROMOTE_MARKERS = ["promote", "activate", "pull into scope", "提级", "提升优先级", "激活", "转为当前"]
DEFER_MARKERS = ["defer", "backlog", "later", "postpone", "推迟", "延期", "放入 backlog", "暂缓"]
ESCALATE_MARKERS = ["escalate", "controller decision", "needs decision", "升级处理", "需要裁决", "升级给 controller"]
MATERIAL_ONLY_MARKERS = ["packet", "review", "worker", "researcher", "writer", "summary", "material only", "handoff", "包", "审查", "复核", "研究", "调研", "撰写", "总结", "材料"]
UPDATE_MARKERS = ["update", "tighten", "revise", "repair", "fix", "change", "更新", "收紧", "修补", "修复", "修改"]


def classify_relation(intent: str, *, endpoint_name: str | None = None) -> dict[str, Any]:
    no_governance_hits = _explicit_no_governance_hits(intent)
    if no_governance_hits:
        return {
            "relation_type": "no_governance_exit",
            "decision_type": "no_write",
            "confidence": "high",
            "evidence_phrases": no_governance_hits,
            "predecessor_required": False,
            "predecessor_hint": None,
            "authority_hint": "ordinary_answer",
            "authority_posture": "ordinary_answer",
            "route_hint": "No Governance",
            "mode_hint": "no_governance",
        }

    negated_review_hits = _negated_review_hits(intent)
    review_hits = [] if negated_review_hits else _independent_review_hits(intent)
    lineage_hits = _non_version_lineage_hits(intent)
    continuation_hits = [*_matches(intent, CONTINUATION_MARKERS), *lineage_hits]
    successor_hits = [*_successor_scope_hits(intent), *_matches(intent, SUCCESSOR_SCOPE_MARKERS)]
    next_scope_hits = _next_scope_hint_hits(intent)
    revision_hits = _matches(intent, REVISION_MARKERS)
    fork_hits = _matches(intent, FORK_MARKERS)
    state_decision_type, state_hits = _state_change_decision(intent)
    state_target_bound = bool(state_hits) and _state_target_bound(intent)
    target_hint = _state_target_hint(intent) if state_hits else None

    relation_type = "independent_root"
    evidence = review_hits or continuation_hits or successor_hits or next_scope_hits or revision_hits or fork_hits or state_hits
    if review_hits:
        relation_type = "independent_review"
    elif successor_hits or (lineage_hits and next_scope_hits):
        relation_type = "successor_scope"
    elif revision_hits:
        relation_type = "revision_or_contradiction"
    elif fork_hits:
        relation_type = "fork_variant"
    elif continuation_hits or state_target_bound or endpoint_name:
        relation_type = "continuation"

    decision_type = "append"
    material_hits = _matches(intent, MATERIAL_ONLY_MARKERS)
    if state_decision_type:
        decision_type = state_decision_type
    elif relation_type == "independent_review" or (material_hits and not negated_review_hits):
        decision_type = "material_only"
    elif relation_type == "revision_or_contradiction":
        decision_type = "supersede"
    elif relation_type in {"continuation", "successor_scope"} or _matches(intent, UPDATE_MARKERS):
        decision_type = "update"

    route_hint = {
        "independent_review": "Delegate",
        "continuation": "Recover",
        "successor_scope": "Recover",
        "revision_or_contradiction": "Recover",
        "fork_variant": "Recover",
        "independent_root": "Execute",
    }[relation_type]
    mode_hint = "explore" if route_hint in {"Recover", "Delegate"} else "standard"
    authority_posture = {
        "independent_review": "reviewer_material",
        "continuation": "controller_recover",
        "successor_scope": "controller_recover",
        "revision_or_contradiction": "controller_recover",
        "fork_variant": "controller_recover",
        "independent_root": "controller_execute",
    }[relation_type]

    predecessor_required = relation_type in {
        "independent_review",
        "continuation",
        "successor_scope",
        "revision_or_contradiction",
        "fork_variant",
    }
    if state_target_bound:
        route_hint = "Recover"
        mode_hint = "explore"
        authority_posture = "controller_recover"
        predecessor_required = True
    predecessor_hint = None
    if relation_type == "independent_review" and endpoint_name:
        predecessor_hint = f"Review against endpoint {endpoint_name}, or name the PR/task/check/artifact that should be reviewed."
    elif relation_type == "independent_review":
        predecessor_hint = "Name the review target first: endpoint, PR, patch, task, check, or artifact."
    elif state_target_bound and endpoint_name:
        predecessor_hint = f"Bind the target task/check/problem through endpoint {endpoint_name} before the controller changes state."
    elif state_target_bound:
        predecessor_hint = "Bind the target task, check, problem, or endpoint before the controller changes state."
    elif predecessor_required and endpoint_name:
        predecessor_hint = f"Bind the predecessor through endpoint {endpoint_name} before writeful work."
    elif predecessor_required:
        predecessor_hint = "Use endpoint suggest or project overview to bind the predecessor scope before writeful work."

    confidence = "high" if evidence else ("medium" if endpoint_name else "low")
    return {
        "relation_type": relation_type,
        "decision_type": decision_type,
        "confidence": confidence,
        "evidence_phrases": evidence,
        "predecessor_required": predecessor_required,
        "predecessor_hint": predecessor_hint,
        "target_binding_required": bool(state_decision_type),
        "target_hint": target_hint,
        "authority_hint": authority_posture,
        "authority_posture": authority_posture,
        "route_hint": route_hint,
        "mode_hint": mode_hint,
    }


__all__ = [
    "NO_GOVERNANCE_MARKERS",
    "classify_relation",
    "explicit_no_governance_hits",
]
