# shujuan

shujuan is a local governance tool for AI coding agents. It connects a request to its tasks, code changes, checks, and evidence so work can be recovered and verified instead of being declared complete from conversation alone.

This repository is an early runnable foundation, not a production platform.

## Requirements

- Python 3.10+
- PostgreSQL

PostgreSQL is the required runtime and write backend. shujuan does not fall back to SQLite.

## Install

```powershell
python -m pip install -e .
python -m shujuan init --postgres-dev --name my-project
python -m shujuan postgres-dev status
```

## Start a workstream

```powershell
python -m shujuan workflow begin --endpoint first-demo --content "Explain this project"
python -m shujuan report endpoint first-demo --active-only --markdown
```

Use `python -m shujuan --help` for the complete command list.

## Data and privacy

Private runtime data—including the database, credentials, evidence, patches, and traces—belongs in `.shujuan/`, which is ignored by Git.

Before publishing:

```powershell
python scripts/audit_public_repository.py --repo . --ref main --require-clean
```

## Documentation

See [docs/README.md](docs/README.md) for architecture, methods, maintenance, and verification details. Agent-facing policy is in [AGENTS.md](AGENTS.md).

## License

[Apache License 2.0](LICENSE)
