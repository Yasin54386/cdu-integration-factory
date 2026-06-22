"""CDU Integration Factory CLI (spec D8).

Subcommands mirror the pipeline stages for local/CI parity:
    python pipeline/cdu.py start-integration
    python pipeline/cdu.py validate | generate | deploy | test | read-mode
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import typer

from pipeline.core.intent import IntentError, load_intent
from pipeline.stages.validate import ValidationResult, validate as run_validate

app = typer.Typer(add_completion=False, help=__doc__)


def _run_id() -> str:
    run = os.environ.get("GITHUB_RUN_ID")
    return f"gh-run-{run}" if run else "local"


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run = os.environ.get("GITHUB_RUN_ID")
    return f"{server}/{repo}/actions/runs/{run}" if server and repo and run else ""


def _validated() -> ValidationResult:
    result = run_validate(REPO_ROOT)
    for warning in result.warnings:
        typer.secho(f"WARNING: {warning}", fg=typer.colors.YELLOW, err=True)
    if not result.ok:
        for error in result.errors:
            typer.secho(f"ERROR: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    return result


@app.command()
def validate() -> None:
    """Validate job/intent.md, referenced files, connections and secrets."""
    result = _validated()
    typer.secho(
        f"validate OK — job '{result.intent.job_name}', mode '{result.intent.mode}'"
        + (f", {len(result.warnings)} warning(s)" if result.warnings else ""),
        fg=typer.colors.GREEN,
    )


@app.command()
def generate() -> None:
    """Regenerate stale artifacts via Copilot CLI; commit back [skip ci]."""
    from pipeline.stages.generate import generate as run_generate

    result = _validated()
    outcome = run_generate(REPO_ROOT, result, run_id=_run_id())
    typer.echo(f"regenerated: {outcome.regenerated or 'nothing'}")
    typer.echo(f"skipped (inputs unchanged): {outcome.skipped or 'nothing'}")


@app.command()
def deploy() -> None:
    """Deploy generated artifacts to the DEV environment (mode: deploy only)."""
    from pipeline.stages.deploy import deploy as run_deploy

    result = _validated()
    if result.intent.mode != "deploy":
        typer.secho(
            "intent mode is 'generate' — set `mode: deploy` in job/intent.md "
            "to cross the human gate (spec D6)",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)
    deployed = run_deploy(REPO_ROOT, result, run_id=_run_id())
    for key, value in deployed.items():
        typer.echo(f"{key}: {value}")


@app.command()
def test() -> None:
    """Run the job's tests against the deployed integration; write report."""
    from pipeline.stages.test import run_tests

    result = _validated()
    passed = run_tests(REPO_ROOT, result, run_url=_run_url())
    if not passed:
        raise typer.Exit(code=1)
    typer.secho("tests passed", fg=typer.colors.GREEN)


@app.command(name="read-mode")
def read_mode() -> None:
    """Print the intent's mode (generate|deploy) for workflow routing."""
    try:
        intent, _, _ = load_intent(REPO_ROOT / "job" / "intent.md")
    except IntentError as exc:
        typer.secho(f"ERROR: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.echo(intent.mode)


@app.command(name="start-integration")
def start_integration(
    name: Optional[str] = typer.Argument(
        None, help="Integration name, e.g. student_download_v2"
    ),
) -> None:
    """Bootstrap a new integration: create feature/<name>, push to origin."""
    from pipeline.stages.bootstrap import BootstrapError
    from pipeline.stages.bootstrap import start_integration as _start

    if name is None:
        name = typer.prompt("Integration name (e.g. student_download_v1)")

    try:
        facts = _start(REPO_ROOT, name)
    except BootstrapError as exc:
        typer.secho(f"ERROR: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho(
        f"Branch '{facts['branch']}' created from '{facts['base_branch']}' "
        "and pushed to origin.",
        fg=typer.colors.GREEN,
    )

    if facts["has_plain_text_intent"]:
        typer.secho(
            f"\nFound {facts['plain_text_path']}\n"
            "Run the next step to auto-draft job/intent.md from it:\n"
            "  python pipeline/cdu.py draft-intent",
            fg=typer.colors.CYAN,
        )
    else:
        typer.echo(
            "\nNext steps:\n"
            f"  Option A — describe your integration in plain English:\n"
            f"               mkdir -p job/docs\n"
            f"               edit job/docs/plain_text_intent.txt\n"
            f"               python pipeline/cdu.py draft-intent\n"
            f"\n"
            f"  Option B — fill in job/intent.md directly, then push to trigger\n"
            f"             the pipeline (or run: python pipeline/cdu.py run)"
        )


if __name__ == "__main__":
    app()
