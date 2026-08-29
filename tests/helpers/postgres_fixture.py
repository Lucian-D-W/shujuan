from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]


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


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


@dataclass
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def json(self) -> dict:
        return json.loads(self.stdout)


@dataclass
class PostgresFixture:
    repo: Path
    port: int
    writes: list[str] = field(default_factory=list)
    commands: list[CommandResult] = field(default_factory=list)

    def run(self, *args: str, expect_ok: bool = True, env_extra: dict[str, str] | None = None) -> CommandResult:
        completed = subprocess.run(
            [sys.executable, "-m", "shujuan", "--repo", str(self.repo), *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_env(env_extra),
        )
        result = CommandResult(args=tuple(args), returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
        self.commands.append(result)
        if expect_ok and completed.returncode:
            raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
        if not expect_ok and completed.returncode == 0:
            raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
        return result

    def run_json(self, *args: str, expect_ok: bool = True, env_extra: dict[str, str] | None = None) -> dict:
        return self.run(*args, expect_ok=expect_ok, env_extra=env_extra).json()

    def run_batch(self, commands: Iterable[tuple[str, ...]]) -> list[CommandResult]:
        return [self.run(*command) for command in commands]

    def stop(self) -> None:
        self.run("postgres-dev", "stop", expect_ok=True)


def postgres_fixture(prefix: str) -> tuple[tempfile.TemporaryDirectory[str], PostgresFixture] | None:
    if not has_postgres_bins():
        return None
    temp = tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)
    repo = Path(temp.name)
    fixture = PostgresFixture(repo=repo, port=free_port())
    fixture.run_json("init", "--name", prefix.rstrip("-"), "--postgres-dev", "--postgres-dev-port", str(fixture.port))
    fixture.writes.append("temporary postgres-dev repo only")
    return temp, fixture


__all__ = [
    "CommandResult",
    "PostgresFixture",
    "clean_env",
    "free_port",
    "has_postgres_bins",
    "postgres_fixture",
]
