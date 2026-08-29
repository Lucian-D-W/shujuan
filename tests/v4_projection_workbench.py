from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_cli(repo: Path, *args: str) -> dict[str, object]:
    completed = run_cli_completed(repo, *args)
    return json.loads(completed.stdout)


def run_cli_completed(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
        env.pop(key, None)
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    return completed


def run_git(repo: Path, *args: str) -> None:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise AssertionError(f"git failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")


def edge_executable() -> Path:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise AssertionError("Microsoft Edge executable not found for headless workbench render proof")


def pwsh_executable() -> str:
    candidates = [
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        shutil.which("pwsh"),
        shutil.which("powershell"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise AssertionError("PowerShell executable not found for headless Edge screenshot proof")


def paeth(left: int, up: int, upper_left: int) -> int:
    p = left + up - upper_left
    pa = abs(p - left)
    pb = abs(p - up)
    pc = abs(p - upper_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return upper_left


def read_png_rgba(path: Path) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError(f"not a PNG screenshot: {path}")
    offset = 8
    width = height = color_type = bit_depth = None
    compressed = bytearray()
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width = int.from_bytes(chunk[0:4], "big")
            height = int.from_bytes(chunk[4:8], "big")
            bit_depth = chunk[8]
            color_type = chunk[9]
        elif chunk_type == b"IDAT":
            compressed.extend(chunk)
        elif chunk_type == b"IEND":
            break
    if width is None or height is None or bit_depth != 8 or color_type not in {2, 6}:
        raise AssertionError(f"unsupported PNG format: bit_depth={bit_depth} color_type={color_type}")
    channels = 4 if color_type == 6 else 3
    stride = width * channels
    raw = zlib.decompress(bytes(compressed))
    rows: list[bytearray] = []
    previous = bytearray(stride)
    position = 0
    for _ in range(height):
        filter_type = raw[position]
        position += 1
        row = bytearray(raw[position : position + stride])
        position += stride
        for i, value in enumerate(row):
            left = row[i - channels] if i >= channels else 0
            up = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                row[i] = (value + left) & 0xFF
            elif filter_type == 2:
                row[i] = (value + up) & 0xFF
            elif filter_type == 3:
                row[i] = (value + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                row[i] = (value + paeth(left, up, upper_left)) & 0xFF
            elif filter_type != 0:
                raise AssertionError(f"unsupported PNG filter: {filter_type}")
        rows.append(row)
        previous = row
    pixels: list[tuple[int, int, int, int]] = []
    for row in rows:
        for x in range(width):
            start = x * channels
            r, g, b = row[start], row[start + 1], row[start + 2]
            a = row[start + 3] if channels == 4 else 255
            pixels.append((r, g, b, a))
    return width, height, pixels


def assert_headless_edge_graph_pixels(html_path: Path, screenshot_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="shujuan-edge-profile-") as profile:
        profile_path = Path(profile)
        profile_path.mkdir(parents=True, exist_ok=True)
        edge_args = [
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile_path}",
            "--window-size=1440,900",
            "--timeout=10000",
            f"--screenshot={screenshot_path}",
            html_path.resolve().as_uri(),
        ]
        env = os.environ.copy()
        env["SHUJUAN_EDGE_EXE"] = str(edge_executable())
        env["SHUJUAN_EDGE_ARGS_JSON"] = json.dumps(edge_args)
        completed = subprocess.run(
            [
                pwsh_executable(),
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "$edge = $env:SHUJUAN_EDGE_EXE; [string[]]$edgeArgs = ConvertFrom-Json $env:SHUJUAN_EDGE_ARGS_JSON; $p = Start-Process -FilePath $edge -ArgumentList $edgeArgs -Wait -PassThru -WindowStyle Hidden; if ($null -eq $p.ExitCode) { exit 0 }; exit $p.ExitCode",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    if completed.returncode:
        raise AssertionError(f"headless Edge screenshot failed\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not screenshot_path.exists():
        raise AssertionError(f"headless Edge did not create screenshot: {screenshot_path}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    width, height, pixels = read_png_rgba(screenshot_path)
    graph_right = int(width * 0.72)
    graph_top = 80
    saturated = 0
    non_background = 0
    for y in range(graph_top, min(height, 860)):
        for x in range(0, graph_right):
            r, g, b, _a = pixels[y * width + x]
            background_distance = abs(r - 11) + abs(g - 15) + abs(b - 20)
            if max(r, g, b) > 80 and max(r, g, b) - min(r, g, b) > 25:
                saturated += 1
            if max(r, g, b) > 45 and background_distance > 35:
                non_background += 1
    if saturated < 500 or non_background < 4000:
        raise AssertionError(
            f"headless Edge graph area did not show visible G6 nodes/edges: saturated={saturated} non_background={non_background} screenshot={screenshot_path}"
        )


def assert_g6_dependency_declared() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    if package["dependencies"].get("@antv/g6") != "5.1.1":
        raise AssertionError(f"@antv/g6 dependency is not pinned: {package}")
    locked = (lock.get("packages") or {}).get("node_modules/@antv/g6", {})
    if locked.get("version") != "5.1.1":
        raise AssertionError(f"@antv/g6 lockfile entry missing: {locked}")


def assert_broken_visible_chain_fixture() -> None:
    from shujuan.commands.graph import broken_visible_chain_items

    items = [
        {
            "node_id": "node_a",
            "visible_chain": [{"id": "node_a"}, {"id": "node_b"}],
            "visible_edges": [],
            "detail_ref": "graph detail --node node_a",
        }
    ]
    broken = broken_visible_chain_items(items)
    if len(broken) != 1 or broken[0]["node_id"] != "node_a":
        raise AssertionError(f"broken visible adjacency was not detected: {broken}")


def main() -> int:
    assert_g6_dependency_declared()
    assert_broken_visible_chain_fixture()
    with tempfile.TemporaryDirectory(prefix="shujuan-v4-workbench-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        run_cli(repo, "init", "--postgres-dev", "--name", "v4-workbench")
        try:
            run_git(repo, "init")
            run_git(repo, "config", "user.email", "test@example.com")
            run_git(repo, "config", "user.name", "Test User")
            (repo / "plan.md").write_text("# v4 workbench\n\nAntV G6 projection workbench fixture.\n", encoding="utf-8")
            fixture = repo / "fixture.py"
            fixture.write_text("def value():\n    return 1\n", encoding="utf-8")
            run_git(repo, "add", "fixture.py")
            run_git(repo, "commit", "-m", "baseline fixture")
            doc = run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan")
            scope = run_cli(repo, "scope", "create", "--body", "Projection workbench scope", "--source-node", doc["document_node_id"])
            task = run_cli(repo, "task", "add", "--body", "Render projection payloads in AntV G6", "--contract", scope["contract_id"], "--from-node", doc["document_node_id"])
            check = run_cli(repo, "acceptance", "add", "--task", task["task_id"], "--body", "G6 workbench interactions are visible and read-only.", "--expected-evidence-type", "test_result", "--from-node", doc["document_node_id"])
            run_cli(repo, "endpoint", "create", "workbench", "--root-node", scope["node_id"])
            run_cli(repo, "graph", "link", "--from-node", task["node_id"], "--to-node", doc["document_node_id"], "--type", "PRODUCES", "--reason", "Synthetic folded produced source edge.")
            discussion = run_cli(repo, "discuss", "capture", "--endpoint", "workbench", "--session-id", "session-workbench", "--content", "Workbench source drawer must expand raw discussion messages.")
            run_cli(repo, "workflow", "begin", "--session-id", "session-workbench", "--endpoint", "workbench", "--content", "Exercise change-set detail expansion.")
            start = run_cli(repo, "exec", "start", "--endpoint", "workbench", "--task-node", task["node_id"], "--session-id", "session-workbench", "--summary", "Change fixture")
            fixture.write_text("def value():\n    return 2\n", encoding="utf-8")
            stop = run_cli(repo, "exec", "stop", "--endpoint", "workbench", "--run", start["run_id"], "--summary", "Change-set fixture stop", "--check", check["acceptance_check_id"])
            change_node = stop["change_set"]["change_set_node_id"]

            projection = run_cli(repo, "graph", "projection", "--endpoint", "workbench", "--view", "all", "--include-history", "--save-snapshot")
            metadata = projection["projection_metadata"]
            if "DECOMPOSES_TO" not in metadata["folded_edge_classes"] or "PRODUCES" not in metadata["folded_edge_classes"]:
                raise AssertionError(f"folded edge classes omitted required types: {metadata}")
            if not projection.get("snapshot") or not metadata.get("snapshot_capable") or not metadata.get("event_anchor_node_id"):
                raise AssertionError(f"projection omitted snapshot/event metadata: {projection}")
            for view_name, view_payload in projection["views"].items():
                if view_payload["broken_visible_chain_count"]:
                    raise AssertionError(f"normal projection view {view_name} had broken visible chains: {view_payload}")
            attention = projection["views"]["attention"]
            task_items = [item for item in attention["items"] if item["kind"] == "task"]
            if not task_items or not {"DECOMPOSES_TO", "PRODUCES"}.issubset(set(task_items[0]["hidden_source_edge_classes"])):
                raise AssertionError(f"hidden source classes did not include folded DECOMPOSES_TO/PRODUCES edges: {task_items}")

            discussion_detail = run_cli(repo, "graph", "detail", "--node", discussion["segment_node_id"])
            if not discussion_detail["discussion"] or "Workbench source drawer" not in json.dumps(discussion_detail["discussion"]):
                raise AssertionError(f"discussion detail did not expand raw messages: {discussion_detail}")
            change_detail = run_cli(repo, "graph", "detail", "--node", change_node)
            if not change_detail["change_set"] or not change_detail["change_set"]["diff_hunks"] or not change_detail["change_set"]["patch_preview"]:
                raise AssertionError(f"change_set detail did not include bounded patch/hunk previews: {change_detail}")
            hunk_node = change_detail["change_set"]["diff_hunks"][0]["node_id"]
            hunk_detail = run_cli(repo, "graph", "detail", "--node", hunk_node)
            if not hunk_detail["diff_hunk"] or "return 2" not in json.dumps(hunk_detail["diff_hunk"]):
                raise AssertionError(f"diff_hunk detail did not expose bounded raw contents: {hunk_detail}")

            workbench = run_cli(repo, "workbench", "export", "--endpoint", "workbench", "--path", "workbench.html", "--include-history")
            html_path = repo / workbench["path"]
            html = html_path.read_text(encoding="utf-8")
            required_html = [
                "AntV G6",
                "id=\"graph-mount\"",
                "new G6.Graph",
                "graph.setOptions(options)",
                "await graph.render()",
                "graph.destroy()",
                "drag-canvas",
                "zoom-canvas",
                "drag-element",
                "click-select",
                "id=\"search-input\"",
                "id=\"view-filter\"",
                "id=\"detail-panel\"",
                "id=\"source-drawer\"",
                "id=\"diff-preview\"",
                "data-g6-rendered",
                "data-g6-node-count",
                "data-g6-edge-count",
            ]
            missing = [text for text in required_html if text not in html]
            if missing:
                raise AssertionError(f"workbench HTML missing G6/control predicates: {missing}")
            forbidden = ["fetch(", "XMLHttpRequest", "method=\"post\"", "action="]
            if any(text in html for text in forbidden) or workbench["db_write_path"] is not False:
                raise AssertionError("workbench export exposed a write/action path")
            if not workbench["g6"]["bundled"] or not (repo / workbench["g6"]["asset_path"]).exists():
                raise AssertionError(f"workbench did not bundle the installed G6 asset: {workbench}")

            exported_payload = json.loads(html.split('<script id="projection-payload" type="application/json">', 1)[1].split("</script>", 1)[0])
            if not exported_payload["detail_payloads"] or exported_payload["workbench"]["default_view"] != "attention":
                raise AssertionError(f"workbench payload omitted details/default attention view: {exported_payload.get('workbench')}")
            screenshot_path = repo / "workbench-msedge.png"
            assert_headless_edge_graph_pixels(html_path, screenshot_path)

            print(json.dumps({"ok": True, "html": str(html_path), "g6_asset": str(repo / workbench["g6"]["asset_path"]), "screenshot": str(screenshot_path), "snapshot": projection["snapshot"], "change_node": change_node}, indent=2, sort_keys=True))
        finally:
            run_cli_completed(repo, "postgres-dev", "stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
