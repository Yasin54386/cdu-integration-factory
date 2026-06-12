"""Deploy stage: generate-if-stale, MuleSoft routing by connection type,
lockfile `deployed` facts. Deployers and git are mocked."""

from __future__ import annotations

import pytest

from pipeline.core import gitops
from pipeline.core.lockfile import read_lockfile
from pipeline.stages import deploy as deploy_stage
from pipeline.stages import generate as gen
from pipeline.stages.validate import validate
from tests.test_generate import FAKE_OUTPUTS


@pytest.fixture
def deploy_ready(factory_repo, monkeypatch):
    """Example job with mode: deploy, generation and git mocked."""
    intent_path = factory_repo / "job" / "intent.md"
    intent_path.write_text(
        intent_path.read_text().replace("mode: generate", "mode: deploy")
    )
    monkeypatch.setattr(
        gen, "invoke_copilot",
        lambda prompt: next(
            out for art, out in FAKE_OUTPUTS.items()
            if {"ords": "ORDS REST module", "mulesoft": "MuleSoft 4 flow",
                "tests": "pytest test file"}[art] in prompt
        ),
    )
    monkeypatch.setattr(gitops, "commit_back", lambda *a, **k: True)
    monkeypatch.setattr(
        deploy_stage.ords_deployer, "deploy_module",
        lambda oracle, path, job: f"/ords/cdu/{job}/",
    )
    result = validate(factory_repo)
    assert result.ok, result.errors
    return factory_repo, result


def test_git_repo_connection_routes_to_git_handoff(deploy_ready, monkeypatch):
    repo, result = deploy_ready
    calls = {}

    def fake_deliver(conn, path, job, delivery):
        calls["conn_type"] = conn.get("type")
        calls["provider"] = conn.get("provider")
        calls["delivery"] = delivery
        return {"mulesoft_repo": "cdu-integration/cdu-student-download-v1",
                "mulesoft_branch": f"cdu/{job}", "mulesoft_commit": "abc123",
                "mulesoft_url": "https://github.com/cdu-integration/cdu-student-download-v1"}

    def must_not_run(*a, **k):
        raise AssertionError("anypoint deployer must not run for git_repo connections")

    monkeypatch.setattr(deploy_stage.mule_git, "deliver", fake_deliver)
    monkeypatch.setattr(deploy_stage.mulesoft_deployer, "deploy_app", must_not_run)

    deployed = deploy_stage.deploy(repo, result, run_id="test")

    # example intent uses mule_repo_dev (type git_repo, provider github)
    assert calls["conn_type"] == "git_repo"
    assert calls["provider"] == "github"
    assert calls["delivery"] is None  # example has no mulesoft_delivery block
    assert deployed["ords_endpoint"] == "/ords/cdu/student_download_v1/"
    assert deployed["staging_table"] == "STG_STUDENT_DOWNLOAD_V1"
    assert deployed["mulesoft_branch"] == "cdu/student_download_v1"
    assert "mulesoft_app" not in deployed

    lock = read_lockfile(repo)
    assert lock.deployed["mulesoft_repo"] == "cdu-integration/cdu-student-download-v1"


def test_anypoint_connection_routes_to_anypoint_deployer(deploy_ready, monkeypatch):
    repo, result = deploy_ready
    intent_path = repo / "job" / "intent.md"
    intent_path.write_text(
        intent_path.read_text().replace("mulesoft: mule_repo_dev", "mulesoft: mule_dev")
    )
    result = validate(repo)
    assert result.ok

    def must_not_run(*a, **k):
        raise AssertionError("git handoff must not run for anypoint connections")

    monkeypatch.setattr(deploy_stage.mule_git, "deliver", must_not_run)
    monkeypatch.setattr(
        deploy_stage.mulesoft_deployer, "deploy_app",
        lambda conn, path, job: f"cdu-{job.replace('_', '-')}",
    )

    deployed = deploy_stage.deploy(repo, result, run_id="test")
    assert deployed["mulesoft_app"] == "cdu-student-download-v1"
    assert "mulesoft_repo" not in deployed


def test_deploy_generates_first_when_no_lockfile(deploy_ready, monkeypatch):
    repo, result = deploy_ready
    monkeypatch.setattr(
        deploy_stage.mule_git, "deliver",
        lambda conn, path, job, delivery: {"mulesoft_repo": "x/y",
                                           "mulesoft_branch": "b",
                                           "mulesoft_commit": "c",
                                           "mulesoft_url": "u"},
    )
    assert read_lockfile(repo) is None
    deploy_stage.deploy(repo, result, run_id="test")
    lock = read_lockfile(repo)
    assert set(lock.artifacts) == {"ords", "mulesoft", "tests"}
    assert (repo / lock.artifacts["mulesoft"].path).is_file()
