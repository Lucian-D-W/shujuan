---
name: shujuan-core
description: Explicit v10 compatibility shim for legacy shujuan recovery; prefer shujuan-harness, recall, execute, delegate, close, capture, or evolve for v11 work.
---

# Shujuan Core Compatibility Shim

Use only when a legacy packet explicitly asks for `shujuan-core` or when recovering v10-era instructions. For ordinary v11 work, select exactly one method Skill from `AGENTS.md`.

This shim does not grant authority, does not close checks/tasks, and does not replace controller adoption. PostgreSQL remains the runtime/write path; SQLite and contracted legacy tables are not write fallbacks.

References from v10 remain available under `references/` for migration reading.
