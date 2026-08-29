# shujuan

**shujuan is a local lab notebook and safety rail for AI coding agents.**

An AI agent can change a lot of code very quickly. That is useful, but it also makes simple questions surprisingly hard to answer:

- What did the person actually ask for?
- Why did the agent make this change?
- What still needs to be done?
- Which test proves that the work is finished?

shujuan keeps those answers connected. It records the request, turns it into tasks and checks, links code changes to evidence, and refuses to call work complete without proof.

> Think of it like a lab notebook for a science project: every goal, experiment, result, and conclusion has a place. The notebook does not write the project for you; it makes the work understandable and checkable.

## The idea in 30 seconds

Without shujuan, an agent conversation can become a pile of messages and code changes. With shujuan, the work follows a small loop:

1. **Capture** — save what the user asked for and where it came from.
2. **Scope** — turn the request into a clear endpoint, tasks, and acceptance checks.
3. **Work** — record the implementation run and the files it changed.
4. **Verify** — attach tests, artifacts, or user confirmation as evidence.
5. **Close** — finish only when the evidence matches the promise.

An **endpoint** is simply a reliable “save point” for one workstream. If an agent stops today, another agent should be able to read the endpoint tomorrow and know what happened, what is open, and what to do next.

## What shujuan is — and is not

shujuan is:

- a Python command-line tool for governed, traceable agent work;
- local-first: project runtime data stays inside the ignored `.shujuan/` directory;
- evidence-based: passing tests or other matching proof are linked to the work;
- designed to make interrupted or multi-agent work recoverable.

shujuan is not:

- an AI model or coding agent;
- a replacement for Git, tests, or human judgment;
- a cloud service that uploads your private runtime data;
- a finished product platform yet. This repository is a runnable early-stage skeleton.

## Try it in about five minutes

You need Python 3.10 or newer and a local PostgreSQL installation. The included development helper creates a project-owned database cluster instead of using another application's database.

```powershell
python -m pip install -e .
python -m shujuan init --postgres-dev --name my-project
python -m shujuan postgres-dev status
```

Start a small workstream and inspect its save point:

```powershell
python -m shujuan workflow begin --endpoint first-demo --content "Explain this project to a new contributor"
python -m shujuan report endpoint first-demo --active-only --markdown
```

The first command records the request and begins the workflow. The second prints the current endpoint: its goal, open work, checks, evidence, and next safe action.

If PostgreSQL is not installed or cannot start, shujuan stops with a clear error. It does not silently switch to a different database.

## Pick the method that matches the question

| Method | Plain-language question |
| --- | --- |
| `Harness` | “What kind of work is this, and where should I start?” |
| `Recall` | “What happened before, and why?” |
| `Capture` | “How do I save this source or conversation without claiming it is a task?” |
| `Execute` | “How do I implement this scoped change?” |
| `Delegate` | “How do I hand bounded work to another role?” |
| `Close` | “Is there enough current evidence to call this finished?” |
| `Evolve` | “How do I change shujuan's own rules or structure?” |

These names appear in `AGENTS.md` and the method Skills under `.agents/skills/`. You do not need to memorize them before trying the quickstart.

## Where things live

```text
shujuan/       Python package and command-line tool
tests/         repeatable checks
migrations/    PostgreSQL schema changes
scripts/       maintenance and privacy tools
docs/          architecture and deeper guides
.agents/       the seven work-method Skills
.codex/        project-owned agent roles and hooks
.shujuan/      private local runtime data (ignored by Git)
```

Before publishing, scan the exact Git history that would become public. The audit reports only the location and rule name; it does not print a matched secret:

```powershell
# Before the first remote is added:
python scripts/audit_public_repository.py --repo . --ref main --require-no-remotes --require-clean

# After a trusted remote already exists:
python scripts/audit_public_repository.py --repo . --ref main --require-clean
```

## Project status

