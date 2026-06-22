"""Task 1: bootstrap — validate_name and start_integration logic."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from pipeline.stages.bootstrap import (
    BootstrapError,
    PLAIN_TEXT_INTENT_REL,
    start_integration,
    validate_name,
)


# ── validate_name ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "student_download_v1",
    "abc",
    "a12",
    "a" * 41,
    "my_job",
])
def test_validate_name_accepts_valid(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("bad", [
    "AB",           # uppercase
    "1abc",         # starts with digit
    "ab",           # too short (< 3)
    "a" * 42,       # too long (> 41)
    "my job",       # space
    "my-job",       # hyphen → converted to _ then validated; "my_job" is fine,
                    # but test the original transforms pass through validate_name
])
def test_validate_name_rejects_invalid(bad):
    # "my-job" becomes "my_job" after normalisation — that should PASS, not fail.
    # Only the truly invalid ones should raise.
    if bad == "my-job":
        assert validate_name(bad) == "my_job"
    else:
        with pytest.raises(BootstrapError, match="valid integration name"):
            validate_name(bad)


def test_validate_name_strips_and_lowercases():
    assert validate_name("  Student_Download_V1  ") == "student_download_v1"


# ── start_integration (git calls mocked) ─────────────────────────────────────

def _make_git_mock(current_branch="main", local_exists="", remote_exists=""):
    """Return a side_effect function that stubs git subcommand output."""
    def _git_side_effect(cmd, **kwargs):
        args = cmd[1:]  # strip "git"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        if args[0] == "rev-parse":
            mock_result.stdout = current_branch + "\n"
        elif args[0] == "branch":
            mock_result.stdout = local_exists + "\n"
        elif args[0] == "ls-remote":
            mock_result.stdout = remote_exists + "\n"
        else:
            mock_result.stdout = "\n"
        return mock_result

    return _git_side_effect


def test_start_integration_creates_branch_and_pushes(tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stdout = "main\n" if "rev-parse" in cmd else "\n"
        result.stderr = ""
        return result

    with patch("pipeline.stages.bootstrap.subprocess.run", side_effect=fake_run):
        facts = start_integration(tmp_path, "my_job_v1")

    assert facts["branch"] == "feature/my_job_v1"
    assert facts["name"] == "my_job_v1"
    assert facts["base_branch"] == "main"
    assert facts["has_plain_text_intent"] is False

    # checkout -b and push -u must both appear
    issued = [" ".join(c) for c in calls]
    assert any("checkout -b feature/my_job_v1" in c for c in issued)
    assert any("push -u origin feature/my_job_v1" in c for c in issued)


def test_start_integration_detects_local_branch_conflict(tmp_path):
    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        if "rev-parse" in cmd:
            result.stdout = "main\n"
        elif cmd[1:4] == ["branch", "--list", "feature/my_job_v1"]:
            result.stdout = "  feature/my_job_v1\n"  # already exists
        else:
            result.stdout = "\n"
        return result

    with patch("pipeline.stages.bootstrap.subprocess.run", side_effect=fake_run):
        with pytest.raises(BootstrapError, match="already exists locally"):
            start_integration(tmp_path, "my_job_v1")


def test_start_integration_detects_remote_branch_conflict(tmp_path):
    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        if "rev-parse" in cmd:
            result.stdout = "main\n"
        elif "branch" in cmd and "--list" in cmd:
            result.stdout = "\n"  # no local conflict
        elif "ls-remote" in cmd:
            result.stdout = "abc123\trefs/heads/feature/my_job_v1\n"
        else:
            result.stdout = "\n"
        return result

    with patch("pipeline.stages.bootstrap.subprocess.run", side_effect=fake_run):
        with pytest.raises(BootstrapError, match="already exists on origin"):
            start_integration(tmp_path, "my_job_v1")


def test_start_integration_detects_plain_text_intent(tmp_path):
    plain = tmp_path / PLAIN_TEXT_INTENT_REL
    plain.parent.mkdir(parents=True)
    plain.write_text("Extract all students nightly to SFTP.")

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = "main\n" if "rev-parse" in cmd else "\n"
        return result

    with patch("pipeline.stages.bootstrap.subprocess.run", side_effect=fake_run):
        facts = start_integration(tmp_path, "my_job_v1")

    assert facts["has_plain_text_intent"] is True
    assert "plain_text_intent.txt" in facts["plain_text_path"]


def test_start_integration_git_failure_raises(tmp_path):
    def fake_run(cmd, **kwargs):
        result = MagicMock()
        if "rev-parse" in cmd:
            result.returncode = 0
            result.stdout = "main\n"
            result.stderr = ""
        elif "push" in cmd:
            result.returncode = 1
            result.stdout = ""
            result.stderr = "remote: Permission denied"
        else:
            result.returncode = 0
            result.stdout = "\n"
            result.stderr = ""
        return result

    with patch("pipeline.stages.bootstrap.subprocess.run", side_effect=fake_run):
        with pytest.raises(BootstrapError, match="push failed"):
            start_integration(tmp_path, "my_job_v1")
