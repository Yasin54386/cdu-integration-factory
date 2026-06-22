"""Manual/Copilot-paste mode: prompt → human paste → ingest."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.core.lockfile import read_lockfile
from pipeline.stages.manual import (
    ManualError,
    ingest,
    write_prompts,
)


def _make_validation(tmp_path: Path):
    import os
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
FAKE_MULE = ('<?xml version="1.0"?><mule xmlns="http://www.mulesoft.org/schema/mule/core">'
             '<flow name="student_download_v1-main-flow"/></mule>')
FAKE_TESTS = "def test_placeholder(): pass\n"


# ── write_prompts ─────────────────────────────────────────────────────────────

def test_write_prompts_creates_files_for_all_substages(tmp_path):
    validation = _make_validation(tmp_path)
    outcomes = write_prompts(tmp_path, validation)

    assert [o.substage for o in outcomes] == ["sql", "mulesoft", "tests"]
    for o in outcomes:
        prompt_file = tmp_path / o.prompt_path
        assert prompt_file.is_file()
        text = prompt_file.read_text()
        assert "PASTE FROM HERE INTO COPILOT CHAT" in text
        assert o.output_path in text


def test_write_prompts_single_substage(tmp_path):
    validation = _make_validation(tmp_path)
    outcomes = write_prompts(tmp_path, validation, substages=["sql"])
    assert [o.substage for o in outcomes] == ["sql"]
    assert (tmp_path / "generated/.prompts/sql.prompt.md").is_file()
    assert not (tmp_path / "generated/.prompts/mulesoft.prompt.md").exists()


def test_write_prompts_includes_intent_contract(tmp_path):
    validation = _make_validation(tmp_path)
    write_prompts(tmp_path, validation, substages=["sql"])
    text = (tmp_path / "generated/.prompts/sql.prompt.md").read_text()
    assert "student_download_v1" in text  # job_name from the intent front-matter


# ── ingest ────────────────────────────────────────────────────────────────────

def test_ingest_validates_and_records_artifact(tmp_path):
    validation = _make_validation(tmp_path)
    # Human "pastes" the Copilot reply into the artifact path.
    out = tmp_path / "generated/ords/student_download_v1_module.sql"
    out.parent.mkdir(parents=True)
    out.write_text(FAKE_ORDS)

    with patch("pipeline.stages.manual.gitops.commit_back", return_value=True):
        outcomes = ingest(tmp_path, validation, substages=["sql"])

    assert outcomes[0].substage == "sql"
    assert outcomes[0].committed is True
    lock = read_lockfile(tmp_path)
    assert "ords" in lock.artifacts
    assert lock.substages["sql"].status == "done"


def test_ingest_strips_code_fences(tmp_path):
    validation = _make_validation(tmp_path)
    out = tmp_path / "generated/ords/student_download_v1_module.sql"
    out.parent.mkdir(parents=True)
    out.write_text("```sql\n" + FAKE_ORDS + "\n```")

    with patch("pipeline.stages.manual.gitops.commit_back", return_value=True):
        ingest(tmp_path, validation, substages=["sql"])

    # the committed file is fence-free
    assert "```" not in out.read_text()
    assert "ORDS.DEFINE_MODULE" in out.read_text()


def test_ingest_missing_file_raises(tmp_path):
    validation = _make_validation(tmp_path)
    with pytest.raises(ManualError, match="No saved output"):
        ingest(tmp_path, validation, substages=["sql"])


def test_ingest_runs_wall_secret_scan(tmp_path):
    validation = _make_validation(tmp_path)
    # Embed an actual secret value → THE WALL must reject it.
    out = tmp_path / "generated/ords/student_download_v1_module.sql"
    out.parent.mkdir(parents=True)
    out.write_text(FAKE_ORDS.replace("END;", "-- test-oracle-pass-xq9\nEND;"))

    from pipeline.stages.generate import GenerateError
    with pytest.raises(GenerateError, match="value of secret"):
        ingest(tmp_path, validation, substages=["sql"])


def test_ingest_rejects_malformed_mule_xml(tmp_path):
    validation = _make_validation(tmp_path)
    out = tmp_path / "generated/mulesoft/student_download_v1_flow.xml"
    out.parent.mkdir(parents=True)
    out.write_text("<mule><flow></mule>")  # not well-formed

    from pipeline.stages.generate import GenerateError
    with pytest.raises(GenerateError, match="not well-formed XML"):
        ingest(tmp_path, validation, substages=["mulesoft"])


def test_ingest_snapshots_prior_version_on_reingest(tmp_path):
    validation = _make_validation(tmp_path)
    out = tmp_path / "generated/ords/student_download_v1_module.sql"
    out.parent.mkdir(parents=True)
    out.write_text(FAKE_ORDS)

    with patch("pipeline.stages.manual.gitops.commit_back", return_value=True):
        ingest(tmp_path, validation, substages=["sql"])
        # second ingest (e.g. regenerated via Copilot) archives the first
        out.write_text(FAKE_ORDS.replace("v1", "v1 -- rev2"))
        ingest(tmp_path, validation, substages=["sql"])

    lock = read_lockfile(tmp_path)
    assert len(lock.stage_history.get("sql", [])) == 1


def test_ingest_no_commit_flag(tmp_path):
    validation = _make_validation(tmp_path)
    out = tmp_path / "generated/ords/student_download_v1_module.sql"
    out.parent.mkdir(parents=True)
    out.write_text(FAKE_ORDS)

    outcomes = ingest(tmp_path, validation, substages=["sql"], commit=False)
    assert outcomes[0].committed is False
    # lockfile still updated even without commit
    lock = read_lockfile(tmp_path)
    assert "ords" in lock.artifacts


def test_prompt_then_ingest_round_trip(tmp_path):
    """Full manual loop: write prompt, 'paste' a reply, ingest it."""
    validation = _make_validation(tmp_path)

    prompts = write_prompts(tmp_path, validation, substages=["sql"])
    # write_prompts pre-creates the output dir; human just saves the file.
    (tmp_path / prompts[0].output_path).write_text(FAKE_ORDS)

    with patch("pipeline.stages.manual.gitops.commit_back", return_value=True):
        ingest(tmp_path, validation, substages=["sql"])

    lock = read_lockfile(tmp_path)
    assert lock.substages["sql"].status == "done"