This is an early, runnable foundation for people exploring reliable AI-agent workflows. The core request → task → change → evidence → endpoint chain works, but packaging polish, broader integrations, and a full product experience are still future work.

If you are new, start with the quickstart above. If you are maintaining or extending shujuan, continue with the precise technical reference below and [the documentation map](docs/README.md).

---

The rest of this README is the detailed technical reference. Exact terms matter here because the CLI, tests, and agent rules use them.

## Current Stage Boundary

This repository is currently proving a runnable skeleton with a usable core evidence chain. It is not trying to become a product-grade platform yet.

The current stage is limited to the smallest shujuan loop that can change Agent execution behavior with a few scripts and SOPs:

- repo-scoped project-owned PostgreSQL database
- compact canonical repo policy in `AGENTS.md`, seven v11 method Skills under `.agents/skills/shujuan-*`, project role profiles under `.codex/agents/`, and `shujuan-core` retained only as a v10 compatibility shim
- script capture for prompts, transcripts, snapshots, diffs, hashes, files, and run events
- document/session import plus semi-automatic semantic extraction with source evidence
- task graph and acceptance checks
- evidence-based task/check closure through diff, test result, artifact, and user confirmation nodes
- `scope_change`, `defer`, `assumption`, and `unresolved` records with source evidence
- mandatory endpoint closeout on `exec stop`

Product-grade work is backlog rather than current-stage scope: schema migration runner, multi-agent adapters, context ranking evaluation sets, provider matrices, broader parser coverage, packaging polish, and a full test matrix. Those items become blockers only after explicit promotion into scope.

## Repository Layout

The public tree is intentionally small and source-first:

- `shujuan/` is the only Python package source tree; generated `build/` and `*.egg-info/` directories are never tracked.
- `tests/`, `migrations/`, and `scripts/` contain repeatable verification, schema changes, and maintained operator tooling.
- `.agents/` and `.codex/` contain project-owned method, role, and hook surfaces.
- `docs/` contains the architecture, maintained guides, a small frozen reference set, and current source-backed plans. See `docs/README.md`.
- `.gitnexus/` is the ignored local GitNexus index. GitNexus skills are installed globally rather than copied into the repository.

Private runtime state belongs under the ignored `.shujuan/` directory. This includes the PostgreSQL cluster, credentials, evidence artifacts, patches, exports, and traces. Raw database dumps, generated releases, temporary prompts, reviewer packets, and provider caches do not belong in public Git history.

Before accepting the first remote, audit exactly the objects reachable from `main`. Findings contain rule/path/line metadata only; matched values are never printed:

```powershell
python scripts/audit_public_repository.py --repo . --ref main --require-no-remotes --require-clean
```

Once a trusted remote is configured, omit `--require-no-remotes` while keeping `--require-clean`.

## Current Terms

These terms are behavioral rules for Agents and CLI checks, not loose glossary prose:

- `endpoint`: a DB-backed recoverable breakpoint for a workstream; it records scope, tasks, checks, evidence, active blockers, and next valid entry.
- `closed`: a task or acceptance check state reached only by accepted closure evidence; it means the scoped commitment was satisfied, not that the direction is finished forever.
- `resolved`: a semantic lifecycle state for an item answered or consumed by later source/evidence; resolved items remain historical.
- `active`: the current workbench state for open mandatory tasks/checks, unresolved questions, actionable audit findings, or needs-user-decision items that still require attention.
- `deferred`: a source-backed decision to pause scoped work outside the active workbench; deferred items remain traceable but are not active obligations.
- `product_backlog`: a valid non-active state for future product-grade work; it blocks current closeout only after explicit promotion into scope.
- `audit_finding`: an actionable issue discovered by review/audit; it is active only while its lifecycle state is active and should be resolved/deferred/backlogged when consumed.
- `evidence`: a traceable node such as `test_result`, `artifact`, `change_set`, or `user_confirmation` that can support closure only when it matches the check contract and remains current/valid.
- `provider_fact` / `provider_hypothesis`: data or hypotheses imported from an optional provider or graph tool such as GitNexus; they carry provenance and confidence for the material/adoption lane and are not closure evidence by themselves.
- `PostgreSQL success`: a real project-owned PostgreSQL runtime chain: prompt/session/run/evidence/endpoint/report operations, migrations, constraints, persistence/restart behavior, backup/restore projection consistency, no L-brain writes, and no SQLite runtime/write fallback.

