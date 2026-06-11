"""M3 done-criteria: hash/regen decisions match the §9 table for all
fixture scenarios."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.core.hashing import combined_hash, hash_file, hash_inputs
from pipeline.core.impact import decide_regeneration
from pipeline.core.lockfile import ArtifactRecord, Lockfile
from pipeline.stages.validate import validate


@pytest.fixture
def validated(factory_repo):
    result = validate(factory_repo)
    assert result.ok
    return factory_repo, result


def _lock_for_current_state(repo: Path, result) -> Lockfile:
    """A lockfile as the pipeline would have written after a full run."""
    hashes = hash_inputs(repo, result.intent)
    combined = combined_hash(hashes)
    artifacts = {}
    for name, rel in (
        ("ords", "generated/ords/student_download_v1_module.sql"),
        ("mulesoft", "generated/mulesoft/student_download_v1_flow.xml"),
        ("tests", "generated/tests/test_student_download_v1.py"),
    ):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"-- generated {name}\n")
        artifacts[name] = ArtifactRecord(
            path=rel, input_hash_at_gen=combined, generated_at="2026-06-11T00:00:00Z"
        )
    return Lockfile(
        job_name=result.intent.job_name,
        input_hashes=hashes,
        combined_input_hash=combined,
        intent_snapshot=result.raw_intent,
        artifacts=artifacts,
    )


def _decide(repo: Path, lock) -> set[str]:
    result = validate(repo)
    assert result.ok, result.errors
    hashes = hash_inputs(repo, result.intent)
    return decide_regeneration(repo, result.intent, result.raw_intent, hashes, lock)


def _edit_intent(repo: Path, old: str, new: str) -> None:
    path = repo / "job" / "intent.md"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_hash_file_format(factory_repo):
    digest = hash_file(factory_repo / "job" / "intent.md")
    assert digest.startswith("sha256:") and len(digest) == 7 + 64


def test_hash_inputs_covers_intent_and_referenced_files(validated):
    repo, result = validated
    hashes = hash_inputs(repo, result.intent)
    assert set(hashes) == {
        "job/intent.md",
        "job/sql/load_staging.sql",
        "job/sql/export_query.sql",
        "job/samples/expected_output.csv",
    }


def test_combined_hash_is_order_independent_and_change_sensitive(validated):
    repo, result = validated
    hashes = hash_inputs(repo, result.intent)
    assert combined_hash(hashes) == combined_hash(dict(reversed(list(hashes.items()))))
    (repo / "job" / "sql" / "export_query.sql").write_text("SELECT 2 FROM dual;")
    assert combined_hash(hash_inputs(repo, result.intent)) != combined_hash(hashes)


# ── §9 table scenarios ──────────────────────────────────────────────────────

def test_first_run_no_lockfile_regenerates_everything(validated):
    repo, _ = validated
    assert _decide(repo, None) == {"ords", "mulesoft", "tests"}


def test_nothing_changed_regenerates_nothing(validated):
    repo, result = validated
    lock = _lock_for_current_state(repo, result)
    assert _decide(repo, lock) == set()


def test_mode_change_only_regenerates_nothing(validated):
    repo, result = validated
    lock = _lock_for_current_state(repo, result)
    _edit_intent(repo, "mode: generate", "mode: deploy")
    assert _decide(repo, lock) == set()


def test_sql_file_change_regenerates_ords_and_tests_skips_mulesoft(validated):
    repo, result = validated
    lock = _lock_for_current_state(repo, result)
    (repo / "job" / "sql" / "load_staging.sql").write_text("SELECT 99 FROM dual;")
    assert _decide(repo, lock) == {"ords", "tests"}


def test_destination_change_regenerates_mulesoft_and_tests_skips_ords(validated):
    repo, result = validated
    lock = _lock_for_current_state(repo, result)
    _edit_intent(repo, "path: /incoming/student/", "path: /incoming/student_v2/")
    assert _decide(repo, lock) == {"mulesoft", "tests"}


def test_file_format_change_regenerates_mulesoft_and_tests(validated):
    repo, result = validated
    lock = _lock_for_current_state(repo, result)
    _edit_intent(repo, "file_format: csv", "file_format: json")
    _edit_intent(repo, "student_{yyyymmdd}.csv", "student_{yyyymmdd}.json")
    assert _decide(repo, lock) == {"mulesoft", "tests"}


def test_spec_file_change_regenerates_all(validated):
    repo, result = validated
    lock = _lock_for_current_state(repo, result)
    (repo / "job" / "specs" / "brd.txt").write_text("new business rule")
    _edit_intent(
        repo,
        "destination:",
        "  specs:\n    - file: specs/brd.txt\n      role: business_rules\ndestination:",
    )
    assert _decide(repo, lock) == {"ords", "mulesoft", "tests"}


def test_testing_block_change_regenerates_tests_only(validated):
    repo, result = validated
    lock = _lock_for_current_state(repo, result)
    _edit_intent(repo, "no null values in STUDENT_ID column",
                 "no null values in STUDENT_ID or COURSE_CODE columns")
    assert _decide(repo, lock) == {"tests"}


def test_job_tests_file_change_regenerates_tests_only(validated):
    repo, result = validated
    lock = _lock_for_current_state(repo, result)
    (repo / "job" / "tests" / "golden_output.csv").write_text("STUDENT_ID\n")
    assert _decide(repo, lock) == {"tests"}


def test_missing_generated_artifact_is_regenerated(validated):
    repo, result = validated
    lock = _lock_for_current_state(repo, result)
    (repo / "generated/mulesoft/student_download_v1_flow.xml").unlink()
    assert _decide(repo, lock) == {"mulesoft"}
