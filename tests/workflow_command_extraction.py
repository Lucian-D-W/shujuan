from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shujuan.cli import build_parser
from shujuan.commands import workflow


def parser_for(name: str) -> argparse.ArgumentParser:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[name]
    raise AssertionError("root parser has no subcommands")


def workflow_subcommands(name: str) -> set[str]:
    parser = parser_for(name)
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError(f"{name} parser has no subcommands")


def main() -> int:
    signature = inspect.signature(workflow.build_workflow_handlers)
    if "exec_stop_handler" not in signature.parameters:
        raise AssertionError("workflow module must own exec_stop_handler dependency injection")
    for name in ("work", "fix"):
        commands = workflow_subcommands(name)
        expected = set(workflow.WORKFLOW_HANDLER_KEYS)
        expected.remove("acceptance_template")
        expected.add("acceptance-template")
        expected.add("audit-source")
        expected.remove("audit_source")
        if commands != expected:
            raise AssertionError(f"{name} command set changed: {sorted(commands)}")
    print('{"ok": true, "workflow_command_extraction": "passed"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