## Self-Use Rule

shujuan should be used to govern shujuan's own development, but self-use is a pressure test rather than the only source of truth. When changing shujuan's CLI, schema, or evidence rules, a run may start under old behavior and stop under new behavior. Record those conflicts as `assumption`, `unresolved`, `scope_change`, or `defer` when they affect the work.

Self-use completion still needs external repeatable evidence. For the current stage, `python tests\smoke_shujuan.py` is the primary smoke evidence before closing self-use acceptance checks/tasks.

## Thin Interface Foundation

These interfaces are intentionally thin. They lock maintenance seams without turning the current stage into a product-grade platform.

Migrations use tracked repo-local sequential SQL files. Migration history lives under `migrations/shujuan/*.sql`; `.shujuan/migrations/` is a legacy runtime/local directory:

```powershell
python -m shujuan migrate status
python -m shujuan migrate apply
```

`init` creates `.shujuan/schema_version.json` and default `AGENTS.md` shujuan instructions when they are missing. If an `AGENTS.md` already exists without shujuan guidance, init appends a small instruction block; use `--no-agents-md` only when an outer project policy owns agent instructions. `migrate status` opens the database without runtime schema gating so it can diagnose version mismatch; `migrate apply` runs pending tracked `.sql` files in filename order, records them in `applied_migrations`, and updates schema metadata only after successful application.

Destructive contraction migrations are guarded in code before SQL execution. If a pending contraction migration such as `004_p2_physical_schema_contraction.sql` would drop a contracted legacy table that still has rows, `migrate apply` and `migrate apply --dry-run` return a blocking diagnostic with the table names, row counts, replacement paths, and manual review guidance. The normal migration path does not silently archive, move, or drop non-empty contracted tables.

Database configuration is PostgreSQL-only. `--database-url` and `SHUJUAN_DATABASE_URL` must use a `postgresql://` or `postgres://` URL. `--db-profile sqlite`, `SHUJUAN_DB_PROFILE=sqlite`, and `sqlite:///` URLs fail closed. Product mode uses the project-owned `postgres-dev` config when present, otherwise it requires an explicit PostgreSQL URL. PostgreSQL execution uses `psycopg` and the same thin DB API across the CLI:

```powershell
$env:SHUJUAN_DATABASE_URL = "postgresql://user:password@localhost:5432/shujuan"
python -m shujuan init
python -m shujuan migrate status
python -m shujuan workflow begin --endpoint shujuan-initial-skeleton --content "User request text"
```

The current PostgreSQL backend supports the core table bootstrap plus runtime `execute`/`fetchone`/`fetchall` paths used by init, migrations, workflow begin, task/check/evidence commands, endpoint status/doctor, and project reports. It intentionally stays thin, but it no longer falls back to SQLite. Missing PostgreSQL configuration, missing drivers, and server connection errors fail loudly.

For project-owned PostgreSQL initialization without touching any external or memory-system database, use the native Windows dev cluster path:

```powershell
python -m shujuan init --postgres-dev --name my-project
python -m shujuan postgres-dev status
python -m shujuan migrate status
python -m shujuan postgres-dev stop
```

