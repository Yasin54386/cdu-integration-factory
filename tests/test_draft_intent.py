"""Task 2: draft-intent — models_api wrapper and draft_intent stage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.core.models_api import ModelsAPIError, call as api_call, find_token
from pipeline.stages.draft_intent import (
    DraftIntentError,
    PLAIN_TEXT_INTENT_REL,
    _build_user_prompt,
    _discover_job_files,
    _validate_draft,
    draft_intent,
)


# ── models_api ────────────────────────────────────────────────────────────────

VALID_API_RESPONSE = json.dumps({
    "choices": [{"message": {"content": "some generated text"}}]
}).encode("utf-8")


def _mock_urlopen(body: bytes, status: int = 200):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.read.return_value = body
    ctx.status = status
    return ctx


def test_api_call_returns_content(monkeypatch):
    with patch("pipeline.core.models_api.urllib.request.urlopen",
               return_value=_mock_urlopen(VALID_API_RESPONSE)):
        result = api_call(
            system_prompt="You are helpful.",
            user_prompt="Hello",
            token="fake-token",
        )
    assert result == "some generated text"


def test_api_call_raises_on_empty_content(monkeypatch):
    body = json.dumps({"choices": [{"message": {"content": ""}}]}).encode()
    with patch("pipeline.core.models_api.urllib.request.urlopen",
               return_value=_mock_urlopen(body)):
        with pytest.raises(ModelsAPIError, match="empty content"):
            api_call(system_prompt="s", user_prompt="u", token="t")


def test_api_call_raises_on_http_error():
    import urllib.error

    exc = urllib.error.HTTPError(
        url="https://x", code=401, msg="Unauthorized",
        hdrs=None, fp=MagicMock(read=lambda: b"bad token"),
    )
    with patch("pipeline.core.models_api.urllib.request.urlopen", side_effect=exc):
        with pytest.raises(ModelsAPIError, match="HTTP 401"):
            api_call(system_prompt="s", user_prompt="u", token="bad")


def test_find_token_order(monkeypatch):
    monkeypatch.delenv("GH_PIPELINE_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("COPILOT_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert find_token() is None

    monkeypatch.setenv("GH_TOKEN", "tok-gh")
    assert find_token() == "tok-gh"

    monkeypatch.setenv("GITHUB_TOKEN", "tok-github")
    assert find_token() == "tok-github"   # higher priority

    monkeypatch.setenv("GH_PIPELINE_TOKEN", "tok-pipeline")
    assert find_token() == "tok-pipeline"  # highest priority


# ── draft_intent helpers ──────────────────────────────────────────────────────

def test_discover_job_files(tmp_path):
    (tmp_path / "job" / "sql").mkdir(parents=True)
    (tmp_path / "job" / "sql" / "load.sql").write_text("SELECT 1")
    (tmp_path / "job" / "specs").mkdir()
    (tmp_path / "job" / "specs" / ".gitkeep").write_text("")  # ignored
    files = _discover_job_files(tmp_path)
    assert files == ["sql/load.sql"]


def test_discover_job_files_empty(tmp_path):
    assert _discover_job_files(tmp_path) == []


def test_build_user_prompt_contains_all_sections():
    prompt = _build_user_prompt(
        description="Nightly student extract.",
        job_files=["sql/load.sql", "specs/brd.docx"],
        connections=["oracle_dev", "sftp_dev"],
    )
    assert "Nightly student extract" in prompt
    assert "sql/load.sql" in prompt
    assert "specs/brd.docx" in prompt
    assert "oracle_dev" in prompt
    assert "sftp_dev" in prompt


def test_validate_draft_accepts_valid():
    content = "---\njob_name: my_job\nmode: generate\n---\n## Notes\nok"
    _validate_draft(content)  # should not raise


def test_validate_draft_rejects_no_front_matter():
    with pytest.raises(DraftIntentError, match="valid front-matter"):
        _validate_draft("just plain text, no YAML block")


# ── draft_intent end-to-end (API mocked) ─────────────────────────────────────

VALID_INTENT_DRAFT = """\
---
job_name: student_download_v1
mode: generate
direction: download
sources:
  sql:
    - file: sql/load_staging.sql
      role: staging_load
destination:
  connection: sftp_dev
  path: /incoming/student/
  file_format: csv
  file_name_pattern: "student_{yyyymmdd}.csv"
connections:
  oracle: oracle_dev
  mulesoft: mule_repo_dev
---

## Notes

Nightly extract of active students.
"""


def _setup_repo(tmp_path: Path) -> Path:
    """Minimal repo fixture: connections.yaml + prompt template + plain text."""
    import shutil
    repo = tmp_path
    shutil.copy(
        Path(__file__).parent.parent / "connections.yaml",
        repo / "connections.yaml",
    )
    shutil.copytree(
        Path(__file__).parent.parent / "prompts",
        repo / "prompts",
    )
    docs_dir = repo / "job" / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "plain_text_intent.txt").write_text(
        "Nightly extract of active students to SFTP as CSV."
    )
    (repo / "job" / "sql").mkdir(parents=True)
    (repo / "job" / "sql" / "load_staging.sql").write_text("SELECT * FROM students")
    return repo


def _fake_api(*args, **kwargs) -> str:
    return VALID_INTENT_DRAFT


def test_draft_intent_writes_intent_md(tmp_path):
    repo = _setup_repo(tmp_path)
    with patch("pipeline.stages.draft_intent._call_api", side_effect=_fake_api):
        facts = draft_intent(repo, token="fake", commit=False)

    intent_path = repo / "job" / "intent.md"
    assert intent_path.is_file()
    assert "student_download_v1" in intent_path.read_text()
    assert facts["committed"] is False
    assert "sql/load_staging.sql" in facts["job_files_discovered"]


def test_draft_intent_strips_code_fences(tmp_path):
    repo = _setup_repo(tmp_path)
    fenced = f"```yaml\n{VALID_INTENT_DRAFT}\n```"

    with patch("pipeline.stages.draft_intent._call_api", return_value=fenced):
        draft_intent(repo, token="fake", commit=False)

    content = (repo / "job" / "intent.md").read_text()
    assert not content.startswith("```")
    assert "student_download_v1" in content


def test_draft_intent_raises_on_missing_plain_text(tmp_path):
    repo = _setup_repo(tmp_path)
    (repo / "job" / "docs" / "plain_text_intent.txt").unlink()
    with pytest.raises(DraftIntentError, match="not found"):
        draft_intent(repo, token="fake", commit=False)


def test_draft_intent_raises_on_empty_plain_text(tmp_path):
    repo = _setup_repo(tmp_path)
    (repo / "job" / "docs" / "plain_text_intent.txt").write_text("   ")
    with pytest.raises(DraftIntentError, match="empty"):
        draft_intent(repo, token="fake", commit=False)


def test_draft_intent_raises_if_api_returns_bad_front_matter(tmp_path):
    repo = _setup_repo(tmp_path)
    with patch("pipeline.stages.draft_intent._call_api", return_value="no yaml here at all"):
        with pytest.raises(DraftIntentError, match="valid front-matter"):
            draft_intent(repo, token="fake", commit=False)


def test_draft_intent_raises_on_api_error(tmp_path):
    repo = _setup_repo(tmp_path)
    with patch("pipeline.stages.draft_intent._call_api",
               side_effect=ModelsAPIError("HTTP 401")):
        with pytest.raises(DraftIntentError, match="API call failed"):
            draft_intent(repo, token="fake", commit=False)
