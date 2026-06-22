"""In-repo MuleSoft workspace — clone the target repo so Copilot can edit it.

This is the Option-C delivery model: instead of the factory writing a single
generated flow file, it clones the real target Mule repo into `mule_workspace/`
(gitignored) on the correct branch. Copilot agent mode then makes whatever
change the dev requirement needs — edit an existing flow, add a new flow,
adjust DataWeave / pom / properties, etc. The factory validates the *changed*
files (THE WALL secret scan + XML well-formedness) and pushes them, honouring
the same branch semantics as mule_git.deliver (existing branch → accommodate
on top; absent/omitted → create fresh off default).

Two steps (separate CLI invocations so Copilot edits in between):
    cdu mule-checkout   → clone target repo to mule_workspace/<repo>, branch set
    (Copilot edits files in mule_workspace/<repo>)
    cdu mule-deliver    → validate changed files, commit, push
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from pipeline.core.resolver import load_connections_yaml
from pipeline.deployers import mule_git
from pipeline.deployers.mule_git import DeliveryError

WORKSPACE_ROOT = "mule_workspace"
STATE_SUFFIX = ".state.json"


def workspace_path(repo_root: Path, repo: str) -> Path:
    return repo_root / WORKSPACE_ROOT / repo


def _state_path(repo_root: Path, repo: str) -> Path:
    return repo_root / WORKSPACE_ROOT / f"{repo}{STATE_SUFFIX}"


def prepare_workspace(
    repo_root: Path,
    conn: dict,
    repo: str,
    job_name: str,
    branch: str | None = None,
    reuse: bool = False,
) -> dict:
    """Clone `repo` into mule_workspace/<repo> on the right branch.

    Token is used for clone/fetch then stripped from the persisted git config
    so no credential sits on disk while Copilot edits. `cdu mule-deliver`
    re-injects it only at push time.
    """
    branch_explicit = bool(branch)
    target_branch = branch or mule_git.default_branch_name(job_name)
    dest = workspace_path(repo_root, repo)

    if dest.exists():
        if not reuse:
            raise DeliveryError(
                f"{dest} already exists. Edit it and run `cdu mule-deliver`, "
                "or pass --reuse to keep it, or delete it to re-clone."
            )
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        mule_git._clone(conn, repo, dest)

        base_on_existing = False
        if branch_explicit and mule_git._remote_branch_exists(conn, dest, target_branch):
            mule_git._fetch_branch(conn, dest, target_branch)
            mule_git._git(conn, dest, "checkout", "-B", target_branch, "FETCH_HEAD")
            base_on_existing = True
        else:
            mule_git._git(conn, dest, "checkout", "-B", target_branch)

        # Strip the token from origin so it is not persisted on disk.
        tokenless = f"https://{conn['host']}/{conn['namespace']}/{repo}.git"
        mule_git._git(conn, dest, "remote", "set-url", "origin", tokenless)

        structure = mule_git.inspect_repo_structure(dest, job_name)
        _write_state(repo_root, repo, {
            "repo": repo,
            "branch": target_branch,
            "based_on_existing_branch": base_on_existing,
            "namespace": conn["namespace"],
            "host": conn["host"],
            "provider": conn["provider"],
            "job_name": job_name,
            "connection_name": conn.get("__name__", ""),
        })
        return {
            "workspace": str(dest),
            "branch": target_branch,
            "based_on_existing_branch": base_on_existing,
            "looks_like_mule_project": structure["looks_like_mule_project"],
            "existing_flows": structure["existing_flows"],
            "mule_version": structure["mule_version"],
        }

    # reuse path: report current state
    state = _read_state(repo_root, repo)
    return {
        "workspace": str(dest),
        "branch": state.get("branch", target_branch),
        "based_on_existing_branch": state.get("based_on_existing_branch", False),
        "reused": True,
    }


def deliver_workspace(
    repo_root: Path,
    conn: dict,
    repo: str,
    job_name: str,
) -> dict:
    """Validate the changes Copilot made in the workspace, commit, and push."""
    dest = workspace_path(repo_root, repo)
    if not dest.is_dir():
        raise DeliveryError(
            f"No workspace at {dest}. Run `cdu mule-checkout` first."
        )
    state = _read_state(repo_root, repo)
    branch = state["branch"]
    base_on_existing = state.get("based_on_existing_branch", False)

    # Stage everything, then inspect what actually changed.
    mule_git._git(conn, dest, "add", "-A")
    name_status = mule_git._git(conn, dest, "diff", "--cached", "--name-status")
    changed = _parse_name_status(name_status)
    if not changed:
        return {
            "mulesoft_repo": f"{conn['namespace']}/{repo}",
            "mulesoft_branch": branch,
            "changed_files": [],
            "pushed": False,
            "note": "no changes in workspace — nothing to deliver",
        }

    # Guardrails on the changed (added/modified) files only.
    secret_values = _secret_values(repo_root)
    for status, rel in changed:
        if status == "D":
            continue
        path = dest / rel
        text = path.read_text(encoding="utf-8", errors="ignore")
        _scan_secrets(rel, text, secret_values)
        if rel.endswith(".xml"):
            try:
                ET.fromstring(text)
            except ET.ParseError as exc:
                raise DeliveryError(f"{rel} is not well-formed XML: {exc}") from exc

    mule_git._git(conn, dest,
         "-c", "user.name=cdu-pipeline",
         "-c", "user.email=cdu-pipeline@noreply.local",
         "commit", "-m", f"cdu: mulesoft changes for {job_name}")
    commit = mule_git._git(conn, dest, "rev-parse", "HEAD")

    # Push using an explicit token URL (origin is tokenless on disk).
    _push_token_url(conn, dest, repo, branch, force=not base_on_existing)

    return {
        "mulesoft_repo": f"{conn['namespace']}/{repo}",
        "mulesoft_branch": branch,
        "mulesoft_commit": commit,
        "mulesoft_url": f"https://{conn['host']}/{conn['namespace']}/{repo}",
        "changed_files": [rel for _, rel in changed],
        "based_on_existing_branch": base_on_existing,
        "pushed": True,
    }


# ── helpers ──────────────────────────────────────────────────────────────────

def _push_token_url(conn: dict, dest: Path, repo: str, branch: str,
                    force: bool) -> None:
    url = mule_git._remote_url(conn, repo)  # embeds the token; never logged (redacted)
    args = ["push", url, f"HEAD:{branch}"]
    if force:
        args.insert(1, "--force")
    mule_git._git(conn, dest, *args)


def _parse_name_status(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][0]  # A | M | D | R...
        rel = parts[-1]       # for renames, the new path
        out.append((status, rel))
    return out


def _secret_values(repo_root: Path) -> dict[str, str]:
    """Map secret env-NAME → its VALUE for every secret in connections.yaml."""
    import os
    names = {
        env_name
        for meta in load_connections_yaml(repo_root).values()
        for env_name in meta.get("secrets", {}).values()
    }
    return {n: os.environ[n] for n in names if os.environ.get(n)}


def _scan_secrets(rel: str, text: str, secret_values: dict[str, str]) -> None:
    for name, value in secret_values.items():
        if value and value in text:
            raise DeliveryError(
                f"{rel} contains the value of secret {name} — refusing to "
                "push it (THE WALL, spec §7)"
            )


def _write_state(repo_root: Path, repo: str, state: dict) -> None:
    path = _state_path(repo_root, repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _read_state(repo_root: Path, repo: str) -> dict:
    path = _state_path(repo_root, repo)
    if not path.is_file():
        raise DeliveryError(
            f"No workspace state for '{repo}' — run `cdu mule-checkout` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))