`init --postgres-dev` creates or reuses `.shujuan/postgres-dev/`, derives a stable database name from the repo path, starts the local cluster, initializes the shujuan schema into PostgreSQL, and writes local config/credentials so later shujuan commands bind to the same project database without repeating `SHUJUAN_DATABASE_URL`. `postgres-dev` discovers native PostgreSQL binaries from `SHUJUAN_POSTGRES_BIN`, `PATH`, or common Windows install paths such as `C:\Program Files\PostgreSQL\17\bin`. Its cluster lives under `.shujuan/postgres-dev/data`, logs under `.shujuan/postgres-dev/logs`, and credentials under `.shujuan/postgres-dev/credentials.json`; `.shujuan/` is ignored and stays local. This is a local-dev helper only. It binds to `127.0.0.1`, uses `scram-sha-256` for TCP host auth, and prints the full secret-bearing URL only from `postgres-dev url` because that command is meant to populate `SHUJUAN_DATABASE_URL`. Use the project-owned database as shujuan's write target.

The legacy SQLite-to-PostgreSQL cutover command is historical. Recreate current project state from project-owned PostgreSQL, PostgreSQL backups, or captured shujuan evidence artifacts.

For manual control you can still run:

```powershell
python -m shujuan postgres-dev init
python -m shujuan postgres-dev start
python -m shujuan postgres-dev url --env
```

Manual adapters expose a standard event model before wider agent-specific adapters exist:

```powershell
python -m shujuan adapter manual events --transcript transcript.txt --session-id session_demo
python -m shujuan adapter manual import --transcript transcript.txt --session-id session_demo
```

The manual adapter maps transcript turns into `user_prompt`, `assistant_message`, `tool_event`, or `system_message` standard events, stores them in `standard_events`, and imports message-bearing events into `messages`.

Provider integration is a material/adoption contract, not a matrix. The default Shujuan route for provider output is the `Delegate` material/adoption lane; provider commands are support surfaces inside that lane or inside an active `Execute` task, and the same controller adoption rule still applies. Provider artifacts and facts become closeout material only after controller adoption and matching `Close` evidence handling.

```powershell
python -m shujuan provider contract
```

For agent-operated impact work, use the globally installed `gitnexus-*` skills. The optional CLI path invokes GitNexus directly; provider absence, a missing index, or execution failure is recorded while shujuan's own diff/task/evidence loop continues.

## Quickstart

Run from the repository root:

```powershell
python -m shujuan init --postgres-dev --name my-project
python -m shujuan postgres-dev status
python -m shujuan workflow begin --session-id session_demo --endpoint shujuan-initial-skeleton --content "User request text"
python -m shujuan report endpoint shujuan-initial-skeleton --active-only --markdown
python -m shujuan endpoint doctor shujuan-initial-skeleton --strict-closeout --read-only --allow-fail
```

The default entry is activation-first: treat `AGENTS.md` as the canonical repo policy. Identify center/endpoint from evidence, name the DCCP role and governance mode, then choose exactly one primary method: `Harness`, `Recall`, `Capture`, `Execute`, `Delegate`, `Close`, or `Evolve`. Load the matching `.agents/skills/shujuan-*/SKILL.md` workflow only when the task needs it. `shujuan-core` remains available as a v10 compatibility shim for explicit legacy recovery, not as the ordinary v11 router. If an endpoint is named, start endpoint-specific. If no endpoint is named, use a lightweight project overview to choose one; reserve the full project report for `Recall` questions that cross endpoints:

```powershell
python -m shujuan report project --overview --markdown
```

An endpoint is a direction-level recoverable cognitive breakpoint. `Harness` or `Recall` can use `report endpoint <name> --active-only --markdown` to surface active obligations and `endpoint doctor <name> --strict-closeout --read-only --allow-fail` as diagnostic orientation. `endpoint status <name>` reads database facts for the endpoint description, root node, scope contract, tasks, open/closed acceptance checks, evidence, audit findings, unresolved questions, scope changes, defer decisions, and assumptions. `endpoint refresh <name>` writes that generated view back as the current endpoint body. `Close` is the writeful controller route: controller closeout may run `endpoint doctor <name> --strict-closeout` without `--read-only`, and that strict closeout path refreshes projection before diagnosing. Completion is inferred from evidence-backed checks/tasks. A closed task/check means the last scoped commitment is complete; new requests, active findings, unresolved questions, and new scope can still continue the direction.

