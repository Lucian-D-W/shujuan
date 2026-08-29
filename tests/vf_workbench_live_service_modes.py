from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import clean_env, free_port, postgres_fixture
from shujuan.commands.workbench import render_workbench_live_shell


def fetch_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8")


def wait_for_http(url: str) -> None:
    deadline = time.time() + 20
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            fetch_text(url)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise AssertionError(f"service did not become reachable at {url}: {last_error}")


def setup_endpoint(fixture) -> dict[str, str]:
    (fixture.repo / "plan.md").write_text("# live workbench\n\nDB backed live projection fixture.\n", encoding="utf-8")
    doc = fixture.run_json("doc", "import", "plan.md", "--source-type", "plan")
    scope = fixture.run_json("scope", "create", "--body", "Live workbench scope", "--source-node", doc["document_node_id"])
    task = fixture.run_json("task", "add", "--body", "Initial live task", "--contract", scope["contract_id"], "--from-node", doc["document_node_id"])
    check = fixture.run_json(
        "acceptance",
        "add",
        "--task",
        task["task_id"],
        "--body",
        "Initial live check",
        "--expected-evidence-type",
        "user_confirmation",
        "--from-node",
        doc["document_node_id"],
    )
    fixture.run_json("endpoint", "create", "live-wb", "--root-node", scope["node_id"])
    return {
        "source_node_id": doc["document_node_id"],
        "contract_id": scope["contract_id"],
        "task_id": task["task_id"],
        "check_id": check["acceptance_check_id"],
    }


def start_service(fixture, port: int) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "shujuan",
            "--repo",
            str(fixture.repo),
            "workbench",
            "serve",
            "--endpoint",
            "live-wb",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--mode",
            "active",
            "--poll-seconds",
            "0.5",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_env(),
    )


def assert_live_shell_stable_poll_signature() -> None:
    shell = render_workbench_live_shell(
        endpoint="live-wb",
        mode="active",
        limit=50,
        layout="endpoint_radial_chain",
        poll_seconds=0.5,
        include_consumed=False,
    )
    required = [
        "function stableProjectionSignature(payload)",
        "delete withoutVolatileFields.generated_at",
        "projection_signature",
        "manual_refresh",
        "refreshProjection(forceFrame = false)",
        "refreshWithStatus(true)",
    ]
    missing = [marker for marker in required if marker not in shell]
    if missing:
        raise AssertionError(f"live shell omitted stable refresh markers: {missing}")
    forbidden = [
        "payload.generated_at, payload.projection_metadata",
        "frameParams.set('refresh', String(Date.now()))",
        "Date.now()",
    ]
    present = [marker for marker in forbidden if marker in shell]
    if present:
        raise AssertionError(f"live shell still forces iframe reload on ordinary polling: {present}")


