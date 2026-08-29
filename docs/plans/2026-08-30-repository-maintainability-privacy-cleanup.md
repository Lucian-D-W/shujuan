# Repository Maintainability and Privacy Cleanup

Date: 2026-08-30

## Delivery target

Produce a compact, maintainable public repository and a clean local workspace while preserving the existing private history and every database-referenced artifact. Remove zhanggong completely from active repository/runtime tooling, use GitNexus directly, and require a repeatable privacy gate before any remote is accepted.

## Relation matrix

| Item | Endpoint | Predecessor | Relation | Source | Method | Write permission |
| --- | --- | --- | --- | --- | --- | --- |
| Canonical repository structure | `shujuan-repository-maintainability-privacy-cleanup-2026-08-30` | `desktop-shujuan-file-triage-2026-05-30` | `successor_scope` | User request and 2026-08-30 audit | Evolve | Controller after backup |
| Clean public Git history | Same | local `main@b0303fa` | `fork_variant` for publication; old history remains recoverable only in the protected external bundle | User confirmation | Evolve | Controller after bundle verification |
| Privacy and credential hardening | Same | 2026-08-30 privacy audit | `continuation` | Audit findings | Evolve | Controller; never print secrets |
| PostgreSQL/runtime cleanup | Same | current `.shujuan` runtime | `revision_or_contradiction` of unmanaged accumulation | Reference audit | Evolve | Only unreferenced files after backup |
| Packaging and verification | Same | v11.2.2/v11.3 release material | `successor_scope` | Existing package contract | Evolve | Controller after impact analysis |
| Remove zhanggong and add a publication gate | Same | clean `main@8f76972` and the earlier zhanggong-assisted workflow | `revision_or_contradiction` | Explicit user revision on 2026-08-30 | Evolve | Remove active integration; preserve private audit history; never push |

## Observed audit facts

- The current branch contains a tracked database dump and generated/private material that must not enter a public Git history.
- Generated release trees are ignored by current rules but remain tracked in existing history.
- Local runtime state is about 5.5 GiB; explicit database references account for about 1.0 GiB of patches/artifacts.
- The local PostgreSQL credential file is ignored by Git and the current credential was not found in Git or database content, but its inherited ACL is overly broad.
- No live GitHub, OpenAI, AWS, or private-key material was confirmed by the boundary-aware scans.
- Four provider artifact path/capture references are missing and must be reported, not silently repaired.

## Canonical public tree

Keep only durable project surfaces:

- `.agents/` for project-owned Shujuan agent methods only. GitNexus skills are installed globally; generated project copies do not belong here.
- `.codex/` for project-owned hooks/configuration.
- `shujuan/` for package source.
- `tests/` for repeatable product tests; exclude transient test outputs.
- `migrations/` for PostgreSQL schema history.
- `scripts/` for maintained build/verification utilities.
- `docs/` for current architecture, operations, privacy, and this plan; historical execution packets remain private.
- Root project files such as `README.md`, `AGENTS.md`, `CLAUDE.md`, `pyproject.toml`, manifests, and dependency metadata when still required.

Exclude from the public tree:

- `build/`, `*.egg-info/`, generated release directories, caches, and provider indexes.
- zhanggong skills, configuration, generated output, provider execution paths, compatibility fields, and documentation.
- Desktop archives, raw database dumps, old execution/reviewer packets, temporary prompts, probe files, and one-off repair scripts.
- `.shujuan/` runtime data and every credential, log, patch, export, artifact, or database file beneath it.

## Execution sequence

1. Create a private snapshot branch and an external Git bundle; include the current non-ignored working tree state.
2. Verify the bundle before any history replacement.
3. Create a new clean public root history from the curated tree; after the public gate passes, remove private local refs and unreachable objects while retaining the verified protected external bundle.
4. Harden PostgreSQL credential-file creation and the current credential ACL without exposing values.
5. Preserve every explicitly referenced runtime file. Archive or remove only files proven unreferenced after a second reference scan and a recoverable backup.
6. Remove zhanggong skills/configuration/generated output and replace any active provider execution contract with direct GitNexus terminology and behavior.
7. Add a repeatable publication audit that scans exactly the Git objects reachable from `main` without printing matched secret values.
8. Reconcile packaging/manifests with the canonical source tree.
9. Run targeted runtime, packaging, privacy, Git-history, and database-reference checks.
10. Do not configure or push a remote until every publication gate passes and the user then supplies the remote.

## Non-goals and boundaries

- Do not add fact-plane database tables or change the PostgreSQL schema.
- Do not close tasks/checks from decomposition or from provider material.
- Do not delete database-referenced files, the private history backup, or the live PostgreSQL cluster.
- Do not publish `.shujuan`, raw dumps, historical private archives, credentials, or user-specific paths.
- Do not claim tests passed when a test was skipped or not mapped.
- Do not push to GitHub in this scope until the user separately supplies/approves the remote repository.
- Do not use, reinstall, or retain active zhanggong integration. Historical private database provenance remains immutable audit material and is not a runtime dependency.

## Acceptance summary

The public branch has a small, intentional root; zhanggong has no active tracked or generated surface; the repeatable publication gate and independent secret/history scans are clean; credential ACL is restricted; PostgreSQL remains ready; all referenced artifacts still exist; manifests and targeted tests pass; private history remains recoverable; no remote exists.
