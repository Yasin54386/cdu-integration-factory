"""Bootstrap a new integration branch (start-integration command).

Creates feature/<name> from current HEAD, pushes to origin, and reports
whether a plain_text_intent.txt is present for the draft-intent step.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from pipeline.core.intent import JOB_NAME_PATTERN

_NAME_RE = re.compile(JOB_NAME_PATTERN)

PLAIN_TEXT_INTENT_REL = Path("job") / "docs" / "plain_text_intent.txt"


class BootstrapError(RuntimeError):
    pass


def _git(repo_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise BootstrapError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def current_branch(repo_root: Path) -> str:
    return _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")


def validate_name(name: str) -> str:
    """Clean and validate an integration name against the job_name pattern."""
    name = name.strip().lower().replace("-", "_")
    if not _NAME_RE.match(name):
        raise BootstrapError(
            f"'{name}' is not a valid integration name.\n"
            "  Rules: starts with a letter · lowercase letters, digits, underscores"
            " · 3–41 chars total.\n"
            "  Example: student_download_v1"
        )
    return name


def start_integration(repo_root: Path, name: str) -> dict:
    """Create feature/<name>, push to origin, return context facts.

    Returns a dict with: branch, name, base_branch, has_plain_text_intent.
    """
    name = validate_name(name)
    branch = f"feature/{name}"

    base = current_branch(repo_root)

    # Guard: branch must not already exist locally
    local_exists = _git(repo_root, "branch", "--list", branch)
    if local_exists:
        raise BootstrapError(
            f"Branch '{branch}' already exists locally. "
            "Choose a different name or delete it first: "
            f"git branch -D {branch}"
        )

    # Guard: branch must not already exist on origin
    remote_exists = _git(
        repo_root, "ls-remote", "--heads", "origin", branch, check=False
    )
    if remote_exists:
        raise BootstrapError(
            f"Branch '{branch}' already exists on origin. "
            "Choose a different name or fetch and continue that branch: "
            f"git fetch origin {branch} && git checkout {branch}"
        )

    # Create and switch to the new branch
    _git(repo_root, "checkout", "-b", branch)

    # Push and set upstream (retry logic lives in the caller via typer)
    _git(repo_root, "push", "-u", "origin", branch)

    plain_text_path = repo_root / PLAIN_TEXT_INTENT_REL
    has_plain_text = plain_text_path.is_file()

    return {
        "branch": branch,
        "name": name,
        "base_branch": base,
        "has_plain_text_intent": has_plain_text,
        "plain_text_path": str(PLAIN_TEXT_INTENT_REL) if has_plain_text else None,
    }
