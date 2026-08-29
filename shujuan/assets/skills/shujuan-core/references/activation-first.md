# Activation-First Entry

Activation-first means the agent starts from current center, endpoint, role, mode, and relation posture, then chooses one of five default routes before reaching for low-level primitives. `No Governance` is the explicit no-write mode/exit, not a sixth default route.

## Shared Route Grammar

Every route uses the same five slots: trigger, first surface, action chain, evidence/adoption rule, and handoff. This keeps route choice, command depth, and closeout authority at the same granularity across agents.

## Default Operating Core

- Identify center/endpoint, DCCP role, and governance mode before acting.
- Run the sovereignty gate before runtime work, then classify the relation as continuation, successor scope, independent review, revision/contradiction, fork variant, or independent root before choosing the route.
- Choose exactly one entry route first: `Recover`, `Recall`, `Execute`, `Close`, or `Delegate`; any transition to another route must be explicit.
- `Recover` and `Recall` read current surfaces for orientation or history; closure claims stay with `Close`.
- `Execute` starts from `workflow begin`, then `exec start`, scoped edits, verification, and controller `exec stop`.
- `Close` is the controller evidence route: match evidence, refresh endpoint, run `evidence verify`, then strict doctor.
- `Delegate` returns bounded material; workers, reviewers, researchers, writers, and provider tools enter this material/adoption lane before controller closure.
- Active obligations are open tasks/checks/findings/unresolved/decisions; deferred/backlog items are inactive until promoted.
- Contracted/dormant schema is not the default working surface; legacy writes are disabled diagnostics.
- Derived read/material surfaces guide decisions and become closure material only after controller evidence adoption.
- PostgreSQL is the runtime/write path; SQLite and contracted legacy tables are not write fallbacks.
- User or controller instructions that explicitly forbid shujuan switch the active mode/exit to `No Governance`; stop DB writes and return an ordinary answer or local-only material.
- Use advanced primitives through routed references after the selected route reveals the need.

## Entry Order

1. Read `AGENTS.md` and `.agents/skills/shujuan-core/SKILL.md`.
2. Identify the endpoint from the user prompt, current handoff, current alias, recent file context, or a lightweight project overview.
3. If the endpoint is named, start endpoint-specific:

```bash
python -m shujuan report endpoint <endpoint> --active-only --markdown
python -m shujuan endpoint doctor <endpoint> --strict-closeout --read-only --allow-fail
```

4. If no endpoint is named, use the lightest project discovery surface first; reserve the full project report for Recall questions that cross endpoints:

```bash
python -m shujuan report project --overview --markdown
```

5. Treat open tasks/checks, active audit findings, unresolved questions, and needs-user-decision items as the next obligations. Closed checks are evidence of prior scoped closure, not permission to claim the direction is complete.

The read-only strict doctor is diagnostic only and does not refresh the current endpoint body.

DB readiness gate: if the project database service is unavailable, run `python -m shujuan postgres-dev start`; continue only after `python -m shujuan postgres-dev status` reports ready.

Execute starts scoped work only after readiness and prompt capture:

```bash
python -m shujuan workflow begin --session-id <session_id> --endpoint "<endpoint>" --content "<current user request>"
python -m shujuan workflow begin --session-id <session_id> --endpoint "<endpoint>" --content-file prompt.txt
python -m shujuan exec start --endpoint <endpoint> --task-node <task_node_id> --summary "<summary>"
```

## Default Routes

- `Recover`: restore the active endpoint surface with endpoint active report plus strict doctor with `--read-only`.
- `Recall`: answer history, rationale, lineage, deferred/backlog/non-goal, and "why did this change" questions from project, endpoint, source, graph, and code-reason read surfaces.
- `Execute`: pass the DB readiness gate, then record the prompt with `workflow begin`, start scoped work with `exec start`, implement, verify, and stop with controller-owned `exec stop`.
- `Close`: gather `endpoint`, `task_id`, `check_id`, `expected_evidence_type`, and `current_matching_evidence_ref`; bare close requests return `missing_closeout_inputs`, then controller-only closeout runs refresh, verify, and strict doctor after inputs exist.
- `Delegate`: create or consume role-bounded packets and provider/impact output; worker flow is `Delegate` -> scoped `Execute`, provider output enters as bounded material, and controller adoption is import -> independent verification -> `Close`. `provider_fact` and `provider_hypothesis` are not closure evidence by themselves. If provider output is gathered inside an active `Execute`, the same adoption rule still applies. Delegate/import closeout flags are controller evidence-adoption controls.

