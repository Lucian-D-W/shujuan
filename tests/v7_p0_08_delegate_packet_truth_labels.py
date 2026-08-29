from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


EXPECTED_NEXT_LABELS = {
    "controller_import_returned_material",
    "controller_exec_stop_change_set",
    "controller_evidence_test_result_or_artifact",
    "controller_acceptance_check_closure",
}


def run_cli(repo: Path, *args: str) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key in ("SHUJUAN_DATABASE_URL", "DATABASE_URL", "SHUJUAN_DB_PROFILE"):
        env.pop(key, None)
    completed = subprocess.run(
        [sys.executable, "-m", "shujuan", "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if completed.returncode:
        raise AssertionError(f"command failed: {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    return json.loads(completed.stdout)


def assert_truth_labels(surface: dict[str, object], *, artifact_saved: bool) -> None:
    if surface["packet_material_classification"] != "delegate_packet_preview_material":
        raise AssertionError(f"packet material classification missing: {surface}")
    if surface["artifact_primary"] is not True:
        raise AssertionError(f"artifact-primary label missing: {surface}")
    if surface["governance_db_row_written"] is not False:
        raise AssertionError(f"governance DB row label drifted: {surface}")
    if surface["delegation_tables"] != "dormant_not_primary_storage":
        raise AssertionError(f"delegation dormant table label missing: {surface}")
    if surface["db_persist_table"] is not None:
        raise AssertionError(f"delegate packet implied DB table persistence: {surface}")
    if surface["delegation_packets_table_status"] != "dormant_not_written":
        raise AssertionError(f"delegation packet table status missing: {surface}")
    if surface["material_classification"] != "delegate_packet_preview_material":
        raise AssertionError(f"material classification missing: {surface}")
    if surface["artifact_saved"] is not artifact_saved:
        raise AssertionError(f"artifact_saved drifted: {surface}")
    if surface["artifact_is_governance_record"] is not False:
        raise AssertionError(f"artifact was mislabeled as governance record: {surface}")
    if surface["governance_record_created"] is not False:
        raise AssertionError(f"delegate packet implied a persisted governance row: {surface}")
    if surface["governance_record_table"] != "delegation_packets":
        raise AssertionError(f"governance table label missing: {surface}")
    labels = set(surface["next_required_governance_record_labels"])
    if not EXPECTED_NEXT_LABELS <= labels:
        raise AssertionError(f"next governance labels incomplete: {surface}")


def assert_no_delegation_db_row_implied(saved: dict[str, object]) -> None:
    if saved["db_backed"] is not False:
        raise AssertionError(f"saved packet implied DB backing: {saved}")
    fact_source = saved["fact_source"]
    if fact_source["db_backed"] is not False or fact_source["kind"] != "source_labeled_cli_args":
        raise AssertionError(f"saved packet fact source implied DB persistence: {saved}")
    if saved["db_write_authority"] is not False or saved["closeout_authority"] is not False:
        raise AssertionError(f"saved worker packet leaked governance authority: {saved}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shujuan-v7-p0-08-", ignore_cleanup_errors=True) as temp:
        repo = Path(temp)
        unsaved = run_cli(
            repo,
            "delegate",
            "packet",
            "--role",
            "worker",
            "--endpoint",
            "truth-labels",
            "--task",
            "task_a",
            "--check",
            "check_a",
            "--body",
            "Return material only.",
        )
        assert_truth_labels(unsaved, artifact_saved=False)
        assert_truth_labels(unsaved["packet"], artifact_saved=False)
        assert_truth_labels(unsaved["packet"]["role_packet"], artifact_saved=False)
        if unsaved["persisted"] or unsaved["artifact_ref"] is not None or (repo / ".shujuan").exists():
            raise AssertionError(f"unsaved packet created artifact or hidden state: {unsaved}")

        saved = run_cli(
            repo,
            "delegate",
            "packet",
            "--role",
            "worker",
            "--endpoint",
            "truth-labels",
            "--task",
            "task_a",
            "--check",
            "check_a",
            "--body",
            "Save packet material only.",
            "--save-artifact",
        )
        assert_truth_labels(saved, artifact_saved=True)
        assert_truth_labels(saved["packet"], artifact_saved=True)
        assert_truth_labels(saved["packet"]["role_packet"], artifact_saved=True)
        if not saved["persisted"] or not saved["artifact_ref"]:
            raise AssertionError(f"saved packet did not report artifact file persistence: {saved}")

        artifact_path = repo / str(saved["artifact_ref"])
        if not artifact_path.exists():
            raise AssertionError(f"saved packet artifact missing: {artifact_path}")
        saved_artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert_truth_labels(saved_artifact, artifact_saved=True)
        assert_no_delegation_db_row_implied(saved_artifact)
        if saved_artifact["artifact_ref"] != saved["artifact_ref"]:
            raise AssertionError(f"saved artifact did not carry its artifact ref: {saved_artifact}")
        if (repo / "shujuan.sqlite").exists() or (repo / ".shujuan" / "shujuan.sqlite").exists():
            raise AssertionError("delegate packet artifact save created a governance database")

    print(json.dumps({"ok": True, "v7_p0_08_delegate_packet_truth_labels": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
