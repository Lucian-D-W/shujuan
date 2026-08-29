# Modes And Terms

Use these current terms precisely in guidance, packets, reports, and tests. Version labels belong in historical analysis, not in the default agent entry surface.

## Governance Modes

- `No Governance`: writes no DB facts and makes no capture claim.
- `Capture`: may capture discussion, but creates no `agent_run` or `change_set`.
- `Explore`: may capture discussion and source material, but creates no execution run.
- `Light`: may start execution with focused evidence expectations.
- `Standard`: execution with scoped evidence, impact awareness, and acceptance closure discipline.
- `Full`: execution with stronger impact review, endpoint doctor, and evidence verify expectations before closeout.

Lower modes keep their stated side effects; higher-governance artifacts belong to the route and mode that explicitly selected them.

Default routes are `Recover`, `Recall`, `Execute`, `Close`, and `Delegate`. `No Governance` is the explicit no-write mode/exit when the user rejects governance or governance friction overtakes the task; it is not a sixth default route. `Recover` is read-only diagnostic orientation, `Recall` is read-only history and rationale review, and `Close` is the writeful controller closeout path. Advanced primitives belong behind those routes as fallback/reference material.

## Core Terms

- `endpoint`: DB-backed recoverable breakpoint for a workstream; it carries current scope, obligations, evidence, blockers, and next valid entry.
- `closed`: task/check state reached only through accepted closure evidence.
- `resolved`: semantic lifecycle state for an item answered or consumed by later source/evidence; it remains historical.
- `active`: current attention state for open mandatory work, unresolved questions, actionable audit findings, or needs-user-decision items.
- `deferred`: source-backed pause decision that returns to active scope only when promoted.
- `product_backlog`: future product-grade work that enters the active workbench only when promoted.
- `audit_finding`: actionable only while lifecycle state is active.
- `evidence`: `test_result`, `artifact`, `change_set`, or `user_confirmation` node that matches the check contract and remains current.
- `provider_fact`: provenance/confidence input for controller adoption.
- `provider_hypothesis`: provider-derived inference for impact review and controller adoption.
- `packet_generated`: handoff material exists, but the reviewer/worker has not executed it yet.
- `reviewer_executed`: a real reviewer return artifact exists; packet generation alone is not enough.
- `controller_adopted`: the controller accepted or rejected returned material; adoption still does not close checks.
- `evidence_imported`: returned material has been imported as matching evidence; only then can closeout logic proceed.
- `artifact index`: endpoint-local `INDEX.md` / `INDEX.json` that separates authoritative artifacts, review material, DB mappings, superseded files, and evidence.
- `PostgreSQL success`: real project-owned PostgreSQL runtime chain with prompt/session/run/evidence/endpoint/report operations, migrations, constraints, persistence, and backup/restore projection consistency.
- `interaction_event`: provenance for captured interaction.
- `discussion_segment`: reviewable source material that becomes a decision only after extraction with source evidence.
- `mode_router`: explicit selector for `No Governance`, `Capture`, `Explore`, `Light`, `Standard`, or `Full`; route and mode side effects stay visible.
- `projection payload` and `read-only workbench`: supervision views that preserve traceability with `detail_ref` and `hidden_source_count`.

Backlog and deferred ideas become active obligations only after promotion. A closed scoped commitment proves the accepted check/task scope, while the broader direction can continue through new request, blocker, or scope contract. If the user explicitly rejects governance or the process starts crowding out the task, apply the friction brake and downgrade to `No Governance`.
