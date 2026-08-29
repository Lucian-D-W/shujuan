from __future__ import annotations

import json
import socket
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.cli import choose_postgres_dev_port, default_postgres_dev_port


def listen_on(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(10)
    return sock


def reserved_listener() -> tuple[int, socket.socket]:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port, listen_on(port)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-pg-ports-") as temp:
        root = Path(temp)
        repos = [root / f"lane-{index:02d}" for index in range(12)]
        for repo in repos:
            repo.mkdir()
        default_ports = [default_postgres_dev_port(repo) for repo in repos]
        if len(set(default_ports)) <= 1:
            raise AssertionError(f"default postgres-dev ports did not vary by repo: {default_ports}")
        for repo, port in zip(repos, default_ports):
            if default_postgres_dev_port(repo) != port:
                raise AssertionError(f"default postgres-dev port was not stable for {repo}")

        fallback_listener = None
        explicit_listener = None
        try:
            candidate, fallback_listener = reserved_listener()
            fallback = choose_postgres_dev_port(candidate, explicit=False)
            if fallback == candidate:
                raise AssertionError("default postgres-dev port selection did not skip occupied candidate")

            explicit_candidate, explicit_listener = reserved_listener()
            explicit_failed = False
            try:
                choose_postgres_dev_port(explicit_candidate, explicit=True)
            except SystemExit as exc:
                explicit_failed = f"requested postgres-dev port is already in use: {explicit_candidate}" in str(exc)
            if not explicit_failed:
                raise AssertionError("explicit occupied postgres-dev port did not fail")
        finally:
            if fallback_listener is not None:
                fallback_listener.close()
            if explicit_listener is not None:
                explicit_listener.close()

    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
