from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan import cli
from shujuan.store import connect


PROVIDER_RUNTIME_PATH = ".claude/skills/gitnexus/runtime.json"
CODEGRAPH_ASSET_PATH = ".codegraph/index.json"
GITNEXUS_ASSET_PATH = ".gitnexus/index.json"
AI_CODEGRAPH_ASSET_PATH = ".ai/codegraph/next-action.json"


def git(repo: Path, *args: str) -> None:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise AssertionError(f"git {' '.join(args)} failed\n{completed.stderr}")


def run_cli(repo: Path, *args: str, expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
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
    if expect_ok and completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return completed


def as_json(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(completed.stdout)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def assert_runtime_classification_payload(change_set: dict[str, object], *, metadata: dict[str, object] | None = None) -> None:
    implementation_files = change_set.get("implementation_files")
    provider_runtime_files = change_set.get("provider_runtime_files")
    ignored_runtime_files = change_set.get("ignored_runtime_files")
    if "app.py" not in implementation_files:
        raise AssertionError(f"implementation file was not classified as implementation: {change_set}")
    if PROVIDER_RUNTIME_PATH in implementation_files:
        raise AssertionError(f"provider runtime file leaked into implementation_files: {change_set}")
    if PROVIDER_RUNTIME_PATH not in provider_runtime_files:
        raise AssertionError(f"provider runtime file missing from provider_runtime_files: {change_set}")
    if ignored_runtime_files is None:
        raise AssertionError(f"ignored_runtime_files field missing from change_set payload: {change_set}")

    file_rows = change_set.get("files") or []
    provider_rows = [row for row in file_rows if row.get("path_new") == PROVIDER_RUNTIME_PATH]
    if not provider_rows:
        raise AssertionError(f"provider runtime file missing from change_set files: {change_set}")
    provider_row = provider_rows[0]
    if provider_row.get("file_lane") != "provider_runtime":
        raise AssertionError(f"provider runtime file row lost lane: {provider_row}")

    impact_input = ((change_set.get("impact") or {}).get("input") or {})
    if PROVIDER_RUNTIME_PATH in impact_input.get("changed_files", []):
        raise AssertionError(f"provider runtime path leaked into implementation impact input: {impact_input}")
    if PROVIDER_RUNTIME_PATH not in impact_input.get("provider_runtime_files", []):
        raise AssertionError(f"impact input lost provider runtime lane: {impact_input}")

    if metadata is not None:
        if PROVIDER_RUNTIME_PATH in metadata.get("implementation_files", []):
            raise AssertionError(f"metadata leaked provider runtime into implementation_files: {metadata}")
        if PROVIDER_RUNTIME_PATH not in metadata.get("provider_runtime_files", []):
            raise AssertionError(f"metadata lost provider runtime files: {metadata}")
        if "ignored_runtime_files" not in metadata:
            raise AssertionError(f"metadata omitted ignored_runtime_files: {metadata}")


def assert_snapshot_delta_classification() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="shujuan-provider-runtime-files-") as temp:
        repo = Path(temp)
        git(repo, "init")
        (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        git(repo, "add", "app.py")
        git(repo, "-c", "user.name=Runtime", "-c", "user.email=runtime@example.invalid", "commit", "-m", "seed")

        before_state = cli.build_snapshot_state(repo)
        (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        provider_file = repo / PROVIDER_RUNTIME_PATH
        provider_file.parent.mkdir(parents=True, exist_ok=True)
        provider_file.write_text('{"provider":"gitnexus","kind":"runtime"}\n', encoding="utf-8")
        ignored_asset_paths = [AI_CODEGRAPH_ASSET_PATH, CODEGRAPH_ASSET_PATH, GITNEXUS_ASSET_PATH]
        for asset_path in ignored_asset_paths:
            ignored_runtime_file = repo / asset_path
            ignored_runtime_file.parent.mkdir(parents=True, exist_ok=True)
            ignored_runtime_file.write_text('{"runtime":true}\n', encoding="utf-8")

        after_state = cli.build_snapshot_state(repo)
        changed_files, patch, evidence = cli.compute_snapshot_delta(before_state, after_state)
        file_lanes = cli.classify_change_set_files(changed_files)

        if "app.py" not in file_lanes["implementation_files"]:
            raise AssertionError(f"implementation file was not classified as implementation: {file_lanes}")
        if PROVIDER_RUNTIME_PATH in file_lanes["implementation_files"]:
            raise AssertionError(f"provider runtime file leaked into implementation_files: {file_lanes}")
        if PROVIDER_RUNTIME_PATH not in file_lanes["provider_runtime_files"]:
            raise AssertionError(f"provider runtime file missing from provider_runtime_files: {file_lanes}")
        changed_paths = {item["path_new"] or item["path_old"] for item in changed_files}
        leaked_assets = sorted(path for path in ignored_asset_paths if path in changed_paths)
        if leaked_assets:
            raise AssertionError(f"ignored provider graph assets leaked into snapshot delta: {leaked_assets}: {changed_files}")
        for asset_path in ignored_asset_paths:
            if cli.runtime_file_classification(asset_path) != "ignored_runtime":
                raise AssertionError(f"{asset_path} was not classified as ignored provider graph asset")

        evidence_by_path = {row["path_new"] or row["path_old"]: row for row in evidence["files"]}
        provider_row = evidence_by_path.get(PROVIDER_RUNTIME_PATH)
        if not provider_row:
            raise AssertionError(f"provider runtime evidence row missing: {evidence}")
        if provider_row.get("classification") != "provider_runtime":
            raise AssertionError(f"provider runtime evidence row lost classification: {provider_row}")
        if provider_row.get("runtime_file_classification") != "provider_runtime":
            raise AssertionError(f"provider runtime evidence row lost runtime classification: {provider_row}")
        if provider_row.get("file_lane") != "provider_runtime":
            raise AssertionError(f"provider runtime evidence row lost file lane: {provider_row}")
        if provider_row.get("skipped_text_reason") != "provider_runtime_file":
            raise AssertionError(f"provider runtime text was not skipped as runtime material: {provider_row}")
        if PROVIDER_RUNTIME_PATH in patch:
            raise AssertionError(f"provider runtime text patch leaked into implementation patch: {patch}")

        return {
            "implementation_files": file_lanes["implementation_files"],
            "provider_runtime_files": file_lanes["provider_runtime_files"],
            "ignored_provider_graph_assets_omitted": ignored_asset_paths,
        }


def assert_exec_stop_change_set_classification() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="shujuan-provider-runtime-change-set-") as temp:
        repo = Path(temp)
        postgres_started = False
        try:
            git(repo, "init")
            (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
            (repo / "plan.md").write_text("# Runtime Classification\n\nProvider runtime files need separate lanes.\n", encoding="utf-8")
            git(repo, "add", "app.py", "plan.md")
            git(repo, "-c", "user.name=Runtime", "-c", "user.email=runtime@example.invalid", "commit", "-m", "seed")

            init_payload = as_json(
                run_cli(repo, "init", "--name", "provider-runtime", "--postgres-dev", "--postgres-dev-port", str(free_port()))
            )
            postgres_started = True
            if init_payload["database"]["backend"] != "postgres":
                raise AssertionError(f"init --postgres-dev did not use PostgreSQL: {init_payload}")
            doc = as_json(run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan"))
            scope = as_json(run_cli(repo, "scope", "create", "--body", "Runtime classification scope.", "--source-node", str(doc["document_node_id"])))
            task = as_json(
                run_cli(
                    repo,
                    "task",
                    "add",
                    "--body",
                    "Classify provider runtime files.",
                    "--contract",
                    str(scope["contract_id"]),
                    "--from-node",
                    str(doc["document_node_id"]),
                )
            )
            as_json(run_cli(repo, "endpoint", "create", "provider-runtime", "--root-node", str(scope["node_id"])))
            as_json(
                run_cli(
                    repo,
                    "exec",
                    "start",
                    "--endpoint",
                    "provider-runtime",
                    "--summary",
                    "runtime classification run",
                    "--task-node",
                    str(task["node_id"]),
                    "--allow-preflight-warning",
                    "--allow-reason",
                    "isolated regression fixture does not import a conversation prompt",
                )
            )

            (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
            provider_file = repo / PROVIDER_RUNTIME_PATH
            provider_file.parent.mkdir(parents=True, exist_ok=True)
            provider_file.write_text('{"provider":"gitnexus","kind":"runtime"}\n', encoding="utf-8")
            stopped = as_json(
                run_cli(
                    repo,
                    "exec",
                    "stop",
                    "--endpoint",
                    "provider-runtime",
                    "--summary",
                    "Runtime file classification stop.",
                )
            )
            change_set = stopped["change_set"]
            conn = connect(repo)
            try:
                row = conn.execute(
                    "SELECT metadata FROM change_sets WHERE id = ?",
                    (change_set["change_set_id"],),
                ).fetchone()
            finally:
                conn.close()
            metadata = json.loads(row["metadata"])
            assert_runtime_classification_payload(change_set, metadata=metadata)
            return {
                "change_set_id": change_set["change_set_id"],
                "implementation_files": change_set["implementation_files"],
                "provider_runtime_files": change_set["provider_runtime_files"],
                "metadata_provider_runtime_files": metadata["provider_runtime_files"],
            }
        finally:
            if postgres_started:
                run_cli(repo, "postgres-dev", "stop")


def main() -> int:
    snapshot_result = assert_snapshot_delta_classification()
    change_set_result = assert_exec_stop_change_set_classification()
    print(
        json.dumps(
            {
                "ok": True,
                "snapshot_delta": snapshot_result,
                "captured_change_set": change_set_result,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
