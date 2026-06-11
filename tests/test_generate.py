"""Generate-stage units that need no Copilot/network: prompt assembly,
fence stripping, sanity checks, secret wall, skip/regen wiring."""

from __future__ import annotations

import pytest

from pipeline.stages import generate as gen
from pipeline.stages.validate import validate

FAKE_OUTPUTS = {
    "ords": (
        "BEGIN\n  ORDS.DEFINE_MODULE(p_module_name => 'student_download_v1');\n"
        "END;\n/\nCOMMIT;\n"
    ),
    "mulesoft": "<mule><flow name='student_download_v1-main-flow'/></mule>",
    "tests": "def test_placeholder():\n    assert True\n",
}


@pytest.fixture
def validated(factory_repo):
    result = validate(factory_repo)
    assert result.ok
    return factory_repo, result


def _patch_copilot(monkeypatch):
    calls = []

    def fake_invoke(prompt: str) -> str:
        for artifact in FAKE_OUTPUTS:
            if f"generated/{artifact}" in prompt or _marker(artifact) in prompt:
                calls.append(artifact)
                return FAKE_OUTPUTS[artifact]
        raise AssertionError("could not infer artifact from prompt")

    def _marker(artifact: str) -> str:
        return {
            "ords": "ORDS REST module",
            "mulesoft": "MuleSoft 4 flow",
            "tests": "pytest test file",
        }[artifact]

    monkeypatch.setattr(gen, "invoke_copilot", fake_invoke)
    return calls


def test_full_generate_writes_artifacts_and_lockfile(validated, monkeypatch):
    repo, result = validated
    calls = _patch_copilot(monkeypatch)
    outcome = gen.generate(repo, result, run_id="test-run", commit=False)
    assert sorted(outcome.regenerated) == ["mulesoft", "ords", "tests"]
    assert outcome.skipped == []
    assert sorted(calls) == ["mulesoft", "ords", "tests"]
    assert (repo / "generated/ords/student_download_v1_module.sql").is_file()
    assert (repo / "generated/mulesoft/student_download_v1_flow.xml").is_file()
    assert (repo / "generated/tests/test_student_download_v1.py").is_file()
    assert (repo / ".cdu-lock.json").is_file()


def test_second_generate_skips_everything(validated, monkeypatch):
    repo, result = validated
    _patch_copilot(monkeypatch)
    gen.generate(repo, result, run_id="run1", commit=False)

    def boom(prompt):
        raise AssertionError("copilot must not be invoked when nothing changed")

    monkeypatch.setattr(gen, "invoke_copilot", boom)
    outcome = gen.generate(repo, result, run_id="run2", commit=False)
    assert outcome.regenerated == []
    assert sorted(outcome.skipped) == ["mulesoft", "ords", "tests"]


def test_sql_change_regenerates_only_ords_and_tests(validated, monkeypatch):
    repo, result = validated
    _patch_copilot(monkeypatch)
    gen.generate(repo, result, run_id="run1", commit=False)
    (repo / "job/sql/export_query.sql").write_text("SELECT 1 FROM dual;")
    result2 = validate(repo)
    outcome = gen.generate(repo, result2, run_id="run2", commit=False)
    assert sorted(outcome.regenerated) == ["ords", "tests"]
    assert outcome.skipped == ["mulesoft"]


def test_strip_code_fences():
    fenced = "```sql\nSELECT 1;\n```"
    assert gen.strip_code_fences(fenced) == "SELECT 1;"
    plain = "SELECT 1;"
    assert gen.strip_code_fences(plain) == plain


def test_ords_sanity_check_requires_define_and_job_name(validated):
    repo, result = validated
    with pytest.raises(gen.GenerateError, match="ORDS.DEFINE_"):
        gen.sanity_check(repo, "ords", "SELECT 1;", result.intent)
    with pytest.raises(gen.GenerateError, match="job_name"):
        gen.sanity_check(repo, "ords", "ORDS.DEFINE_MODULE(...)", result.intent)


def test_mulesoft_sanity_check_requires_well_formed_xml(validated):
    repo, result = validated
    with pytest.raises(gen.GenerateError, match="well-formed XML"):
        gen.sanity_check(repo, "mulesoft", "<mule><unclosed>", result.intent)
    gen.sanity_check(repo, "mulesoft", "<mule/>", result.intent)


def test_secret_value_in_output_is_refused(validated):
    repo, result = validated
    leaked = "<mule>password=test-password</mule>"  # value of ORACLE_DEV_PASSWORD
    with pytest.raises(gen.GenerateError, match="ORACLE_DEV_PASSWORD"):
        gen.sanity_check(repo, "mulesoft", leaked, result.intent)


def test_prompt_contains_no_secret_values(validated, secrets_env):
    repo, result = validated
    prompt = gen.assemble_prompt(
        repo, "ords", result.intent, result.raw_intent, result.body_notes
    )
    for value in secrets_env.values():
        assert value not in prompt
    assert "student_download_v1" in prompt
    assert "load_staging.sql" in prompt
