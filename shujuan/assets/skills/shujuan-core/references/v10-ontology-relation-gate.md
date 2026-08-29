# v10 Ontology Relation Gate

Use this reference when route choice, authority posture, or task-chain provenance is the active question.

## First Three Gates

1. Sovereignty gate: explicit `No Governance` exits short-circuit before DB connect, layout creation, trace write, or closeout inference unless `--trace` is explicitly requested.
2. Relation gate: classify the current request as `no_governance_exit`, `continuation`, `successor_scope`, `independent_review`, `revision_or_contradiction`, `fork_variant`, or `independent_root`.
3. Authority gate: controller closes, worker implements, reviewer returns material, writer drafts prose, and provider output stays material until controller adoption.

## Relation Decision Contract

`route guard` should expose a structured `relation_decision` with:

- `relation_type`
- `decision_type`
- `confidence`
- `evidence_phrases`
- `predecessor_required`
- `predecessor_hint`
- `authority_hint`
- `route_hint`
- `mode_hint`

Decision types stay small and operational: `append`, `update`, `supersede`, `delete`, `promote`, `defer`, `escalate`, `material_only`, and `no_write`.

## Boundary Corrections

- `继续` / `接手` / `resume` / `take over` means governance-internal `Recover`, not `No Governance`.
- `独立审查` / `单独检查` / `independent review` means reviewer material, not `No Governance`.
- Explicit no-record / no-shujuan wording wins over closeout wording; do not upgrade it into a `Close` error path.
- Complete closeout input sets should route to `Close` with the close chain visible.

## Task-Chain Source Coverage

`plan-to-db import-task-chain` must fail when a task or check has no `source_item` coverage.

Allowed synthetic exceptions must be explicit:

- `synthetic=true`
- `controller_allowed_synthetic=true`
- non-empty `synthetic_rationale`
- non-empty `derived_from_source_items`

Synthetic items are still derived from source; they do not restore source-free fallback imports.
