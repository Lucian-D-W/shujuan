from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.commands import workbench as workbench_command  # noqa: E402
from shujuan.commands.report import render_project_report_markdown  # noqa: E402
from shujuan.schema_roles import schema_visibility_policy  # noqa: E402


DEFAULT_CORE_REQUIRED = [
    "Recover",
    "Recall",
    "Execute",
    "Close",
    "Delegate",
    "read current surfaces",
    "controller evidence route",
    "bounded material",
    "legacy writes are disabled diagnostics",
    "PostgreSQL",
]


def section_bullets(path: Path, heading: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == heading)
    bullets: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## ") or line.startswith("# "):
            break
        if line.startswith("- "):
            bullets.append(line)
    return bullets


def assert_default_operating_core() -> None:
    targets = [
        ROOT / "AGENTS.md",
        ROOT / ".agents" / "skills" / "shujuan-core" / "references" / "activation-first.md",
    ]
    for path in targets:
        bullets = section_bullets(path, "## Default Operating Core")
        if not 8 <= len(bullets) <= 12:
            raise AssertionError(f"{path} Default Operating Core should be 8-12 bullets: {len(bullets)}")
        text = "\n".join(bullets)
        missing = [phrase for phrase in DEFAULT_CORE_REQUIRED if phrase not in text]
        if missing:
            raise AssertionError(f"{path} Default Operating Core omitted {missing}")
        if "contracted/dormant schema is not the default working surface" not in text.lower():
            raise AssertionError(f"{path} did not keep contracted/dormant schema out of the default surface")
    skill = ROOT / ".agents" / "skills" / "shujuan-core" / "SKILL.md"
    skill_text = skill.read_text(encoding="utf-8")
    required = ["## Authority", "## Activation", "## Five Routes", "## Minimal Hard Boundaries", "## References"]
    missing = [phrase for phrase in required if phrase not in skill_text]
    if missing:
        raise AssertionError(f"{skill} omitted activation-card sections: {missing}")
    if "## Default Operating Core" in skill_text or "python -m shujuan " in skill_text:
        raise AssertionError(f"{skill} should stay an activation card, not a second operating surface")


def assert_recall_checklist() -> None:
    reference = ROOT / ".agents" / "skills" / "shujuan-core" / "references" / "activation-first.md"
    text = reference.read_text(encoding="utf-8")
    if "### Recall Checklist" not in text:
        raise AssertionError("Recall checklist section is missing")
    checklist = text.split("### Recall Checklist", 1)[1].split("## Advanced Fallback", 1)[0]
    required_commands = [
        "report project --markdown",
        "report endpoint <endpoint> --active-only --markdown",
        "report endpoint <endpoint> --full --markdown",
        "endpoint brief <endpoint>",
        "graph detail --node <node_id>",
        "why --path <path>",
        "why --symbol <symbol>",
    ]
    missing = [command for command in required_commands if command not in checklist]
    if missing:
        raise AssertionError(f"Recall checklist omitted existing read-only surfaces: {missing}")
    forbidden = ["recall_records", "recall lifecycle", "recall_store"]
    leaked = [phrase for phrase in forbidden if phrase in checklist]
    if leaked:
        raise AssertionError(f"Recall checklist introduced a new recall mechanism: {leaked}")
    for phrase in ["Keep Recall in the history lane", "execution, endpoint refresh, check/task closure", "governance fact writes", "belong to Execute/Close"]:
        if phrase not in checklist:
            raise AssertionError(f"Recall checklist omitted boundary phrase: {phrase}")


