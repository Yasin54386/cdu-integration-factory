"""Stage — draft-intent (Task 2).

Reads job/docs/plain_text_intent.txt, discovers existing files under job/,
calls the GitHub Models API to produce a draft job/intent.md, validates
the front-matter parses, writes the file, and commits it locally.

The developer reviews the diff (git diff job/intent.md) and pushes when
satisfied — pushing triggers the pipeline.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from pipeline.core.intent import IntentError, split_front_matter
from pipeline.core.models_api import ModelsAPIError, call as api_call
from pipeline.core.preprocess import preprocess_file
from pipeline.core.resolver import load_connections_yaml

DOCS_REL = Path("job") / "docs"
PLAIN_TEXT_INTENT_REL = DOCS_REL / "plain_text_intent.txt"
PROMPT_TEMPLATE_REL = Path("prompts") / "intent_drafter.prompt.md"

_FENCE_RE = re.compile(r"\A```[a-zA-Z0-9_-]*\n(.*)\n```\s*\Z", re.DOTALL)

JOB_SUBDIRS = ("sql", "specs", "samples", "mappings", "tests")


class DraftIntentError(RuntimeError):
    pass


def _read_docs(repo_root: Path) -> tuple[str, list[str]]:
    """Read the contents of EVERY file under job/docs/ and concatenate them.

    The drafter's understanding comes from these documents — not just from
    plain_text_intent.txt. Text files are read directly; .docx/.pdf/.xlsx are
    extracted via preprocess_file. Each file's content is labelled with its
    path so the model knows the source. Returns (combined_text, file_names).
    """
    docs_dir = repo_root / DOCS_REL
    if not docs_dir.is_dir():
        return "", []
    chunks: list[str] = []
    names: list[str] = []
    for path in sorted(docs_dir.rglob("*")):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        rel = path.relative_to(repo_root / "job").as_posix()
        try:
            text = preprocess_file(path)
        except Exception:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue  # unreadable binary — skip, but it's still listed by name
        text = text.strip()
        if not text:
            continue
        chunks.append(f"### {rel}\n\n{text}")
        names.append(rel)
    return "\n\n".join(chunks), names


def _discover_job_files(repo_root: Path) -> list[str]:
    """List files under job/ subdirectories, relative to job/."""
    job_dir = repo_root / "job"
    found: list[str] = []
    for sub in JOB_SUBDIRS:
        d = job_dir / sub
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*")):
            if path.is_file() and path.name != ".gitkeep":
                found.append(path.relative_to(job_dir).as_posix())
    return found


def _list_connections(repo_root: Path) -> list[str]:
    try:
        return sorted(load_connections_yaml(repo_root).keys())
    except Exception:
        return []


def _build_user_prompt(
    description: str,
    job_files: list[str],
    connections: list[str],
) -> str:
    files_block = "\n".join(f"  - {f}" for f in job_files) or "  (none yet)"
    conn_block = "\n".join(f"  - {c}" for c in connections) or "  (none defined)"
    return (
        "## Integration description\n\n"
        f"{description.strip()}\n\n"
        "## Files already present under job/\n\n"
        f"{files_block}\n\n"
        "## Available connection names (from connections.yaml)\n\n"
        f"{conn_block}\n\n"
        "Produce the complete job/intent.md content now."
    )


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.match(text.strip())
    return m.group(1) if m else text


def _validate_draft(content: str) -> None:
    """Confirm the draft has parseable YAML front-matter."""
    try:
        split_front_matter(content)
    except IntentError as exc:
        raise DraftIntentError(
            f"Generated draft does not have valid front-matter: {exc}"
        ) from exc


def _git(repo_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise DraftIntentError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=2, min=2, max=20), reraise=True)
def _call_api(system: str, user: str, token: str, model: str) -> str:
    return api_call(
        system_prompt=system,
        user_prompt=user,
        token=token,
        model=model,
    )


def draft_intent(
    repo_root: Path,
    token: str,
    model: str = "gpt-4o-mini",
    commit: bool = True,
) -> dict:
    """Draft job/intent.md from plain_text_intent.txt via the Models API.

    Returns: {plain_text_path, intent_path, job_files, committed}.
    Raises DraftIntentError for missing inputs or API/parse failures.
    """
    description, doc_files = _read_docs(repo_root)
    if not description:
        raise DraftIntentError(
            f"No readable content found under {DOCS_REL}/.\n"
            "Add at least one document describing your integration, e.g.:\n"
            "  mkdir -p job/docs\n"
            "  echo 'Nightly extract of active students...' > job/docs/plain_text_intent.txt\n"
            "(.txt/.md plus .docx/.pdf/.xlsx are all read.)"
        )

    prompt_template_path = repo_root / PROMPT_TEMPLATE_REL
    if not prompt_template_path.is_file():
        raise DraftIntentError(f"Prompt template not found: {PROMPT_TEMPLATE_REL}")
    system_prompt = prompt_template_path.read_text(encoding="utf-8")

    job_files = _discover_job_files(repo_root)
    connections = _list_connections(repo_root)
    user_prompt = _build_user_prompt(description, job_files, connections)

    try:
        raw = _call_api(system_prompt, user_prompt, token, model)
    except ModelsAPIError as exc:
        raise DraftIntentError(f"GitHub Models API call failed: {exc}") from exc

    content = _strip_fences(raw)
    _validate_draft(content)

    intent_path = repo_root / "job" / "intent.md"
    intent_path.write_text(content, encoding="utf-8")

    committed = False
    if commit:
        _git(repo_root, "add", "--", "job/intent.md")
        status = _git(repo_root, "status", "--porcelain", "--", "job/intent.md")
        if status:
            _git(
                repo_root,
                "-c", "user.name=cdu-pipeline",
                "-c", "user.email=cdu-pipeline@noreply.local",
                "commit", "-m", "cdu: draft intent.md from plain_text_intent.txt [draft]",
            )
            committed = True

    return {
        "plain_text_path": str(PLAIN_TEXT_INTENT_REL),
        "intent_path": "job/intent.md",
        "docs_read": doc_files,
        "job_files_discovered": job_files,
        "committed": committed,
    }
