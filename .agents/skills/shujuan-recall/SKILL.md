---
name: shujuan-recall
description: Read-only hybrid retrieval over endpoint, graph, source, lifecycle, and code why chains; use for history, rationale, lineage, contradictions, predecessor discovery, and relationship-aware recall; never writes.
---

# Shujuan Recall

Trigger for "why", history, rationale, lineage, version comparison, report-vs-code contradiction, deferred/non-goal, current state questions, or predecessor discovery.

Do not trigger for code modification, capture, task/check closure, or broad exhaustive reading unless the user asks for exhaustive research.

Anti-drift: do not let read-only history become task creation; if a relationship is likely but predecessor is unbound, return to Harness for a Task Relation Matrix.

First surface: `recall frontier` or endpoint active/full report when endpoint is known; project overview only when endpoint is unknown.

Action chain: create a claim ledger, rank recall frontier by source value, read only enough anchored surfaces, separate observed facts/report claims/inference, and stop with a reason.

Completion: answer includes anchors, contradictions, unsearched frontier, and Recall Stop Decision. DB writes: zero.

Templates: `templates/claim-ledger.md`, `templates/recall-frontier.md`, `templates/recall-stop-decision.md`.
