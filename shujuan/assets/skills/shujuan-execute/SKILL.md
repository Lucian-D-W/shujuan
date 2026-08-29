---
name: shujuan-execute
description: Scoped implementation after endpoint and predecessor relation are bound; use for code changes only after Harness/Recall gates; if prompt mentions previous/rebuild/replace/lineage, get relation matrix first.
---

# Shujuan Execute

Trigger for scoped implementation once route, endpoint, predecessor relation, and authority are known.

Do not trigger for independent review, no-governance ordinary answers, source-only capture, or controller closeout.

Anti-drift: if predecessor, replacement, fork, or lineage is explicit but unbound, stay read-only and return to Harness/Recall before writeful work.

First surface: endpoint active report and the source packet/check contract.

Action chain: confirm runtime readiness if writeful work is required, run `workflow begin`/`exec start` only under controller authority, implement scoped changes, verify with focused tests, and return material.

Completion: changed files, tests, task-chain coverage, risks/blockers, and no-closure attestation are explicit.