DB readiness gate: if the project database service is unavailable, run `python -m shujuan postgres-dev start`; continue only after `python -m shujuan postgres-dev status` reports ready. Then continue DB-backed reports, `workflow begin`, execution capture, endpoint refresh/doctor, evidence commands, and delegate/audit imports.

`Recall` is the read-only route for historical review rather than execution. Use it when the question is about lineage, rationale, cross-version changes, deferred/backlog/non-goal decisions, or why a rule/code path exists. Start from the named endpoint when possible; use project-wide history when the question crosses endpoints:

```powershell
python -m shujuan report endpoint <endpoint> --active-only --markdown
python -m shujuan report endpoint <endpoint> --full --markdown
python -m shujuan endpoint brief <endpoint> --role <role> --mode <mode> --markdown
python -m shujuan graph detail --node <node_id>
python -m shujuan why --path <path>
python -m shujuan report project --markdown
```

Recall preserves the active-only tradeoff: active reports show current obligations, while `--full`, `detail_ref`, imported documents, terms, graph/detail views, `why`, read-only DB queries, and text search supply resolved history. A Recall answer separates observed facts from inference and hands implementation or closure to `Execute` or `Close`.

Plan-to-DB conversion has a lightweight non-compression gate. Source-plan deliverables must remain individually visible with classification, graph destination, rationale, and promotion/reopen rules; absorbed, superseded, and indirectly dissolved items keep the consuming destination/rationale instead of disappearing under an umbrella. `python -m shujuan plan-to-db verify-artifact --artifact <json>` checks decomposition artifacts for compressed deliverables, artifact-only active slices, unsafe broad-parent promotion, unlinked inactive items, and false closeout claims. `python -m shujuan plan-to-db lifecycle-reconcile --endpoint <name> --allow-fail` dry-runs lifecycle residual reconciliation for already graph-backed `RESOLVES`/`SUPERSEDES` edges; controller `--apply` updates semantic lifecycle state without closing tasks/checks.

Capture conversation evidence from hooks or transcripts:

```powershell
python -m shujuan workflow begin --session-id session_demo --endpoint shujuan-initial-skeleton --content "User request text"
python -m shujuan hook user-prompt --session-id session_demo --content-file prompt.txt
python -m shujuan hook stop --session-id session_demo --content "Final agent response"
python -m shujuan session import --transcript transcript.jsonl
```

For long Chinese prompts, nested quotes, or delegation packets, prefer stable file or stdin input instead of dense shell quoting:

```powershell
python -m shujuan workflow begin --session-id session_demo --endpoint shujuan-initial-skeleton --content-file prompt.txt
python -m shujuan scope change --source-node <source_node_id> --applies-to <target_node_id> --body-file scope-note.txt
python -m shujuan delegate packet --role worker --endpoint shujuan-initial-skeleton --body-file worker-packet.txt
```

`workflow begin` is the preferred first command for self-use when no prompt has already been captured: it records the current user prompt and loads context in one step. Do not run both `workflow begin` and `hook user-prompt` for the same user prompt unless you intentionally want two capture events. Use `hook user-prompt --content-file` when an outer runner has already chosen the session/run lifecycle and only stable prompt capture is missing; use plain `context load` when prompt capture is already done. `session import` accepts JSONL records such as `{"actor":"user","content":"..."}` or a simple text transcript with `User:`, `Assistant:`, `Tool:`, and `System:` prefixes. Hook and transcript message imports are minimally idempotent by `session_id + actor + content_hash`.

Conversation can be source evidence. If a discussion changes design intent, execution priority, scope, acceptance criteria, or Agent operating rules, record it through `workflow begin`, `hook user-prompt`, `session import`, or `audit record` before relying on it for implementation. Important audit/research summaries should become DB artifacts or audit findings and refresh the relevant endpoint.

