from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import postgres_fixture


REQUIRED_EFFECT_KEYS = {
    "schema",
    "coverage",
    "covered_command_boundary",
    "command",
    "read_db",
    "write_db",
    "refresh_projection",
    "close_scope",
    "provider_output",
    "role_limited",
    "reads",
    "writes",
    "refresh",
    "close",
    "provider",
    "role",
}


def effects(payload: dict, command: str) -> dict:
    command_effects = payload.get("command_effects")
    if not isinstance(command_effects, dict):
        raise AssertionError(f"{command} omitted command_effects: {payload}")
    missing = REQUIRED_EFFECT_KEYS - set(command_effects)
    if missing:
        raise AssertionError(f"{command} command_effects omitted keys {sorted(missing)}: {command_effects}")
    if command_effects["schema"] != "command_effects.v1":
        raise AssertionError(f"{command} command_effects schema drifted: {command_effects}")
    boundary = command_effects.get("covered_command_boundary") or []
    for covered in [
        "endpoint doctor --strict-closeout --read-only",
        "endpoint doctor --strict-closeout",
        "exec stop",
        "scope change",
        "delegate packet",
        "work close --dry-run",
    ]:
        if covered not in boundary:
            raise AssertionError(f"{command} finite command boundary omitted {covered}: {command_effects}")
    return command_effects


def setup_scope(fixture) -> dict:
    repo = fixture.repo
    (repo / "plan.md").write_text("# Effects\n\nCommand effects test plan.\n", encoding="utf-8")
    doc = fixture.run_json("doc", "import", "plan.md", "--source-type", "plan")
    scope = fixture.run_json(
        "scope",
        "create",
        "--body",
        "Command effects output surface contract.",
        "--source-node",
        doc["document_node_id"],
    )
    task = fixture.run_json(
        "task",
        "add",
        "--body",
        "Expose command effects.",
        "--contract",
        scope["contract_id"],
        "--from-node",
        doc["document_node_id"],
    )
    check = fixture.run_json(
        "acceptance",
        "add",
        "--task",
        task["task_id"],
        "--body",
        "Command effects are visible in high-risk outputs.",
        "--expected-evidence-type",
        "change_set",
        "--from-node",
        doc["document_node_id"],
    )
    endpoint = fixture.run_json("endpoint", "create", "effects", "--root-node", scope["node_id"])
    return {"doc": doc, "scope": scope, "task": task, "check": check, "endpoint": endpoint}


