from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.postgres_fixture import PostgresFixture, free_port, has_postgres_bins, postgres_fixture


def main() -> int:
    port = free_port()
    if not isinstance(port, int) or port <= 0:
        raise AssertionError(f"free_port did not return a TCP port: {port}")
    fixture_pair = postgres_fixture("shujuan-pg-fixture-contract-")
    if fixture_pair is None:
        print(json.dumps({"ok": True, "skipped": "native PostgreSQL binaries not found"}))
        return 0
    temp, fixture = fixture_pair
    try:
        if not isinstance(fixture, PostgresFixture):
            raise AssertionError("postgres_fixture did not return a PostgresFixture")
        results = fixture.run_batch((("migrate", "status"), ("postgres-dev", "status")))
        if len(results) != 2 or any(result.returncode != 0 for result in results):
            raise AssertionError(f"batch execution did not capture two passing commands: {results}")
        status = results[0].json()
        if status.get("backend") != "postgres" or status.get("schema_state") != "current":
            raise AssertionError(f"fixture migrate status did not prove PostgreSQL runtime: {status}")
        if fixture.writes != ["temporary postgres-dev repo only"]:
            raise AssertionError(f"fixture writes were not reported separately: {fixture.writes}")
        print(json.dumps({"ok": True, "postgres_fixture_helper_contract": "passed", "fixture_writes": fixture.writes}))
        return 0
    finally:
        fixture.stop()
        temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