def assert_live_service_modes_and_refresh() -> None:
    assert_live_shell_stable_poll_signature()
    fixture_pair = postgres_fixture("shujuan-wb-live-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return
    temp, fixture = fixture_pair
    proc: subprocess.Popen[str] | None = None
    try:
        setup = setup_endpoint(fixture)
        port = free_port()
        proc = start_service(fixture, port)
        base = f"http://127.0.0.1:{port}"
        wait_for_http(f"{base}/workbench")
        shell = fetch_text(f"{base}/workbench")
        required_shell = [
            "fetch(`/api/projection",
            "mode-select",
            "workbench-frame",
            "read-only",
        ]
        missing_shell = [marker for marker in required_shell if marker not in shell]
        if missing_shell:
            raise AssertionError(f"live shell omitted dynamic fetch markers: {missing_shell}")

        all_url_shell = fetch_text(f"{base}/workbench?mode=all&limit=120")
        required_all_url_shell = [
            '<option value="all" selected>all</option>',
            'id="limit-input" type="number" min="1" max="500" value="120"',
            "new URLSearchParams({ mode: modeSelect.value, limit: limitInput.value",
            "modeSelect.addEventListener('change'",
        ]
        missing_all_url_shell = [marker for marker in required_all_url_shell if marker not in all_url_shell]
        if missing_all_url_shell:
            raise AssertionError(f"workbench URL query did not initialize live controls: {missing_all_url_shell}")
        if '<option value="active" selected>active</option>' in all_url_shell:
            raise AssertionError("workbench URL mode=all still selected the active live mode")

        active_before = fetch_json(f"{base}/api/projection?mode=active")
        before_count = int(active_before["mode_counts"]["active"])
        fixture.run_json(
            "task",
            "add",
            "--body",
            "Second live task after server start",
            "--contract",
            setup["contract_id"],
            "--from-node",
            setup["source_node_id"],
        )
        active_after = fetch_json(f"{base}/api/projection?mode=active")
        after_count = int(active_after["mode_counts"]["active"])
        if after_count <= before_count:
            raise AssertionError(f"projection endpoint did not reread DB state: before={before_count} after={after_count}")
        if (fixture.repo / ".shujuan" / "exports" / "workbench.html").exists():
            raise AssertionError("live service regenerated the static workbench export path")

        fixture.run_json(
            "evidence",
            "user-confirmation",
            "--body",
            "fixture confirmation",
            "--check",
            setup["check_id"],
            "--close-check",
        )
        modes = {mode: fetch_json(f"{base}/api/projection?mode={mode}") for mode in ("active", "history", "evidence", "all")}
        expected_defaults = {
            "active": ("active", "attention_route", True),
            "history": ("history", "all_route", False),
            "evidence": ("evidence", "evidence_route", False),
            "all": ("all", "all_route", False),
        }
        for mode, payload in modes.items():
            if payload.get("mode") != mode:
                raise AssertionError(f"{mode} payload used wrong mode: {payload.get('mode')}")
            if "mode_counts" not in payload or mode not in payload.get("views", {}):
                raise AssertionError(f"{mode} payload omitted mode-scoped data: {payload.keys()}")
            expected_view, expected_route, expected_active_only = expected_defaults[mode]
            workbench = payload.get("workbench") or {}
            overlay = payload.get("overlay") or {}
            active_filters = ((overlay.get("filters") or {}).get("active") or {})
            if (
                workbench.get("default_view") != expected_view
                or workbench.get("default_route") != expected_route
                or workbench.get("default_active_only") is not expected_active_only
                or overlay.get("default_flow_id") != expected_route
                or active_filters.get("active_only") is not expected_active_only
            ):
                raise AssertionError(f"{mode} payload defaults drifted: workbench={workbench} overlay={overlay.get('default_flow_id')} filters={active_filters}")
        if int(modes["history"]["mode_counts"]["history"]) < 1:
            raise AssertionError(f"history mode did not surface closed/history DB facts: {modes['history']['mode_counts']}")
        if int(modes["evidence"]["mode_counts"]["evidence"]) < 1:
            raise AssertionError(f"evidence mode did not surface evidence DB facts: {modes['evidence']['mode_counts']}")
        if not {"active", "history", "evidence", "all"}.issubset(set(modes["all"]["views"])):
            raise AssertionError(f"all mode did not expose active/history/evidence/all views: {modes['all']['views'].keys()}")

        frame = fetch_text(f"{base}/frame?mode=evidence")
        embedded = json.loads(frame.split('<script id="projection-payload" type="application/json">', 1)[1].split("</script>", 1)[0])
        if embedded["workbench"]["default_view"] != "evidence" or embedded["workbench"]["default_route"] != "evidence_route" or embedded["workbench"]["default_active_only"] is not False:
            raise AssertionError(f"frame did not honor evidence mode defaults: {embedded['workbench']}")
        empty_routes = [route for route in embedded["overlay"]["flows"] if not route.get("node_ids")]
        if not empty_routes:
            raise AssertionError("fixture did not expose a zero-node route to validate")
        if any(route.get("count_scope") != "route_visible_nodes_edges" or not route.get("empty_state", {}).get("is_empty") for route in empty_routes):
            raise AssertionError(f"zero-node routes were not honestly scoped/marked empty: {empty_routes}")
        required_frame_markers = [
            "selectedRouteIsEmpty",
            "data-empty-route",
            "aria-disabled",
            "nodes.size > 0 && nodes.size < 10",
            "routeCountScope",
        ]
        missing_frame = [marker for marker in required_frame_markers if marker not in frame]
        if missing_frame:
            raise AssertionError(f"frame HTML omitted zero-route empty-state markers: {missing_frame}")

        print(json.dumps({"ok": True, "live_refresh_before": before_count, "live_refresh_after": after_count, "modes": {mode: payload["mode_counts"] for mode, payload in modes.items()}}, sort_keys=True))
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        fixture.stop()
        temp.cleanup()


if __name__ == "__main__":
    assert_live_service_modes_and_refresh()
