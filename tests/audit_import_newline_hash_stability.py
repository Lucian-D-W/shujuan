from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import hashlib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.store import connect, json_dumps


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
        raise AssertionError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def run_cli(repo: Path, *args: str) -> dict[str, Any]:
    return json.loads(run_cli_completed(repo, *args).stdout)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def has_postgres_bins() -> bool:
    candidates = []
    env_bin = os.environ.get("SHUJUAN_POSTGRES_BIN")
    if env_bin:
        candidates.append(Path(env_bin))
    candidates.append(Path(r"C:\Program Files\PostgreSQL\17\bin"))
    return any((path / "initdb.exe").exists() or (path / "initdb").exists() for path in candidates)


def assert_artifact_check_ok(verify_result: dict[str, Any], node_id: str) -> None:
    matches = [
        item
        for item in verify_result["checks"]
        if item["node_id"] == node_id and item["label"] == "artifact_ref"
    ]
    if not matches or matches[0]["status"] != "ok":
        raise AssertionError(f"artifact verify did not pass for {node_id}: {verify_result}")


def main() -> int:
    if not has_postgres_bins():
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0

    postgres_started = False
    with tempfile.TemporaryDirectory(prefix="shujuan-newline-hash-") as temp:
        repo = Path(temp)
        try:
            run_cli(
                repo,
                "init",
                "--name",
                "audit-import-newline-hash-stability",
                "--postgres-dev",
                "--postgres-dev-port",
                str(free_port()),
            )
            postgres_started = True
            (repo / "plan.md").write_text(
                "# Newline Hash Stability\n\n"
                "Audit import must match artifact hashes across Windows CRLF and LF text inputs.\n",
                encoding="utf-8",
            )
            doc = run_cli(repo, "doc", "import", "plan.md", "--source-type", "plan")
            scope = run_cli(repo, "scope", "create", "--body", "Newline hash stability scope.", "--source-node", doc["document_node_id"])
            run_cli(repo, "endpoint", "create", "newline-hash", "--root-node", scope["node_id"])

            body = "Changed files:\n- shujuan/cli.py\nTests:\n- python tests\\audit_import_newline_hash_stability.py\n"
            lf_path = repo / "agent_output_lf.md"
            crlf_path = repo / "agent_output_crlf.md"
            lf_path.write_bytes(body.encode("utf-8"))
            crlf_path.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))

            lf_import = run_cli(
                repo,
                "audit",
                "import-agent-output",
                "--endpoint",
                "newline-hash",
                "--source-node",
                doc["document_node_id"],
                "--classification",
                "summary",
                "--path",
                str(lf_path),
            )
            crlf_import = run_cli(
                repo,
                "audit",
                "import-agent-output",
                "--endpoint",
                "newline-hash",
                "--source-node",
                doc["document_node_id"],
                "--classification",
                "summary",
                "--path",
                str(crlf_path),
            )
            body_import = run_cli(
                repo,
                "audit",
                "import-agent-output",
                "--endpoint",
                "newline-hash",
                "--source-node",
                doc["document_node_id"],
                "--classification",
                "summary",
                "--body",
                body,
            )

            lf_artifact = lf_import["artifact"]
            crlf_artifact = crlf_import["artifact"]
            if lf_artifact["normalized_text_hash"] != crlf_artifact["normalized_text_hash"]:
                raise AssertionError(f"LF and CRLF normalized hashes diverged: {lf_artifact}, {crlf_artifact}")
            if lf_artifact["sha256"] != crlf_artifact["sha256"]:
                raise AssertionError(f"LF and CRLF matching hashes diverged: {lf_artifact}, {crlf_artifact}")
            if lf_artifact["capture_byte_hash"] == crlf_artifact["capture_byte_hash"]:
                raise AssertionError("raw byte hashes should preserve the original LF/CRLF distinction")
            if crlf_artifact["text_normalization"] != "lf-newlines":
                raise AssertionError(f"text normalization metadata was not stored: {crlf_artifact}")

            body_capture = repo / body_import["artifact"]["capture_ref"]
            if body_capture.read_bytes() != body.encode("utf-8"):
                raise AssertionError("body artifact capture used platform newline translation")

            legacy_props = dict(body_import["artifact"])
            legacy_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            legacy_props.update(
                {
                    "sha256": legacy_hash,
                    "capture_byte_hash": legacy_hash,
                    "normalized_text_hash": legacy_hash,
                    "hash_schema_version": 2,
                    "text_normalization": "none",
                }
            )
            body_capture.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
            conn = connect(repo)
            try:
                conn.execute("UPDATE nodes SET props = ? WHERE id = ?", (json_dumps(legacy_props), body_import["artifact_node_id"]))
                conn.commit()
            finally:
                conn.close()

            verify_result = run_cli(repo, "evidence", "verify", "--endpoint", "newline-hash", "--include-history")
            if not verify_result["ok"] or verify_result["buckets"]["tampered"]:
                raise AssertionError(f"evidence verify reported a false mismatch: {verify_result}")
            for imported in (lf_import, crlf_import, body_import):
                assert_artifact_check_ok(verify_result, imported["artifact_node_id"])
            body_checks = [
                item
                for item in verify_result["checks"]
                if item["node_id"] == body_import["artifact_node_id"] and item["label"] == "artifact_ref"
            ]
            if body_checks[0].get("hash_match") != "fallback":
                raise AssertionError(f"legacy CRLF body artifact did not use normalized fallback: {verify_result}")

            print(
                json.dumps(
                    {
                        "ok": True,
                        "normalized_text_hash": lf_artifact["normalized_text_hash"],
                        "lf_artifact_node_id": lf_import["artifact_node_id"],
                        "crlf_artifact_node_id": crlf_import["artifact_node_id"],
                        "body_artifact_node_id": body_import["artifact_node_id"],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        finally:
            if postgres_started:
                run_cli_completed(repo, "postgres-dev", "stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
