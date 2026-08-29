# shujuan Repository Instructions

<!-- shujuan-agent-instructions:v11.3 -->

Use this file as the always-on shujuan policy surface. It chooses the positive route first, then applies only the boundary checks needed by that route.

## First Route

1. State the user delivery target in one line.
2. Bind the center/endpoint only from evidence; otherwise stay read-only and suggest the first surface.
3. Name DCCP role and governance mode.
4. Choose exactly one primary method: `Harness`, `Recall`, `Capture`, `Execute`, `Delegate`, `Close`, or `Evolve`.
5. Use `route guard --pure` for read-only route checks, then load the matching method Skill only when the task needs its workflow.

## Intent Priority

- Explicit sovereignty exits win first: no shujuan, no governance, no record, no capture, and do not save mean `No Governance`.
- Close/accept/sign-off wording routes to `Close` only when it is not negated, and missing closeout inputs must fail closed without writes.
- Implementation verbs dominate incidental history, lineage, why, or recall wording; mark auxiliary Recall and read the Recall surface first before scoped Execute work.
- Capture applies only to source/provenance/traceability/context material and must not override private, off-the-books, no-log, or no-record wording.
- Independent review and reviewer-packet requests stay `Delegate` material until controller adoption.

## Topology Operating Card

- User is first judge; a shujuan agent is second-judgment messenger for recoverable, verifiable, auditable, delegable structure.
- Before task/check/endpoint writes, classify relation: independent, continuation, successor, revision/supersession, fork, or material-only.
- Prompts with previous/recent/刚才/前一个小时/旧任务/重建/取代/不对/继承/lineage/相关 must bind a predecessor or stay read-only.
- Multiple deliverables need a Task Relation Matrix first: item, endpoint, predecessor, relation, source, method, write permission.
- Replacement work must leave a `SUPERSEDES`/`RESOLVES` graph plan or semantic lifecycle plan; never silently append independent tasks.
- Hybrid Recall default: endpoint -> graph/source edges -> why/code chain -> targeted text search -> stop with unsearched frontier.
- Codex hooks are advisory guardrails only; CLI route, authority, source, and closeout gates remain authoritative.

## Four Gates

- Sovereignty: explicit no shujuan, no governance, no record, no capture, or do not save means `No Governance` immediately. Read-only commands must not create `.shujuan`, trace files, DB rows, endpoint projections, or capture records; trace writes require explicit `--trace`.
- Relation: classify the request as `continuation`, `successor_scope`, `independent_review`, `revision_or_contradiction`, `fork_variant`, `independent_root`, or `no_governance_exit` before state-changing work.
- Authority: controller governs and closes; worker implements; reviewer reviews; researcher gathers facts; writer drafts. Worker/reviewer/provider output is material until controller adoption.
- Source coverage: task/check import uses `plan-to-db import-task-chain`; every task/check must derive from a source item or explicit synthetic controller rationale.

## Method Map

- `Harness` / `shujuan-harness`: first 90 seconds, route/mode/endpoint/role selection, and next safe action.
- `Recall` / `shujuan-recall`: history, rationale, lineage, why, version comparison, and contradiction questions. Read-only.
- `Capture` / `shujuan-capture`: provenance capture only; capture does not create decisions, tasks, checks, execution, or closure.
- `Execute` / `shujuan-execute`: scoped implementation through PostgreSQL readiness, workflow begin, exec start, verification, and material handoff.
- `Delegate` / `shujuan-delegate`: worker/reviewer/researcher/writer/provider packets and returns. Material only before controller adoption.
- `Close` / `shujuan-close`: controller-only evidence adoption, endpoint refresh, evidence verify, and strict doctor.
- `Evolve` / `shujuan-evolve`: changes to shujuan policy, ontology, skills, hooks, installer, package, schema, or route behavior.

`shujuan-core` is a v10 compatibility shim for explicit legacy recovery. It is not the ordinary primary method router.

Codex hooks are advisory guardrails only; CLI route, authority, source, and closeout gates remain authoritative.

## Runtime

PostgreSQL is the runtime/write path. If a writeful route needs the DB and it is unavailable, run `python -m shujuan postgres-dev start` and continue only after `python -m shujuan postgres-dev status` is ready. SQLite and contracted legacy tables are diagnostics, not runtime write fallbacks.

## Boundaries

- Active obligations are open tasks, checks, findings, unresolved decisions, and promoted defers.
- Deferred/backlog/non-goal items stay inactive until explicitly promoted.
- Closure requires current matching `change_set`, `test_result`, `artifact`, or `user_confirmation` evidence and controller adoption.
- Provider facts, codegraph/GitNexus output, reviewer returns, and worker handoffs are material, not closure evidence by themselves.
- Do not create new fact-plane DB tables for v11.0, promote v11.1 cleanup, promote stop-hook work to a blocker, or use SQLite as fallback.

## Commands

- Recover first surface: `python -m shujuan report endpoint <endpoint> --active-only --markdown`
- Recall first surface: `python -m shujuan report endpoint <endpoint> --full --markdown`
- Route guard first surface: `python -m shujuan route guard --pure --intent-file prompt.txt`; add `--trace` only when an explicit trace write is requested.
- Import task chain: `python -m shujuan plan-to-db import-task-chain --artifact chain.json --endpoint <endpoint> --dry-run`
- Close chain: evidence command, then `endpoint refresh`, `evidence verify`, and `endpoint doctor --strict-closeout --allow-fail`

## Roles

Use `controller_agent`, `worker_agent`, `reviewer_agent`, `researcher_agent`, or `writer_agent`; aliases such as `controller` normalize to the `*_agent` form. Unknown roles fail closed. User-specified worker model overrides the default.
