# Delegation And Role Boundaries

The controller runs shujuan governance writes and closure. Workers, reviewers, researchers, writers, and provider tools return bounded material for controller adoption unless a controller packet grants more authority.

## Role Cards

- `controller_agent`: runs shujuan DB writes, scope changes, endpoint refresh, exec stop, evidence import, check/task closure, and final closeout claims.
- `worker_agent`: implements scoped code, docs, templates, or tests and returns changed files, tests, impact notes, and unresolved risks. Code-modifying worker subagents default to `gpt-5.4 medium` unless the user explicitly specifies another model.
- `reviewer_agent`: performs independent read-only review against source, diffs, tests, and packets, then returns findings, evidence sufficiency, and risk notes.
- `researcher_agent`: gathers source-backed facts, separates observation from inference, and returns material for controller adoption.
- `writer_agent`: drafts reports or prose. Default writer work is `writing_no_governance`.

## Delegate Route

```bash
python -m shujuan delegate packet --endpoint <endpoint> --task <task_id> --check <check_id> --role worker --body "<delegation body>"
python -m shujuan delegate review --endpoint <endpoint> --task <task_id> --check <check_id> --result accept --summary "<review summary>"
python -m shujuan delegate import --endpoint <endpoint> --task <task_id> --check <check_id> --import-kind summary --artifact <handoff.md>
python -m shujuan audit import-agent-output --endpoint <endpoint> --source-node <source_node_id> --path <handoff.md>
python -m shujuan review start --endpoint <endpoint>
```

`Delegate` is the default route for role-bounded handoff, review, and provider/impact output. A worker packet flows `Delegate` -> scoped `Execute`; provider output enters as bounded material; controller adoption flows returned material -> import -> independent verification -> `Close`. `provider_fact` and `provider_hypothesis` are not closure evidence by themselves. If provider output is gathered inside an active `Execute` task, keep it as execution input until the controller adopts it; the same adoption rule still applies. Delegate packets state role, scope, authority boundary, expected return fields, tests, changed files, impact expectations, worker model, and unresolved risks. Packets that permit code modification default the worker model to `gpt-5.4 medium` unless the user explicitly specifies another model, and include explicit `gitnexus-impact-analysis` expectations. Controller packets are the authority surface for governance write or closeout permission.

## Return Material

Use `templates/delegate-return.md` for worker handoff and `templates/reviewer-return.md` for read-only review. Returned packets say which checks appear materially satisfied and leave closure to controller adoption.
