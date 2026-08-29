from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.error import HTTPError, URLError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "windows" / "open-shujuan-workbench.ps1"
INSTALLER = ROOT / "scripts" / "windows" / "install-shujuan-workbench-shortcut.ps1"
CMD_WRAPPER = ROOT / "scripts" / "windows" / "open-shujuan-workbench.cmd"
WORKBENCH_ENDPOINT = "shujuan-endpoint-workbench"
DEFAULT_SHORTCUT_NAME = f"{ROOT.name} Roadmap Workbench"


def powershell_host() -> str:
    host = shutil.which("pwsh") or shutil.which("powershell")
    if not host:
        raise AssertionError("PowerShell is required for the Windows launcher regression")
    return host


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_ps(args: list[str], *, timeout: int = 40) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [powershell_host(), "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def assert_script_parses(path: Path) -> None:
    command = f"$ErrorActionPreference='Stop'; [void][scriptblock]::Create((Get-Content -LiteralPath '{path}' -Raw))"
    result = run_ps(["-Command", command])
    if result.returncode != 0:
        raise AssertionError(f"{path.name} failed PowerShell parse check\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def fetch_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8")


def stop_process(pid: int) -> None:
    run_ps(["-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"], timeout=15)


def stop_popen(process: subprocess.Popen[object]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def ps_single_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_launcher(
    port: int,
    output_path: Path,
    *,
    endpoint: str | None = None,
    mode: str | None = None,
    extra_args: tuple[str, ...] = (),
    expected_returncodes: tuple[int, ...] = (0,),
) -> dict[str, object]:
    # Write JSON to a file so the background workbench process cannot keep a
    # Python stdout pipe open on Windows.
    mode_arg = f"-Mode {mode} " if mode else ""
    extra = " ".join(extra_args)
    if extra:
        extra += " "
    command = (
        f"& {ps_single_quote(LAUNCHER)} "
        f"-RepoRoot {ps_single_quote(ROOT)} "
        f"-Port {port} "
        f"{mode_arg}"
        "-Limit 10 "
        "-NoOpen "
        f"{f'-Endpoint {ps_single_quote(endpoint)} ' if endpoint else ''}"
        f"{extra}"
        "-PassThru "
        "-StartupTimeoutSeconds 30 "
        f"| Set-Content -LiteralPath {ps_single_quote(output_path)} -Encoding UTF8"
    )
    result = subprocess.run(
        [powershell_host(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=60,
    )
    if result.returncode not in expected_returncodes:
        raise AssertionError(f"launcher exited with {result.returncode}, expected one of {expected_returncodes}")
    if not output_path.exists() or not output_path.read_text(encoding="utf-8-sig").strip():
        raise AssertionError(f"launcher produced no PassThru JSON at {output_path}")
    return json.loads(output_path.read_text(encoding="utf-8-sig"))


def wait_for_wrong_service(port: int) -> None:
    deadline = time.time() + 15
    url = f"http://127.0.0.1:{port}/api/projection"
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except HTTPError as exc:
            if exc.code == 404:
                return
            last_error = exc
        except URLError as exc:
            last_error = exc
        time.sleep(0.2)
    raise AssertionError(f"wrong service did not become reachable on port {port}: {last_error}")


def start_wrong_service(port: int, directory: Path) -> subprocess.Popen[object]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
            "--directory",
            str(directory),
        ],
        cwd=directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_wrong_service(port)
    except Exception:
        stop_popen(process)
        raise
    return process


def start_stale_shell_service(port: int, directory: Path) -> subprocess.Popen[object]:
    script = directory / "stale_shell_service.py"
    script.write_text(
        f"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_body(self, status, body, content_type):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/api/projection":
            payload = {{
                "endpoint": {WORKBENCH_ENDPOINT!r},
                "mode": (query.get("mode") or ["active"])[0],
                "generated_at": "stale",
                "mode_counts": {{"all": 1, "active": 1}},
            }}
            self.send_body(200, json.dumps(payload), "application/json; charset=utf-8")
            return
        if parsed.path in ("/", "/workbench"):
            body = '<select id="mode-select"><option value="active" selected>active</option><option value="all">all</option></select>'
            self.send_body(200, body, "text/html; charset=utf-8")
            return
        self.send_body(404, "not found", "text/plain; charset=utf-8")


ThreadingHTTPServer(("127.0.0.1", {port}), Handler).serve_forever()
""",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_wrong_service(port)
    except Exception:
        stop_popen(process)
        raise
    return process


def assert_all_mode_route_is_nonempty(payload: dict[str, object], label: str) -> None:
    if payload.get("endpoint") != WORKBENCH_ENDPOINT or payload.get("mode") != "all":
        raise AssertionError(f"{label} did not use all mode for the project endpoint: {payload.keys()}")
    workbench = payload.get("workbench")
    overlay = payload.get("overlay")
    mode_counts = payload.get("mode_counts")
    if not isinstance(workbench, dict) or not isinstance(overlay, dict) or not isinstance(mode_counts, dict):
        raise AssertionError(f"{label} omitted workbench overlay or mode_counts data: {payload.keys()}")
    if (
        workbench.get("default_view") != "all"
        or workbench.get("default_route") != "all_route"
        or workbench.get("default_active_only") is not False
    ):
        raise AssertionError(f"{label} defaulted to a blank-prone route: {workbench}")
    nodes = overlay.get("nodes")
    if int(mode_counts.get("all") or 0) < 1 and (not isinstance(nodes, list) or not nodes):
        raise AssertionError(f"{label} all-mode route should be non-empty: counts={mode_counts} nodes={nodes}")


def assert_windows_launcher() -> None:
    for path in (LAUNCHER, INSTALLER, CMD_WRAPPER):
        if not path.exists():
            raise AssertionError(f"missing launcher file: {path}")

    assert_script_parses(LAUNCHER)
    assert_script_parses(INSTALLER)

    launcher_text = LAUNCHER.read_text(encoding="utf-8")
    required_launcher_markers = [
        "/api/projection",
        "/workbench",
        "Start-Process",
        "workbench",
        "serve",
        "service_reused",
        "service_started",
        "requested_port",
        "actual_port",
        "fallback_used",
        "preferred_port_accepts_tcp",
        "Test-PortAcceptsTcpConnection",
        "Find-HealthyFallbackService",
        "ConnectAsync",
        "NoOpen",
        "Cache-Control",
        "Invoke-WebRequest",
        "did not select requested mode",
        "selected stale active mode",
        "Resolve-WorkbenchEndpoint",
        "report endpoint",
    ]
    missing_launcher = [marker for marker in required_launcher_markers if marker not in launcher_text]
    if missing_launcher:
        raise AssertionError(f"launcher omitted expected behavior markers: {missing_launcher}")
    launcher_param_block = launcher_text.split(")", 1)[0]
    if "$PSScriptRoot" in launcher_param_block:
        raise AssertionError("launcher must not use PSScriptRoot in parameter defaults")
    if "[string]$Endpoint = $null" not in launcher_text:
        raise AssertionError("launcher endpoint default must be automatic")
    if '[string]$Mode = "all"' not in launcher_text:
        raise AssertionError("launcher default mode must use all for a non-empty project roadmap route")

    installer_text = INSTALLER.read_text(encoding="utf-8")
    required_installer_markers = ["WScript.Shell", "CreateShortcut", "IconLocation", "open-shujuan-workbench.ps1"]
    missing_installer = [marker for marker in required_installer_markers if marker not in installer_text]
    if missing_installer:
        raise AssertionError(f"installer omitted shortcut markers: {missing_installer}")
    installer_param_block = installer_text.split(")", 1)[0]
    if "$PSScriptRoot" in installer_param_block:
        raise AssertionError("installer must not use PSScriptRoot in parameter defaults")
    if "[string]$Endpoint = $null" not in installer_text:
        raise AssertionError("installer endpoint default must be automatic")
    if '[string]$Mode = "all"' not in installer_text:
        raise AssertionError("installer default mode must use all for a non-empty project roadmap route")

    dry_run = run_ps(["-File", str(INSTALLER), "-RepoRoot", str(ROOT), "-DryRun", "-PassThru"], timeout=30)
    if dry_run.returncode != 0:
        raise AssertionError(f"installer dry run failed\nSTDOUT:\n{dry_run.stdout}\nSTDERR:\n{dry_run.stderr}")
    shortcut = json.loads(dry_run.stdout)
    if not shortcut["dry_run"] or "open-shujuan-workbench.ps1" not in shortcut["arguments"]:
        raise AssertionError(f"installer dry run did not target the launcher: {shortcut}")
    if shortcut["endpoint_argument"] is not None or "-Endpoint" in shortcut["arguments"]:
        raise AssertionError(f"default shortcut should resolve endpoint at click time: {shortcut}")
    if shortcut["mode"] != "all" or "-Mode all" not in shortcut["arguments"]:
        raise AssertionError(f"default shortcut should open the non-empty all-mode route: {shortcut}")
    if not str(shortcut["shortcut_path"]).endswith(f"{DEFAULT_SHORTCUT_NAME}.lnk"):
        raise AssertionError(f"default shortcut name should be project-derived: {shortcut}")

    explicit_dry_run = run_ps(
        ["-File", str(INSTALLER), "-RepoRoot", str(ROOT), "-Endpoint", WORKBENCH_ENDPOINT, "-DryRun", "-PassThru"],
        timeout=30,
    )
    if explicit_dry_run.returncode != 0:
        raise AssertionError(
            f"explicit endpoint installer dry run failed\nSTDOUT:\n{explicit_dry_run.stdout}\nSTDERR:\n{explicit_dry_run.stderr}"
        )
    explicit_shortcut = json.loads(explicit_dry_run.stdout)
    if f"-Endpoint {WORKBENCH_ENDPOINT}" not in explicit_shortcut["arguments"]:
        raise AssertionError(f"explicit endpoint override was not preserved: {explicit_shortcut}")

    explicit_active_dry_run = run_ps(
        ["-File", str(INSTALLER), "-RepoRoot", str(ROOT), "-Mode", "active", "-DryRun", "-PassThru"],
        timeout=30,
    )
    if explicit_active_dry_run.returncode != 0:
        raise AssertionError(
            f"explicit active-mode installer dry run failed\nSTDOUT:\n{explicit_active_dry_run.stdout}\nSTDERR:\n{explicit_active_dry_run.stderr}"
        )
    explicit_active_shortcut = json.loads(explicit_active_dry_run.stdout)
    if explicit_active_shortcut["mode"] != "active" or "-Mode active" not in explicit_active_shortcut["arguments"]:
        raise AssertionError(f"explicit mode override was not preserved: {explicit_active_shortcut}")

    windows_powershell = shutil.which("powershell")
    if windows_powershell:
        legacy_dry_run = subprocess.run(
            [
                windows_powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALLER),
                "-RepoRoot",
                str(ROOT),
                "-DryRun",
                "-PassThru",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if legacy_dry_run.returncode != 0:
            raise AssertionError(
                f"Windows PowerShell installer dry run failed\nSTDOUT:\n{legacy_dry_run.stdout}\nSTDERR:\n{legacy_dry_run.stderr}"
            )
        legacy_shortcut = json.loads(legacy_dry_run.stdout)
        if not legacy_shortcut["dry_run"] or "open-shujuan-workbench.ps1" not in legacy_shortcut["arguments"]:
            raise AssertionError(f"Windows PowerShell installer dry run did not target the launcher: {legacy_shortcut}")
        if legacy_shortcut["endpoint_argument"] is not None or "-Endpoint" in legacy_shortcut["arguments"]:
            raise AssertionError(f"Windows PowerShell default shortcut should resolve endpoint at click time: {legacy_shortcut}")
        if legacy_shortcut["mode"] != "all" or "-Mode all" not in legacy_shortcut["arguments"]:
            raise AssertionError(f"Windows PowerShell default shortcut should open all mode: {legacy_shortcut}")

    port = free_port()
    pid: int | None = None
    with tempfile.TemporaryDirectory(prefix="shujuan-wb-launcher-") as raw_temp:
        temp = Path(raw_temp)
        first_path = temp / "first.json"
        second_path = temp / "second.json"
        try:
            first = run_launcher(port, first_path)
            if not first["ok"] or not first["service_started"]:
                raise AssertionError(f"first launcher run should start a healthy service: {first}")
            if first["endpoint"] != WORKBENCH_ENDPOINT or not first["endpoint_auto_resolved"]:
                raise AssertionError(f"default launcher run should resolve the project workbench endpoint: {first}")
            if first["mode"] != "all":
                raise AssertionError(f"default launcher run should use all mode: {first}")
            pid_value = first.get("process_id")
            if pid_value is not None:
                pid = int(pid_value)

            projection = fetch_json(f"http://127.0.0.1:{port}/api/projection?mode=all&limit=10")
            assert_all_mode_route_is_nonempty(projection, "default launcher projection")

            active_projection = fetch_json(f"http://127.0.0.1:{port}/api/projection?mode=active&limit=1")
            if active_projection.get("endpoint") != WORKBENCH_ENDPOINT or active_projection.get("mode") != "active":
                raise AssertionError(f"launcher service did not serve active-mode projection: {active_projection.keys()}")
            if int(active_projection.get("mode_counts", {}).get("active") or 0) > int(projection["mode_counts"]["all"]):
                raise AssertionError(f"active mode unexpectedly exceeded all-mode count: active={active_projection['mode_counts']} all={projection['mode_counts']}")
            shell = fetch_text(f"http://127.0.0.1:{port}/workbench?mode=all&limit=120")
            if '<option value="all" selected>all</option>' not in shell or '<option value="active" selected>active</option>' in shell:
                raise AssertionError("launcher-started workbench shell did not honor all-mode URL query")

            time.sleep(0.5)
            second = run_launcher(port, second_path, endpoint=WORKBENCH_ENDPOINT, mode="active")
            if not second["ok"] or not second["service_reused"] or second["service_started"]:
                raise AssertionError(f"second launcher run should reuse the healthy service: {second}")
            if second["endpoint"] != WORKBENCH_ENDPOINT or second["endpoint_auto_resolved"]:
                raise AssertionError(f"explicit endpoint override should be preserved: {second}")
            if second["mode"] != "active":
                raise AssertionError(f"explicit launcher mode override should be preserved: {second}")

            print(
                json.dumps(
                    {
                        "ok": True,
                        "port": port,
                        "first_started": first["service_started"],
                        "second_reused": second["service_reused"],
                    },
                    sort_keys=True,
                )
            )
        finally:
            if pid is not None:
                stop_process(pid)

    collision_port = free_port()
    wrong_process: subprocess.Popen[object] | None = None
    fallback_pid: int | None = None
    with tempfile.TemporaryDirectory(prefix="shujuan-wb-wrong-service-") as raw_wrong_temp:
        wrong_temp = Path(raw_wrong_temp)
        with tempfile.TemporaryDirectory(prefix="shujuan-wb-fallback-") as raw_fallback_temp:
            fallback_temp = Path(raw_fallback_temp)
            fallback_path = fallback_temp / "fallback.json"
            nostart_path = fallback_temp / "nostart.json"
            try:
                wrong_process = start_wrong_service(collision_port, wrong_temp)
                nostart = run_launcher(collision_port, nostart_path, extra_args=("-NoStart",), expected_returncodes=(1, 2))
                if nostart["ok"] or not nostart["preferred_port_accepts_tcp"] or nostart["fallback_used"]:
                    raise AssertionError(f"NoStart wrong-service probe should emit JSON without fallback: {nostart}")
                if not nostart.get("last_error"):
                    raise AssertionError(f"NoStart wrong-service probe did not report the rejected service error: {nostart}")

                fallback = run_launcher(collision_port, fallback_path)
                if not fallback["ok"] or not fallback["service_started"] or not fallback["fallback_used"]:
                    raise AssertionError(f"launcher should start a fallback service when preferred port is wrong: {fallback}")
                if not fallback["preferred_port_accepts_tcp"]:
                    raise AssertionError(f"launcher did not detect the wrong service with a TCP connection: {fallback}")
                if fallback["actual_port"] == collision_port or fallback["port"] == collision_port:
                    raise AssertionError(f"fallback launcher reused the occupied wrong-service port: {fallback}")
                if f":{collision_port}/" in str(fallback["url"]) or f":{collision_port}/" in str(fallback["base_url"]):
                    raise AssertionError(f"fallback launcher reported the wrong-service URL: {fallback}")
                if fallback["requested_port"] != collision_port or fallback["preferred_port"] != collision_port:
                    raise AssertionError(f"launcher did not preserve requested/preferred port metadata: {fallback}")
                if not fallback.get("last_error") or not fallback.get("rejected_service_last_error"):
                    raise AssertionError(f"launcher did not report the rejected service error: {fallback}")
                fallback_pid_value = fallback.get("process_id")
                if fallback_pid_value is not None:
                    fallback_pid = int(fallback_pid_value)

                projection = fetch_json(str(fallback["projection_endpoint"]))
                assert_all_mode_route_is_nonempty(projection, "fallback launcher projection")

                print(
                    json.dumps(
                        {
                            "ok": True,
                            "preferred_port": collision_port,
                            "actual_port": fallback["actual_port"],
                            "fallback_used": fallback["fallback_used"],
                        },
                        sort_keys=True,
                    )
                )
            finally:
                if fallback_pid is not None:
                    stop_process(fallback_pid)
                if wrong_process is not None:
                    stop_popen(wrong_process)

    stale_port = free_port()
    stale_process: subprocess.Popen[object] | None = None
    stale_fallback_pid: int | None = None
    with tempfile.TemporaryDirectory(prefix="shujuan-wb-stale-shell-") as raw_stale_temp:
        stale_temp = Path(raw_stale_temp)
        stale_path = stale_temp / "stale.json"
        stale_second_path = stale_temp / "stale-second.json"
        try:
            stale_process = start_stale_shell_service(stale_port, stale_temp)
            stale = run_launcher(stale_port, stale_path)
            if not stale["ok"] or not stale["service_started"] or not stale["fallback_used"]:
                raise AssertionError(f"launcher should reject API-healthy but shell-stale service: {stale}")
            if stale["actual_port"] == stale_port or f":{stale_port}/" in str(stale["url"]):
                raise AssertionError(f"stale shell rejection should use a fallback port: {stale}")
            if "shell" not in str(stale.get("rejected_service_last_error") or "").lower():
                raise AssertionError(f"launcher did not report stale shell health failure: {stale}")
            stale_fallback_pid_value = stale.get("process_id")
            if stale_fallback_pid_value is not None:
                stale_fallback_pid = int(stale_fallback_pid_value)
            fresh_shell = fetch_text(str(stale["url"]))
            if '<option value="all" selected>all</option>' not in fresh_shell or '<option value="active" selected>active</option>' in fresh_shell:
                raise AssertionError("fallback service shell did not render the requested all mode")

            repeated = run_launcher(stale_port, stale_second_path)
            if not repeated["ok"] or not repeated["service_reused"] or repeated["service_started"]:
                raise AssertionError(f"second stale-preferred launch should reuse the healthy fallback service: {repeated}")
            if not repeated["fallback_used"] or repeated["actual_port"] != stale["actual_port"]:
                raise AssertionError(f"second stale-preferred launch should keep the first fallback port: first={stale} second={repeated}")
            if repeated.get("process_id") is not None:
                raise AssertionError(f"reused fallback service should not start another process: {repeated}")

            print(
                json.dumps(
                    {
                        "ok": True,
                        "preferred_port": stale_port,
                        "actual_port": stale["actual_port"],
                        "stale_shell_rejected": stale["fallback_used"],
                        "second_reused": repeated["service_reused"],
                    },
                    sort_keys=True,
                )
            )
        finally:
            if stale_fallback_pid is not None:
                stop_process(stale_fallback_pid)
            if stale_process is not None:
                stop_popen(stale_process)


if __name__ == "__main__":
    assert_windows_launcher()
