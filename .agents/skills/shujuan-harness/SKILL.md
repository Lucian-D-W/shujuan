---
name: shujuan-harness
description: First-entry and multi-intent relation planning for shujuan; use for ambiguous starts, predecessor binding, route/mode/endpoint/role selection, Task Relation Matrix, and next safe action before writes.
---

# Shujuan Harness

Trigger for ambiguous starts, recovery entry, route/mode selection, endpoint binding, predecessor binding, multi-task prompts, or when the user asks how to proceed.

Do not trigger for deep history answers, implementation after route and predecessor are already selected, reviewer packet execution, or controller closeout.

Anti-drift: ordinary multi-task prompts produce a Task Relation Matrix first and do not spawn subagents unless the user explicitly asks for parallel or multi-agent work.

First surface: `python -m shujuan route guard --intent-file prompt.txt`, then endpoint active report or project overview.

Action chain: state goal, bind endpoint by evidence, name role/mode, choose one primary method, expose forbidden actions, and hand off to the selected method.

Completion: sovereignty/relation/method/endpoint/role/mode/first surface/next safe action are explicit.
