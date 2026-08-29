from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.compat_exports import INTERNAL_ONLY_IMPORT_RULES, PUBLIC_COMPAT_EXPORTS


def iter_python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


def assert_public_exports_exist() -> None:
    for module_name, names in PUBLIC_COMPAT_EXPORTS.items():
        module = importlib.import_module(module_name)
        for name in names:
            if not hasattr(module, name):
                raise AssertionError(f"{module_name} does not export {name}")


def assert_internal_import_discipline() -> None:
    if "Command modules must not import shujuan.cli directly." not in "\n".join(INTERNAL_ONLY_IMPORT_RULES["shujuan.commands"]):
        raise AssertionError("manifest does not state command import discipline")
    offenders = []
    for path in iter_python_files(ROOT / "shujuan" / "commands"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {"shujuan.cli", "..cli", ".cli"}:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "shujuan.cli":
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    if offenders:
        raise AssertionError(f"command modules import cli directly: {offenders}")


def assert_service_import_discipline() -> None:
    offenders = []
    for path in iter_python_files(ROOT / "shujuan" / "services"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and (node.module.endswith(".cli") or ".commands" in node.module):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "shujuan.cli" or alias.name.startswith("shujuan.commands"):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    if offenders:
        raise AssertionError(f"service modules import CLI/command owners: {offenders}")


def main() -> int:
    assert_public_exports_exist()
    assert_internal_import_discipline()
    assert_service_import_discipline()
    print(json.dumps({"ok": True, "compatibility_export_manifest": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
