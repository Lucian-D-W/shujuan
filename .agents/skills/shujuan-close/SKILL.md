---
name: shujuan-close
description: Controller-only closeout with complete endpoint/task/check/evidence inputs; sign-off/accept/approve routes here, missing inputs fail closed, material never closes by itself.
---

# Shujuan Close

Trigger only for explicit controller closeout with endpoint, task id, check id, expected evidence type, and current matching evidence reference.

Do not trigger for "does this pass", independent review, worker returns, provider output, or missing closeout inputs.

Anti-drift: reviewer, provider, worker, GitNexus, and CodeGraph material can support adoption, but none is closure evidence until the controller adopts matching evidence.

First surface: closeout input packet and endpoint status.

Action chain: match/adopt evidence, run endpoint refresh, evidence verify, and strict doctor. Failed or missing evidence leaves checks/tasks open.

Completion: controller reports which checks/tasks closed and which remain open. Worker/reviewer/provider material alone is never closure evidence.

Template: `templates/closeout-handoff.md`.
