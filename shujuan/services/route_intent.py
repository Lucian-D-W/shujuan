from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


_CLOSE_PATTERNS = (
    r"\bclose\s+(?:task|check|endpoint|review|acceptance)\s+[A-Za-z0-9_.:-]+\b",
    r"\bclose\s+(?:task|check|endpoint|review|acceptance)\b",
    r"\bclose\s+(?:remaining|open|all|the\s+remaining)?\s*(?:tasks|checks|endpoints|reviews|acceptance\s+checks)\b",
    r"\bclose\s+(this|that|the)\s+(check|task|endpoint|review|acceptance)\b",
    r"\bclose\s+with\s+evidence\b",
    r"\bperform\s+closeout\b",
    r"\bexecute\s+closeout\b",
    r"\bcomplete\s+closeout\b",
    r"\bclose\s+out\b",
    r"\bsign\s+off\s+(?:on\s+)?(?:this|that|the|current)?\s*(?:task|check|endpoint|review|acceptance|closure|closeout)?\b",
    r"\bmark\s+(?:this|that|the|current)?\s*(?:task|check|endpoint|review|acceptance)\s+done\b",
    r"\bmark\s+(?:this|that|the|current)?\s*(?:task|check|endpoint|review|acceptance)\s+(?:complete|completed|accepted)\b",
    r"\bmark\s+done\s+(?:task|check|endpoint|review|acceptance)\b",
    r"\bmark\s+(?:complete|completed|accepted)\s+(?:this|that|the|current)?\s*(?:task|check|endpoint|review|acceptance)\b",
    r"\bresolve\s+(?:this|that|the|current)?\s*(?:task|check|endpoint|review|acceptance)\s+as\s+accepted\b",
    r"\baccept\s+(?:this|that|the|current)?\s*(?:task|check|endpoint|review|acceptance)\b",
    r"\bapprove\s+(?:the\s+)?(?:closure|closeout)\b",
    r"\bcloseout\b",
    r"关闭(这个|该|当前)?(检查|任务|端点|验收)",
    r"用证据关闭",
    r"执行验收",
    r"完成验收",
    r"签收(这个|该|当前)?(检查|任务|端点|验收)",
    r"标记(这个|该|当前)?(检查|任务|端点|验收)(完成|通过|已接受|接受)",
    r"将(这个|该|当前)?(检查|任务|端点|验收)标记为(完成|通过|已接受|接受)",
    r"接受(这个|该|当前)?(检查|任务|端点|验收)",
)

_NEGATED_CLOSE_PATTERNS = (
    r"\bdo\s+not\s+(directly\s+)?close\b",
    r"\bdo\s+not\s+close\s+anything\b",
    r"\bdon't\s+(directly\s+)?close\b",
    r"\bdon't\s+close\s+anything\b",
    r"\bwithout\s+closing\b",
    r"\bnot\s+close\b",
    r"\bno\s+close\b",
    r"\bno\s+closeout\b",
    r"\bno\s+direct\s+closeout\b",
    r"\bclose\s+nothing(?:\s+yet)?\b",
    r"\bnot\s+adopt\s+evidence\b",
    r"不\s*close",
    r"不要\s*close",
    r"不要(直接)?关闭",
    r"不用关闭",
    r"无需关闭",
    r"不需要关闭",
    r"不要(直接)?执行验收",
)

_REVIEW_PATTERNS = (
    r"\bindependent(ly)?\s+review\b",
    r"\breview\s+.*\bindependent(ly)?\b",
    r"\bindependent(ly)?\s+.*\breview\b",
    r"\breview\s+independently\b",
    r"\breview\s+separately\b",
    r"\bindependent(ly)?\s+check\b",
    r"\bindependent(ly)?\s+.*\bcheck\b",
    r"\bcheck\s+.*\bindependent(ly)?\b",
    r"\bindependent(ly)?\s+.*\bcheck\b",
    r"\bstandalone\s+review\b",
    r"\bseparate\s+review\b",
    r"\bseparately\s+check\b",
    r"\bjust\s+review\b",
    r"\breview\s+this\s+fix\b",
    r"\breview\s+whether\s+this\s+can\s+close\b",
    r"\b(?:does|is)\s+this\s+evidence\s+(?:look\s+)?sufficient\s+for\s+closure\b",
    r"\bevidence\s+(?:look\s+)?sufficient\s+for\s+closure\b",
    r"\bclosure\s+sufficien(?:t|cy)\b",
    r"\breview\b.*\bacceptance\s+summary\b",
    r"\bno\s+direct\s+closeout\b",
    r"独立审查",
    r"独立检查",
    r"单独审查",
    r"单独检查",
    r"独立\s*review",
    r"单独.*review",
    r"复核",
)

