"""Deliver the generated MuleSoft app to a git repository (handoff deployer).

The institute's MuleSoft code lives in GitHub/GitLab and existing CI/CD
deploys it to Anypoint from there. So "deploying" the mulesoft artifact
means: push the generated app to a target repo on a factory-owned branch
(default `cdu/<job_name>`), creating the repo when it doesn't exist. The
institute's CI/CD takes over from the push. (Spec §17 amendment.)

Two modes, selected by the intent's optional `mulesoft_delivery` block:
- existing repo (`mulesoft_delivery.repo` set): clone the default branch,
  replace ONLY `src/main/mule/<job_name>_flow.xml`, push the branch. Build
  files are untouched — the repo is assumed to already be buildable.
- new repo (no repo named): create `cdu-<job-name>` under the connection's
  namespace and push a full minimal Mule 4 project scaffold.

Re-delivery force-pushes the same branch rebuilt from the current default
branch — the factory owns `cdu/*` branches (full-regen analog of D5).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from pipeline.core.intent import MulesoftDelivery


class DeliveryError(RuntimeError):
    pass


def app_repo_name(job_name: str) -> str:
    """D11 namespacing for factory-created repos: cdu-<job-name-with-dashes>."""
    return f"cdu-{job_name.replace('_', '-')}"


def default_branch_name(job_name: str) -> str:
    return f"cdu/{job_name}"


def inspect_repo_structure(checkout: Path, job_name: str) -> dict:
    """Non-destructive inspection of a cloned Mule repo checkout.

    Returns a dict with keys:
      has_pom, has_mule_artifact, has_mule_src_dir,
      existing_flows, mule_version, looks_like_mule_project, our_flow_exists
    """
    import re
    import xml.etree.ElementTree as ET

    has_pom = (checkout / "pom.xml").exists()
    has_mule_artifact = (checkout / "mule-artifact.json").exists()
    mule_src = checkout / "src" / "main" / "mule"
    has_mule_src_dir = mule_src.is_dir()

    existing_flows: list[str] = []
    if has_mule_src_dir:
        existing_flows = sorted(p.name for p in mule_src.glob("*.xml"))

    mule_version: Optional[str] = None
    if has_mule_artifact:
        try:
            import json as _json
            artifact = _json.loads((checkout / "mule-artifact.json").read_text())
            mule_version = artifact.get("minMuleVersion")
        except Exception:
            pass
    if mule_version is None and has_pom:
        try:
            pom_text = (checkout / "pom.xml").read_text()
            match = re.search(r"<packaging>\s*mule-application\s*</packaging>", pom_text)
            if match:
                mule_version = "4.x"  # packaging confirms Mule 4, version unknown from pom alone
        except Exception:
            pass

    looks_like_mule_project = has_pom or has_mule_artifact or has_mule_src_dir
    our_flow_exists = f"{job_name}_flow.xml" in existing_flows

    return {
        "has_pom": has_pom,
        "has_mule_artifact": has_mule_artifact,
        "has_mule_src_dir": has_mule_src_dir,
        "existing_flows": existing_flows,
        "mule_version": mule_version,
        "looks_like_mule_project": looks_like_mule_project,
        "our_flow_exists": our_flow_exists,
    }


def inspect_mule_repo(conn: dict, repo: str, job_name: str) -> dict:
    """Non-destructive pre-flight: clone repo, inspect structure, discard checkout.

    Returns the same structure dict as inspect_repo_structure plus
    'repo' and 'namespace' for reference.
    """
    with tempfile.TemporaryDirectory(prefix="cdu-mule-inspect-") as workdir:
        checkout = Path(workdir) / repo
        _clone(conn, repo, checkout)
        info = inspect_repo_structure(checkout, job_name)
    return {"repo": repo, "namespace": conn["namespace"], **info}


def deliver(conn: dict, flow_xml_path: Path, job_name: str,
            delivery: Optional[MulesoftDelivery]) -> dict:
    """Push the generated Mule app to the target repo; return lockfile facts.

    `conn` is a resolved git_repo connection dict (provider, host,
    namespace, token) — contains a live token; never log it.
    """
    existing_repo = delivery.repo if delivery and delivery.repo else None
    repo = existing_repo or app_repo_name(job_name)
    branch = (delivery.branch if delivery and delivery.branch
              else default_branch_name(job_name))
    flow_xml = flow_xml_path.read_text(encoding="utf-8")

    if not _repo_exists(conn, repo):
        if existing_repo:
            raise DeliveryError(
                f"mulesoft_delivery.repo '{repo}' does not exist under "
                f"{conn['namespace']} on {conn['host']} — create it first or "
                "omit `repo:` to let the factory create a namespaced one"
            )
        _create_repo(conn, repo)

    with tempfile.TemporaryDirectory(prefix="cdu-mule-") as workdir:
        checkout = Path(workdir) / repo
        _clone(conn, repo, checkout)
        _git(conn, checkout, "checkout", "-B", branch)
        if existing_repo:
            structure = inspect_repo_structure(checkout, job_name)
            if not structure["looks_like_mule_project"]:
                raise DeliveryError(
                    f"mulesoft_delivery.repo '{repo}' does not look like a "
                    "MuleSoft project (no pom.xml, mule-artifact.json, or "
                    "src/main/mule/). Verify the repo name and try again."
                )
            flow_rel = f"src/main/mule/{job_name}_flow.xml"
            target = checkout / flow_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(flow_xml, encoding="utf-8")
        else:
            structure = {"existing_flows": [], "mule_version": None, "our_flow_exists": False}
            _clear_worktree(checkout)
            scaffold_project(checkout, job_name, flow_xml)
        _git(conn, checkout, "add", "-A")
        if _git(conn, checkout, "status", "--porcelain"):
            _git(conn, checkout,
                 "-c", "user.name=cdu-pipeline",
                 "-c", "user.email=cdu-pipeline@noreply.local",
                 "commit", "-m", f"cdu: generated mulesoft app for {job_name}")
        commit = _git(conn, checkout, "rev-parse", "HEAD")
        _push(conn, checkout, branch)

    return {
        "mulesoft_repo": f"{conn['namespace']}/{repo}",
        "mulesoft_branch": branch,
        "mulesoft_commit": commit,
        "mulesoft_url": f"https://{conn['host']}/{conn['namespace']}/{repo}",
        "existing_flows": structure["existing_flows"],
        "mule_version": structure["mule_version"],
    }


def scaffold_project(dst: Path, job_name: str, flow_xml: str) -> None:
    """Minimal buildable Mule 4 project around the generated flow."""
    app = app_repo_name(job_name)
    flow_path = dst / "src" / "main" / "mule" / f"{job_name}_flow.xml"
    flow_path.parent.mkdir(parents=True, exist_ok=True)
    flow_path.write_text(flow_xml, encoding="utf-8")
    (dst / "mule-artifact.json").write_text(
        json.dumps({"minMuleVersion": "4.6.0"}, indent=2) + "\n", encoding="utf-8"
    )
    (dst / ".gitignore").write_text("target/\n", encoding="utf-8")
    (dst / "README.md").write_text(
        f"# {app}\n\nGenerated by the CDU Integration Factory for job "
        f"`{job_name}`.\nDo not hand-edit the flow XML — fix the job inputs "
        "on the factory branch and let it regenerate.\n",
        encoding="utf-8",
    )
    (dst / "pom.xml").write_text(_POM_TEMPLATE.format(app=app), encoding="utf-8")


_POM_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>au.edu.cdu.integration</groupId>
  <artifactId>{app}</artifactId>
  <version>1.0.0-SNAPSHOT</version>
  <packaging>mule-application</packaging>
  <name>{app}</name>
  <properties>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <mule.maven.plugin.version>4.1.1</mule.maven.plugin.version>
  </properties>
  <build>
    <plugins>
      <plugin>
        <groupId>org.mule.tools.maven</groupId>
        <artifactId>mule-maven-plugin</artifactId>
        <version>${{mule.maven.plugin.version}}</version>
        <extensions>true</extensions>
      </plugin>
    </plugins>
  </build>
</project>
"""


