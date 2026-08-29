from __future__ import annotations

PUBLIC_COMPAT_EXPORTS: dict[str, tuple[str, ...]] = {
    "shujuan.cli": (
        "build_parser",
        "ensure_agents_md",
        "ensure_shujuan_skill",
        "choose_postgres_dev_port",
        "default_postgres_dev_port",
        "props_dict",
        "project_report_payload",
        "render_project_report_markdown",
        "render_workbench_html",
    ),
    "shujuan.commands.workflow": (
        "WORKFLOW_HANDLER_KEYS",
        "build_workflow_handlers",
        "register_workflows",
    ),
    "shujuan.commands.evidence": (
        "EVIDENCE_HANDLER_KEYS",
        "build_evidence_handlers",
        "register_evidence",
    ),
    "shujuan.commands.endpoint": (
        "build_endpoint_handlers",
        "endpoint_status_payload",
        "endpoint_agcp_doctor_findings",
        "refresh_endpoint_projection",
        "register_endpoint",
    ),
    "shujuan.commands.report": (
        "build_report_handlers",
        "endpoint_report_payload",
        "project_report_payload",
        "register_report",
    ),
}

INTERNAL_ONLY_IMPORT_RULES: dict[str, tuple[str, ...]] = {
    "shujuan.commands": (
        "Command modules receive shared runtime functions through explicit build_* boundary helpers.",
        "Command modules must not import shujuan.cli directly.",
    ),
    "shujuan.services": (
        "Service modules own policy/projection helpers and must not import command modules or shujuan.cli.",
    ),
}

__all__ = ["INTERNAL_ONLY_IMPORT_RULES", "PUBLIC_COMPAT_EXPORTS"]
