from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
        env.pop(key, None)
    return env


def _run(repo: Path, *args: str, expect_ok: bool = True) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_env(),
    )
    if expect_ok and completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    if not expect_ok and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}")
    return json.loads(completed.stdout)


def _run_hook(path: Path, input_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_env(),
    )


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _minimal_task_chain_payload() -> dict:
    return {
        "declares_no_closure": True,
        "closed_by_decomposition": False,
        "source_items": [
            {
                "id": "SRC01",
                "classification": "P0",
                "status": "active",
                "graph_destination": {"kind": "task", "id": "T01"},
                "task_ids": ["T01"],
                "check_ids": ["C01"],
                "rationale": "Imported from the controller source item.",
                "promotion_rule": "Active repair source.",
                "reopen_rule": "Reopen if the route contract regresses.",
            }
        ],
        "tasks": [
            {
                "key": "T01",
                "title": "Route contract repair",
                "body": "Repair route contract behavior.",
                "phase": "P0",
                "order": 10,
                "mandatory": True,
                "source_refs": ["SRC01"],
            }
        ],
        "checks": [
            {
                "key": "C01",
                "task_key": "T01",
                "body": "Route contract probes pass.",
                "expected_evidence_type": "test_result",
                "source_refs": ["SRC01"],
            }
        ],
    }


def _assert_agents_kernel() -> None:
    for path in (ROOT / "AGENTS.md", ROOT / "shujuan" / "assets" / "AGENTS.md"):
        text = path.read_text(encoding="utf-8")
        if len(text.encode("utf-8")) > 6144 or len(text.splitlines()) > 90:
            raise AssertionError(f"AGENTS kernel exceeds v11.2 budget: {path}")
        first = text.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        required = (
            "## Four Gates",
            "## Intent Priority",
            "## Method Map",
            "route guard --pure",
            "Read-only commands must not create",
            "Worker/reviewer/provider output is material until controller adoption",
            "hooks are advisory guardrails",
        )
        for fragment in required:
            if fragment not in first:
                raise AssertionError(f"{path} missed first-surface fragment: {fragment}")


