"""Stage — generate (spec §10, §11).

Per-artifact skip-or-full-regen (D5) decided by core/impact.py. For each
artifact to regenerate: preprocess inputs → assemble prompt → call GitHub
Models REST API → sanity-check → write under generated/ → update lockfile →
commit back with [skip ci].
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_exponential

from pipeline.core import gitops
from pipeline.core.hashing import combined_hash, hash_inputs
from pipeline.core.impact import decide_regeneration
from pipeline.core.intent import Intent
from pipeline.core.lockfile import (
    ARTIFACTS,
    LOCKFILE_NAME,
    ArtifactRecord,
    Lockfile,
    read_lockfile,
    write_lockfile,
)
from pipeline.core.preprocess import enforce_prompt_cap, preprocess_file
from pipeline.core.resolver import load_connections_yaml
from pipeline.stages.validate import ValidationResult


class GenerateError(RuntimeError):
    pass


@dataclass
class GenerateOutcome:
    regenerated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    committed: bool = False


def artifact_output_path(artifact: str, job_name: str) -> str:
    return {
        "ords": f"generated/ords/{job_name}_module.sql",
        "mulesoft": f"generated/mulesoft/{job_name}_flow.xml",
        "tests": f"generated/tests/test_{job_name}.py",
    }[artifact]


PROMPT_TEMPLATES = {
    "ords": "prompts/ords_generator.prompt.md",
    "mulesoft": "prompts/mulesoft_generator.prompt.md",
    "tests": "prompts/test_generator.prompt.md",
}


def generate(repo_root: Path, validation: ValidationResult,
             run_id: str = "local", commit: bool = True) -> GenerateOutcome:
    intent = validation.intent
    assert intent is not None, "generate requires a passing validation"

    new_hashes = hash_inputs(repo_root, intent)
    lock = read_lockfile(repo_root)
    to_regen = decide_regeneration(
        repo_root, intent, validation.raw_intent, new_hashes, lock
    )
    outcome = GenerateOutcome(
        regenerated=sorted(to_regen),
        skipped=sorted(set(ARTIFACTS) - to_regen),
    )

    lock = lock or Lockfile()
    lock.job_name = intent.job_name
    lock.last_run_id = run_id
    lock.last_run_at = _now()
    lock.last_mode = intent.mode
    lock.input_hashes = new_hashes
    lock.combined_input_hash = combined_hash(new_hashes)
    lock.intent_snapshot = validation.raw_intent

    changed_paths: list[str] = []
    for artifact in sorted(to_regen):
        prompt = assemble_prompt(repo_root, artifact, intent,
                                 validation.raw_intent, validation.body_notes)
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
        changed_paths.append(rel_path)

    write_lockfile(repo_root, lock)
    if commit and changed_paths:
        outcome.committed = gitops.commit_back(
            repo_root,
            ["generated/", LOCKFILE_NAME],
            f"cdu: generate {intent.job_name} [skip ci]",
        )
    return outcome


def assemble_prompt(repo_root: Path, artifact: str, intent: Intent,
                    raw_intent: dict, body_notes: str) -> str:
    """Template + intent front-matter + body notes + preprocessed files.

    THE WALL (spec §7): only logical names and non-secret metadata may enter
    the prompt — never env-var values. Inputs here are intent fields,
    connections.yaml metadata-by-name and job/ file contents only.
    """
    import yaml

    template = (repo_root / PROMPT_TEMPLATES[artifact]).read_text(encoding="utf-8")
    sections = [
        template,
        "\n## Intent (machine contract)\n```yaml\n"
        + yaml.safe_dump(raw_intent, sort_keys=False)
        + "```",
    ]
    if body_notes:
        sections.append("\n## Developer notes\n" + body_notes)

    job_dir = repo_root / "job"
    for group_name, group in (
        ("sql", intent.sources.sql),
        ("specs", intent.sources.specs),
        ("samples", intent.sources.samples),
        ("mappings", intent.sources.mappings),
    ):
        for source in group:
            text = preprocess_file(job_dir / source.file)
            sections.append(
                f"\n## Supporting file: job/{source.file} (role: {source.role})\n"
                f"```\n{text}\n```"
            )
    if artifact == "tests" and intent.testing:
        sections.append(
            "\n## Human-authored test assertions (build tests from THESE)\n```yaml\n"
            + yaml.safe_dump(raw_intent.get("testing"), sort_keys=False)
            + "```"
        )
    return enforce_prompt_cap("\n".join(sections))


_SYSTEM_PROMPT = (
    "You are a precise code generator for the CDU Integration Factory. "
    "Follow the instructions in the user prompt exactly. "
    "Output only the requested file content — no extra commentary."
)

# Override the model via CDU_MODEL env var; default is gpt-4o-mini (fast, low-cost).
_DEFAULT_MODEL = "gpt-4o-mini"


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=2, min=2, max=20), reraise=True)
def invoke_copilot(prompt: str) -> str:
    """Call the GitHub Models REST API to generate artifact content.

    Token is resolved from GH_PIPELINE_TOKEN / GITHUB_TOKEN / COPILOT_TOKEN
    / GH_TOKEN (first found wins). Override the model with CDU_MODEL env var.
    Retries cover transient network / rate-limit errors.
    """
    from pipeline.core.models_api import ModelsAPIError, find_token
    from pipeline.core import models_api

    token = find_token()
    if not token:
        raise GenerateError(
            "No GitHub token found. Set GH_PIPELINE_TOKEN, GITHUB_TOKEN, "
            "COPILOT_TOKEN, or GH_TOKEN to authenticate with GitHub Models."
        )
    model = os.environ.get("CDU_MODEL", _DEFAULT_MODEL)
    try:
        return models_api.call(
            user_prompt=prompt,
            system_prompt=_SYSTEM_PROMPT,
            token=token,
            model=model,
        )
    except ModelsAPIError as exc:
        raise GenerateError(f"GitHub Models API error: {exc}") from exc


_FENCE_RE = re.compile(r"\A```[a-zA-Z0-9_-]*\n(.*)\n```\s*\Z", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Templates forbid fences, but strip defensively anyway (spec §11)."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text


def sanity_check(repo_root: Path, artifact: str, content: str, intent: Intent) -> None:
    """Cheap, non-AI post-generation checks (spec §10 generate.4)."""
    if artifact == "ords":
        if "ORDS.DEFINE_" not in content.upper():
            raise GenerateError("ORDS output contains no ORDS.DEFINE_ calls")
        if intent.job_name not in content:
            raise GenerateError(
                f"ORDS output does not mention job_name '{intent.job_name}'"
            )
    elif artifact == "mulesoft":
        try:
            ET.fromstring(content)
        except ET.ParseError as exc:
            raise GenerateError(f"MuleSoft output is not well-formed XML: {exc}") from exc
    _check_no_secrets(repo_root, artifact, content)


def _check_no_secrets(repo_root: Path, artifact: str, content: str) -> None:
    """Generated code must never contain a credential value (spec §7 WALL).

    Scan for the VALUES of every secret env var named in connections.yaml.
    """
    secret_names = {
        env_name
        for meta in load_connections_yaml(repo_root).values()
        for env_name in meta.get("secrets", {}).values()
    }
    for name in secret_names:
        value = os.environ.get(name)
        if value and value in content:
            raise GenerateError(
                f"{artifact} output contains the value of secret {name} — "
                "refusing to write it"
            )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
