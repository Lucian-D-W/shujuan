from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Mapping
from typing import Any

from ..services.method_policy import method_payload
from ..services.relation_policy import classify_relation
from ..services.route_intent import parse_route_intent


RouteHandler = Callable[[argparse.Namespace], int]
ROUTE_HANDLER_KEYS = ("guard",)
ROUTE_DEPENDENCY_KEYS = (
    "connect",
    "read_arg_or_stdin",
    "suggest_mode_from_args",
    "explicit_no_governance_reasons",
    "recover_like_reasons",
    "resolve_current_endpoint",
    "query_endpoint",
    "endpoint_status_payload",
    "print_json",
    "append_trace_event",
)


def _configure(deps: Mapping[str, Any]) -> None:
    missing = [key for key in ROUTE_DEPENDENCY_KEYS if key not in deps]
    if missing:
        raise RuntimeError(f"route command boundary is missing: {', '.join(missing)}")
    globals().update({key: deps[key] for key in ROUTE_DEPENDENCY_KEYS})


def _read_intent(args: argparse.Namespace) -> str:
    return read_arg_or_stdin(getattr(args, "intent", None), file_path=getattr(args, "intent_file", None), label="intent")


def _close_intent(text: str) -> bool:
    facts = parse_route_intent(text)
    return facts.asks_close and not facts.negates_close


_NEGATED_REVIEW_PATTERNS = (
    "不需要独立审查",
    "无需独立审查",
    "不用独立审查",
    "不要独立审查",
    "不需要独立检查",
    "无需独立检查",
    "不用独立检查",
    "不要独立检查",
    "不需要审查",
    "无需审查",
    "不用审查",
    "不要审查",
    "不审查",
    "不需要 reviewer",
    "不需要reviewer",
    "无需 reviewer",
    "无需reviewer",
    "不要 reviewer",
    "不要reviewer",
    "no review",
    "without review",
    "do not review",
    "don't review",
    "skip review",
    "skip reviewer",
    "skip the reviewer",
)
_REVIEW_OR_DELEGATE_TOKENS = (
    "reviewer",
    "worker",
    "researcher",
    "writer",
    "independent review",
    "review packet",
    "worker packet",
    "researcher packet",
    "writer packet",
    "delegate",
    "handoff",
    "controller adoption",
    "controller adopt",
    "wait for controller",
    "do not adopt evidence",
    "审查包",
    "独立审查",
    "独立检查",
    "单独审查",
    "单独检查",
    "复核",
    "研究员",
    "调研员",
    "撰写者",
    "等待 controller",
    "不要采纳",
)


def _closeout_execution_intent(text: str) -> bool:
    facts = parse_route_intent(text)
    return facts.asks_close and not facts.negates_close