def _assert_route_side_effects_and_intents() -> None:
    with tempfile.TemporaryDirectory(prefix="v112-route-") as temp:
        repo = Path(temp)
        default_payload = _run(repo, "route", "guard", "--intent", "implement a small fix")
        if default_payload.get("trace_explicit") is not False or default_payload.get("trace_written") is not False:
            raise AssertionError(f"default route guard trace fields drifted: {default_payload}")
        if default_payload.get("filesystem_writes") != 0 or default_payload.get("db_writes") != 0:
            raise AssertionError(f"default route guard reported writes: {default_payload}")
        if (repo / ".shujuan").exists():
            raise AssertionError("default route guard created .shujuan without --trace")

        missing_close = _run(repo, "route", "guard", "--intent", "execute closeout", expect_ok=False)
        if missing_close.get("trace_written") is not False or (repo / ".shujuan").exists():
            raise AssertionError(f"closeout failure wrote trace without --trace: {missing_close}")

        traced = _run(repo, "route", "guard", "--trace", "--intent", "implement a small fix")
        if traced.get("trace_explicit") is not True or traced.get("trace_written") is not True:
            raise AssertionError(f"explicit trace was not reported: {traced}")
        if not (repo / ".shujuan" / "trace" / "workflow_trace.jsonl").exists():
            raise AssertionError("explicit --trace did not create workflow_trace.jsonl")

    with tempfile.TemporaryDirectory(prefix="v112-task-chain-dry-run-") as temp:
        repo = Path(temp)
        artifact = _write_json(repo / "chain.json", _minimal_task_chain_payload())
        preview = _run(repo, "plan-to-db", "import-task-chain", "--artifact", str(artifact), "--endpoint", "ep", "--dry-run")
        if not preview["read_only"] or preview.get("filesystem_writes") != 0 or preview.get("trace_written") is not False or preview.get("out") is not None:
            raise AssertionError(f"dry-run import should be zero-write by default: {preview}")
        if (repo / ".shujuan").exists():
            raise AssertionError("import-task-chain --dry-run created .shujuan without --out/--trace")
        out_path = repo / "preview.json"
        explicit_out = _run(repo, "plan-to-db", "import-task-chain", "--artifact", str(artifact), "--endpoint", "ep", "--dry-run", "--out", str(out_path))
        if explicit_out.get("filesystem_writes") != 1 or explicit_out.get("trace_written") is not False or not out_path.exists():
            raise AssertionError(f"explicit dry-run --out did not report exactly one file write: {explicit_out}")
        if (repo / ".shujuan").exists():
            raise AssertionError("import-task-chain --dry-run --out created .shujuan without --trace")
        traced_preview = _run(repo, "plan-to-db", "import-task-chain", "--artifact", str(artifact), "--endpoint", "ep", "--dry-run", "--trace")
        if traced_preview.get("filesystem_writes") != 1 or traced_preview.get("trace_written") is not True:
            raise AssertionError(f"explicit dry-run --trace did not report trace write: {traced_preview}")
        if not (repo / ".shujuan" / "trace" / "ep" / "workflow_trace.jsonl").exists():
            raise AssertionError("import-task-chain --dry-run --trace did not create endpoint trace")

    for intent in (
        "只需要 worker return 和材料，不要认领任务。",
        "Review this independently. Do not close anything.",
        "Review this worker return independently without closing.",
        "Please independently check this material; no direct closeout.",
        "请单独做一次独立 review，这次不执行、也不 close。",
        "Can you just review this fix and give acceptance summary?",
        "Review whether this can close, but do not close it.",
        "Worker says it is done, close nothing yet.",
        "Does this evidence look sufficient for closure?",
        "请签收检查, but do not close anything.",
        "Have a worker implement this slice, but do not close anything.",
        "Use a researcher to gather facts and then wait for controller adoption.",
        "Writer should draft a closeout report but not adopt evidence.",
        "Can a worker refresh endpoint and close checks after tests pass?",
        "先回顾 history，再让 worker 修改代码，controller 不要采纳。",
    ):
        with tempfile.TemporaryDirectory(prefix="v112-review-") as temp:
            payload = _run(Path(temp), "route", "guard", "--pure", "--intent", intent)
            if payload["recommended_route"] != "Delegate" or payload["recommended_skill"] != "shujuan-delegate":
                raise AssertionError(f"review intent did not route to Delegate: {payload}")
            if payload["authority_posture"] != "reviewer_material":
                raise AssertionError(f"review intent did not stay material-only: {payload}")

    for intent in (
        "Can we close task T01_AGENTS_KERNEL now?",
        "Can we close check C01 with evidence?",
        "Please sign off task T01 with current evidence.",
        "Mark done check C01 using evidence ref E01.",
            "Approve closure for this endpoint.",
            "Close out this check now.",
            "mark task complete",
            "resolve this check as accepted",
            "accept this check",
            "Continue the previous endpoint and close remaining checks.",
            "签收检查",
            "标记任务完成",
            "将检查标记为通过",
            "接受这个检查",
        ):
        with tempfile.TemporaryDirectory(prefix="v112-close-") as temp:
            payload = _run(Path(temp), "route", "guard", "--pure", "--intent", intent, expect_ok=False)
            if payload["recommended_route"] != "Close" or payload["recommended_skill"] != "shujuan-close":
                raise AssertionError(f"close request did not route to Close: {payload}")
            if payload.get("error", {}).get("code") != "missing_closeout_inputs" or payload["exit_brake"]["stop_writes"] is not True:
                raise AssertionError(f"close request did not fail closed on missing inputs: {payload}")

    with tempfile.TemporaryDirectory(prefix="v112-meta-") as temp:
        repo = Path(temp)
        meta = _run(repo, "route", "guard", "--pure", "--intent", "Explain the No Governance phrase and why it exists.")
        if meta["recommended_route"] != "Recall":
            raise AssertionError(f"No Governance meta-topic did not route to Recall: {meta}")
        record_meta = _run(repo, "route", "guard", "--pure", "--intent", "Explain what do not record means in this governance policy.")
        if record_meta["recommended_route"] != "Recall":
            raise AssertionError(f"do-not-record meta-topic did not route to Recall: {record_meta}")
        explicit = _run(repo, "route", "guard", "--pure", "--intent", "No Governance for this task; just answer.")
        if explicit["recommended_route"] != "No Governance":
            raise AssertionError(f"explicit sovereignty exit stopped winning: {explicit}")
        explicit_record = _run(repo, "route", "guard", "--pure", "--intent", "do not record this task")
        if explicit_record["recommended_route"] != "No Governance":
            raise AssertionError(f"explicit do-not-record directive stopped winning: {explicit_record}")
        for intent in (
            "This is off the books; log it as source.",
            "This is off the books; just answer normally.",
            "off the books note for myself",
            "keep this as a private note",
            "no log for this source context",
            "save this in my vault",
            "不要捕获，只回答。",
            "不要捕获，不要保存，但是帮我关闭这个检查。",
        ):
            no_gov = _run(repo, "route", "guard", "--pure", "--intent", intent)
            if no_gov["recommended_route"] != "No Governance":
                raise AssertionError(f"sovereignty exit phrase did not win: {no_gov}")

    with tempfile.TemporaryDirectory(prefix="v112-priority-") as temp:
        repo = Path(temp)
        execute = _run(repo, "route", "guard", "--pure", "--intent", "Implement this fix and check git history first")
        if execute["recommended_route"] != "Execute" or execute["recommended_skill"] != "shujuan-execute":
            raise AssertionError(f"primary implementation request did not stay Execute: {execute}")
        execute_after_recall = _run(repo, "route", "guard", "--pure", "--intent", "请先回顾 lineage，然后修复 route 误判。")
        if execute_after_recall["recommended_route"] != "Execute" or execute_after_recall["recommended_skill"] != "shujuan-execute":
            raise AssertionError(f"implementation request after incidental lineage did not stay Execute: {execute_after_recall}")
        if execute_after_recall.get("auxiliary_recall") is not True or "Recall surface" not in execute_after_recall.get("safe_next_action", ""):
            raise AssertionError(f"execute+recall payload missed auxiliary recall marker/read-first action: {execute_after_recall}")
        patch_after_recall = _run(repo, "route", "guard", "--pure", "--intent", "Why was this designed this way? After that, patch the hook.")
        if patch_after_recall["recommended_route"] != "Execute" or patch_after_recall.get("auxiliary_recall") is not True:
            raise AssertionError(f"patch-after-recall request did not stay Execute with auxiliary recall: {patch_after_recall}")
        compare = _run(repo, "route", "guard", "--pure", "--intent", "Compare v11.2 and v11.2.2 without changing anything.")
        if compare["recommended_route"] != "Recall":
            raise AssertionError(f"read-only version comparison did not route to Recall: {compare}")
        successor = _run(repo, "route", "guard", "--pure", "--intent", "Start a successor patch scope to v11.2.2.")
        if successor["relation_decision"]["relation_type"] != "successor_scope" or successor["recommended_route"] != "Recover":
            raise AssertionError(f"successor patch scope did not bind successor relation: {successor}")
        capture = _run(repo, "route", "guard", "--pure", "--intent", "Capture these source snippets for provenance only.")
        if capture["recommended_route"] != "Capture" or capture["recommended_skill"] != "shujuan-capture":
            raise AssertionError(f"capture-only request did not route to Capture: {capture}")
        for capture_intent in (
            "Bookmark this discussion for traceability only.",
            "Note down these source snippets for traceability.",
            "Keep this material for traceability only.",
            "log as source",
            "log as provenance",
            "log as context",
            "stash conversation context",
            "stash source material",
            "存档来源",
            "作为出处保存",
        ):
            capture_synonym = _run(repo, "route", "guard", "--pure", "--intent", capture_intent)
            if capture_synonym["recommended_route"] != "Capture" or capture_synonym["recommended_skill"] != "shujuan-capture":
                raise AssertionError(f"capture synonym did not route to Capture: {capture_synonym}")
        capture_then_execute = _run(repo, "route", "guard", "--pure", "--intent", "Capture these source snippets then implement the fix.")
        if capture_then_execute["recommended_route"] != "Execute" or capture_then_execute["recommended_skill"] != "shujuan-execute":
            raise AssertionError(f"capture-then-implementation request did not stay Execute: {capture_then_execute}")
        for boundary_intent in (
            "keep this as a private note",
            "off the books note for myself",
            "no log for this source context",
            "save this in my vault",
            "do not save this source",
            "no capture for this context",
        ):
            boundary = _run(repo, "route", "guard", "--pure", "--intent", boundary_intent)
            if boundary["recommended_route"] == "Capture":
                raise AssertionError(f"capture sovereignty/private boundary routed to Capture: {boundary}")