Maintain the active center body as versioned database state:

```powershell
python -m shujuan center update --body "Updated long-term project boundary." --from-node <source_node_id>
python -m shujuan center show --all
python -m shujuan export center
python -m shujuan export glossary
```

Capture an execution run and its diff evidence:

```powershell
python -m shujuan exec start --summary "Start implementation run"
# edit tracked files or create new untracked files
python -m shujuan exec stop --endpoint shujuan-initial-skeleton --summary "Captured implementation diff"
python -m shujuan why --path shujuan/cli.py
python -m shujuan why --symbol shujuan.cli.capture_change_set
```

To connect a captured change set to task and acceptance evidence:

```powershell
python -m shujuan exec stop --endpoint shujuan-initial-skeleton --summary "Captured implementation diff" --task <task_id> --check <check_id>
python -m shujuan diff capture --run <run_id> --task-node <task_node_id> --check <check_id>
```

`exec stop` requires `--endpoint`. It writes an endpoint closeout body every time, derived from the captured change set and stop check. If `--endpoint-body` is provided, shujuan appends the script-generated stop check to that semantic closeout. Open mandatory tasks and open acceptance checks are reported in `stop_check` and in the endpoint body; they do not become a status field. Use `--close-check` only when the change set itself is the intended evidence for that check. Use `--close-task` only after every acceptance check for that task is closed or is being closed by the same evidence.

Diff capture records the delta between the run's `before` and `after` snapshots, so dirty worktree changes that already existed before `exec start` do not pollute the run's `change_set`. Snapshots store text patch refs plus file-state refs with path/hash evidence. Ignored files and local runtime/provider graph assets such as `.shujuan/`, `.codegraph/`, `.gitnexus/`, `.ai/codegraph/`, and `__pycache__/` are skipped. Text files get diff hunks; binary files are recorded as `diff_files` and file-level `code_objects` without text hunks. Python `.py` files are also parsed with the stdlib AST parser for function, async function, and class symbols. Overlapping diff hunks are linked to those symbol `code_objects` while the file-level link is preserved, so `why --symbol package.module.Name` can trace a symbol back to its recent change set. `change_sets.patch_hash` fingerprints the whole snapshot-delta evidence package, including binary path/hash evidence, while `change_sets.metadata.text_patch_hash` records the text patch hash separately.

`context load` returns the raw center/endpoint/term/task/check/semantic sections plus a deterministic `ranked_context` list. Ranking is intentionally lightweight: task keywords, the selected endpoint, active terms, open tasks/checks, semantic nodes, and known code objects are scored by text overlap. The activated ranked node ids are saved in `activation_logs.loaded_node_ids`; no agent-maintained status field is introduced.

The CLI also exposes task/evidence primitives:

```powershell
python -m shujuan scope create --body "Implement the first shujuan skeleton without downgrading the blueprint." --source-node <source_node_id>
python -m shujuan task add --body "Create the repo-local CLI and PostgreSQL schema." --from-node <source_node_id>
python -m shujuan acceptance add --task <task_id> --body "init, doc import, exec start/stop all run successfully" --expected-evidence-type test --from-node <source_node_id>
```

Evidence nodes are first-class graph nodes. Closeout starts from `endpoint`, `task_id`, `check_id`, `expected_evidence_type`, and `current_matching_evidence_ref`; a bare "tests passed, close it" request returns `missing_closeout_inputs` before closure. Reviewer output supplies sufficiency/risk/missing-input notes, and the controller executes the closeout chain.

```powershell
python -m shujuan evidence test-result --check <check_id> --close-check -- python -m pytest
python -m shujuan evidence artifact --path .shujuan/exports/center.md --validates-node <node_id>
python -m shujuan evidence user-confirmation --body "User confirmed this behavior." --from-node <message_node_id> --check <check_id> --close-check
```