def _clear_worktree(checkout: Path) -> None:
    for path in checkout.iterdir():
        if path.name == ".git":
            continue
        if path.is_dir():
            import shutil

            shutil.rmtree(path)
        else:
            path.unlink()


# ── git transport (token never logged) ──────────────────────────────────────
# Only the network-touching calls are retried (CLI/network flakiness);
# logic errors like a missing named repo fail immediately.

@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=2, min=2, max=20), reraise=True)
def _clone(conn: dict, repo: str, checkout: Path) -> None:
    _git(conn, None, "clone", "--depth", "1", _remote_url(conn, repo),
         str(checkout))


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=2, min=2, max=20), reraise=True)
def _push(conn: dict, checkout: Path, branch: str) -> None:
    _git(conn, checkout, "push", "--force", "origin", f"HEAD:{branch}")


def _remote_url(conn: dict, repo: str) -> str:
    user = "oauth2" if conn["provider"] == "gitlab" else "x-access-token"
    return (f"https://{user}:{conn['token']}@{conn['host']}/"
            f"{conn['namespace']}/{repo}.git")


def _git(conn: dict, cwd: Optional[Path], *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise DeliveryError(
            f"git {args[0]} failed: {_redact(result.stderr.strip(), conn)}"
        )
    return result.stdout.strip()


def _redact(text: str, conn: dict) -> str:
    token = conn.get("token")
    return text.replace(token, "***") if token else text


# ── provider APIs (repo existence / creation) ───────────────────────────────

def _api_base(conn: dict) -> str:
    if conn["provider"] == "gitlab":
        return f"https://{conn['host']}/api/v4"
    if conn["host"] == "github.com":
        return "https://api.github.com"
    return f"https://{conn['host']}/api/v3"  # GitHub Enterprise Server


def _headers(conn: dict) -> dict:
    if conn["provider"] == "gitlab":
        return {"PRIVATE-TOKEN": conn["token"]}
    return {"Authorization": f"Bearer {conn['token']}",
            "Accept": "application/vnd.github+json"}


def _api(conn: dict, method: str, url: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={**_headers(conn), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise DeliveryError(
            f"{conn['provider']} API {method} {url} failed: HTTP {exc.code}"
        ) from exc


def _repo_exists(conn: dict, repo: str) -> bool:
    base = _api_base(conn)
    if conn["provider"] == "gitlab":
        project = urllib.parse.quote(f"{conn['namespace']}/{repo}", safe="")
        return _api(conn, "GET", f"{base}/projects/{project}") is not None
    return _api(conn, "GET", f"{base}/repos/{conn['namespace']}/{repo}") is not None


def _create_repo(conn: dict, repo: str) -> None:
    base = _api_base(conn)
    if conn["provider"] == "gitlab":
        namespaces = _api(
            conn, "GET",
            f"{base}/namespaces?search={urllib.parse.quote(conn['namespace'])}",
        ) or []
        matches = [n for n in namespaces if n.get("path") == conn["namespace"]
                   or n.get("full_path") == conn["namespace"]]
        if not matches:
            raise DeliveryError(
                f"gitlab namespace '{conn['namespace']}' not found or token "
                "lacks access"
            )
        _api(conn, "POST", f"{base}/projects", {
            "name": repo, "path": repo,
            "namespace_id": matches[0]["id"], "visibility": "private",
        })
    else:
        _api(conn, "POST", f"{base}/orgs/{conn['namespace']}/repos",
             {"name": repo, "private": True})
