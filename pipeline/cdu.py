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


def _load_dotenv(repo_root: Path) -> None:
    """Load secret VALUES from a gitignored .env into the environment.

    Local convenience only: .env holds credential values (Oracle login, GitLab
    token, …) so they don't have to be re-exported each shell session. It is
    gitignored and MUST never be committed (THE WALL, spec §7). Real env vars
    and CI Actions Secrets always win — an existing os.environ entry is not
    overwritten — so CI (which sets real env vars, no .env) is unaffected.
    """
    env_path = repo_root / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(REPO_ROOT)

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


@app.command(name="run")
def run_cmd(
    sub: Optional[list[str]] = typer.Option(
        None, "--sub",
        help="Sub-stage(s) to run: sql, mulesoft, tests. Repeat for multiple. "
             "Omit to run all in order.",
    ),
) -> None:
    """Run sub-stages (sql → mulesoft → tests); respects mode and skips clean artifacts.

    Examples:
      cdu run                         # all sub-stages
      cdu run --sub sql               # ORDS only
      cdu run --sub sql --sub mulesoft  # two sub-stages
    """
    from pipeline.stages.run import RunError, SubstageOutcome
    from pipeline.stages.run import run as _run

    result = _validated()

    try:
        outcome = _run(
            REPO_ROOT, result,
            substages=list(sub) if sub else None,
            run_id=_run_id(),
            run_url=_run_url(),
        )
    except RunError as exc:
        typer.secho(f"ERROR: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    for o in outcome.outcomes:
        if o.skipped:
            typer.echo(f"  {o.name}: skipped (inputs unchanged)")
        else:
            parts = []
            if o.generated:
                parts.append("generated")
            if o.deployed:
                parts.append("deployed" if o.name != "tests" else
                             f"tested ({o.test_result})")
            typer.secho(f"  {o.name}: {', '.join(parts)}", fg=typer.colors.GREEN)


@app.command(name="plan")
def plan_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit the plan as JSON."),
) -> None:
    """Decide the right action per sub-stage from the intent (no model needed).

    Used by the unified /cdu Copilot command so one command can 'do the right
    thing': generate, skip clean work, edit an existing Mule repo, or scaffold a
    new flow — chosen automatically from the intent and lockfile state.
    """
    from pipeline.stages.plan import build_plan

    result = _validated()
    plan = build_plan(REPO_ROOT, result)

    if as_json:
        import json
        typer.echo(json.dumps(plan.as_dict(), indent=2))
        return

    typer.secho(f"Plan for '{plan.job_name}' (mode: {plan.mode}):\n",
                fg=typer.colors.CYAN)
    for step in plan.steps:
        colour = typer.colors.BRIGHT_BLACK if step.action == "skip" else typer.colors.GREEN
        typer.secho(f"  {step.substage:9s} → {step.action}", fg=colour)
        typer.echo(f"      {step.reason}")
        for cmd in step.commands:
            typer.echo(f"        {cmd}")
    typer.echo("\nRun the steps in order (or use the /cdu Copilot command).")


@app.command(name="prompt")
def prompt_cmd(
    sub: Optional[list[str]] = typer.Option(
        None, "--sub",
        help="Sub-stage(s) to prompt for: sql, mulesoft, tests. Repeat for "
             "multiple. Omit for all.",
    ),
) -> None:
    """Manual/Copilot mode: write the prompt file(s) to paste into Copilot Chat.

    No API call is made. For each sub-stage the assembled prompt is written to
    generated/.prompts/<sub>.prompt.md. Paste it into Copilot Chat, save the
    reply to the artifact path shown, then run `cdu ingest --sub <sub>`.
    """
    from pipeline.stages.manual import write_prompts

    result = _validated()
    outcomes = write_prompts(REPO_ROOT, result, substages=list(sub) if sub else None)

    typer.secho("Prompt file(s) written — paste each into GitHub Copilot Chat:\n",
                fg=typer.colors.CYAN)
    for o in outcomes:
        typer.echo(f"  sub-stage: {o.substage}")
        typer.echo(f"    prompt : {o.prompt_path}")
        typer.echo(f"    save to: {o.output_path}")
        typer.echo("")
    typer.echo(
        "Next: open each prompt file, copy from the marker into Copilot Chat,\n"
        "save Copilot's reply to the 'save to' path, then run:\n"
        "  python pipeline/cdu.py ingest" + ("".join(f" --sub {o.substage}" for o in outcomes))
    )


