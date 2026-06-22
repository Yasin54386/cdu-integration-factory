"""Plan stage: decide the right action per sub-stage from the intent."""

from __future__ import annotations

import shutil
from pathlib import Path

from pipeline.stages.plan import build_plan


_SQL_BLOCK = """  sql:
    - file: sql/load_staging.sql
      role: staging_load
    - file: sql/export_query.sql
      role: export
"""


def _make_validation(tmp_path: Path, mode: str = "generate", with_repo: bool = False,
                     no_sql: bool = False):
    import os
    from pipeline.stages.validate import validate

    root = Path(__file__).parent.parent
    shutil.copy(root / "connections.yaml", tmp_path / "connections.yaml")
    shutil.copytree(root / "prompts", tmp_path / "prompts")
    shutil.copytree(root / "examples" / "student_download", tmp_path / "job")
    for sub in ("specs", "mappings", "tests"):
        (tmp_path / "job" / sub).mkdir(exist_ok=True)

    intent_path = tmp_path / "job" / "intent.md"
    text = intent_path.read_text()
    if no_sql:
        text = text.replace(_SQL_BLOCK, "  sql: []\n")
    if mode == "deploy":
        text = text.replace("mode: generate", "mode: deploy")
    if with_repo:
        text = text.replace(
            "connections:",
            "mulesoft_delivery:\n  repo: existing-mule-app\n  branch: cdu/x\n\nconnections:",
        )
    intent_path.write_text(text)

    for k in ("ORACLE_DEV_USER", "ORACLE_DEV_PASSWORD", "MULE_REPO_TOKEN",
              "MULE_DEV_CLIENT_ID", "MULE_DEV_CLIENT_SECRET", "SFTP_DEV_USER",
              "SFTP_DEV_PRIVATE_KEY", "GH_PIPELINE_TOKEN"):
        os.environ[k] = f"test-{k.lower()}-xq9"
    return validate(tmp_path)


def _by_sub(plan):
    return {s.substage: s for s in plan.steps}


def test_first_run_generates_everything(tmp_path):
    plan = build_plan(tmp_path, _make_validation(tmp_path))
    steps = _by_sub(plan)
    assert steps["sql"].action == "generate"
    assert steps["mulesoft"].action == "generate-new-flow"  # no repo named
    assert steps["tests"].action == "generate"


def test_existing_repo_routes_mulesoft_to_workspace_edit(tmp_path):
    plan = build_plan(tmp_path, _make_validation(tmp_path, with_repo=True))
    steps = _by_sub(plan)
    assert steps["mulesoft"].action == "workspace-edit"
    assert "mule-checkout" in " ".join(steps["mulesoft"].commands)
    assert "mule-deliver" in " ".join(steps["mulesoft"].commands)


def test_deploy_mode_tests_always_run(tmp_path):
    plan = build_plan(tmp_path, _make_validation(tmp_path, mode="deploy"))
    steps = _by_sub(plan)
    assert steps["tests"].action == "run-tests"
    assert "run --sub tests" in " ".join(steps["tests"].commands)


def test_no_sql_job_skips_ords(tmp_path):
    """A job with no SQL sources validates and never generates ORDS."""
    validation = _make_validation(tmp_path, no_sql=True)
    assert validation.ok, validation.errors
    plan = build_plan(tmp_path, validation)
    steps = _by_sub(plan)
    assert steps["sql"].action == "skip"
    assert "no SQL" in steps["sql"].reason
    # mulesoft + tests still generate on a first run
    assert steps["mulesoft"].action in ("generate-new-flow", "workspace-edit")
    assert steps["tests"].action == "generate"


def test_plan_as_dict_shape(tmp_path):
    plan = build_plan(tmp_path, _make_validation(tmp_path))
    d = plan.as_dict()
    assert d["job_name"] == "student_download_v1"
    assert d["mode"] == "generate"
    assert {s["substage"] for s in d["steps"]} == {"sql", "mulesoft", "tests"}
    assert all("commands" in s for s in d["steps"])