_EXECUTE_PATTERNS = (
    r"\bimplement\b",
    r"\bexecute\b",
    r"\bfix\b",
    r"\bpatch\b",
    r"\bchange\b",
    r"\brebuild\b",
    r"\brecreate\b",
    r"\bredo\b",
    r"\badjust\b",
    r"\brevise\b",
    r"\bmodify\b",
    r"\bupdate\b",
    r"实现",
    r"修复",
    r"修改",
    r"调整",
    r"修订",
    r"更新",
    r"执行",
    r"重建",
    r"重做",
    r"重新建",
    r"改成新的方案",
)

_RECALL_PATTERNS = (
    r"\bwhy\b",
    r"\bhistory\b",
    r"\brationale\b",
    r"\blineage\b",
    r"\brecall\b",
    r"\bcompare\b",
    r"\bsource\s+chain\b",
    r"\bwhat\s+changed\s+between\b",
    r"\bchanged\s+between\b",
    r"\bversion\s+comparison\b",
    r"\bfrom\s+design\s+report\s+to\b",
    r"\bcode\s+changes\b",
    r"\bwithout\s+changing\s+anything\b",
    r"\bexplain\b.*\bno\s+governance\b",
    r"\bwhat\s+does\b.*\bno\s+governance\b",
    r"\bmeaning\s+of\b.*\bno\s+governance\b",
    r"\bno\s+governance\b.*\b(mean|means|meaning|phrase|concept|topic)\b",
    r"\bexplain\b.*\bdo\s+not\s+record\b",
    r"\bwhat\s+does\b.*\bdo\s+not\s+record\b",
    r"\bmeaning\s+of\b.*\bdo\s+not\s+record\b",
    r"\bdo\s+not\s+record\b.*\b(mean|means|meaning|phrase|concept|topic|policy|rule)\b",
    r"回顾",
    r"为什么",
    r"历史",
    r"解释.*(不要使用\s*shujuan|不要记录|不用治理|不做治理|no governance)",
    r"(不要使用\s*shujuan|不要记录|不用治理|不做治理).*是什么意思",
    r"(不要使用\s*shujuan|不要记录|不用治理|不做治理).*含义",
)


def _hits(text: str, patterns: tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(match.group(0) for pattern in patterns for match in re.finditer(pattern, text, re.IGNORECASE)))


@dataclass(frozen=True)
class RouteIntentFacts:
    asks_recall: bool
    asks_review: bool
    asks_close: bool
    negates_close: bool
    asks_execute: bool
    closeout_inputs_complete: bool
    evidence_phrases: dict[str, list[str]]

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def parse_route_intent(text: str, *, closeout_inputs_complete: bool = False) -> RouteIntentFacts:
    close_hits = _hits(text, _CLOSE_PATTERNS)
    negated_close_hits = _hits(text, _NEGATED_CLOSE_PATTERNS)
    return RouteIntentFacts(
        asks_recall=bool(_hits(text, _RECALL_PATTERNS)),
        asks_review=bool(_hits(text, _REVIEW_PATTERNS)),
        asks_close=bool(close_hits),
        negates_close=bool(negated_close_hits),
        asks_execute=bool(_hits(text, _EXECUTE_PATTERNS)),
        closeout_inputs_complete=closeout_inputs_complete,
        evidence_phrases={
            "recall": _hits(text, _RECALL_PATTERNS),
            "review": _hits(text, _REVIEW_PATTERNS),
            "close": close_hits,
            "negated_close": negated_close_hits,
            "execute": _hits(text, _EXECUTE_PATTERNS),
        },
    )