@app.command(name="prompt-targets")
def prompt_targets(
    as_json: bool = typer.Option(False, "--json", help="Emit the targets as JSON."),
) -> None:
    """List the per-job generator-prompt templates /cdu-generate-prompt authors.

    For each artifact this job needs (ORDS dropped when there are no SQL
    sources), shows the static default to base the tailoring on and the
    job/prompts/ path to write. Used by the /cdu-generate-prompt Copilot command.
    """
    from pipeline.stages.generate import prompt_template_targets

    result = _validated()
    targets = prompt_template_targets(REPO_ROOT, result.intent)

    if as_json:
        import json
        typer.echo(json.dumps(targets, indent=2))
        return

    typer.secho(
        f"Generator-prompt targets for '{result.intent.job_name}':\n",
        fg=typer.colors.CYAN,
    )
    for t in targets:
        state = "exists (will be overwritten)" if t["exists"] else "missing (will be created)"
        typer.secho(f"  {t['artifact']:9s} → {t['job_template']}", fg=typer.colors.GREEN)
        typer.echo(f"      base on: {t['default_template']}")
        typer.echo(f"      status : {state}")
    typer.echo(
        "\nAuthor each with the /cdu-generate-prompt Copilot command, then run\n"
        "the per-stage generate commands (they prefer job/prompts/ over the defaults)."
    )


