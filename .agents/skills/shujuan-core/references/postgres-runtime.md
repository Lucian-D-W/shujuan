# PostgreSQL Runtime

shujuan's current runtime/write backend is PostgreSQL. DB readiness gate: if the project database service is unavailable, run `python -m shujuan postgres-dev start`; continue only after `python -m shujuan postgres-dev status` reports ready. PostgreSQL is the setup, recovery, execution, and closeout write path.


## Project-Owned Dev Database

```bash
python -m shujuan init --postgres-dev --name "<project>"
python -m shujuan postgres-dev start
python -m shujuan postgres-dev status
```

`init --postgres-dev` creates or reuses `.shujuan/postgres-dev/`, derives a stable project database name from the repo path, initializes shujuan schema into PostgreSQL, and writes local config/credentials for future commands. Keep `.shujuan/` local and use the project-owned database as the write target.

## Explicit PostgreSQL URL

```bash
$env:SHUJUAN_DATABASE_URL = "postgresql://user:password@localhost:5432/shujuan" # pragma: allowlist secret
python -m shujuan init
python -m shujuan migrate status
```

`SHUJUAN_DATABASE_URL` must use `postgresql://` or `postgres://`. `sqlite:///`, `--db-profile sqlite`, and `SHUJUAN_DB_PROFILE=sqlite` fail closed.

## Disabled Legacy Path

Legacy SQLite cutover commands are historical diagnostics. Recreate current project state from project-owned PostgreSQL, PostgreSQL backups, or captured shujuan evidence artifacts; current state comes from PostgreSQL.