`evidence test-result` always records the test result node and captured stdout/stderr refs. If the test command exits nonzero, requested check/task closing is skipped and reported in JSON as `close_skipped`; failed tests cannot close acceptance checks or tasks through any closure path.

Audit and research summaries can be recorded as first-class artifacts plus structured findings:

```powershell
python -m shujuan audit record --endpoint <endpoint> --source-node <source_node_id> --path six_way_audit.md --task <task_id> --check <check_id> --finding "P0 failed tests must not close checks" --refresh-endpoint
```

The command captures the markdown/text file under `.shujuan/artifacts/`, creates an `artifact` node with a unique `capture_ref`, stores byte-integrity and normalized-text hashes separately, creates `audit_finding` nodes, links them to the endpoint and source node, and can refresh the endpoint workbench body.

Scope changes, deferrals, assumptions, and unresolved questions also stay in the graph with source evidence:

```powershell
python -m shujuan scope change --body "Scope boundary changed after source review." --source-node <source_node_id> --applies-to <target_node_id>
python -m shujuan task defer --task <task_id> --body "Blocked by missing API decision." --source-node <source_node_id>
python -m shujuan assumption add --body "Assume project-owned PostgreSQL for the local store." --source-node <source_node_id> --applies-to <task_node_id>
python -m shujuan unresolved add --body "Need user confirmation on retention policy." --source-node <source_node_id>
```

Use `scope change --applies-to` for non-state-changing scope notes. `scope change --task` is state-changing/defer-like because it adds `DEFERRED_BY` task edges and endpoint reports treat those task targets as deferred/non-active; prefer `task defer --task` for ordinary deferral decisions.

These commands do not close tasks by themselves. Completion is still expressed only by evidence nodes closing acceptance checks and tasks.

Inspect graph evidence and create explicit manual semantic nodes from transcript messages:

```powershell
python -m shujuan graph candidates --from-document <document_id> --type acceptance_check
python -m shujuan graph candidates --from-session <session_id>
python -m shujuan graph extract --from-session <session_id>
python -m shujuan graph extract --from-session <session_id> --from-message <message_id> --type requirement --label "Requirement label" --summary "Manual extraction only"
python -m shujuan graph extract --from-section <section_id> --type acceptance_check --label "Acceptance label" --summary "Manual extraction only" --task <task_id>
python -m shujuan graph show --node <node_id>
python -m shujuan graph edges --from-node <node_id>
```

`graph candidates` is a deterministic hinting pass over messages or document sections. It does not create nodes. `graph extract` creates nodes only after an explicit `--type` and `--label`, and every extracted node gets a `DERIVED_FROM` edge to the selected message or document section. When extracting `scope_contract`, `task`, or `acceptance_check`, pass the relevant structured options so the node also becomes a real contract/task/check row:

```powershell
python -m shujuan graph extract --from-section <section_id> --type scope_contract --label "Scope" --summary "Contract body"
python -m shujuan graph extract --from-section <section_id> --type task --label "Task" --summary "Task body" --contract <contract_id>
python -m shujuan graph extract --from-section <section_id> --type acceptance_check --label "Check" --summary "Check body" --task <task_id> --expected-evidence-type test
```

Task and acceptance commands require `--from-node` so confirmed task graph rows stay tied to source evidence. `graph extract --type acceptance_check` requires `--task`; orphan acceptance checks are rejected. Manual `acceptance close` refuses non-evidence nodes; checks and tasks close only through `change_set`, `test_result`, `artifact`, or `user_confirmation` evidence.

## GitNexus Provider

shujuan is the primary system. For code impact analysis, dependency lookup, change-scope assessment, or delegated implementation impact requirements, use the globally installed `gitnexus-*` skills or direct GitNexus CLI. Provider output remains material only (`provider_fact` or `provider_hypothesis`) until controller import, independent verification, and `Close` evidence-node handling.

Refresh the ignored local index without generating repository instructions or skill copies:

```powershell
gitnexus analyze --index-only .
```

