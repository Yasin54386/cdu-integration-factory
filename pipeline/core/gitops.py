"""Git operations: commit-back with [skip ci], PR comment (spec §4/§10).

The `git_main` connection in connections.yaml selects the hosting flavour
(github | gitlab); its token secret authenticates pushes and API calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

from pipeline.core.resolver import get_connection_meta

GIT_CONNECTION = "git_main"


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def current_branch(repo_root: Path) -> str:
    return _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")


def commit_back(repo_root: Path, paths: list[str], message: str) -> bool:
    """Commit pipeline outputs to the current branch and push.

    The message must contain [skip ci]; combined with the workflow's
    job/** path filter this is the double loop guard (spec §12).
    Returns False when there is nothing to commit.
    """
    if "[skip ci]" not in message:
        message += " [skip ci]"
    _git(repo_root, "add", "--", *paths)
    status = _git(repo_root, "status", "--porcelain", "--", *paths)
    if not status:
        return False
    committer = [
        "-c", "user.name=cdu-pipeline",
        "-c", "user.email=cdu-pipeline@noreply.local",
    ]
    subprocess.run(
        ["git", *committer, "commit", "-m", message],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    # In CI, actions/checkout configured the remote with GH_PIPELINE_TOKEN
    # (workflow checkout `token:` input), so a plain push authenticates.
    _git(repo_root, "push", "origin", f"HEAD:{current_branch(repo_root)}")
    return True


def post_pr_comment(repo_root: Path, body: str) -> bool:
    """Post `body` as a comment on the PR for the current branch, if one exists.

    Best-effort: returns False (without raising) when no PR or no token —
    the report is always also written to the job summary and reports/.
    """
    meta = get_connection_meta(repo_root, GIT_CONNECTION)
    if meta.get("type") != "github":
        return False  # gitlab variant: out of scope for v1 (spec §16)
    token = os.environ.get(meta.get("secrets", {}).get("token", "GH_PIPELINE_TOKEN"))
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return False
    branch = current_branch(repo_root)
    try:
        prs = _github_api(
            token,
            f"https://api.github.com/repos/{repo}/pulls"
            f"?head={repo.split('/')[0]}:{branch}&state=open",
        )
        if not prs:
            return False
        _github_api(
            token,
            f"https://api.github.com/repos/{repo}/issues/{prs[0]['number']}/comments",
            payload={"body": body},
        )
        return True
    except Exception:
        return False


def write_job_summary(body: str) -> None:
    """Append to the GitHub Actions job summary when running in CI."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(body + "\n")


def _github_api(token: str, url: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))
