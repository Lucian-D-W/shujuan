# Shujuan v11 Migration And Release Notes

Endpoint: `shujuan-v11-method-plane-2026-06-26`

## Migration

v11 keeps the v10 PostgreSQL fact plane intact and adds a method plane above it. No new fact-plane DB tables are introduced for v11.0.

- `AGENTS.md` is the compact always-on policy surface.
- Seven method Skills are installed: `shujuan-harness`, `shujuan-recall`, `shujuan-capture`, `shujuan-execute`, `shujuan-delegate`, `shujuan-close`, and `shujuan-evolve`.
- `shujuan-core` remains as an explicit v10 compatibility shim for one release.
- `python -m shujuan init --install-skills` installs required Skills and role profiles. `--install-skill` is a compatibility alias.
- `install-layout doctor` reports every required Skill and role profile with presence, metadata, version, compatibility, and hash.

## Glossary

- Policy plane: the compact always-on rules in `AGENTS.md` and deterministic CLI gates.
- Method plane: the seven task-specific Skills and `MethodContract` output.
- Role plane: DCCP role policy and `.codex/agents/*.toml` profiles.
- Enforcement plane: route intent facts, role normalization, CLI closeout inputs, and optional advisory hooks.
- Fact plane: existing PostgreSQL objects, evidence, tasks, checks, sources, endpoints, and projections.

## Risk Controls

- Independent review with negated close routes to Delegate material.
- Unknown roles fail closed instead of silently becoming worker authority.
- Hooks are optional and non-authoritative; route/role/closeout CLI gates still protect disabled or untrusted-hook operation.
- Provider/codegraph/GitNexus output stays material until controller adoption.
- SQLite remains a disabled runtime fallback.

## Rollback

Rollback restores the prior AGENTS/core-skill/installer/package surfaces. Because v11.0 does not add DB tables, rollback needs no reverse migration. New packets and artifacts remain material unless the controller adopts or supersedes them.

## Evidence Notes

Worker material should include pre-edit codegraph/GitNexus analysis, changed files, focused tests, task-chain coverage, remaining risks, and no-closure attestation. Controller closeout remains separate.
