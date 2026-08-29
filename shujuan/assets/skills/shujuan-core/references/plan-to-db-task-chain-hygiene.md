# Plan-to-DB Task Chain Hygiene

This reference defines the lightweight boundary for converting a source plan into shujuan DB tasks and acceptance checks. It is a decomposition hygiene guide, not a planner framework, migration system, or closure engine. Use it as advanced fallback after the `Recover`, `Execute`, `Close`, or `Delegate` route shows that decomposition work is actually needed.

## Non-Compression Rule

Source-plan deliverables must not be collapsed into a broad parent, deferred umbrella, or artifact-only ordered plan. Each named deliverable needs a visible classification, graph destination, and rationale. If a source item is absorbed, superseded, or indirectly dissolved by later work, keep it visible as a non-active decomposition row with the destination item and rationale that consumed it.

Promotion-time re-expansion is required: when a deferred/backlog/non-active item is promoted back into scope, reopen the source item into explicit task/check/lifecycle rows instead of promoting only the old umbrella label. Promotion must preserve the original source reference, prior absorption/supersession rationale, and the new active graph destination.

## Method Boundary

1. Classify before creating tasks.
   - `P0` is the current golden path: the smallest source-backed chain that proves the active success outcome.
   - `P1` is important follow-up work, but it must not block P0 unless the source explicitly makes it a prerequisite.
   - `P2` is product backlog or later product-grade work.
   - `non-goal` is explicitly out of scope and must not become an implicit task.
2. Make phase and order explicit.
   - Record phase labels or ordered slices when the plan depends on sequence.
   - Do not rely on prose order, document section order, task id order, or endpoint history to imply execution order.
   - A later phase is not a blocker just because it is a successor phase.
3. Keep the golden path minimal.
   - P0 should contain only the tasks and checks needed for the current scoped proof.
   - Do not let P1/P2 polish, broad matrices, packaging, adapters, or platform work leak into P0 acceptance.
4. Separate relationships by meaning.
   - `source` means the task/check is grounded in a source document, section, prompt, or adopted review note.
   - `successor` means a scope follows another scope without reopening or blocking the predecessor by default.
   - `lineage` means historical ancestry or design inheritance.
   - `blocker` means an active dependency prevents current scoped closure.
   - Do not encode successor, lineage, or source traceability as a blocker.
5. Keep acceptance checks single-intent.
   - Each check should ask for one verifiable outcome.
   - Split mixed checks such as "add docs and prove behavior" into separate `change_set`, `test_result`, or `artifact` checks.
6. Align evidence type with the check body.
   - `change_set` checks should be satisfied by changed repository files.
   - `test_result` checks should be satisfied by repeatable command output.
   - `artifact` checks should be satisfied by a named file or report artifact.
   - `user_confirmation` checks should be satisfied by explicit user confirmation.
   - Do not close a check with evidence whose type only partially matches the body.
7. Treat reviewer, provider, and delegate outputs as material only.
   - Reviewer findings, provider facts/hypotheses, codegraph/GitNexus/provider output, and delegate packets can inform controller decisions.
   - They do not close tasks/checks, create active findings, or rewrite scope until the controller adopts them into shujuan records.
8. Record controller adoption or rejection of feedback.
   - Adopted feedback should point to the source material and the resulting task, check, scope change, unresolved item, defer decision, or assumption.
   - Rejected feedback should remain historical material with a short rejection reason.
   - Silence is not adoption.
9. Avoid false closeout.
   - A good task chain may propose checks, blockers, and evidence expectations, but it does not claim closure.
   - Tasks remain open until their acceptance checks have matching current evidence.

## Required Decomposition Output Shape

Every source-plan item should have:

- `source_ref` or `id`: the named source-plan deliverable or section.
- `classification`: `P0`, `P1`, `P2`, `non-goal`, `deferred`, or `product_backlog`.
- `status`: `active`, `absorbed`, `superseded`, `indirectly_dissolved`, `deferred`, `product_backlog`, or `out_of_scope`.
- `graph_destination`: the task/check/scope/lifecycle/defer/unresolved/audit node, or the planned graph row for material not yet created.
- `rationale`: why this destination is faithful to the source.
- `promotion_rule`: what must happen before a non-active item can re-enter active scope.
- `reopen_rule`: how to re-expand the source item into explicit rows if later work needs it.
- `absorbed_by`, `superseded_by`, or `dissolved_by` when status is absorbed, superseded, or indirectly dissolved.
- `task_ids` and `check_ids` for active implementation/verification items; artifact-only prose is not enough for active source-plan commitments.

Use `python -m shujuan plan-to-db verify-artifact --artifact <json>` to check this shape before controller import. The gate flags compressed named deliverables, artifact-only active slices, unsafe broad-parent promotion, unlinked inactive items, and false closeout claims.

When the deliverable is a large v9-style task chain, use `python -m shujuan plan-to-db import-task-chain --artifact <json> --endpoint <endpoint> --dry-run` first, then `--apply` only after the preview is accepted. Do not replace the missing importer with wrapper subprocess loops.

v10 source coverage rule: every imported task/check must derive from at least one `source_item`. Synthetic controller-added rows must declare `synthetic=true`, `controller_allowed_synthetic=true`, a non-empty `synthetic_rationale`, and non-empty `derived_from_source_items`.

## Lifecycle Reconciliation

Known residuals can appear when a node already has an incoming `RESOLVES` or `SUPERSEDES` graph edge but its semantic lifecycle is still `active`. Use `python -m shujuan plan-to-db lifecycle-reconcile --endpoint <endpoint> --allow-fail` as the dry-run gate. The output lists source node, affected node, current state, target state, rationale, and graph destination.

Only a controller should run `python -m shujuan plan-to-db lifecycle-reconcile --endpoint <endpoint> --apply` on the current project. Apply updates semantic lifecycle state through the same lifecycle machinery used by `semantic set-state`; it is not an artifact-prose closeout and it does not close tasks/checks.

## Good Chain Checklist

- P0/P1/P2/non-goal classification is visible before or beside the task list.
- Phase order is explicit where order matters.
- The P0 golden path is small enough to prove the scoped outcome without product-grade spillover.
- Source, successor, lineage, and blocker links are not conflated.
- Each acceptance check has one intent and one expected evidence type.
- The expected evidence type matches the check body.
- Reviewer/provider/delegate material is clearly advisory until controller adoption.
- Controller adoption/rejection of feedback is recorded as a decision, not inferred.
- No task/check is described as closed by plan decomposition alone.

## Recurring Failure Modes To Catch

| Failure mode | Hygiene violation |
| --- | --- |
| Implicit phase order | Tasks rely on document order or ids instead of explicit phase/order labels. |
| P1/P2 leakage into P0 golden path | Later polish, platform, or backlog work is listed as required for P0 proof. |
| Relation-as-blocker mistake | Successor, lineage, or source relationships are recorded as active blockers. |
| Evidence/body mismatch | A check body asks for a test but expects `change_set`, or asks for files but expects `test_result`. |
| Reviewer/provider material treated as closure | Advisory output is treated as accepted closure evidence without controller adoption. |
| Unsynchronized decomposition artifacts | A doc, task list, fixture, or endpoint projection disagrees about classification, phase, relation, or evidence expectation. |
| Compressed source-plan deliverables | Multiple named deliverables are represented by one broad parent without one row per deliverable. |
| Artifact-only active slice | A source-plan item is marked active but points only to a report/doc instead of task/check/lifecycle graph rows. |
| Unlinked absorbed/superseded item | A non-active source item lacks the graph destination and rationale that consumed it. |
