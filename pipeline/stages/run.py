"""Stage — run (sub-stage orchestrator, Task 3).

Runs one or more named sub-stages in canonical order:
  sql       → generate ORDS module SQL; deploy to Oracle (mode: deploy)
  mulesoft  → generate Mule flow XML; push to git repo / Anypoint (mode: deploy)
  tests     → generate pytest file; run tests + write report (mode: deploy)

Each sub-stage independently checks whether its artifact is stale before
generating. The tests sub-stage is always RUN in deploy mode even when the
test file itself was not regenerated (test inputs may have changed externally).

Commits are made after each sub-stage's generate phase and again after its
deploy/run phase — so a failure mid-run leaves all completed work committed.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pipeline.core import gitops
from pipeline.core.hashing import combined_hash, hash_inputs
from pipeline.core.impact import decide_regeneration
from pipeline.core.lockfile import (
    LOCKFILE_NAME,
    SUBSTAGE_TO_ARTIFACT,
    SUBSTAGES,
    ArtifactRecord,
    Lockfile,
    SubstageRecord,
    read_lockfile,
    write_lockfile,
)
from pipeline.core.resolver import get_connection_meta, resolve
from pipeline.deployers import mule_git
from pipeline.deployers import mulesoft as mulesoft_deployer
from pipeline.deployers import ords as ords_deployer
from pipeline.stages.generate import (
    artifact_output_path,
    assemble_prompt,
    invoke_copilot,
    sanity_check,
    strip_code_fences,
)
from pipeline.stages.test import AI_AUTHORED_WARNING, build_report, has_human_assertions
from pipeline.stages.validate import ValidationResult


class RunError(RuntimeError):
    pass


@dataclass
class SubstageOutcome:
    name: str
    skipped: bool = False
    generated: bool = False
    deployed: bool = False
    test_result: str = ""   # "pass" | "fail" | ""


@dataclass
class RunOutcome:
    mode: str
    outcomes: list[SubstageOutcome] = field(default_factory=list)

    @property
    def tests_passed(self) -> bool:
        return all(o.test_result != "fail" for o in self.outcomes)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_substages(requested: list[str] | None) -> list[str]:
    """Validate and sort requested sub-stages into canonical order."""
    if requested is None:
        return list(SUBSTAGES)
    unknown = set(requested) - set(SUBSTAGES)
    if unknown:
        raise RunError(
            f"Unknown sub-stage(s): {sorted(unknown)}. "
            f"Valid: {list(SUBSTAGES)}"
        )
    return [s for s in SUBSTAGES if s in requested]


def run(
    repo_root: Path,
    validation: ValidationResult,
    substages: list[str] | None = None,
    run_id: str = "local",
    run_url: str = "",
) -> RunOutcome:
    """Run the requested sub-stages in order; commit after each phase."""
    intent = validation.intent
    assert intent is not None

    requested = resolve_substages(substages)

    new_hashes = hash_inputs(repo_root, intent)
    lock = read_lockfile(repo_root)
    stale_artifacts = decide_regeneration(
        repo_root, intent, validation.raw_intent, new_hashes, lock
    )

    lock = lock or Lockfile()
    lock.job_name = intent.job_name
    lock.last_run_id = run_id
    lock.last_run_at = _now()
    lock.last_mode = intent.mode
    lock.input_hashes = new_hashes
    lock.combined_input_hash = combined_hash(new_hashes)
    lock.intent_snapshot = validation.raw_intent

    outcome = RunOutcome(mode=intent.mode)

    for substage in requested:
        artifact = SUBSTAGE_TO_ARTIFACT[substage]
        is_stale = artifact in stale_artifacts
        sub = SubstageOutcome(name=substage)

        # ── sql and mulesoft: skip entirely if not stale ──────────────────
        if substage in ("sql", "mulesoft"):
            if not is_stale:
                sub.skipped = True
                outcome.outcomes.append(sub)
                continue

            _generate_artifact(repo_root, artifact, intent, validation, lock)
            sub.generated = True
            write_lockfile(repo_root, lock)
            gitops.commit_back(
                repo_root,
                ["generated/", LOCKFILE_NAME],
                f"cdu: generate {substage} for {intent.job_name} [skip ci]",
            )

            if intent.mode == "deploy":
                if substage == "sql":
                    _deploy_ords(repo_root, intent, lock)
                else:
                    _deploy_mulesoft(repo_root, intent, lock)
                lock.substages[substage] = SubstageRecord(
                    status="done",
                    input_hash=lock.combined_input_hash,
                    generated_at=lock.artifacts[artifact].generated_at,
                    deployed_at=_now(),
                )
                write_lockfile(repo_root, lock)
                gitops.commit_back(
                    repo_root, [LOCKFILE_NAME],
                    f"cdu: deploy {substage} for {intent.job_name} [skip ci]",
                )
                sub.deployed = True

        # ── tests: generate if stale; run always in deploy mode ───────────
        elif substage == "tests":
            if is_stale:
                _generate_artifact(repo_root, artifact, intent, validation, lock)
                sub.generated = True
                write_lockfile(repo_root, lock)
                gitops.commit_back(
                    repo_root,
                    ["generated/", LOCKFILE_NAME],
                    f"cdu: generate tests for {intent.job_name} [skip ci]",
                )
            elif intent.mode != "deploy":
                # generate mode + not stale → nothing to do
                sub.skipped = True
                outcome.outcomes.append(sub)
                continue

            if intent.mode == "deploy":
                if "tests" not in lock.artifacts or not (
                    repo_root / lock.artifacts["tests"].path
                ).is_file():
                    raise RunError(
                        "tests artifact not found — run all sub-stages "
                        "(or at least `--sub tests`) to generate it first"
                    )
                result, report_rel = _run_tests(repo_root, validation, lock, run_url)
                lock.last_test_result = {"status": result, "report": report_rel}
                lock.substages[substage] = SubstageRecord(
                    status="done",
                    input_hash=lock.combined_input_hash,
                    generated_at=lock.artifacts["tests"].generated_at,
                    test_result=result,
                )
                write_lockfile(repo_root, lock)
                gitops.commit_back(
                    repo_root,
                    ["reports/", LOCKFILE_NAME],
                    f"cdu: test report for {intent.job_name} [skip ci]",
                )
                sub.deployed = True
                sub.test_result = result

        outcome.outcomes.append(sub)

        if sub.test_result == "fail":
            raise RunError(
                f"Tests FAILED for {intent.job_name} — "
                f"see reports/ and fix job inputs, then push to re-run."
            )

    return outcome


# ── per-artifact generation ────────────────────────────────────────────────────

def _generate_artifact(
    repo_root: Path,
    artifact: str,
    intent,
    validation: ValidationResult,
    lock: Lockfile,
) -> None:
    """Generate one artifact in-place; update lock.artifacts."""
    prompt = assemble_prompt(
        repo_root, artifact, intent, validation.raw_intent, validation.body_notes
    )
    content = invoke_copilot(prompt)
    content = strip_code_fences(content)
    sanity_check(repo_root, artifact, content, intent)
    rel_path = artifact_output_path(artifact, intent.job_name)
    out_path = repo_root / rel_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    lock.artifacts[artifact] = ArtifactRecord(
        path=rel_path,
        input_hash_at_gen=lock.combined_input_hash,
        generated_at=_now(),
    )


# ── deploy helpers ─────────────────────────────────────────────────────────────

def _deploy_ords(repo_root: Path, intent, lock: Lockfile) -> None:
    oracle = resolve(repo_root, intent.connections.oracle)
    ords_path = repo_root / lock.artifacts["ords"].path
    endpoint = ords_deployer.deploy_module(oracle, ords_path, intent.job_name)
    lock.deployed["ords_endpoint"] = endpoint
    lock.deployed["staging_table"] = f"STG_{intent.job_name.upper()}"


def _deploy_mulesoft(repo_root: Path, intent, lock: Lockfile) -> None:
    mule_conn = resolve(repo_root, intent.connections.mulesoft)
    mule_type = get_connection_meta(
        repo_root, intent.connections.mulesoft
    ).get("type")
    mule_path = repo_root / lock.artifacts["mulesoft"].path
    if mule_type == "git_repo":
        facts = mule_git.deliver(
            mule_conn, mule_path, intent.job_name, intent.mulesoft_delivery
        )
    else:
        facts = {
            "mulesoft_app": mulesoft_deployer.deploy_app(
                mule_conn, mule_path, intent.job_name
            )
        }
    lock.deployed.update(facts)


# ── test run helper ────────────────────────────────────────────────────────────

def _run_tests(
    repo_root: Path,
    validation: ValidationResult,
    lock: Lockfile,
    run_url: str,
) -> tuple[str, str]:
    """Run the generated tests; write report; return (result, report_rel)."""
    intent = validation.intent
    test_path = repo_root / lock.artifacts["tests"].path
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-v", "--tb=short"],
        cwd=repo_root, capture_output=True, text=True,
    )
    passed = result.returncode == 0

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    report_rel = f"reports/run_{timestamp}.md"
    report = build_report(
        repo_root, validation, passed, result.stdout, run_url, lock
    )
    report_path = repo_root / report_rel
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    gitops.write_job_summary(report)
    gitops.post_pr_comment(repo_root, report)

    return ("pass" if passed else "fail"), report_rel
