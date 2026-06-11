"""Lockfile read/write round-trip and first-run behaviour (spec §8)."""

from __future__ import annotations

import json

from pipeline.core.lockfile import (
    ArtifactRecord,
    Lockfile,
    lockfile_path,
    read_lockfile,
    write_lockfile,
)


def test_first_run_returns_none(tmp_path):
    assert read_lockfile(tmp_path) is None


def test_round_trip_preserves_all_fields(tmp_path):
    lock = Lockfile(
        job_name="student_download_v1",
        last_run_id="gh-run-9182736",
        last_run_at="2026-06-11T14:30:22Z",
        last_mode="generate",
        input_hashes={"job/intent.md": "sha256:ab12"},
        combined_input_hash="sha256:9988",
        intent_snapshot={"job_name": "student_download_v1", "mode": "generate"},
        artifacts={
            "ords": ArtifactRecord(
                path="generated/ords/student_download_v1_module.sql",
                input_hash_at_gen="sha256:9988",
                generated_at="2026-06-11T14:31:00Z",
            )
        },
        deployed={"staging_table": "STG_STUDENT_DOWNLOAD_V1"},
        last_test_result={"status": "pass", "report": "reports/run_x.md"},
    )
    write_lockfile(tmp_path, lock)
    loaded = read_lockfile(tmp_path)
    assert loaded == lock


def test_lockfile_is_named_and_shaped_per_spec(tmp_path):
    write_lockfile(tmp_path, Lockfile(job_name="x_job"))
    path = lockfile_path(tmp_path)
    assert path.name == ".cdu-lock.json"
    data = json.loads(path.read_text())
    assert data["schema_version"] == 1
    for key in ("job_name", "input_hashes", "combined_input_hash",
                "artifacts", "deployed", "last_test_result"):
        assert key in data