def _assert_hooks_and_agents() -> None:
    hooks_config = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    if hooks_config.get("advisory") is not True or "UserPromptSubmit" not in hooks_config.get("hooks", {}):
        raise AssertionError(f"hooks config is not discoverable/advisory: {hooks_config}")
    for event, groups in hooks_config["hooks"].items():
        if not isinstance(groups, list) or not groups:
            raise AssertionError(f"hooks event has no matcher groups: {event} {hooks_config}")
        for group in groups:
            if event == "PreToolUse" and not isinstance(group.get("matcher"), str):
                raise AssertionError(f"PreToolUse group missed matcher: {group}")
            handlers = group.get("hooks")
            if not isinstance(handlers, list) or not handlers:
                raise AssertionError(f"hook group missed handlers: {group}")
            for handler in handlers:
                if handler.get("type") != "command" or not isinstance(handler.get("command"), str):
                    raise AssertionError(f"hook handler is not official command shape: {handler}")
                command = handler["command"]
                if ".codex/hooks/" in command and "git rev-parse --show-toplevel" not in command:
                    raise AssertionError(f"hook handler command is not repo-root-safe: {handler}")
    hook_bases = (
        ROOT / ".codex",
        ROOT / "shujuan" / "assets",
    )
    for hook_name in ("shujuan-method-hint.py", "shujuan-pretool-guard.py"):
        texts = [(base / "hooks" / hook_name).read_text(encoding="utf-8") for base in hook_bases]
        if len(set(texts)) != 1:
            raise AssertionError(f"hook mirror drifted for {hook_name}")
    for base in hook_bases:
        method_hook = base / "hooks" / "shujuan-method-hint.py"
        pretool_hook = base / "hooks" / "shujuan-pretool-guard.py"
        for hook_path in (method_hook, pretool_hook):
            for input_text in ("", "not json", "[]"):
                noop = _run_hook(hook_path, input_text)
                if noop.returncode or noop.stdout or noop.stderr:
                    raise AssertionError(f"{hook_path} did not no-op for invalid input: {noop}")
        empty_prompt = _run_hook(method_hook, json.dumps({"prompt": "   "}))
        if empty_prompt.returncode or empty_prompt.stdout or empty_prompt.stderr:
            raise AssertionError(f"method hook did not no-op for empty prompt: {empty_prompt}")
        non_string_prompt = _run_hook(method_hook, json.dumps({"prompt": {"bad": "type"}}))
        if non_string_prompt.returncode or non_string_prompt.stdout or non_string_prompt.stderr:
            raise AssertionError(f"method hook did not no-op for invalid prompt type: {non_string_prompt}")
        nul_prompt = _run_hook(method_hook, json.dumps({"prompt": "fix this\x00 please"}))
        if nul_prompt.returncode:
            raise AssertionError(f"method hook crashed on NUL prompt: {nul_prompt}")
        method_text = method_hook.read_text(encoding="utf-8")
        if "--intent-file" not in method_text or "--intent\", prompt" in method_text:
            raise AssertionError(f"method hook still passes prompt through argv: {method_hook}")

        hook = _run_hook(method_hook, json.dumps({"prompt": "Explain the No Governance phrase."}))
        if hook.returncode:
            raise AssertionError(f"method hook failed: {hook.stderr}")
        payload = json.loads(hook.stdout)
        output = payload.get("hookSpecificOutput") or {}
        if output.get("hookEventName") != "UserPromptSubmit" or "shujuan-recall" not in output.get("additionalContext", ""):
            raise AssertionError(f"method hook schema drifted: {payload}")

        pretool = _run_hook(pretool_hook, json.dumps({"tool_input": "python -m shujuan endpoint refresh ep"}))
        if pretool.returncode:
            raise AssertionError(f"pretool hook failed: {pretool.stderr}")
        guard = json.loads(pretool.stdout)
        specific = guard.get("hookSpecificOutput") or {}
        if guard.get("decision") != "block" or specific.get("permissionDecision") != "deny":
            raise AssertionError(f"pretool deny/block schema drifted: {guard}")
        list_form_pretool = _run_hook(pretool_hook, json.dumps({"tool_input": ["python", "-m", "shujuan", "endpoint", "refresh", "ep"]}))
        list_form_guard = json.loads(list_form_pretool.stdout)
        if list_form_guard.get("decision") != "block":
            raise AssertionError(f"pretool hook missed list-form risky command: {list_form_guard}")
        evidence_close = _run_hook(pretool_hook, json.dumps({"tool_input": "python -m shujuan evidence close --endpoint ep"}))
        evidence_closeout = _run_hook(pretool_hook, json.dumps({"tool_input": "python -m shujuan evidence closeout --endpoint ep"}))
        for blocked in (evidence_close, evidence_closeout):
            payload = json.loads(blocked.stdout)
            if payload.get("decision") != "block":
                raise AssertionError(f"pretool hook missed evidence close/closeout command: {payload}")
        safe = _run_hook(pretool_hook, json.dumps({"tool_input": "python -m shujuan report endpoint ep --active-only --markdown"}))
        if safe.returncode or safe.stdout or safe.stderr:
            raise AssertionError(f"pretool hook did not stay quiet for safe command: {safe}")
        evidence_list = _run_hook(pretool_hook, json.dumps({"tool_input": "python -m shujuan evidence list --endpoint ep"}))
        if evidence_list.returncode or evidence_list.stdout or evidence_list.stderr:
            raise AssertionError(f"pretool hook blocked safe evidence list command: {evidence_list}")

    for base in (ROOT / ".codex" / "agents", ROOT / "shujuan" / "assets" / "agents"):
        for path in base.glob("*.toml"):
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
            for field in ("name", "description", "developer_instructions"):
                if not payload.get(field):
                    raise AssertionError(f"{path} missed canonical field {field}")
            if "instructions" in payload:
                raise AssertionError(f"{path} kept legacy instructions field")


