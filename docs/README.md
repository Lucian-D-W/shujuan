# Documentation map

This directory contains durable project documentation only.

- `architecture.md` records the system model and long-lived design principles.
- `guides/` contains maintained operator and developer guides.
- `history/` contains the small set of frozen source contracts still referenced by the v11 verification suite.
- `plans/` contains current source-backed implementation plans. Completed execution logs, reviewer packets, generated screenshots, and database exports belong in the private `.shujuan/` runtime or an external private archive, not in Git.

The repository root is intentionally limited to project entry points and package metadata. Runtime databases, credentials, provider indexes, build outputs, release bundles, and temporary probes are local-only.