Recover-route commands report current facts and keep recovery diagnostic. Missing layout or schema metadata should produce diagnostics with explicit repair guidance.

## Recall Route

Use Recall when the user asks to reconstruct history, compare versions, explain why a rule exists, inspect deferred/backlog/non-goal decisions, or recover the lineage behind a current endpoint. Start from the named endpoint when possible; use project-wide history when the question crosses endpoints:

```bash
python -m shujuan report endpoint <endpoint> --active-only --markdown
python -m shujuan report endpoint <endpoint> --full --markdown
python -m shujuan endpoint brief <endpoint> --role <role> --mode <mode> --markdown
python -m shujuan report project --markdown
```

For source and code rationale, add only the read-only surfaces needed:

```bash
python -m shujuan graph candidates --from-document <document_id>
python -m shujuan graph detail --node <node_id>
python -m shujuan why --path <path>
python -m shujuan why --symbol <symbol>
```

Recall preserves the active-only tradeoff: active reports show current obligations, while `--full`, `detail_ref`, imported documents, terms, graph/detail views, `why`, DB reads, and text search supply resolved history. Distinguish observed facts, source-backed claims, and inference. Recall material becomes closure evidence only when a controller later records and verifies it through the normal evidence path.

### Recall Checklist

1. Start with the named endpoint when available; use `python -m shujuan report project --overview --markdown` to find an endpoint and `python -m shujuan report project --markdown` when the question may cross endpoints.
2. Load `python -m shujuan report endpoint <endpoint> --active-only --markdown` to separate current obligations from historical material.
3. Use `python -m shujuan report endpoint <endpoint> --full --markdown` when closed, resolved, deferred, backlog, non-goal, evidence, or discussion history is needed.
4. Use `python -m shujuan endpoint brief <endpoint> --role <role> --mode <mode> --markdown` for role-aware handoff context without creating a run.
5. Use source documents, `detail_ref`, `python -m shujuan graph detail --node <node_id>`, and text search to cite where the remembered claim came from.
6. Use `python -m shujuan why --path <path>` or `python -m shujuan why --symbol <symbol>` for code rationale.
7. Label observed facts, source-backed claims, and inference separately.
8. Keep Recall in the history lane: execution, endpoint refresh, check/task closure, governance fact writes, and lifecycle changes belong to Execute/Close.

## Advanced Fallback

Use graph extraction, task/check primitives, assumptions, unresolved questions, defers, or scope changes only when a default route reveals they are needed. Clarification notes stay lifecycle-neutral; `scope change --task` is defer-like and needs explicit state-change acknowledgement. When many tasks/checks/edges must be created, use the formal task-chain importer instead of wrapper subprocess loops.

```bash
python -m shujuan task add --body "<task body>" --from-node <source_node_id>
python -m shujuan acceptance add --task <task_id> --body "<check body>" --expected-evidence-type <change_set|test_result|artifact|user_confirmation> --from-node <source_node_id>
python -m shujuan scope change --body "<why scope changed>" --source-node <source_node_id> --applies-to <target_node_id>
python -m shujuan task defer --task <task_id> --body "<why deferred>" --source-node <source_node_id>
python -m shujuan unresolved add --body "<question>" --source-node <source_node_id> --applies-to <target_node_id>
python -m shujuan assumption add --body "<assumption>" --source-node <source_node_id> --applies-to <target_node_id>
```

Continue a direction from a new request, open scoped work, unresolved blockers, active audit findings, defer decisions being promoted, or a new scope contract.
