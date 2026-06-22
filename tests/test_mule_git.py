"""MuleSoft git-handoff deployer (spec §17 amendment).

`deliver` is exercised against a real local bare repository standing in for
GitHub/GitLab; only the provider API calls (_repo_exists/_create_repo) and
the remote URL are stubbed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pipeline.core.intent import MulesoftDelivery
from pipeline.deployers import mule_git

FLOW_XML = "<mule><flow name='student_download_v1-main-flow'/></mule>"

CONN = {
    "provider": "github",
    "host": "github.example.edu",
    "namespace": "cdu-integration",
    "token": "sekret-token-value",
}


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    return bare


@pytest.fixture
def flow_file(tmp_path: Path) -> Path:
    path = tmp_path / "flow.xml"
    path.write_text(FLOW_XML)
    return path


def _show(origin: Path, ref: str, path: str) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(origin), "show", f"{ref}:{path}"],
        capture_output=True, text=True, check=True,
    ).stdout


def _stub_remote(monkeypatch, origin: Path, exists: bool, created: list):
    monkeypatch.setattr(mule_git, "_repo_exists", lambda conn, repo: exists)
    monkeypatch.setattr(
        mule_git, "_create_repo",
        lambda conn, repo: created.append(repo),
    )
    monkeypatch.setattr(
        mule_git, "_remote_url", lambda conn, repo: str(origin)
    )


def test_new_repo_mode_creates_repo_and_pushes_full_scaffold(
    monkeypatch, origin, flow_file
):
    created: list = []
    _stub_remote(monkeypatch, origin, exists=False, created=created)
    facts = mule_git.deliver(CONN, flow_file, "student_download_v1", None)

    assert created == ["cdu-student-download-v1"]
    assert facts["mulesoft_repo"] == "cdu-integration/cdu-student-download-v1"
    assert facts["mulesoft_branch"] == "cdu/student_download_v1"
    assert facts["mulesoft_url"] == (
        "https://github.example.edu/cdu-integration/cdu-student-download-v1"
    )
    branch = "cdu/student_download_v1"
    assert _show(origin, branch, "src/main/mule/student_download_v1_flow.xml") == FLOW_XML
    assert "cdu-student-download-v1" in _show(origin, branch, "pom.xml")
    assert "minMuleVersion" in _show(origin, branch, "mule-artifact.json")


def test_existing_repo_mode_replaces_only_the_flow(monkeypatch, origin, flow_file, tmp_path):
    # Seed the "existing" repo with build files on its default branch.
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    (seed / "pom.xml").write_text("<project>institute pom</project>")
    subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "seed"], check=True,
    )
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "HEAD"], check=True)

    _stub_remote(monkeypatch, origin, exists=True, created=[])
    delivery = MulesoftDelivery(repo="student-export-app", branch="cdu/custom")
    facts = mule_git.deliver(CONN, flow_file, "student_download_v1", delivery)

    assert facts["mulesoft_repo"] == "cdu-integration/student-export-app"
    assert facts["mulesoft_branch"] == "cdu/custom"
    assert _show(origin, "cdu/custom",
                 "src/main/mule/student_download_v1_flow.xml") == FLOW_XML
    # the institute's build files survive on the delivery branch
    assert _show(origin, "cdu/custom", "pom.xml") == "<project>institute pom</project>"


def test_redelivery_force_updates_the_same_branch(monkeypatch, origin, flow_file):
    _stub_remote(monkeypatch, origin, exists=False, created=[])
    mule_git.deliver(CONN, flow_file, "student_download_v1", None)
    monkeypatch.setattr(mule_git, "_repo_exists", lambda conn, repo: True)
    flow_file.write_text("<mule><flow name='v2'/></mule>")
    facts = mule_git.deliver(CONN, flow_file, "student_download_v1", None)
    assert "v2" in _show(origin, facts["mulesoft_branch"],
                         "src/main/mule/student_download_v1_flow.xml")


def test_named_but_missing_repo_fails_clearly(monkeypatch, origin, flow_file):
    _stub_remote(monkeypatch, origin, exists=False, created=[])
    with pytest.raises(mule_git.DeliveryError, match="'nope' does not exist"):
        mule_git.deliver(CONN, flow_file, "student_download_v1",
                         MulesoftDelivery(repo="nope"))


def test_git_errors_never_leak_the_token():
    # Unreachable local port: fails instantly, and the failing URL embeds
    # the token, exercising the redaction path without any DNS/network.
    url = f"https://x-access-token:{CONN['token']}@127.0.0.1:1/x/y.git"
    with pytest.raises(mule_git.DeliveryError) as excinfo:
        mule_git._git(CONN, None, "ls-remote", url)
    assert CONN["token"] not in str(excinfo.value)


def test_remote_url_shape_per_provider():
    github = mule_git._remote_url(CONN, "myrepo")
    assert github.startswith("https://x-access-token:sekret-token-value@")
    gitlab = mule_git._remote_url({**CONN, "provider": "gitlab"}, "myrepo")
    assert gitlab.startswith("https://oauth2:sekret-token-value@")


# ── inspect_repo_structure ───────────────────────────────────────────────────

def test_inspect_full_mule_project(tmp_path):
    import json
    job_name = "student_download_v1"
    mule_src = tmp_path / "src" / "main" / "mule"
    mule_src.mkdir(parents=True)
    (mule_src / f"{job_name}_flow.xml").write_text("<mule/>")
    (mule_src / "other_flow.xml").write_text("<mule/>")
    (tmp_path / "pom.xml").write_text("<project/>")
    (tmp_path / "mule-artifact.json").write_text(
        json.dumps({"minMuleVersion": "4.6.0"})
    )

    info = mule_git.inspect_repo_structure(tmp_path, job_name)

    assert info["has_pom"] is True
    assert info["has_mule_artifact"] is True
    assert info["has_mule_src_dir"] is True
    assert info["looks_like_mule_project"] is True
    assert info["our_flow_exists"] is True
    assert info["mule_version"] == "4.6.0"
    assert f"{job_name}_flow.xml" in info["existing_flows"]
    assert "other_flow.xml" in info["existing_flows"]


def test_inspect_empty_repo(tmp_path):
    info = mule_git.inspect_repo_structure(tmp_path, "my_job")
    assert info["looks_like_mule_project"] is False
    assert info["our_flow_exists"] is False
    assert info["existing_flows"] == []
    assert info["mule_version"] is None


def test_inspect_our_flow_not_yet_present(tmp_path):
    mule_src = tmp_path / "src" / "main" / "mule"
    mule_src.mkdir(parents=True)
    (mule_src / "other_flow.xml").write_text("<mule/>")
    (tmp_path / "pom.xml").write_text("<project/>")

    info = mule_git.inspect_repo_structure(tmp_path, "new_job")
    assert info["looks_like_mule_project"] is True
    assert info["our_flow_exists"] is False


def test_inspect_mule_repo_uses_clone(monkeypatch, origin, flow_file):
    """inspect_mule_repo clones the real bare repo and reads its structure."""
    # Seed the bare repo with a minimal Mule project
    import json as _json
    seed = flow_file.parent / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    mule_src = seed / "src" / "main" / "mule"
    mule_src.mkdir(parents=True)
    (mule_src / "my_job_flow.xml").write_text("<mule/>")
    (seed / "mule-artifact.json").write_text(
        _json.dumps({"minMuleVersion": "4.5.0"})
    )
    subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "seed"], check=True,
    )
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "HEAD"], check=True)

    monkeypatch.setattr(mule_git, "_remote_url", lambda conn, repo: str(origin))
    info = mule_git.inspect_mule_repo(CONN, "my-mule-app", "my_job")

    assert info["repo"] == "my-mule-app"
    assert info["namespace"] == CONN["namespace"]
    assert info["mule_version"] == "4.5.0"
    assert info["our_flow_exists"] is True


def test_deliver_raises_for_non_mule_existing_repo(monkeypatch, origin, flow_file, tmp_path):
    """deliver() in existing-repo mode rejects repos that aren't Mule projects."""
    # Seed a bare repo WITHOUT mule structure
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    (seed / "README.md").write_text("not a mule project")
    subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "init"], check=True,
    )
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "HEAD"], check=True)

    _stub_remote(monkeypatch, origin, exists=True, created=[])
    delivery = MulesoftDelivery(repo="not-a-mule-repo")
    with pytest.raises(mule_git.DeliveryError, match="does not look like a MuleSoft project"):
        mule_git.deliver(CONN, flow_file, "student_download_v1", delivery)


def test_deliver_existing_repo_includes_inspection_facts(monkeypatch, origin, flow_file, tmp_path):
    """deliver() returns existing_flows and mule_version from the repo."""
    import json as _json
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    mule_src = seed / "src" / "main" / "mule"
    mule_src.mkdir(parents=True)
    (mule_src / "other_flow.xml").write_text("<mule/>")
    (seed / "pom.xml").write_text("<project/>")
    (seed / "mule-artifact.json").write_text(_json.dumps({"minMuleVersion": "4.7.0"}))
    subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "seed"], check=True,
    )
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "HEAD"], check=True)

    _stub_remote(monkeypatch, origin, exists=True, created=[])
    delivery = MulesoftDelivery(repo="institute-mule-app")
    facts = mule_git.deliver(CONN, flow_file, "student_download_v1", delivery)

    assert facts["mule_version"] == "4.7.0"
    assert "other_flow.xml" in facts["existing_flows"]
    # existing_flows reflects pre-delivery state; our flow wasn't there yet
    assert "student_download_v1_flow.xml" not in facts["existing_flows"]
