"""Task 3: sub-stage run orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.core.lockfile import (
    SUBSTAGE_TO_ARTIFACT,
    SUBSTAGES,
    Lockfile,
    SubstageRecord,
    read_lockfile,
    write_lockfile,
)
from pipeline.stages.run import RunError, SubstageOutcome, resolve_substages, run


# ── resolve_substages ─────────────────────────────────────────────────────────

def test_resolve_substages_none_returns_all():
    assert resolve_substages(None) == list(SUBSTAGES)


def test_resolve_substages_preserves_canonical_order():
    # even if caller passes them reversed
    assert resolve_substages(["tests", "sql"]) == ["sql", "tests"]


def test_resolve_substages_single():
    assert resolve_substages(["mulesoft"]) == ["mulesoft"]


def test_resolve_substages_rejects_unknown():
    with pytest.raises(RunError, match="Unknown sub-stage"):
        resolve_substages(["sql", "oracle"])


# ── lockfile SubstageRecord round-trip ────────────────────────────────────────

def test_substage_record_round_trip(tmp_path):
    lock = Lockfile(job_name="my_job")
    lock.substages["sql"] = SubstageRecord(
        status="done",
        input_hash="sha256:abc",
        generated_at="2026-01-01T00:00:00Z",
        deployed_at="2026-01-01T00:01:00Z",
    )
    write_lockfile(tmp_path, lock)
    reloaded = read_lockfile(tmp_path)
    assert reloaded.substages["sql"].status == "done"
    assert reloaded.substages["sql"].deployed_at == "2026-01-01T00:01:00Z"


def test_old_lockfile_without_substages_loads_cleanly(tmp_path):
    """Backwards compat: existing lockfiles without substages field still load."""
    old = {
        "schema_version": 1,
        "job_name": "old_job",
        "artifacts": {},
    }
    (tmp_path / ".cdu-lock.json").write_text(json.dumps(old))
    lock = read_lockfile(tmp_path)
    assert lock.substages == {}


# ── run() end-to-end (generate mocked) ───────────────────────────────────────

def _make_validation(tmp_path: Path, mode: str = "generate"):
    """Minimal ValidationResult from the example job."""
    import shutil
    from pipeline.stages.validate import validate

    root = Path(__file__).parent.parent
    shutil.copy(root / "connections.yaml", tmp_path / "connections.yaml")
    shutil.copytree(root / "prompts", tmp_path / "prompts")
    shutil.copytree(root / "examples" / "student_download", tmp_path / "job")
    for sub in ("specs", "mappings", "tests"):
        (tmp_path / "job" / sub).mkdir(exist_ok=True)

    if mode == "deploy":
        intent_path = tmp_path / "job" / "intent.md"
        intent_path.write_text(
            intent_path.read_text().replace("mode: generate", "mode: deploy")
        )

    import os
    # Use long unique values so they never accidentally appear in fake generated content.
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
FAKE_MULE = '<?xml version="1.0"?><mule xmlns="http://www.mulesoft.org/schema/mule/core"><flow name="student_download_v1-main-flow"/></mule>'
FAKE_TESTS = "def test_placeholder(): pass\n"

# Prompt templates include a Role section identifying the artifact type.
def _fake_copilot(prompt: str) -> str:
    if "ORDS REST module" in prompt:
        return FAKE_ORDS
    if "MuleSoft" in prompt:
        return FAKE_MULE
    return FAKE_TESTS


def test_run_generate_mode_all_substages(tmp_path):
    validation = _make_validation(tmp_path, mode="generate")

    with patch("pipeline.stages.run.invoke_copilot", side_effect=_fake_copilot), \
         patch("pipeline.stages.run.gitops.commit_back", return_value=True), \
         patch("pipeline.stages.run.gitops.post_pr_comment", return_value=False), \
         patch("pipeline.stages.run.gitops.write_job_summary"):
        outcome = run(tmp_path, validation)

    assert outcome.mode == "generate"
    names = [o.name for o in outcome.outcomes]
    assert names == ["sql", "mulesoft", "tests"]

    generated = [o for o in outcome.outcomes if o.generated]
    assert len(generated) == 3

    skipped = [o for o in outcome.outcomes if o.skipped]
    assert skipped == []

    deployed = [o for o in outcome.outcomes if o.deployed]
    assert deployed == []  # generate mode: no deployment

    lock = read_lockfile(tmp_path)
    assert "ords" in lock.artifacts
    assert "mulesoft" in lock.artifacts
    assert "tests" in lock.artifacts


def test_run_generate_mode_sql_only(tmp_path):
    validation = _make_validation(tmp_path, mode="generate")

    with patch("pipeline.stages.run.invoke_copilot", side_effect=_fake_copilot), \
         patch("pipeline.stages.run.gitops.commit_back", return_value=True):
        outcome = run(tmp_path, validation, substages=["sql"])

    assert [o.name for o in outcome.outcomes] == ["sql"]
    assert outcome.outcomes[0].generated is True
    assert outcome.outcomes[0].deployed is False

    lock = read_lockfile(tmp_path)
    assert "ords" in lock.artifacts
    assert "mulesoft" not in lock.artifacts


def test_run_skips_clean_artifact_on_second_run(tmp_path):
    validation = _make_validation(tmp_path, mode="generate")

    with patch("pipeline.stages.run.invoke_copilot", side_effect=_fake_copilot), \
         patch("pipeline.stages.run.gitops.commit_back", return_value=True):
        run(tmp_path, validation, substages=["sql"])
        # second run: same inputs → sql should be skipped
        outcome2 = run(tmp_path, validation, substages=["sql"])

    assert outcome2.outcomes[0].skipped is True
    assert outcome2.outcomes[0].generated is False


def test_run_tests_substage_skipped_in_generate_mode_when_clean(tmp_path):
    validation = _make_validation(tmp_path, mode="generate")

    # first run: generate tests
    with patch("pipeline.stages.run.invoke_copilot", return_value=FAKE_TESTS), \
         patch("pipeline.stages.run.gitops.commit_back", return_value=True):
        run(tmp_path, validation, substages=["tests"])

    # second run: tests not stale + mode=generate → skipped
    with patch("pipeline.stages.run.invoke_copilot", return_value=FAKE_TESTS), \
         patch("pipeline.stages.run.gitops.commit_back", return_value=True):
        outcome2 = run(tmp_path, validation, substages=["tests"])

    assert outcome2.outcomes[0].skipped is True


def test_run_tests_substage_always_runs_in_deploy_mode(tmp_path):
    validation = _make_validation(tmp_path, mode="deploy")

    # Pre-populate tests artifact so it's not stale
    lock = Lockfile(job_name="student_download_v1", combined_input_hash="")
    test_file = tmp_path / "generated" / "tests" / "test_student_download_v1.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_x(): pass\n")
    lock.artifacts["tests"] = __import__(
        "pipeline.core.lockfile", fromlist=["ArtifactRecord"]
    ).ArtifactRecord(
        path="generated/tests/test_student_download_v1.py",
        input_hash_at_gen="sha256:abc",
        generated_at="2026-01-01T00:00:00Z",
    )
    # Set combined hash so it matches → tests not stale
    from pipeline.core.hashing import combined_hash, hash_inputs
    new_hashes = hash_inputs(tmp_path, validation.intent)
    lock.input_hashes = new_hashes
    lock.combined_input_hash = combined_hash(new_hashes)
    lock.intent_snapshot = validation.raw_intent
    write_lockfile(tmp_path, lock)

    pytest_result = MagicMock()
    pytest_result.returncode = 0
    pytest_result.stdout = "1 passed"

    with patch("pipeline.stages.run.subprocess.run", return_value=pytest_result), \
         patch("pipeline.stages.run.gitops.commit_back", return_value=True), \
         patch("pipeline.stages.run.gitops.post_pr_comment", return_value=False), \
         patch("pipeline.stages.run.gitops.write_job_summary"):
        outcome = run(tmp_path, validation, substages=["tests"])

    assert outcome.outcomes[0].deployed is True
    assert outcome.outcomes[0].test_result == "pass"
    assert outcome.outcomes[0].skipped is False


def test_run_raises_on_test_failure(tmp_path):
    validation = _make_validation(tmp_path, mode="deploy")

    pytest_result = MagicMock()
    pytest_result.returncode = 1
    pytest_result.stdout = "1 failed"

    # Pre-write tests artifact
    test_file = tmp_path / "generated" / "tests" / "test_student_download_v1.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_x(): assert False\n")

    with patch("pipeline.stages.run.invoke_copilot", return_value=FAKE_TESTS), \
         patch("pipeline.stages.run.subprocess.run", return_value=pytest_result), \
         patch("pipeline.stages.run.gitops.commit_back", return_value=True), \
         patch("pipeline.stages.run.gitops.post_pr_comment", return_value=False), \
         patch("pipeline.stages.run.gitops.write_job_summary"):
        with pytest.raises(RunError, match="FAILED"):
            run(tmp_path, validation, substages=["tests"])


def test_run_deploy_mode_runs_all_substages(tmp_path):
    """In deploy mode, all sub-stages generate AND deploy/run."""
    validation = _make_validation(tmp_path, mode="deploy")

    pytest_result = MagicMock()
    pytest_result.returncode = 0
    pytest_result.stdout = "1 passed"

    with patch("pipeline.stages.run.invoke_copilot", side_effect=_fake_copilot), \
         patch("pipeline.stages.run.ords_deployer.deploy_module", return_value="/ords/cdu/student_download_v1/"), \
         patch("pipeline.stages.run.mule_git.deliver", return_value={"mulesoft_repo": "org/repo", "mulesoft_branch": "cdu/x", "mulesoft_commit": "abc", "mulesoft_url": "https://github.com/org/repo"}), \
         patch("pipeline.stages.run.subprocess.run", return_value=pytest_result), \
         patch("pipeline.stages.run.gitops.commit_back", return_value=True), \
         patch("pipeline.stages.run.gitops.post_pr_comment", return_value=False), \
         patch("pipeline.stages.run.gitops.write_job_summary"):
        outcome = run(tmp_path, validation)

    assert outcome.mode == "deploy"
    by_name = {o.name: o for o in outcome.outcomes}
    assert by_name["sql"].deployed is True
    assert by_name["mulesoft"].deployed is True
    assert by_name["tests"].test_result == "pass"

    lock = read_lockfile(tmp_path)
    assert lock.substages["sql"].status == "done"
    assert lock.substages["mulesoft"].status == "done"
    assert lock.substages["tests"].test_result == "pass"
