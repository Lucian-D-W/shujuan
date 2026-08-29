from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

from shujuan.commands import postgres_dev


def windows_acl(path: Path) -> str:
    completed = subprocess.run(
        ["icacls", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise AssertionError("could not inspect the test credential ACL")
    return completed.stdout


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-credential-permissions-") as raw_temp:
        credential_path = Path(raw_temp) / "credentials.json"
        expected = '{"password":"test-only"}'  # pragma: allowlist secret
        postgres_dev.write_private_text(credential_path, expected)
        replacement = '{"password":"replacement"}'  # pragma: allowlist secret
        postgres_dev.write_private_text(credential_path, replacement)

        if credential_path.read_text(encoding="utf-8") != replacement:
            raise AssertionError("private credential write did not preserve contents")
        if list(credential_path.parent.glob(".credentials.json.*.tmp")):
            raise AssertionError("private credential write left a temporary secret file")
        if list(credential_path.parent.glob(".credentials.json.*.previous")):
            raise AssertionError("private credential write left a previous secret file")

        if os.name == "nt":
            acl_text = windows_acl(credential_path)
            if "(I)" in acl_text:
                raise AssertionError("credential ACL contains inherited rules")
            if acl_text.count(":(F)") != 3:
                raise AssertionError("credential ACL does not contain exactly three full-control identities")
        else:
            mode = stat.S_IMODE(credential_path.stat().st_mode)
            if mode != stat.S_IRUSR | stat.S_IWUSR:
                raise AssertionError(f"credential mode is {oct(mode)}, expected 0o600")

    print("postgres-dev credential permissions: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