def fake_projection_payload(conn: object, endpoint_name: str, view: str, **kwargs: object) -> dict[str, object]:
    items = [
        {"id": "endpoint-1", "node_id": "node_endpoint", "kind": "endpoint", "label": endpoint_name, "visible_chain": [{"id": "node_endpoint", "type": "endpoint", "label": endpoint_name}], "visible_edges": [], "filter_metadata": {"node_type": "endpoint", "active_state": "active", "lane_lifecycle": "active"}},
        {"id": "task-1", "node_id": "node_task", "kind": "task", "label": "Open task", "visible_chain": [{"id": "node_task", "type": "task", "label": "Open task"}], "visible_edges": [], "filter_metadata": {"node_type": "task", "active_state": "active", "lane_lifecycle": "open"}},
        {"id": "check-1", "node_id": "node_check", "kind": "acceptance_check", "label": "Open check", "visible_chain": [{"id": "node_check", "type": "acceptance_check", "label": "Open check"}], "visible_edges": [], "filter_metadata": {"node_type": "acceptance_check", "active_state": "active", "lane_lifecycle": "open"}},
        {"id": "semantic-1", "node_id": "node_semantic", "kind": "semantic_item", "label": "Unresolved item", "visible_chain": [{"id": "node_semantic", "type": "semantic_item", "label": "Unresolved item"}], "visible_edges": [], "filter_metadata": {"node_type": "semantic_item", "active_state": "active", "lane_lifecycle": "active"}},
        {"id": "evidence-1", "node_id": "node_evidence", "kind": "evidence", "label": "Evidence", "visible_chain": [{"id": "node_evidence", "type": "evidence", "label": "Evidence"}], "visible_edges": [], "filter_metadata": {"node_type": "evidence", "active_state": "active", "lane_lifecycle": "verified", "evidence_type": "test_result"}},
        {"id": "source-1", "node_id": "node_source", "kind": "source_document", "label": "Source document", "visible_chain": [{"id": "node_source", "type": "source_document", "label": "Source document"}], "visible_edges": [], "filter_metadata": {"node_type": "source_document", "active_state": "active", "lane_lifecycle": "active"}},
        {"id": "change-1", "node_id": "node_change", "kind": "change_set", "label": "Change set", "visible_chain": [{"id": "node_change", "type": "change_set", "label": "Change set"}], "visible_edges": [], "filter_metadata": {"node_type": "change_set", "active_state": "active", "lane_lifecycle": "closed"}},
    ]
    return {
        "endpoint": endpoint_name,
        "view": view,
        "mode": kwargs.get("mode"),
        "views": {"active": {"items": items, "item_count": len(items)}},
        "mode_counts": {"active": len(items)},
        "overlay": {
            "default_flow_id": "attention_route",
            "filters": {"active": {"view": "active", "active_only": True}},
            "visual_feature_contract": {},
            "semantic_highlight_palette": {},
        },
    }


def fake_detail_payload(conn: object, node_id: str, **kwargs: object) -> dict[str, object]:
    return {"node": {"id": node_id, "type": "fixture", "label": node_id}, "detail_contract": "fixture"}


def assert_workbench_default_surface() -> None:
    workbench_command.graph_projection_payload = fake_projection_payload
    workbench_command.graph_detail_payload = fake_detail_payload
    payload = workbench_command.build_workbench_payload(
        object(),
        ROOT,
        "v8-p1-fixture",
        mode="active",
        include_history=False,
    )
    visible = set(payload["schema_visibility"]["default_visible_objects"])
    expected_visible = {"endpoints", "tasks", "acceptance_checks", "semantic_items", "evidence_records", "source_documents", "change_sets"}
    if visible != expected_visible:
        raise AssertionError(f"default visible workbench layer drifted: {visible}")
    serialized = json.dumps(payload["schema_visibility"], sort_keys=True)
    if "advanced_schema_visibility" in payload["schema_visibility"]:
        raise AssertionError("active workbench schema visibility exposed advanced schema history by default")
    for table_name in ("delegation_packets", "work_chains", "review_results", "source_promises"):
        if table_name in serialized:
            raise AssertionError(f"default active workbench schema visibility exposed advanced table {table_name}")
    items = payload["views"]["active"]["items"]
    if not items:
        raise AssertionError("default active workbench fixture became empty")
    contract = payload["workbench"]["default_surface_contract"]
    if contract["first_screen"] != "current_governance_objects":
        raise AssertionError(f"workbench did not identify current governance first screen: {contract}")
    if contract["visible_object_classes"] != payload["schema_visibility"]["default_visible_objects"]:
        raise AssertionError(f"workbench contract and schema visibility diverged: {contract}")
    if "contracted_table" not in contract["advanced_opt_in_roles"] or "dormant_extension" not in contract["advanced_opt_in_roles"]:
        raise AssertionError(f"workbench did not keep advanced roles opt-in: {contract}")
    history_payload = workbench_command.build_workbench_payload(
        object(),
        ROOT,
        "v8-p1-fixture",
        mode="history",
        include_history=True,
    )
    if "advanced_schema_visibility" not in history_payload["schema_visibility"]:
        raise AssertionError("history/all workbench mode should expose advanced schema visibility by opt-in")


def assert_report_default_surface() -> None:
    visibility = schema_visibility_policy()
    report = render_project_report_markdown(
        {
            "schema": {"backend": "fixture", "state": "ok", "project_meta_versions": ["1"]},
            "schema_visibility": visibility,
            "center": {"body": "fixture center"},
            "endpoints": [],
            "current_tasks": [],
            "open_checks": [],
            "acceptance_checks": [],
            "evidence": [],
            "risks_and_notes": [],
            "terms": [],
        }
    )
    if "Default visible objects: endpoints, tasks, acceptance_checks, semantic_items, evidence_records, source_documents, change_sets" not in report:
        raise AssertionError(f"project report did not show the current governance object layer: {report}")
    if "delegation_packets" in report or "work_chains" in report or "review_results" in report:
        raise AssertionError(f"project report default markdown leaked advanced/contracted table names: {report}")


def main() -> int:
    assert_default_operating_core()
    assert_recall_checklist()
    assert_workbench_default_surface()
    assert_report_default_surface()
    print(json.dumps({"ok": True, "v8_p1_default_operating_surface": "passed"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