def main() -> int:
    fixture_pair = postgres_fixture("command-effects-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    temp, fixture = fixture_pair
    try:
        setup = setup_scope(fixture)

        read_only = fixture.run_json("endpoint", "doctor", "effects", "--strict-closeout", "--read-only", "--allow-fail")
        read_only_effects = effects(read_only, "endpoint doctor read-only")
        if read_only_effects["write_db"] or read_only_effects["refresh_projection"]:
            raise AssertionError(f"read-only doctor reported write/refresh effects: {read_only_effects}")
        if read_only_effects["role"]["worker_allowed"] is not True:
            raise AssertionError(f"read-only doctor did not expose read-only role allowance: {read_only_effects}")

        writeful = fixture.run_json("endpoint", "doctor", "effects", "--strict-closeout", "--allow-fail")
        writeful_effects = effects(writeful, "endpoint doctor writeful")
        if not writeful_effects["write_db"] or not writeful_effects["refresh_projection"]:
            raise AssertionError(f"writeful doctor omitted write/refresh effects: {writeful_effects}")
        if not writeful_effects["role"].get("controller_only_closeout"):
            raise AssertionError(f"writeful doctor omitted controller closeout boundary: {writeful_effects}")

        packet = fixture.run_json(
            "delegate",
            "packet",
            "--endpoint",
            "effects",
            "--task",
            setup["task"]["task_id"],
            "--check",
            setup["check"]["acceptance_check_id"],
            "--role",
            "worker",
            "--body",
            "Implement only command effects.",
            "--save-artifact",
        )
        packet_effects = effects(packet, "delegate packet")
        nested_packet_effects = (packet.get("packet") or {}).get("command_effects")
        if nested_packet_effects != packet_effects:
            raise AssertionError(f"delegate packet did not expose effects at both useful levels: {packet}")
        if packet_effects["write_db"] or "packet artifact file" not in packet_effects["writes"]:
            raise AssertionError(f"delegate packet effects confused artifact and DB writes: {packet_effects}")
        if not packet_effects["provider"].get("material_only"):
            raise AssertionError(f"delegate packet omitted provider material boundary: {packet_effects}")

        fixture.run_json("workflow", "begin", "--session-id", "effects-session", "--endpoint", "effects", "--content", "Run effects test.")
        fixture.run_json(
            "exec",
            "start",
            "--endpoint",
            "effects",
            "--task-node",
            setup["task"]["node_id"],
            "--session-id",
            "effects-session",
            "--summary",
            "Effects run.",
        )
        (fixture.repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

        close_dry_run = fixture.run_json("work", "close", "--mode", "full", "--endpoint", "effects")
        close_effects = effects(close_dry_run, "work close dry-run")
        if close_effects["write_db"] or close_effects["refresh_projection"] or close_effects["close_scope"]:
            raise AssertionError(f"work close dry-run reported mutating effects: {close_effects}")
        if not close_effects["close"].get("dry_run") or not close_effects["close"].get("would_create_change_set"):
            raise AssertionError(f"work close dry-run omitted worksheet effects: {close_effects}")

        stopped = fixture.run_json(
            "exec",
            "stop",
            "--endpoint",
            "effects",
            "--summary",
            "Effects stop.",
            "--check",
            setup["check"]["acceptance_check_id"],
            "--close-check",
        )
        stop_effects = effects(stopped, "exec stop")
        if not stop_effects["write_db"] or not stop_effects["close_scope"]:
            raise AssertionError(f"exec stop omitted write/close effects: {stop_effects}")
        if stop_effects["refresh_projection"] or not stop_effects["refresh"].get("writes_endpoint_closeout_body"):
            raise AssertionError(f"exec stop confused projection refresh with endpoint closeout body writes: {stop_effects}")
        if stop_effects["provider_output"] or stop_effects["provider"].get("runs_provider"):
            raise AssertionError(f"default exec stop should expose provider skip: {stop_effects}")
        stop_provider = stop_effects["provider"]
        if stop_provider.get("default_source") != "GitNexus direct CLI and global gitnexus-* skills":
            raise AssertionError(f"default exec stop effects did not lead with GitNexus: {stop_effects}")
        if stop_provider.get("entrypoint_used") != "default_skipped_no_impact":
            raise AssertionError(f"default exec stop effects did not label skipped entrypoint: {stop_effects}")
        provider_detail = stop_provider.get("provider_detail") or {}
        if provider_detail.get("name") != "gitnexus" or provider_detail.get("invoked"):
            raise AssertionError(f"default exec stop effects misreported direct provider state: {stop_effects}")
        closure_boundary = stop_provider.get("closure_evidence_boundary") or {}
        if not closure_boundary.get("material_only") or not closure_boundary.get("cannot_close_checks"):
            raise AssertionError(f"exec stop effects omitted provider closure boundary: {stop_effects}")
        if closure_boundary.get("output_classification") != "provider_fact or provider_hypothesis":
            raise AssertionError(f"exec stop effects did not classify graph/provider output as material: {stop_effects}")

        scope_change = fixture.run_json(
            "scope",
            "change",
            "--body",
            "Scope change effect test.",
            "--source-node",
            setup["doc"]["document_node_id"],
            "--task",
            setup["task"]["task_id"],
            "--state-changing",
            "--ack-defer-like",
        )
        scope_effects = effects(scope_change, "scope change")
        if not scope_effects["write_db"] or scope_effects["refresh_projection"]:
            raise AssertionError(f"scope change effects missed DB write or invented refresh: {scope_effects}")
        if not scope_effects["close"].get("defers_task") or scope_effects["role"]["worker_allowed"]:
            raise AssertionError(f"scope change effects missed defer/role boundary: {scope_effects}")
        state_effects = scope_effects["close"].get("state_effects") or {}
        if state_effects.get("task_targets", {}).get("endpoint_lifecycle_effect") != "treated_as_deferred_non_active":
            raise AssertionError(f"scope change effects missed deferred lifecycle reporting: {scope_effects}")
        if state_effects.get("applies_to_targets", {}).get("endpoint_lifecycle_effect") != "unchanged":
            raise AssertionError(f"scope change effects missed applies-to lifecycle reporting: {scope_effects}")

        print(json.dumps({"ok": True, "command_effects_output_surface": "passed", "fixture_writes": fixture.writes}))
        return 0
    finally:
        try:
            fixture.stop()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
