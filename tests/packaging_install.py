from __future__ import annotations

import os
import json
import socket
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def has_postgres_bins() -> bool:
    candidates = []
    env_bin = os.environ.get("SHUJUAN_POSTGRES_BIN")
    if env_bin:
        candidates.append(Path(env_bin))
    candidates.append(Path(r"C:\Program Files\PostgreSQL\17\bin"))
    candidates.extend(Path(item) for item in os.environ.get("PATH", "").split(os.pathsep) if item)
    return any((path / "initdb.exe").exists() or (path / "initdb").exists() for path in candidates)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def venv_python(env_dir: Path) -> Path:
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def venv_script(env_dir: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    script_dir = "Scripts" if os.name == "nt" else "bin"
    return env_dir / script_dir / f"{name}{suffix}"


def run(cwd: Path, *args: str | Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    completed = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if completed.returncode:
        command = " ".join(str(arg) for arg in args)
        raise AssertionError(f"command failed: {command}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    return completed


def run_optional(cwd: Path, *args: str | Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-packaging-") as temp:
        temp_dir = Path(temp)
        env_dir = temp_dir / "venv"
        external_cwd = temp_dir / "external-cwd"
        repo = temp_dir / "external-repo"
        external_cwd.mkdir()
        repo.mkdir()

        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = venv_python(env_dir)
        shujuan = venv_script(env_dir, "shujuan")

        run(external_cwd, python, "-m", "pip", "install", "-e", ROOT)
        console_help = run(external_cwd, shujuan, "--help")
        module_help = run(external_cwd, python, "-m", "shujuan", "--help")

        if "usage:" not in console_help.stdout or "usage:" not in module_help.stdout:
            raise AssertionError("installed console/module help output was not usable")
        if not has_postgres_bins():
            print(json.dumps({"ok": True, "packaging_install": "static_smoke_passed", "postgres_smoke": "skipped_native_postgresql_bins_missing"}))
            return 0
        init_result = run(
            external_cwd,
            python,
            "-m",
            "shujuan",
            "--repo",
            repo,
            "init",
            "--name",
            "packaging-install",
            "--postgres-dev",
            "--postgres-dev-port",
            str(free_port()),
        )
        report_result = run(external_cwd, shujuan, "--repo", repo, "report", "project", "--markdown")
        init_payload = json.loads(init_result.stdout)
        if init_payload.get("ok") is not True:
            raise AssertionError(f"installed module init did not return JSON success: {init_result.stdout}")
        migrations = init_payload.get("migrations") or {}
        if migrations.get("path") != "migrations/shujuan":
            raise AssertionError(f"installed init did not expose repo-local migrations: {init_payload}")
        if not (repo / "migrations" / "shujuan" / "001_agcp10_minimal_data_model.sql").exists():
            raise AssertionError("installed init did not create target repo migrations/shujuan files")
        migrate_result = run(external_cwd, shujuan, "--repo", repo, "migrate", "status")
        migrate_payload = json.loads(migrate_result.stdout)
        if not migrate_payload.get("migrations"):
            raise AssertionError(f"migrate status did not see repo-local migration files: {migrate_payload}")
        if "shujuan Project Report" not in report_result.stdout:
            raise AssertionError(f"installed console report did not render markdown: {report_result.stdout}")
        stop_result = run_optional(external_cwd, shujuan, "--repo", repo, "postgres-dev", "stop")
        if stop_result.returncode:
            raise AssertionError(f"postgres-dev stop failed after packaging smoke:\nSTDOUT:\n{stop_result.stdout}\nSTDERR:\n{stop_result.stderr}")
    print(json.dumps({"ok": True, "packaging_install": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
