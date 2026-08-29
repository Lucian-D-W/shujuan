# Evidence Closeout

Closure starts with `endpoint`, `task_id`, `check_id`, `expected_evidence_type`, and `current_matching_evidence_ref`. A bare "tests passed, close it" request is handled by producing `missing_closeout_inputs` and gathering those inputs before closure. Reviewer output supplies sufficiency/risk/missing-input notes; the controller executes the closeout chain.

## Evidence Commands

```bash
python -m shujuan evidence test-result --check <check_id> --close-check -- <test command>
python -m shujuan evidence artifact --path <file> --check <check_id> --close-check
python -m shujuan evidence user-confirmation --body "<confirmation>" --check <check_id> --close-check
```

`change_set` evidence is normally captured through controller-owned execution stop:

```bash
python -m shujuan exec stop --endpoint <endpoint> --summary "<summary>" --task <task_id> --check <check_id>
```

Use `--close-check` when the captured change set is the intended evidence for that check. Use `--close-task` after every acceptance check for the task is already closed or is being closed by the same evidence.

## Close Route

```bash
python -m shujuan endpoint refresh <endpoint>
python -m shujuan evidence verify --endpoint <endpoint>
python -m shujuan endpoint doctor <endpoint> --strict-closeout --allow-fail
python -m shujuan report project --markdown
```

`Close` is the writeful controller path. `endpoint refresh` generates the current endpoint body from DB-backed facts; `endpoint doctor --strict-closeout` without `--read-only` may refresh projection before diagnosing. Strict doctor is the endpoint readiness gate after matching evidence exists.

`evidence verify` is an evidence-layer verifier. Its machine output should make this explicit with fields such as `layer: evidence`, `closeout_gate: false`, and the next strict-doctor command; if it auto-invalidates evidence or clears closures, the top-level result is recomputed from the post-invalidation state.

For `Recover` entry or blocker inspection, run `endpoint doctor --strict-closeout --read-only --allow-fail`; that strict doctor is diagnostic only and does not refresh the current endpoint body.

Evidence type and predicate coverage overrides use the shared lightweight interpretation. Matching override warnings/props/edges are the explicit exception record; a resolved warning remains effective when its reason clearly accepts risk within scope. Superseded, replaced, revoked, or unclear reasons keep the gate red.

## Material Boundaries

- Reviewer output supplies sufficiency, risk, and missing-input notes for controller adoption.
- Delegate output enters the graph through `audit import-agent-output` or as an evidence artifact.
- codegraph/GitNexus/provider reports enter as `provider_fact` or `provider_hypothesis`; controller adoption maps them to evidence only through normal checks.
- A failed `evidence test-result` is recorded as evidence history and leaves the check/task open.
