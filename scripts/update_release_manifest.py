from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "MANIFEST.json"
MANIFEST_SHA_PATH = ROOT / "MANIFEST.sha256"
EXCLUDED_PATHS = {"MANIFEST.json", "MANIFEST.sha256"}


def tracked_paths() -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise SystemExit(completed.stderr.strip() or "could not list tracked files")
    return sorted(
        path
        for path in completed.stdout.splitlines()
        if path and path not in EXCLUDED_PATHS
    )


def main() -> int:
    files = []
    for relative_path in tracked_paths():
        target = ROOT / relative_path
        if not target.is_file():
            raise SystemExit(f"tracked manifest input is missing: {relative_path}")
        contents = target.read_bytes()
        files.append(
            {
                "path": relative_path,
                "sha256": hashlib.sha256(contents).hexdigest(),
                "size": len(contents),
            }
        )

    payload = {
        "files": files,
        "generated_for": "canonical-public-tree",
        "schema": "shujuan.release_manifest.v11",
    }
    manifest_bytes = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    MANIFEST_PATH.write_bytes(manifest_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    MANIFEST_SHA_PATH.write_text(f"{manifest_hash}  MANIFEST.json\n", encoding="utf-8")
    print(json.dumps({"ok": True, "file_count": len(files), "manifest_sha256": manifest_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