def _acceptance_review_intent(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        r"能不能过验收",
        r"是否通过验收",
        r"是否可以验收",
        r"请验收",
        r"帮我验收",
        r"请帮我验收",
        r"验收这个",
        r"验收一下",
        r"检查是否通过",
        r"这个修复通过了吗",
        r"看一下是否通过",
        r"帮我看看.*验收",
        r"review if this passes",
        r"please review this fix",
        r"just review this fix",
        r"review this fix.*acceptance summary",
        r"review this fix and give acceptance summary",
        r"separately check if this fix passes",
        r"review whether accepted",
        r"review whether this passes acceptance",
        r"review whether this can close",
        r"does this pass acceptance",
        r"does this evidence look sufficient for closure",
        r"is this evidence sufficient for closure",
        r"evidence look sufficient for closure",
        r"evidence sufficient for closure",
        r"is this accepted",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _primary_execute_intent(text: str) -> bool:
    lowered = text.lower().strip()
    patterns = (
        r"^(?:please\s+|can\s+you\s+|could\s+you\s+|help\s+me\s+)?(?:implement|fix|patch|change|update|modify|adjust|revise|add)\b",
        r"^(?:请|麻烦|帮我|请你|麻烦你)?(?:实现|修复|修改|调整|修订|更新|执行)\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _action_execute_intent(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        r"\b(?:then|and|also)\s+(?:implement|fix|patch|change|update|modify|adjust|revise|add)\b",
        r"\b(?:please|need\s+to|can\s+you|could\s+you|help\s+me)\b.*\b(?:implement|fix|patch|change|update|modify|adjust|revise|add)\b.*\b(?:bug|issue|route|hook|test|script|code|logic|file|behavior|regression|skill|text)\b",
        r"\b(?:implement|fix|patch|change|update|modify|adjust|revise|add)\s+(?:this|that|the|a|an)?\s*(?:bug|issue|route|hook|test|script|code|logic|file|behavior|regression|skill|text)\b",
        r"(?:然后|并|再|同时|顺便).*?(?:实现|修复|修改|调整|修订|更新|执行)",
        r"(?:请|麻烦|帮我|请你|麻烦你|需要).*?(?:实现|修复|修改|调整|修订|更新|执行).*?(?:bug|问题|误判|代码|route|hook|脚本|测试|文件|逻辑|回归|skill|文本)",
        r"(?:实现|修复|修改|调整|修订|更新|执行).*?(?:bug|问题|误判|代码|route|hook|脚本|测试|文件|逻辑|回归|skill|文本)",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _role_bound_delegate_intent(text: str) -> bool:
    lowered = text.lower()
    role = r"(?:worker|researcher|writer|reviewer|provider|研究员|调研员|撰写者|作者|审查者|复核者)"
    patterns = (
        rf"\b(?:ask|have|use|let|tell|assign)\s+(?:a\s+|the\s+)?{role}\b",
        rf"\b{role}\b.*\b(?:packet|handoff|return|material|draft|gather|research|review|check|implement|patch|modify|wait\s+for\s+controller|controller\s+adoption|do\s+not\s+adopt)\b",
        r"\bworker\s+says\b.*\b(?:done|complete|finished)\b",
        r"\bworker\b.*\b(?:done|complete|finished)\b.*\bclose\s+nothing\b",
        rf"\bcan\s+(?:a\s+|the\s+)?{role}\b.*\b(?:refresh|close|adopt|claim|write|execute)\b",
        r"\bwait\s+for\s+controller\s+adoption\b",
        r"\bcontroller\s+do\s+not\s+adopt\b",
        r"\bnot\s+adopt\s+evidence\b",
        r"让\s*(?:worker|researcher|writer|reviewer|研究员|调研员|撰写者|审查者|复核者)",
        r"(?:worker|researcher|writer|reviewer|研究员|调研员|撰写者|审查者|复核者).*?(?:材料|草稿|调研|研究|审查|复核|修改|修复|等待|采纳)",
        r"controller\s*不要采纳",
        r"不要采纳",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _explicit_parallel_delegate_intent(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        r"\buse\s+\d+\s+(?:subagents?|sub-agents?|agents?)\b",
        r"\b\d+\s+(?:subagents?|sub-agents?|agents?)\b.*\b(?:material|review|audit|brute[- ]force|outputs?)\b",
        r"\b(?:subagents?|sub-agents?|multi-agent|parallel\s+agents?)\b.*\b(?:material|review|audit|brute[- ]force|outputs?)\b",
        r"(?:子代理|多代理|并行代理).*?(?:材料|审查|复核|暴力测试|输出)",
        r"(?:使用|派出|启动)\s*\d+\s*(?:个)?\s*(?:子代理|代理)",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _external_provider_mentioned(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("gitnexus", "codegraph", "provider"))


def _external_provider_boundary_risk(text: str) -> bool:
    lowered = text.lower()
    if not _external_provider_mentioned(lowered):
        return False
    risk_patterns = (
        r"\b(?:gitnexus|codegraph|provider)\b.*\b(?:says|verified|proves?|adopt|evidence|close|skip\s+tests?|worker|only\s+.*impacted)\b",
        r"\b(?:says|verified|proves?|adopt|evidence|close|skip\s+tests?|worker|only\s+.*impacted)\b.*\b(?:gitnexus|codegraph|provider)\b",
        r"自动采纳.*(?:gitnexus|codegraph|provider)",
        r"(?:gitnexus|codegraph|provider).*?(?:自动采纳|证据|关闭|跳过测试|worker|工人)",
    )
    return any(re.search(pattern, lowered) for pattern in risk_patterns)


def _material_only_delegate_intent(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        r"\bworker\s+return\b.*\bmaterial\b",
        r"\bmaterial\b.*\bworker\s+return\b",
        r"\breturn\s+material\b",
        r"\bmaterial\s+only\b",
        r"\bdo\s+not\s+claim\s+(?:the\s+)?task\b",
        r"\bdon't\s+claim\s+(?:the\s+)?task\b",
        r"只需要.*(?:worker\s*return|材料)",
        r"(?:worker\s*return|材料).*不要认领任务",
        r"不要认领任务",
        r"不认领任务",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _negated_review_intent(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in _NEGATED_REVIEW_PATTERNS)


def _formal_importer_needed(text: str) -> bool:
    lowered = text.lower()
    bulk_tokens = (
        "task chain",
        "task_chain",
        "long plan",
        "long implementation plan",
        "implementation plan",
        "large plan",
        "many tasks",
        "many checks",
        "tasks and checks",
        "task/check",
        "task / check",
        "split into tasks and checks",
        "decompose into tasks and checks",
        "many writes",
        "batch write",
        "bulk write",
        "bulk create",
        "create many tasks",
        "import plan",
        "import this plan",
        "import into db",
        "governance db",
        "governance database",
        "turn this plan into",
        "decompose this plan",
        "repair mapping",
        "批量",
        "批量创建",
        "长计划",
        "导入计划",
        "导入数据库",
        "导入这些任务和检查",
        "任务链",
        "拆成任务和检查",
        "拆成任务/检查",
        "拆解成任务和检查",
        "分解成任务和检查",
        "转成任务和检查",
        "转成任务和验收标准",
        "拆成任务和验收标准",
        "拆解成任务和验收标准",
        "分解成任务和验收标准",
    )
    target_tokens = (
        "task",
        "tasks",
        "check",
        "checks",
        "db",
        "database",
        "endpoint",
        "task/check",
        "任务",
        "检查",
        "验收",
        "数据库",
        "落库",
        "端点",
    )
    return any(token in lowered for token in bulk_tokens) and any(token in lowered for token in target_tokens)


def _evolve_boundary_risks(text: str) -> list[str]:
    lowered = text.lower()
    risks: list[str] = []
    if re.search(r"\bnew\s+(?:db|database)\s+table\b|\badd\s+a\s+new\s+db\s+table\b|新.*(?:数据库表|db\s*表)|fact[- ]plane", lowered):
        risks.append("new fact-plane/schema table requires explicit scope approval")
    if "sqlite fallback" in lowered or ("sqlite" in lowered and "fallback" in lowered) or ("sqlite" in lowered and "postgresql" in lowered):
        risks.append("SQLite fallback is not a shujuan runtime write path")
    if ("hook" in lowered and "authoritative" in lowered) or ("hook" in lowered and "block" in lowered and "bad routes" in lowered):
        risks.append("hooks are advisory; route guard remains authoritative")
    if ".agents" in lowered and "assets" in lowered and any(token in lowered for token in ("不用", "不要", "without", "only", "只改")):
        risks.append("skill edits must keep source/assets/package mirrors aligned")
    if "route behavior" in lowered and ("without tests" in lowered or "no tests" in lowered or "不用测试" in lowered):
        risks.append("route behavior changes require regression tests")
    return risks


def _project_memory_target_risk(text: str) -> bool:
    return "project-memory" in text.lower()


def _wrapper_loop_risk(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "wrapper loop",
            "wrapper subprocess loop",
            "subprocess loop",
            "shell loop",
            "for each task",
            "loop over tasks",
            "wrapper",
        )
    )


def _capture_intent(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        r"\bcapture\s+(?:this|that|the|these|those)?\s*(discussion|conversation|transcript|source|sources|snippet|snippets|material|materials|prompt)\b",
        r"\brecord\s+(?:this|that|the|these|those)?\s*(discussion|conversation|transcript|source|sources|snippet|snippets|material|materials|prompt)\b",
        r"\bsave\s+(?:this|that|the|these|those)?\s*(discussion|conversation|transcript|source|sources|snippet|snippets|material|materials|prompt)\b",
        r"\bimport\s+(?:this|that|the|these|those)?\s*(discussion|conversation|transcript|source|sources|snippet|snippets|material|materials|prompt)\b",
        r"\bbookmark\s+(?:this|that|the|these|those)?\s*(discussion|conversation|transcript|source|sources|snippet|snippets|material|materials|prompt)\b",
        r"\bnote\s+down\s+(?:this|that|the|these|those)?\s*(discussion|conversation|transcript|source|sources|snippet|snippets|material|materials|prompt)\b",
        r"\bkeep\s+(?:this|that|the|these|those)?\s*(discussion|conversation|transcript|source|sources|snippet|snippets|material|materials|prompt)\s+for\s+traceability\b",
        r"\b(?:bookmark|note\s+down|keep|save|record)\b.*\btraceability\b",
        r"\blog\s+as\s+(?:source|provenance|context)\b",
        r"\blog\s+(?:this|that|the|these|those)?\s*(?:discussion|conversation|transcript|source|sources|snippet|snippets|material|materials|prompt)?\s*(?:as|for)\s+(?:source|provenance|context)\b",
        r"\bstash\s+(?:this|that|the|these|those)?\s*(conversation|context|source\s+material|source|sources|material|materials)\b",
        r"\bpreserve\s+provenance\b",
        r"\bkeep\s+for\s+traceability\s+only\b",
        r"捕获(这段|这个)?(讨论|对话|材料|来源|提示)",
        r"记录(这段|这个)?(讨论|对话|材料|来源|提示)",
        r"保存(这段|这个)?(讨论|对话|材料|来源|提示)",
        r"导入(这段|这个)?(讨论|对话|材料|来源|提示)",
        r"记下(这段|这个)?(讨论|对话|材料|来源|提示)",
        r"留存(这段|这个)?(讨论|对话|材料|来源|提示)",
        r"保留来源",
        r"保留出处",
        r"存档来源",
        r"作为出处保存",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _auxiliary_recall_needed(intent: str, route: str, intent_facts: Any) -> bool:
    return route in {"Execute", "Recover", "Delegate"} and bool(intent_facts.asks_recall)


def _relation_write_gate_needed(relation_decision: dict[str, Any]) -> bool:
    if not relation_decision.get("predecessor_required"):
        return False
    relation_type = str(relation_decision.get("relation_type") or "")
    if relation_type in {"revision_or_contradiction", "successor_scope", "fork_variant"}:
        return True
    evidence = " ".join(str(item).lower() for item in relation_decision.get("evidence_phrases") or [])
    gate_tokens = (
        "previous",
        "recent",
        "last hour",
        "just created",
        "task from before",
        "上一个任务",
        "上个任务",
        "前一个任务",
        "前一个小时",
        "刚才",
        "刚建立",
        "旧任务",
        "不对",
        "取代",
        "重建",
        "替代",
        "分叉",
        "平行",
        "后续",
        "独立创建",
    )
    return any(token in evidence for token in gate_tokens)


def _infer_route(intent: str, *, endpoint_name: str | None, relation_decision: dict[str, Any], intent_facts: Any | None = None) -> tuple[str, list[str]]:
    text = intent.lower()
    facts = intent_facts or parse_route_intent(intent)
    primary_execute = _primary_execute_intent(intent)
    action_execute = primary_execute or _action_execute_intent(intent) or bool(facts.asks_execute)
    reasons: list[str] = []
    if (facts.asks_recall or any(token in text for token in ("why", "history", "rationale", "lineage", "recall", "回顾", "为什么", "历史"))) and not action_execute:
        return "Recall", ["history_or_rationale"]
    if _project_memory_target_risk(intent):
        return "Recover", ["project_memory_requires_endpoint_binding"]
    if _external_provider_boundary_risk(intent):
        return "Delegate", ["external_provider_material_boundary"]
    if _explicit_parallel_delegate_intent(intent):
        return "Delegate", ["explicit_parallel_delegate_material"]
    if facts.asks_close and facts.negates_close:
        return "Delegate", ["close_boundary_negated_material"]
    if _role_bound_delegate_intent(intent):
        return "Delegate", ["role_bound_delegate_material"]
    if _capture_intent(intent) and facts.asks_close and not facts.negates_close:
        return "Recover", ["capture_close_collision_requires_relation_matrix"]
    if len(_split_multi_intent_items(intent)) >= 2:
        return "Recover", ["multi_intent_relation_matrix_required"]
    if _evolve_boundary_risks(intent):
        return "Recover", ["evolve_boundary_read_first"]
    if facts.asks_close and not facts.negates_close:
        return "Close", ["close_request"]
    if relation_decision.get("relation_type") == "independent_review":
        return "Delegate", ["independent_review_material"]
    if facts.asks_review and facts.negates_close:
        return "Delegate", ["review_material_negates_close"]
    if _material_only_delegate_intent(intent) and not primary_execute:
        return "Delegate", ["material_only_delegate"]
    if _acceptance_review_intent(intent):
        return "Delegate", ["acceptance_review_material"]
    if not _negated_review_intent(intent) and any(token in text for token in _REVIEW_OR_DELEGATE_TOKENS):
        return "Delegate", ["review_or_delegate"]
    if _capture_intent(intent) and not facts.asks_execute and not _formal_importer_needed(intent):
        return "Capture", ["source_material_capture"]
    if action_execute and _relation_write_gate_needed(relation_decision):
        return "Recover", [f"relation_predecessor_gate:{relation_decision.get('relation_type')}"]
    if action_execute:
        return "Execute", ["primary_execute_request"]
    if relation_decision.get("route_hint"):
        hinted = str(relation_decision["route_hint"])
        if hinted != "Execute":
            return hinted, [f"relation:{relation_decision['relation_type']}"]
    if any(token in text for token in ("resume", "recover", "handoff", "接手", "继续", "恢复")):
        return "Recover", ["recover_like"]
    return "Execute", ["default_execute"]


def _goal_from_intent(intent: str) -> str:
    compact = " ".join(intent.strip().split())
    return compact[:220] if compact else "Clarify the current user goal before any writeful route."


def _first_surface(route: str, endpoint_name: str | None) -> dict[str, Any]:
    if route == "No Governance":
        return {"kind": "ordinary_answer", "command": None}
    if route == "Recall":
        return {
            "kind": "report",
            "command": f"python -m shujuan report endpoint {endpoint_name} --full --markdown" if endpoint_name else "python -m shujuan report project --markdown",
        }
    if route == "Recover":
        return {
            "kind": "report",
            "command": f"python -m shujuan report endpoint {endpoint_name} --active-only --markdown" if endpoint_name else "python -m shujuan report project --overview --markdown",
        }
    if route == "Delegate":
        return {
            "kind": "review_bundle",
            "command": f"python -m shujuan review start --endpoint {endpoint_name}" if endpoint_name else "python -m shujuan endpoint suggest --from-prompt prompt.txt --top 3",
        }
    if route == "Capture":
        return {
            "kind": "source_material",
            "command": "source text plus explicit capture mode; do not infer tasks/checks/closure",
        }
    if route == "Close":
        return {
            "kind": "closeout_inputs",
            "command": f"python -m shujuan endpoint status {endpoint_name}" if endpoint_name else "python -m shujuan endpoint suggest --from-prompt prompt.txt --top 3",
        }
    return {
        "kind": "route_contract",
        "command": (
            f"python -m shujuan report endpoint {endpoint_name} --active-only --markdown"
            if endpoint_name
            else "python -m shujuan report project --overview --markdown"
        ),
    }


def _forbidden_actions(route: str, *, no_governance: bool, close_without_inputs: bool, intent: str = "") -> list[str]:
    actions: list[str] = []
    if no_governance:
        actions.extend(["workflow begin", "exec start", "endpoint refresh", "evidence closeout", "scope change --task"])
    if route == "Delegate":
        actions.extend(["claim reviewer_executed without reviewer return artifact", "treat delegated material as controller adoption or closure evidence"])
    if route == "Execute":
        actions.append("wrapper subprocess loop for bulk task/check import")
    if close_without_inputs:
        actions.append("close checks/tasks without endpoint/task/check/evidence inputs")
    if _external_provider_mentioned(intent):
        actions.append("treat GitNexus/CodeGraph/provider output as shujuan method authority or closure evidence")
        if _external_provider_boundary_risk(intent):
            actions.append("skip tests or close tasks from external provider material")
    if _project_memory_target_risk(intent):
        actions.append("use project-memory as fallback endpoint without evidence-backed binding")
    actions.extend(_evolve_boundary_risks(intent))
    return actions[:5]


def _safe_next_action(route: str, intent: str, endpoint_name: str | None, *, closeout_complete: bool, relation_decision: dict[str, Any]) -> str:
    lowered = intent.lower()
    if route == "No Governance":
        return "Answer directly outside shujuan and stop governance writes."
    if _project_memory_target_risk(intent):
        return "Bind an evidence-backed endpoint; do not use project-memory as a fallback endpoint."
    if relation_decision.get("predecessor_required") and not endpoint_name:
        return str(relation_decision.get("predecessor_hint") or "Bind the predecessor scope before writeful work.")
    if len(_split_multi_intent_items(intent)) >= 2:
        return "Create a Task Relation Matrix first; bind endpoint/predecessor/write permission per item before any closeout or execution."
    if route == "Delegate":
        if _external_provider_mentioned(intent):
            return "Treat GitNexus/CodeGraph/provider output as external material only; controller adoption and closure remain separate."
        if parse_route_intent(intent).asks_recall:
            return "Read the relevant Recall surface first, then generate or consume role-bounded Delegate material without controller adoption."
        return "Generate or consume role-bounded Delegate material, but keep controller adoption and closure claims separate."
    if route == "Recall":
        return "Use endpoint report or recall frontier, answer with anchors and unsearched frontier, and keep DB writes at zero."
    if _formal_importer_needed(lowered) or "计划" in lowered:
        return "Run plan-to-db import-task-chain --dry-run after the endpoint and source artifact are confirmed."
    if route == "Capture":
        return "Capture source material with provenance only; do not infer tasks, checks, execution, or closure."
    if route == "Close":
        if closeout_complete:
            return "Run the Close chain: match evidence, refresh the endpoint, run evidence verify, then strict doctor."
        return "Collect explicit closeout inputs first; do not guess endpoint, task, check, or evidence ids."
    if route == "Recover":
        return f"Load the active endpoint surface for {endpoint_name} before deciding whether the next route is Execute or Close." if endpoint_name else "Use endpoint suggest or project overview before any writeful command."
    if route == "Execute" and parse_route_intent(intent).asks_recall:
        return "Read the relevant Recall surface first, then continue only with the scoped Execute implementation."
    return "Stay read-only until the first surface confirms endpoint, route, and batch entrance."


_ITEM_PREFIX_RE = re.compile(
    r"(?:^|[；;\n]|(?<=。)|(?<=：)|(?<=:)|(?<=，)|(?<=,))\s*("
    r"[A-Z]\s*(?:端点|endpoint|[.:：、])|"
    r"\d+\s*[).、:：]|"
    r"第[一二三四五六七八九十]+个?"
    r")",
    re.IGNORECASE,
)


def _split_multi_intent_items(intent: str, *, limit: int = 12) -> list[dict[str, str]]:
    text = " ".join(intent.strip().split())
    if not text:
        return []
    markers = list(_ITEM_PREFIX_RE.finditer(text))
    if len(markers) >= 2:
        items: list[dict[str, str]] = []
        for index, match in enumerate(markers[:limit]):
            start = match.start()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            chunk = text[start:end].strip(" ，,；;。")
            prefix = match.group(1).strip()
            body = text[match.end():end].strip(" ：:，,；;。")
            items.append({"item_ref": prefix, "text": body or chunk})
        return items
    if any(token in text for token in ("两个任务", "多任务", "同时", "分别")):
        chunks = [part.strip(" ：:，,；;。") for part in re.split(r"[；;\n]|\s+and\s+", text) if part.strip()]
        if len(chunks) >= 2:
            return [{"item_ref": str(index + 1), "text": chunk} for index, chunk in enumerate(chunks[:limit])]
    lowered = text.lower()
    if re.search(r"\bone\s+item\b.*\banother\b", lowered):
        chunks = [part.strip(" ：:，,；;。.") for part in re.split(r"\band\s+another\b", text, flags=re.IGNORECASE) if part.strip()]
        if len(chunks) >= 2:
            return [{"item_ref": str(index + 1), "text": chunk} for index, chunk in enumerate(chunks[:limit])]
    natural_separator = re.search(r"[，,、；;]|\s+and\s+|(?:然后|接着|并且)|\bthen\b", text, re.IGNORECASE)
    deliverable_hits = re.findall(
        r"\b(?:skills?|tests?|hooks?|tasks?|checks?|evidence|endpoint|route|assets?|packages?|schema|policy|code|docs?)\b|"
        r"(?:任务|检查|证据|端点|测试|方案|关闭|修|补|拆|代码|文档|策略)",
        lowered,
        re.IGNORECASE,
    )
    if natural_separator and len(deliverable_hits) >= 3:
        chunks = [
            part.strip(" ：:，,；;。.")
            for part in re.split(r"[，,、；;]|\s+and\s+|(?:然后|接着|并且)|\bthen\b", text, flags=re.IGNORECASE)
            if part.strip(" ：:，,；;。.")
        ]
        if len(chunks) >= 2:
            return [{"item_ref": str(index + 1), "text": chunk} for index, chunk in enumerate(chunks[:limit])]
    return []


def _multi_intent_plan(intent: str, *, endpoint_name: str | None, limit: int = 12) -> dict[str, Any]:
    raw_items = _split_multi_intent_items(intent, limit=limit)
    if len(raw_items) < 2:
        return {
            "detected": False,
            "bounded": True,
            "max_items": limit,
            "items": [],
            "ordinary_multitask_policy": "Use Task Relation Matrix first; do not auto-spawn subagents unless explicitly requested.",
        }
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        text = raw["text"]
        relation = classify_relation(text, endpoint_name=endpoint_name)
        facts = parse_route_intent(text)
        route, reasons = _infer_route(text, endpoint_name=endpoint_name, relation_decision=relation, intent_facts=facts)
        if route == "Execute" and relation.get("predecessor_required"):
            route = "Recover"
            reasons = [*reasons, "multi_intent_predecessor_gate"]
        if relation.get("predecessor_required"):
            write_permission = "blocked_until_predecessor_bound"
        elif route in {"Recover", "Delegate", "Close"}:
            write_permission = "blocked_until_route_inputs_confirmed"
        else:
            write_permission = "read_first_then_execute_if_scope_bound"
        method = (method_payload(route, intent=text).get("method_contract") or {}).get("method")
        items.append(
            {
                "item_ref": raw["item_ref"],
                "text": text,
                "recommended_route": route,
                "route_reasons": reasons,
                "relation_type": relation.get("relation_type"),
                "decision_type": relation.get("decision_type"),
                "predecessor_required": bool(relation.get("predecessor_required")),
                "predecessor_hint": relation.get("predecessor_hint"),
                "endpoint_candidate": endpoint_name,
                "method": method,
                "write_permission": write_permission,
                "auxiliary_recall": _auxiliary_recall_needed(text, route, facts),
            }
        )
    return {
        "detected": True,
        "bounded": True,
        "max_items": limit,
        "items": items,
        "safe_next_action": "Bind endpoint/predecessor per item before task-chain import or writeful execution.",
        "ordinary_multitask_policy": "Use Task Relation Matrix first; do not auto-spawn subagents unless explicitly requested.",
    }


def _write_state(*, trace_explicit: bool, trace_written: bool) -> dict[str, Any]:
    return {
        "filesystem_writes": 1 if trace_written else 0,
        "db_writes": 0,
        "trace_explicit": trace_explicit,
        "trace_written": trace_written,
    }


def _pure_route_payload(
    *,
    args: argparse.Namespace,
    intent: str,
    relation_decision: dict[str, Any],
    recommended_mode: str,
    mode_reasons: list[str],
) -> tuple[dict[str, Any], int]:
    endpoint_name = getattr(args, "endpoint", None)
    missing_closeout_fields = [
        field
        for field, value in (
            ("endpoint", endpoint_name),
            ("task_id", getattr(args, "task_id", None)),
            ("check_id", getattr(args, "check_id", None)),
            ("expected_evidence_type", getattr(args, "expected_evidence_type", None)),
            ("current_matching_evidence_ref", getattr(args, "current_matching_evidence_ref", None)),
        )
        if not value
    ]
    no_governance_exit = relation_decision["relation_type"] == "no_governance_exit" or recommended_mode == "no_governance"
    if no_governance_exit:
        route = "No Governance"
        route_reasons = ["explicit_no_governance" if relation_decision["relation_type"] == "no_governance_exit" else "explicit_mode_no_governance"]
        recommended_mode = "no_governance"
        intent_facts = parse_route_intent(intent)
    else:
        intent_facts = parse_route_intent(intent, closeout_inputs_complete=not missing_closeout_fields)
        route, route_reasons = _infer_route(intent, endpoint_name=endpoint_name, relation_decision=relation_decision, intent_facts=intent_facts)
        if _formal_importer_needed(intent) or _wrapper_loop_risk(intent):
            if route == "Execute":
                route = "Recover"
                route_reasons = [*route_reasons, "formal_batch_import_requires_read_only_entrance"]
            if recommended_mode not in {"no_governance", "capture", "explore"}:
                recommended_mode = "explore"
                mode_reasons = [*mode_reasons, "downgraded_for_formal_batch_import"]
        if route == "Delegate" and recommended_mode not in {"no_governance", "capture", "explore"}:
            recommended_mode = "explore"
            mode_reasons = [*mode_reasons, "delegate_route_prefers_explore"]
        if route == "Capture" and recommended_mode != "no_governance":
            recommended_mode = "capture"
            mode_reasons = [*mode_reasons, "capture_route_source_material_only"]
        if route != "Recall" and intent_facts.asks_close and not intent_facts.negates_close and not missing_closeout_fields:
            route = "Close"
            route_reasons = [*route_reasons, "close_request_inputs_complete"]
    close_intent = intent_facts.asks_close and not intent_facts.negates_close
    close_without_inputs = route == "Close" and close_intent and bool(missing_closeout_fields)
    payload = {
        "ok": not close_without_inputs,
        "pure": True,
        "read_only": True,
        **_write_state(trace_explicit=bool(getattr(args, "trace", False)), trace_written=False),
        "runtime_access": "skipped_pure",
        "user_goal": _goal_from_intent(intent),
        "recommended_mode": "explore" if close_without_inputs and recommended_mode != "no_governance" else recommended_mode,
        "recommended_route": route,
        "authority_posture": "controller_close" if route == "Close" else ("reviewer_material" if route == "Delegate" else ("capture_source_material" if route == "Capture" else relation_decision["authority_posture"])),
        "authority_hint": "controller_close" if route == "Close" else ("reviewer_material" if route == "Delegate" else ("capture_source_material" if route == "Capture" else relation_decision["authority_hint"])),
        "relation_decision": relation_decision,
        "multi_intent_plan": _multi_intent_plan(intent, endpoint_name=endpoint_name),
        "confidence": "pure_classification",
        "endpoint_status": {
            "candidate": endpoint_name,
            "status": "skipped_pure",
            "write_allowed": False,
        },
        "first_surface": _first_surface(route, endpoint_name),
        **method_payload(route, intent=intent),
        "intent_facts": intent_facts.payload(),
        "auxiliary_recall": _auxiliary_recall_needed(intent, route, intent_facts),
        "forbidden_next_actions": _forbidden_actions(route, no_governance=route == "No Governance", close_without_inputs=close_without_inputs, intent=intent),
        "safe_next_action": _safe_next_action(
            route,
            intent,
            endpoint_name,
            closeout_complete=close_intent and not missing_closeout_fields,
            relation_decision=relation_decision,
        ),
        "exit_brake": {
            "read_only": True,
            "no_governance": route == "No Governance",
            "friction_brake": route == "No Governance" or close_without_inputs,
            "stop_writes": True,
            "reason": "explicit_no_governance" if route == "No Governance" else ("missing_closeout_inputs" if close_without_inputs else "pure_classification"),
        },
        "reasons": {"mode": mode_reasons, "route": [*route_reasons, *(["close_request_missing_required_inputs"] if close_without_inputs else [])]},
    }
    if close_without_inputs:
        payload["error"] = {
            "code": "missing_closeout_inputs",
            "message": "Close requests require endpoint, task_id, check_id, expected_evidence_type, and current_matching_evidence_ref.",
            "missing_fields": missing_closeout_fields,
        }
    return payload, 1 if close_without_inputs else 0


def build_route_handlers(deps: Mapping[str, Any]) -> dict[str, RouteHandler]:
    _configure(deps)

    def route_guard(args: argparse.Namespace) -> int:
        repo = args.repo.resolve()
        trace_explicit = bool(getattr(args, "trace", False))
        intent = _read_intent(args)
        relation_decision = classify_relation(intent, endpoint_name=getattr(args, "endpoint", None))
        probe_args = argparse.Namespace(
            intent=intent,
            mode=getattr(args, "mode", None),
            no_governance=False,
            capture_only=False,
        )
        recommended_mode, mode_reasons = suggest_mode_from_args(probe_args)
        if getattr(args, "pure", False):
            payload, code = _pure_route_payload(
                args=args,
                intent=intent,
                relation_decision=relation_decision,
                recommended_mode=recommended_mode,
                mode_reasons=mode_reasons,
            )
            print_json(payload)
            return code
        if relation_decision["relation_type"] == "no_governance_exit" or recommended_mode == "no_governance":
            payload = {
                "ok": True,
                "read_only": True,
                **_write_state(trace_explicit=trace_explicit, trace_written=False),
                "user_goal": _goal_from_intent(intent),
                "recommended_mode": "no_governance",
                "recommended_route": "No Governance",
                "authority_posture": relation_decision["authority_posture"],
                "authority_hint": relation_decision["authority_hint"],
                "relation_decision": relation_decision,
                "multi_intent_plan": _multi_intent_plan(intent, endpoint_name=args.endpoint),
                "endpoint_status": {
                    "candidate": args.endpoint,
                    "status": "skipped_no_governance",
                    "write_allowed": False,
                },
                "first_surface": _first_surface("No Governance", args.endpoint),
                "forbidden_next_actions": _forbidden_actions("No Governance", no_governance=True, close_without_inputs=False),
                "safe_next_action": _safe_next_action(
                    "No Governance",
                    intent,
                    args.endpoint,
                    closeout_complete=False,
                    relation_decision=relation_decision,
                ),
                "exit_brake": {
                    "read_only": True,
                    "no_governance": True,
                    "friction_brake": True,
                    "stop_writes": True,
                    "reason": "explicit_no_governance",
                },
                **method_payload("No Governance", intent=intent),
                "intent_facts": parse_route_intent(intent).payload(),
                "auxiliary_recall": False,
                "reasons": {"mode": mode_reasons, "route": ["explicit_no_governance" if relation_decision["relation_type"] == "no_governance_exit" else "explicit_mode_no_governance"]},
            }
            if trace_explicit:
                append_trace_event(
                    repo,
                    event_type="route_guard",
                    endpoint=args.endpoint,
                    route="No Governance",
                    mode="no_governance",
                    read_only=True,
                    status="no_governance_exit",
                    details={"intent_excerpt": re.sub(r"\s+", " ", intent)[:240]},
                )
                payload.update(_write_state(trace_explicit=trace_explicit, trace_written=True))
            print_json(payload)
            return 0
        conn = None
        try:
            conn = connect(repo)
        except BaseException:
            conn = None
        endpoint_name = args.endpoint or (resolve_current_endpoint(repo, conn) if conn is not None else None)
        endpoint_status = None
        endpoint_state = "missing"
        if endpoint_name and conn is not None:
            try:
                endpoint_row = query_endpoint(conn, endpoint_name)
                endpoint_name = str(endpoint_row["name"])
                endpoint_status = endpoint_status_payload(conn, endpoint_name, include_chain=False)
                endpoint_state = "known"
            except BaseException:
                endpoint_state = "unknown"
        elif endpoint_name and conn is None:
            endpoint_state = "unavailable_without_runtime"
        missing_closeout_fields = [
            field
            for field, value in (
                ("endpoint", endpoint_name),
                ("task_id", getattr(args, "task_id", None)),
                ("check_id", getattr(args, "check_id", None)),
                ("expected_evidence_type", getattr(args, "expected_evidence_type", None)),
                ("current_matching_evidence_ref", getattr(args, "current_matching_evidence_ref", None)),
            )
            if not value
        ]
        intent_facts = parse_route_intent(intent, closeout_inputs_complete=not missing_closeout_fields)
        route, route_reasons = _infer_route(intent, endpoint_name=endpoint_name, relation_decision=relation_decision, intent_facts=intent_facts)
        close_intent = intent_facts.asks_close and not intent_facts.negates_close
        formal_importer_needed = _formal_importer_needed(intent)
        wrapper_loop_risk = _wrapper_loop_risk(intent)
        high_friction_batch = formal_importer_needed or wrapper_loop_risk
        if high_friction_batch and route == "Execute":
            route = "Recover"
            route_reasons = [*route_reasons, "formal_batch_import_requires_read_only_entrance"]
        if high_friction_batch and recommended_mode not in {"no_governance", "capture", "explore"}:
            recommended_mode = "explore"
            mode_reasons = [*mode_reasons, "downgraded_for_formal_batch_import"]
        if route == "Delegate" and recommended_mode not in {"no_governance", "capture", "explore"}:
            recommended_mode = "explore"
            mode_reasons = [*mode_reasons, "delegate_route_prefers_explore"]
        if route == "Capture" and recommended_mode != "no_governance":
            recommended_mode = "capture"
            mode_reasons = [*mode_reasons, "capture_route_source_material_only"]
        if recommended_mode != "no_governance" and relation_decision.get("mode_hint") == "explore" and route in {"Recover", "Delegate"}:
            recommended_mode = "explore"
            mode_reasons = [*mode_reasons, f"relation_mode_hint:{relation_decision['relation_type']}"]
        no_governance = route == "No Governance" or recommended_mode == "no_governance"
        close_without_inputs = route == "Close" and close_intent and bool(missing_closeout_fields)
        if route != "Recall" and intent_facts.asks_close and not intent_facts.negates_close and not missing_closeout_fields:
            route = "Close"
            route_reasons = [*route_reasons, "close_request_inputs_complete"]
        if close_without_inputs:
            payload = {
                "ok": False,
                "read_only": True,
                **_write_state(trace_explicit=trace_explicit, trace_written=False),
                "user_goal": _goal_from_intent(intent),
                "recommended_mode": "explore" if recommended_mode != "no_governance" else "no_governance",
                "recommended_route": "Close",
                "authority_posture": "controller_close",
                "authority_hint": "controller_close",
                "relation_decision": relation_decision,
                "multi_intent_plan": _multi_intent_plan(intent, endpoint_name=endpoint_name),
                "endpoint_status": {
                    "candidate": endpoint_name,
                    "status": endpoint_state,
                    "write_allowed": False,
                },
                "error": {
                    "code": "missing_closeout_inputs",
                    "message": "Close requests require endpoint, task_id, check_id, expected_evidence_type, and current_matching_evidence_ref.",
                    "missing_fields": missing_closeout_fields,
                },
                "first_surface": _first_surface("Close", endpoint_name),
                **method_payload("Close", intent=intent),
                "intent_facts": intent_facts.payload(),
                "auxiliary_recall": False,
                "forbidden_next_actions": _forbidden_actions("Close", no_governance=False, close_without_inputs=True, intent=intent),
                "safe_next_action": _safe_next_action(
                    "Close",
                    intent,
                    endpoint_name,
                    closeout_complete=False,
                    relation_decision=relation_decision,
                ),
                "exit_brake": {
                    "read_only": True,
                    "no_governance": False,
                    "friction_brake": True,
                    "stop_writes": True,
                    "reason": "missing_closeout_inputs",
                },
                "reasons": {"mode": mode_reasons, "route": [*route_reasons, "close_request_missing_required_inputs"]},
            }
            if trace_explicit:
                append_trace_event(
                    repo,
                    event_type="route_guard",
                    endpoint=endpoint_name,
                    route="Close",
                    mode=payload["recommended_mode"],
                    read_only=True,
                    status="blocked_missing_closeout_inputs",
                    details={"intent_excerpt": re.sub(r"\s+", " ", intent)[:240], "missing_fields": missing_closeout_fields},
                )
                payload.update(_write_state(trace_explicit=trace_explicit, trace_written=True))
            print_json(payload)
            return 1
        payload = {
            "ok": True,
            "read_only": True,
            **_write_state(trace_explicit=trace_explicit, trace_written=False),
            "user_goal": _goal_from_intent(intent),
            "recommended_mode": recommended_mode,
            "recommended_route": route,
            "authority_posture": "controller_close" if route == "Close" else ("reviewer_material" if route == "Delegate" else ("capture_source_material" if route == "Capture" else relation_decision["authority_posture"])),
            "authority_hint": "controller_close" if route == "Close" else ("reviewer_material" if route == "Delegate" else ("capture_source_material" if route == "Capture" else relation_decision["authority_hint"])),
            "relation_decision": relation_decision,
            "multi_intent_plan": _multi_intent_plan(intent, endpoint_name=endpoint_name),
            "confidence": "high" if endpoint_state == "known" else ("medium" if endpoint_name else "low"),
            "endpoint_status": {
                "candidate": endpoint_name,
                "status": endpoint_state,
                "open_task_count": len((endpoint_status or {}).get("current_tasks") or []),
                "open_check_count": len((endpoint_status or {}).get("open_checks") or []),
                "write_allowed": False,
            },
            "first_surface": _first_surface(route, endpoint_name),
            **method_payload(route, intent=intent),
            "intent_facts": intent_facts.payload(),
            "auxiliary_recall": _auxiliary_recall_needed(intent, route, intent_facts),
            "forbidden_next_actions": list(
                dict.fromkeys(
                    [
                        *_forbidden_actions(route, no_governance=no_governance, close_without_inputs=close_without_inputs, intent=intent),
                        *(["direct task/check DB writes before plan-to-db import-task-chain preview"] if formal_importer_needed else []),
                        *(["wrapper subprocess loop for bulk task/check import"] if wrapper_loop_risk else []),
                    ]
                )
            )[:5],
            "safe_next_action": _safe_next_action(
                route,
                intent,
                endpoint_name,
                closeout_complete=close_intent and not missing_closeout_fields,
                relation_decision=relation_decision,
            ),
            "exit_brake": {
                "read_only": True,
                "no_governance": no_governance,
                "friction_brake": no_governance or high_friction_batch,
                "stop_writes": no_governance or high_friction_batch,
                "reason": (
                    "explicit_no_governance"
                    if no_governance
                    else ("wrapper_loop_forbidden" if wrapper_loop_risk else ("missing_formal_batch_entrance" if formal_importer_needed else "none"))
                ),
            },
            "reasons": {"mode": mode_reasons, "route": route_reasons},
        }
        if trace_explicit:
            append_trace_event(
                repo,
                event_type="route_guard",
                endpoint=endpoint_name,
                route=route,
                mode=recommended_mode,
                read_only=True,
                status="no_governance_exit" if no_governance else "recommended",
                details={"intent_excerpt": re.sub(r"\s+", " ", intent)[:240]},
            )
            payload.update(_write_state(trace_explicit=trace_explicit, trace_written=True))
        print_json(payload)
        return 0

    return {"guard": route_guard}


def register_route(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, handlers: Mapping[str, RouteHandler]) -> None:
    missing = [key for key in ROUTE_HANDLER_KEYS if key not in handlers]
    if missing:
        raise RuntimeError(f"route command boundary is missing: {', '.join(missing)}")
    route = subparsers.add_parser("route")
    route_sub = route.add_subparsers(dest="route_command", required=True)
    guard = route_sub.add_parser("guard")
    guard.add_argument("--intent")
    guard.add_argument("--intent-file", help="Read long intent text from a UTF-8 file.")
    guard.add_argument("--endpoint")
    guard.add_argument("--mode")
    guard.add_argument("--task-id")
    guard.add_argument("--check-id")
    guard.add_argument("--expected-evidence-type")
    guard.add_argument("--current-matching-evidence-ref")
    guard.add_argument("--pure", action="store_true", help="Classify route/method without runtime access, DB access, or trace/filesystem writes.")
    guard.add_argument("--trace", action="store_true", help="Write a route_guard trace event even for No Governance/no-record exits.")
    guard.set_defaults(func=handlers["guard"])


__all__ = ["ROUTE_HANDLER_KEYS", "build_route_handlers", "register_route"]
