"""Task 6: per-stage version snapshots and rollback."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.core.lockfile import (
    ArtifactRecord,
    Lockfile,
    StageSnapshot,
    SubstageRecord,
    push_stage_snapshot,
    read_lockfile,
    write_lockfile,
)


# ── StageSnapshot round-trip ──────────────────────────────────────────────────

def test_stage_snapshot_round_trip(tmp_path):
    lock = Lockfile(job_name="my_job")
    snap = StageSnapshot(
        generated_at="2026-06-01T10:00:00Z",
        input_hash="sha256:abc",
        artifact_path="generated/ords/my_job_module.sql",
        git_commit="deadbeef01234567",
        run_id="gh-run-123",
        mode="generate",
    )
    push_stage_snapshot(lock, "sql", snap)
    write_lockfile(tmp_path, lock)

    reloaded = read_lockfile(tmp_path)
    assert len(reloaded.stage_history["sql"]) == 1
    restored = reloaded.stage_history["sql"][0]
    assert restored.git_commit == "deadbeef01234567"
    assert restored.artifact_path == "generated/ords/my_job_module.sql"
    assert restored.run_id == "gh-run-123"


def test_push_stage_snapshot_prepends_newest_first(tmp_path):
    lock = Lockfile(job_name="j")
    for ts in ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z"]:
        push_stage_snapshot(lock, "sql", StageSnapshot(
            generated_at=ts, input_hash="h", artifact_path="p"
        ))
    # newest is last-pushed
    assert lock.stage_history["sql"][0].generated_at == "2026-03-01T00:00:00Z"
    assert lock.stage_history["sql"][2].generated_at == "2026-01-01T00:00:00Z"


def test_push_stage_snapshot_caps_at_max_history():
    lock = Lockfile(job_name="j")
    for i in range(15):
        push_stage_snapshot(lock, "sql", StageSnapshot(
            generated_at=f"2026-{i+1:02d}-01T00:00:00Z", input_hash="h", artifact_path="p"
        ), max_history=10)
    assert len(lock.stage_history["sql"]) == 10


def test_old_lockfile_without_stage_history_loads_cleanly(tmp_path):
    old = {"schema_version": 1, "job_name": "j", "artifacts": {}}
    (tmp_path / ".cdu-lock.json").write_text(json.dumps(old))
    lock = read_lockfile(tmp_path)
    assert lock.stage_history == {}


# ── run() integration: snapshot is written on re-generation ──────────────────

def _make_validation(tmp_path: Path, mode: str = "generate"):
    import shutil, os
    from pipeline.stages.validate import validate

    root = Path(__file__).parent.parent
    shutil.copy(root / "connections.yaml", tmp_path / "connections.yaml")
    shutil.copytree(root / "prompts", tmp_path / "prompts")
    shutil.copytree(root / "examples" / "student_download", tmp_path / "job")
    for sub in ("specs", "mappings", "tests"):
        (tmp_path / "job" / sub).mkdir(exist_ok=True)

    env = {
        "ORACLE_DEV_USER":        "test-oracle-user-xq9",
        "ORACLE_DEV_PASSWORD":    "test-oracle-pass-xq9",
        "MULE_REPO_TOKEN":        "test-mule-token-xq9",
        "MULE_DEV_CLIENT_ID":     "test-client-id-xq9",
        "MULE_DEV_CLIENT_SECRET": "test-client-secret-xq9",
        "SFTP_DEV_USER":          "test-sftp-user-xq9",
        "SFTP_DEV_PRIVATE_KEY":   "test-sftp-key-xq9",
        "GH_PIPELINE_TOKEN":      "test-gh-token-xq9",
    }
    for k, v in env.items():
        os.environ[k] = v

    return validate(tmp_path)


FAKE_ORDS = "BEGIN ORDS.DEFINE_MODULE(p_module_name=>'student_download_v1'); END;\n/"
FAKE_TESTS = "def test_placeholder(): pass\n"


def _force_stale(tmp_path: Path, artifact_key: str) -> None:
    """Delete the generated artifact file so decide_regeneration marks it stale."""
    lock = read_lockfile(tmp_path)
    if lock and artifact_key in lock.artifacts:
        (tmp_path / lock.artifacts[artifact_key].path).unlink(missing_ok=True)


def test_run_records_snapshot_on_second_generate(tmp_path):
    """First run generates; second run (forced stale) records the first version in history."""
    from pipeline.stages.run import run

    validation = _make_validation(tmp_path)

    with patch("pipeline.stages.run.invoke_copilot", return_value=FAKE_ORDS), \
         patch("pipeline.stages.run.gitops.commit_back", return_value=True):
        run(tmp_path, validation, substages=["sql"])

    _force_stale(tmp_path, "ords")

    with patch("pipeline.stages.run.invoke_copilot", return_value=FAKE_ORDS), \
         patch("pipeline.stages.run.gitops.commit_back", return_value=True):
        run(tmp_path, validation, substages=["sql"])

    lock2 = read_lockfile(tmp_path)
    assert "sql" in lock2.stage_history
    assert len(lock2.stage_history["sql"]) >= 1
    snap = lock2.stage_history["sql"][0]
    assert snap.artifact_path.endswith("_module.sql")


def test_run_history_grows_across_three_regenerations(tmp_path):
    from pipeline.stages.run import run

    validation = _make_validation(tmp_path)

    for i in range(3):
        _force_stale(tmp_path, "ords")
        with patch("pipeline.stages.run.invoke_copilot", return_value=FAKE_ORDS), \
             patch("pipeline.stages.run.gitops.commit_back", return_value=True):
            run(tmp_path, validation, substages=["sql"])

    final = read_lockfile(tmp_path)
    # history entries = number of times an EXISTING artifact was overwritten = 2
    assert len(final.stage_history.get("sql", [])) == 2