When `diff capture` or `exec stop --impact` explicitly requests provider execution, shujuan runs `gitnexus detect-changes --scope all --repo .` with a bounded timeout. Impact metadata records `default_source`, `entrypoint_used`, `provider_detail`, and `closure_evidence_boundary`. Executed output is represented as traceable `artifact` and `provider_fact` material linked to the `change_set`. Missing CLI/index, skipped execution, or failure keeps `reports: []` where appropriate and never blocks shujuan's own diff/task/evidence loop. `.gitnexus/` and any other provider indexes remain ignored local analysis assets and never enter Git history.

`Delegate` is the default route for role-bounded implementation, review, research, and writing. A worker packet flows `Delegate` -> scoped `Execute`; controller adoption flows returned material -> import -> independent verification -> `Close`. Record the delegation as a source-backed handoff before spawning. If the packet permits code modification, include explicit `gitnexus-impact-analysis` expectations in that packet. Require the subagent to return changed files, tests/commands, impact/provider output paths when used, and unresolved risks. Import that final report with `audit import-agent-output` or `evidence artifact`; capture the local change set with `exec stop`; run independent verification; then close checks/tasks only with the resulting evidence nodes. A subagent prose handoff is useful context, not completion by itself.

Contracted legacy write commands are diagnostics, not successful governance writes. If a command such as `work intake`, `work split`, or contracted `review submit` would depend on removed legacy tables, it returns `ok: false`, `diagnostic_only: true`, `db_writes: 0`, and `closure_claim: false` with the replacement path. Read/material surfaces such as `work focus`, `review start`, and `delegate packet` label derived or artifact-primary material explicitly and cannot close tasks/checks without controller evidence adoption.

## Smoke Test

```powershell
python tests/governance_invariants.py
python tests/smoke_shujuan.py
```

## Release Smoke Layers

Use the lightest layer that matches the package being checked:

- Static runtime smoke: `python -m shujuan --help`, `python -m shujuan schema verify`, `python tests/compatibility_export_manifest.py`, and `python tests/schema_stewardship_p0.py`. PostgreSQL-dependent sections self-skip when native PostgreSQL binaries are missing.
- PostgreSQL smoke: `python tests/packaging_install.py` and the PostgreSQL sections of schema stewardship tests. These require native PostgreSQL binaries and use `init --postgres-dev`; no-DB `init --name` is not a success path.
- Full repo tests: docs/history-dependent checks such as `tests/sqlite_legacy_residual_classification.py` with repository docs present, plus broader smoke/governance suites. Runtime packages that intentionally omit docs should skip docs-only assertions rather than fail the runtime smoke.

`governance_invariants.py` locks the current-stage discipline: migrations apply in order and reject checksum mismatch, manual adapters persist standard events, `workflow begin` records prompt plus context, provider contract remains optional and diagnostic, document sections remain traceable, source evidence is required for scope/task/check creation, orphan extracted acceptance checks are rejected, failed test results do not close checks, tasks cannot close while checks remain open, and `exec stop` reports open acceptance work.

The smoke test creates a temporary Git repository, initializes shujuan, imports a plan document and transcript, captures hook messages, performs candidate/manual graph extraction, records test/artifact/user-confirmation evidence, records audit findings, refreshes an endpoint workbench, records scope-change/defer/assumption/unresolved nodes, updates/exports center and glossary state, starts a run, edits a tracked Python function, creates an untracked Python module, stops the run with task/check evidence links, and verifies that document sections, snapshots, change sets, diff files, diff hunks, file and symbol code objects, graph edges, ranked context, endpoint facts, audit artifacts, and closed acceptance evidence were saved without capturing ignored/runtime files. It also covers unique capture refs, `why --symbol`, dirty-worktree baseline exclusion, deleted-file hunk attribution, rename capture, binary untracked fingerprint evidence, and duplicate message import idempotence.

## License

shujuan is available under the [Apache License 2.0](LICENSE).
