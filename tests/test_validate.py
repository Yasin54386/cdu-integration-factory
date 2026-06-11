"""M2 done-criteria: the example passes; each broken-fixture case fails
with the right message (spec §5 rules)."""

from __future__ import annotations

from pathlib import Path

from pipeline.core.intent import load_intent
from pipeline.stages.validate import validate


def _edit_intent(repo: Path, old: str, new: str) -> None:
    path = repo / "job" / "intent.md"
    text = path.read_text()
    assert old in text, f"fixture edit target not found: {old!r}"
    path.write_text(text.replace(old, new))


def test_example_intent_parses_into_model():
    intent, raw, body = load_intent(
        Path(__file__).resolve().parent.parent
        / "examples" / "student_download" / "intent.md"
    )
    assert intent.job_name == "student_download_v1"
    assert intent.mode == "generate"
    assert intent.testing is not None
    assert "Nightly extract" in body


def test_example_job_passes_validation(factory_repo):
    result = validate(factory_repo)
    assert result.errors == []
    assert result.ok
    assert result.intent.job_name == "student_download_v1"


def test_missing_intent_file_fails(factory_repo):
    (factory_repo / "job" / "intent.md").unlink()
    result = validate(factory_repo)
    assert not result.ok
    assert "not found" in result.errors[0]


def test_no_front_matter_fails(factory_repo):
    (factory_repo / "job" / "intent.md").write_text("just some markdown\n")
    result = validate(factory_repo)
    assert not result.ok
    assert "front-matter" in result.errors[0]


def test_bad_job_name_fails(factory_repo):
    _edit_intent(factory_repo, "job_name: student_download_v1",
                 "job_name: Student-Download!")
    result = validate(factory_repo)
    assert not result.ok
    assert "job_name" in result.errors[0]


def test_bad_mode_fails(factory_repo):
    _edit_intent(factory_repo, "mode: generate", "mode: yolo")
    result = validate(factory_repo)
    assert not result.ok
    assert "mode" in result.errors[0]


def test_unknown_role_fails(factory_repo):
    _edit_intent(factory_repo, "role: staging_load", "role: mystery_role")
    result = validate(factory_repo)
    assert not result.ok
    assert "unknown role 'mystery_role'" in result.errors[0]


def test_missing_referenced_file_fails_with_exact_path(factory_repo):
    (factory_repo / "job" / "sql" / "export_query.sql").unlink()
    result = validate(factory_repo)
    assert not result.ok
    assert any("job/sql/export_query.sql" in e for e in result.errors)


def test_unreferenced_file_warns_but_passes(factory_repo):
    (factory_repo / "job" / "sql" / "orphan.sql").write_text("SELECT 1 FROM dual;")
    result = validate(factory_repo)
    assert result.ok
    assert any("job/sql/orphan.sql" in w for w in result.warnings)
    reports = list((factory_repo / "reports").glob("validate_*.md"))
    assert reports, "warnings must be written to reports/validate_<ts>.md"
    assert "orphan.sql" in reports[0].read_text()


def test_unknown_connection_fails(factory_repo):
    _edit_intent(factory_repo, "connection: sftp_dev", "connection: sftp_prod")
    result = validate(factory_repo)
    assert not result.ok
    assert any("'sftp_prod'" in e and "connections.yaml" in e for e in result.errors)


def test_missing_secret_fails_with_name_only(factory_repo, monkeypatch):
    monkeypatch.delenv("ORACLE_DEV_PASSWORD")
    result = validate(factory_repo)
    assert not result.ok
    message = next(e for e in result.errors if "ORACLE_DEV_PASSWORD" in e)
    assert message == (
        "Secret ORACLE_DEV_PASSWORD not configured in repo Settings → "
        "Secrets → Actions"
    )


def test_multiple_problems_reported_together(factory_repo, monkeypatch):
    (factory_repo / "job" / "sql" / "export_query.sql").unlink()
    monkeypatch.delenv("SFTP_DEV_USER")
    result = validate(factory_repo)
    assert len(result.errors) >= 2