@app.command(name="ingest")
def ingest_cmd(
    sub: Optional[list[str]] = typer.Option(
        None, "--sub",
        help="Sub-stage(s) to ingest: sql, mulesoft, tests. Repeat for "
             "multiple. Omit for all.",
    ),
    no_commit: bool = typer.Option(False, "--no-commit", help="Validate but do not git commit"),
) -> None:
    """Manual/Copilot mode: validate the saved Copilot reply, lock it, commit.

    Reads the artifact file you saved from Copilot, runs the same sanity and
    secret-scan checks as the API path, records it in the lockfile + version
    history, and commits [skip ci].
    """
    from pipeline.stages.manual import ManualError, ingest

    result = _validated()
    try:
        outcomes = ingest(
            REPO_ROOT, result,
            substages=list(sub) if sub else None,
            run_id=_run_id(),
            commit=not no_commit,
        )
    except ManualError as exc:
        typer.secho(f"ERROR: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    for o in outcomes:
        state = "committed" if o.committed else "validated (not committed)"
        typer.secho(f"  {o.substage}: {state} → {o.output_path}", fg=typer.colors.GREEN)
    typer.echo(
        "\nDeploy stays gated behind `mode: deploy` (D6). To deploy the "
        "ingested artifacts, set mode: deploy in job/intent.md and run:\n"
        "  python pipeline/cdu.py run"
    )


@app.command(name="draft-intent")
def draft_intent(
    model: str = typer.Option("gpt-4o-mini", help="GitHub Models model ID"),
    no_commit: bool = typer.Option(False, "--no-commit", help="Write but do not git commit"),
) -> None:
    """Draft job/intent.md from job/docs/plain_text_intent.txt via GitHub Models API."""
    from pipeline.core.models_api import ModelsAPIError, find_token
    from pipeline.stages.draft_intent import DraftIntentError
    from pipeline.stages.draft_intent import draft_intent as _draft

    token = find_token()
    if not token:
        typer.secho(
            "ERROR: no GitHub token found. Set GH_PIPELINE_TOKEN, GITHUB_TOKEN, "
            "or COPILOT_TOKEN in your environment.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Calling GitHub Models API (model: {model}) …")
    try:
        facts = _draft(REPO_ROOT, token=token, model=model, commit=not no_commit)
    except (DraftIntentError, ModelsAPIError) as exc:
        typer.secho(f"ERROR: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho("Draft written to job/intent.md", fg=typer.colors.GREEN)
    if facts["job_files_discovered"]:
        typer.echo(f"  Files referenced from job/: {', '.join(facts['job_files_discovered'])}")
    if facts["committed"]:
        typer.echo("  Committed locally. Review with: git diff HEAD~1 job/intent.md")
    typer.echo(
        "\nNext steps:\n"
        "  1. Review and adjust job/intent.md\n"
        "  2. git push  →  triggers the pipeline (validate + generate)"
    )


@app.command(name="history")
def history(
    sub: Optional[str] = typer.Option(
        None, "--sub", help="Filter to a single sub-stage (sql, mulesoft, tests)."
    ),
) -> None:
    """Show the generation history for each sub-stage from the lockfile."""
    from pipeline.core.lockfile import SUBSTAGES, read_lockfile

    lock = read_lockfile(REPO_ROOT)
    if lock is None:
        typer.secho("No lockfile found — pipeline has not run yet.", fg=typer.colors.YELLOW)
        raise typer.Exit()

    stages = [sub] if sub else list(SUBSTAGES)
    for substage in stages:
        snapshots = lock.stage_history.get(substage, [])
        typer.secho(f"\n── {substage} history ({'no entries' if not snapshots else f'{len(snapshots)} version(s)'}) ──",
                    fg=typer.colors.CYAN)
        for i, snap in enumerate(snapshots):
            label = "(latest)" if i == 0 else f"(v-{i})"
            result_part = f"  test:{snap.test_result}" if snap.test_result else ""
            sha_short = snap.git_commit[:8] if snap.git_commit else "unknown"
            typer.echo(
                f"  [{i}] {label}  {snap.generated_at}  "
                f"sha:{sha_short}  run:{snap.run_id or 'local'}{result_part}"
            )
        if not snapshots:
            typer.echo("  (no history yet)")


@app.command(name="rollback")
def rollback(
    sub: str = typer.Option(..., "--sub", help="Sub-stage to roll back: sql, mulesoft, tests."),
    version: int = typer.Option(0, "--version", "-v",
                                help="History index to restore (0=latest archived, 1=one older, …)."),
) -> None:
    """Restore a previously generated artifact from git history.

    Checks out the artifact file at the commit stored in stage_history[sub][version]
    and writes it back to the working tree for review. Does NOT commit — review the
    restored file, then commit manually or run the pipeline again.
    """
    import subprocess as _sp
    from pipeline.core.lockfile import SUBSTAGE_TO_ARTIFACT, read_lockfile

    lock = read_lockfile(REPO_ROOT)
    if lock is None:
        typer.secho("ERROR: no lockfile found.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if sub not in SUBSTAGE_TO_ARTIFACT:
        typer.secho(f"ERROR: unknown sub-stage '{sub}'.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    snapshots = lock.stage_history.get(sub, [])
    if not snapshots:
        typer.secho(f"No history for sub-stage '{sub}'.", fg=typer.colors.YELLOW)
        raise typer.Exit()

    if version >= len(snapshots):
        typer.secho(
            f"ERROR: version {version} out of range — only {len(snapshots)} snapshot(s) available.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)

    snap = snapshots[version]
    if not snap.git_commit:
        typer.secho(
            "ERROR: snapshot has no git commit SHA — cannot restore from git history.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)

    rel_path = snap.artifact_path
    typer.echo(f"Restoring {rel_path} from commit {snap.git_commit[:8]} ({snap.generated_at}) …")
    res = _sp.run(
        ["git", "show", f"{snap.git_commit}:{rel_path}"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if res.returncode != 0:
        typer.secho(
            f"ERROR: git show failed — {res.stderr.strip()}",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)

    out_path = REPO_ROOT / rel_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(res.stdout, encoding="utf-8")
    typer.secho(
        f"Restored {rel_path} from {snap.generated_at}. "
        "Review the file, then commit or re-run the pipeline.",
        fg=typer.colors.GREEN,
    )


def _resolve_mule_git_conn(result: ValidationResult):
    """Resolve the intent's mulesoft connection; require it to be git_repo + a repo."""
    from pipeline.core.resolver import get_connection_meta, resolve

    intent = result.intent
    delivery = intent.mulesoft_delivery if intent else None
    if not delivery or not delivery.repo:
        typer.secho(
            "ERROR: job/intent.md needs mulesoft_delivery.repo set (the target "
            "MuleSoft repo name).",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)
    meta = get_connection_meta(REPO_ROOT, intent.connections.mulesoft)
    if meta.get("type") != "git_repo":
        typer.secho(
            f"ERROR: connection '{intent.connections.mulesoft}' is type "
            f"'{meta.get('type')}', not git_repo — the workspace flow needs a "
            "git_repo connection.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)
    conn = resolve(REPO_ROOT, intent.connections.mulesoft)
    conn["__name__"] = intent.connections.mulesoft
    return conn, delivery


@app.command(name="mule-checkout")
def mule_checkout(
    reuse: bool = typer.Option(False, "--reuse", help="Keep an existing workspace instead of erroring."),
) -> None:
    """Clone the target MuleSoft repo into mule_workspace/ for Copilot to edit.

    Branch from mulesoft_delivery.branch: if it exists on the remote the
    workspace is set to it (changes go on top); if absent or omitted a fresh
    branch is created off the default branch.
    """
    from pipeline.deployers.mule_workspace import DeliveryError, prepare_workspace

    result = _validated()
    conn, delivery = _resolve_mule_git_conn(result)
    try:
        info = prepare_workspace(
            REPO_ROOT, conn, delivery.repo, result.intent.job_name,
            branch=delivery.branch, reuse=reuse,
        )
    except DeliveryError as exc:
        typer.secho(f"ERROR: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho(f"Workspace ready: {info['workspace']}", fg=typer.colors.GREEN)
    typer.echo(f"  branch: {info['branch']}"
               + ("  (existing — changes go on top)" if info.get('based_on_existing_branch')
                  else "  (new — created off default)"))
    if info.get("existing_flows"):
        typer.echo(f"  existing flows: {', '.join(info['existing_flows'])}")
    typer.echo(
        "\nNext: in Copilot agent mode, make the required changes inside that\n"
        "folder (edit an existing file or add new ones), then run:\n"
        "  python pipeline/cdu.py mule-deliver"
    )


@app.command(name="mule-deliver")
def mule_deliver() -> None:
    """Validate the changes in mule_workspace/, commit, and push to the target repo."""
    from pipeline.deployers.mule_workspace import DeliveryError, deliver_workspace

    result = _validated()
    if result.intent.mode != "deploy":
        typer.secho(
            "intent mode is 'generate' — set `mode: deploy` in job/intent.md "
            "to push MuleSoft changes (spec D6).",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)
    conn, delivery = _resolve_mule_git_conn(result)
    try:
        facts = deliver_workspace(REPO_ROOT, conn, delivery.repo, result.intent.job_name)
    except DeliveryError as exc:
        typer.secho(f"ERROR: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if not facts["pushed"]:
        typer.secho(f"  {facts['note']}", fg=typer.colors.YELLOW)
        return
    typer.secho(
        f"Pushed {len(facts['changed_files'])} changed file(s) to "
        f"{facts['mulesoft_repo']} @ {facts['mulesoft_branch']}",
        fg=typer.colors.GREEN,
    )
    for f in facts["changed_files"]:
        typer.echo(f"  • {f}")
    typer.echo(f"  {facts['mulesoft_url']}")


@app.command(name="inspect-mule-repo")
def inspect_mule_repo() -> None:
    """Inspect the existing MuleSoft repo named in mulesoft_delivery.repo."""
    from pipeline.core.resolver import load_connections_yaml
    from pipeline.deployers import mule_git
    from pipeline.deployers.mule_git import DeliveryError

    result = _validated()
    delivery = result.intent.mulesoft_delivery if result.intent else None
    if not delivery or not delivery.repo:
        typer.secho(
            "ERROR: intent has no mulesoft_delivery.repo — nothing to inspect.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)

    connections = load_connections_yaml(REPO_ROOT)
    git_conn_name = "mulesoft_git"
    conn_meta = connections.get(git_conn_name)
    if conn_meta is None:
        typer.secho(
            f"ERROR: connection '{git_conn_name}' not found in connections.yaml.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)

    import os
    token_env = (conn_meta.get("secrets") or {}).get("token", "MULE_REPO_TOKEN")
    token = os.environ.get(token_env)
    if not token:
        typer.secho(
            f"ERROR: env var {token_env} not set — cannot authenticate.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)

    conn = {
        "provider": conn_meta.get("provider", "github"),
        "host": conn_meta.get("host", "github.com"),
        "namespace": conn_meta.get("namespace", ""),
        "token": token,
    }

    try:
        info = mule_git.inspect_mule_repo(conn, delivery.repo, result.intent.job_name)
    except DeliveryError as exc:
        typer.secho(f"ERROR: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Repo:                {conn['namespace']}/{info['repo']}")
    typer.echo(f"Looks like Mule:     {info['looks_like_mule_project']}")
    typer.echo(f"Mule version:        {info['mule_version'] or 'unknown'}")
    typer.echo(f"Has pom.xml:         {info['has_pom']}")
    typer.echo(f"Has mule-artifact:   {info['has_mule_artifact']}")
    typer.echo(f"Has src/main/mule/:  {info['has_mule_src_dir']}")
    if info["existing_flows"]:
        typer.echo(f"Existing flows:      {', '.join(info['existing_flows'])}")
    else:
        typer.echo("Existing flows:      (none)")
    typer.echo(f"Our flow present:    {info['our_flow_exists']}")


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
