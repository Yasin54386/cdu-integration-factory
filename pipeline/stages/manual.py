"""Stage — manual (Copilot-paste workflow).

A ToS-safe alternative to the GitHub Models API call: instead of the factory
calling an LLM, it writes the assembled prompt to a file for a human to paste
into GitHub Copilot Chat. The human saves Copilot's reply to the artifact's
output path, then `ingest` validates it, snapshots the prior version, updates
the lockfile and commits — every guardrail (impact map, THE WALL secret scan,
ORDS/XML sanity checks, version history) is preserved.

Two-step loop per sub-stage:
    cdu prompt --sub sql     → writes generated/.prompts/sql.prompt.md
    (paste into Copilot, save reply to generated/ords/<job>_module.sql)
    cdu ingest --sub sql     → validate + lockfile + commit [skip ci]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pipeline.core import gitops
from pipeline.core.hashing import combined_hash, hash_inputs
from pipeline.core.lockfile import (
    LOCKFILE_NAME,
    SUBSTAGE_TO_ARTIFACT,
    ArtifactRecord,
    Lockfile,
    StageSnapshot,
    SubstageRecord,
    push_stage_snapshot,
    read_lockfile,
    write_lockfile,
)
from pipeline.stages.generate import (
    artifact_output_path,
    assemble_prompt,
    sanity_check,
    strip_code_fences,
)
from pipeline.stages.run import resolve_substages
from pipeline.stages.validate import ValidationResult

PROMPTS_DIR = "generated/.prompts"

# How each sub-stage maps to where its prompt and output files live.
_PROMPT_HEADER = (
    "<!-- CDU manual-mode prompt for sub-stage: {substage} (artifact: {artifact}).\n"
    "     1. Copy EVERYTHING below the marker into GitHub Copilot Chat.\n"
    "     2. Save Copilot's reply (file content only, no fences) to:\n"
    "          {output_path}\n"
    "     3. Run:  python pipeline/cdu.py ingest --sub {substage}\n"
    "-->\n\n"
    "=== PASTE FROM HERE INTO COPILOT CHAT ===\n\n"
)


class ManualError(RuntimeError):
    pass


@dataclass
class PromptOutcome:
    substage: str
    artifact: str
    prompt_path: str
    output_path: str


@dataclass
class IngestOutcome:
    substage: str
    artifact: str
    output_path: str
    committed: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_prompts(
    repo_root: Path,
    validation: ValidationResult,
    substages: list[str] | None = None,
) -> list[PromptOutcome]:
    """Assemble and write the prompt file for each requested sub-stage."""
    intent = validation.intent
    assert intent is not None
    requested = resolve_substages(substages)

    prompts_dir = repo_root / PROMPTS_DIR
    prompts_dir.mkdir(parents=True, exist_ok=True)

    outcomes: list[PromptOutcome] = []
    for substage in requested:
        artifact = SUBSTAGE_TO_ARTIFACT[substage]
        prompt = assemble_prompt(
            repo_root, artifact, intent, validation.raw_intent, validation.body_notes
        )
        output_path = artifact_output_path(artifact, intent.job_name)
        header = _PROMPT_HEADER.format(
            substage=substage, artifact=artifact, output_path=output_path
        )
        prompt_rel = f"{PROMPTS_DIR}/{substage}.prompt.md"
        (repo_root / prompt_rel).write_text(header + prompt, encoding="utf-8")
        # Pre-create the output directory so the human can save Copilot's
        # reply straight to output_path without making folders by hand.
        (repo_root / output_path).parent.mkdir(parents=True, exist_ok=True)
        outcomes.append(
            PromptOutcome(
                substage=substage,
                artifact=artifact,
                prompt_path=prompt_rel,
                output_path=output_path,
            )
        )
    return outcomes


def ingest(
    repo_root: Path,
    validation: ValidationResult,
    substages: list[str] | None = None,
    run_id: str = "local",
    commit: bool = True,
) -> list[IngestOutcome]:
    """Validate human-saved artifact files, update lockfile, commit.

    Each requested sub-stage's output file (the one the human saved from
    Copilot) is read, fence-stripped, sanity-checked (THE WALL included),
    snapshotted to stage_history, recorded in the lockfile and committed.
    """
    intent = validation.intent
    assert intent is not None
    requested = resolve_substages(substages)

    new_hashes = hash_inputs(repo_root, intent)
    lock = read_lockfile(repo_root) or Lockfile()
    lock.job_name = intent.job_name
    lock.last_run_id = run_id
    lock.last_run_at = _now()
    lock.last_mode = intent.mode
    lock.input_hashes = new_hashes
    lock.combined_input_hash = combined_hash(new_hashes)
    lock.intent_snapshot = validation.raw_intent

    outcomes: list[IngestOutcome] = []
    for substage in requested:
        artifact = SUBSTAGE_TO_ARTIFACT[substage]
        rel_path = artifact_output_path(artifact, intent.job_name)
        out_path = repo_root / rel_path
        if not out_path.is_file():
            raise ManualError(
                f"No saved output for sub-stage '{substage}'. Expected the "
                f"Copilot reply saved at:\n  {rel_path}\n"
                f"Run `cdu prompt --sub {substage}` first, paste into Copilot, "
                "then save the reply to that path."
            )

        content = strip_code_fences(out_path.read_text(encoding="utf-8"))
        sanity_check(repo_root, artifact, content, intent)
        # Re-write the fence-stripped, validated content so the committed file
        # is exactly what passed the checks.
        out_path.write_text(content, encoding="utf-8")

        # Snapshot the prior version (if any) before recording the new one.
        prior = lock.artifacts.get(artifact)
        if prior is not None:
            push_stage_snapshot(lock, substage, StageSnapshot(
                generated_at=prior.generated_at,
                input_hash=prior.input_hash_at_gen,
                artifact_path=prior.path,
                run_id=run_id,
                mode=intent.mode,
            ))

        lock.artifacts[artifact] = ArtifactRecord(
            path=rel_path,
            input_hash_at_gen=lock.combined_input_hash,
            generated_at=_now(),
        )
        lock.substages[substage] = SubstageRecord(
            status="done",
            input_hash=lock.combined_input_hash,
            generated_at=lock.artifacts[artifact].generated_at,
        )

        ingest_outcome = IngestOutcome(
            substage=substage, artifact=artifact, output_path=rel_path
        )
        write_lockfile(repo_root, lock)
        if commit:
            ingest_outcome.committed = gitops.commit_back(
                repo_root,
                ["generated/", LOCKFILE_NAME],
                f"cdu: ingest {substage} for {intent.job_name} (manual) [skip ci]",
            )
            # Backfill the commit SHA into the freshly-pushed snapshot, if any.
            history = lock.stage_history.get(substage)
            if history:
                import subprocess
                sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=repo_root,
                    capture_output=True, text=True,
                ).stdout.strip()
                history[0] = StageSnapshot(**{**history[0].model_dump(), "git_commit": sha})
                write_lockfile(repo_root, lock)
        outcomes.append(ingest_outcome)

    return outcomes