def _assert_install_surfaces() -> None:
    with tempfile.TemporaryDirectory(prefix="v112-install-") as temp:
        repo = Path(temp)
        init_payload = _run(repo, "init", "--install-skills", expect_ok=False)
        installed = json.dumps(init_payload.get("installed_assets") or [], ensure_ascii=False)
        if ".codex/hooks.json" not in installed:
            raise AssertionError(f"init did not surface hooks install: {init_payload}")
        doctor = _run(repo, "install-layout", "doctor")
        diagnostics = doctor.get("v11_2_diagnostics") or {}
        for field in ("agents_md", "skills", "core_shim", "route_guard", "role_profiles", "hooks", "evidence_pack"):
            if field not in diagnostics:
                raise AssertionError(f"doctor missed v11.2 diagnostic field {field}: {doctor}")
        if diagnostics["hooks"].get("authoritative") is not False or diagnostics["hooks"].get("advisory") is not True:
            raise AssertionError(f"doctor did not mark hooks advisory: {diagnostics['hooks']}")
        if diagnostics["hooks"].get("config_ok") is not True:
            raise AssertionError(f"doctor did not validate official hook schema: {diagnostics['hooks']}")
        if (repo / ".shujuan").exists():
            raise AssertionError("install-layout doctor created .shujuan")


def main() -> int:
    _assert_agents_kernel()
    _assert_route_side_effects_and_intents()
    _assert_hooks_and_agents()
    _assert_install_surfaces()
    print(json.dumps({"ok": True, "v11_2_verification_repair": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
