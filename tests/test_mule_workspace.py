"""In-repo MuleSoft workspace: clone → (Copilot edits) → validate + push."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pipeline.deployers import mule_git, mule_workspace

CONN = {
    "provider": "gitlab",
    "host": "gitlab.com",
    "namespace": "charles-darwin-university/itms/mulesoft",
    "token": "sekret-token-value",
}


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    return bare


def _seed(origin: Path, work: Path, files: dict, branch: str = "HEAD"):
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    if branch != "HEAD":
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", branch], check=True)
    for rel, content in files.items():
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "seed"], check=True,
    )
    ref = "HEAD" if branch == "HEAD" else branch
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", ref], check=True)


def _show(origin: Path, ref: str, path: str) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(origin), "show", f"{ref}:{path}"],
        capture_output=True, text=True, check=True,
    ).stdout


def _stub(monkeypatch, origin):
    monkeypatch.setattr(mule_git, "_remote_url", lambda conn, repo: str(origin))


def test_checkout_creates_workspace_on_new_branch(monkeypatch, origin, tmp_path):
    _seed(origin, tmp_path / "seed",
          {"pom.xml": "<project/>", "src/main/mule/existing.xml": "<mule/>"})
    _stub(monkeypatch, origin)
    repo_root = tmp_path / "factory"
    repo_root.mkdir()

    info = mule_workspace.prepare_workspace(
        repo_root, CONN, "mule-app", "student_download_v1", branch=None,
    )
    assert info["based_on_existing_branch"] is False
    assert Path(info["workspace"]).is_dir()
    # token must NOT be persisted in the workspace git config
    cfg = (Path(info["workspace"]) / ".git" / "config").read_text()
    assert CONN["token"] not in cfg
    assert "existing.xml" in info["existing_flows"]


def test_checkout_uses_existing_branch(monkeypatch, origin, tmp_path):
    _seed(origin, tmp_path / "seed", {"pom.xml": "<project/>"})
    _seed(origin, tmp_path / "seed2",
          {"RELEASE.md": "release content", "pom.xml": "<project/>"},
          branch="release/2026")
    _stub(monkeypatch, origin)
    repo_root = tmp_path / "factory"
    repo_root.mkdir()

    info = mule_workspace.prepare_workspace(
        repo_root, CONN, "mule-app", "job1", branch="release/2026",
    )
    assert info["based_on_existing_branch"] is True
    assert (Path(info["workspace"]) / "RELEASE.md").read_text() == "release content"


def test_checkout_refuses_existing_workspace(monkeypatch, origin, tmp_path):
    _seed(origin, tmp_path / "seed", {"pom.xml": "<project/>"})
    _stub(monkeypatch, origin)
    repo_root = tmp_path / "factory"
    repo_root.mkdir()
    mule_workspace.prepare_workspace(repo_root, CONN, "mule-app", "job1")
    with pytest.raises(mule_workspace.DeliveryError, match="already exists"):
        mule_workspace.prepare_workspace(repo_root, CONN, "mule-app", "job1")


def test_deliver_pushes_new_and_edited_files(monkeypatch, origin, tmp_path):
    _seed(origin, tmp_path / "seed",
          {"pom.xml": "<project>v1</project>",
           "src/main/mule/existing.xml": "<mule><flow name='old'/></mule>"})
    _stub(monkeypatch, origin)
    repo_root = tmp_path / "factory"
    repo_root.mkdir()
    # need a connections.yaml for the secret scan (empty secrets is fine here)
    (repo_root / "connections.yaml").write_text("dummy: {type: x}\n")

    info = mule_workspace.prepare_workspace(repo_root, CONN, "mule-app", "job1",
                                            branch="cdu/job1")
    ws = Path(info["workspace"])
    # Simulate Copilot's edits: modify an existing flow AND add a new one.
    (ws / "src/main/mule/existing.xml").write_text("<mule><flow name='updated'/></mule>")
    (ws / "src/main/mule/new_flow.xml").write_text("<mule><flow name='brand-new'/></mule>")
    (ws / "src/main/resources").mkdir(parents=True, exist_ok=True)
    (ws / "src/main/resources/transform.dwl").write_text("%dw 2.0\n---\npayload")

    facts = mule_workspace.deliver_workspace(repo_root, CONN, "mule-app", "job1")
    assert facts["pushed"] is True
    assert set(facts["changed_files"]) == {
        "src/main/mule/existing.xml",
        "src/main/mule/new_flow.xml",
        "src/main/resources/transform.dwl",
    }
    assert "updated" in _show(origin, "cdu/job1", "src/main/mule/existing.xml")
    assert "brand-new" in _show(origin, "cdu/job1", "src/main/mule/new_flow.xml")


def test_deliver_no_changes_is_noop(monkeypatch, origin, tmp_path):
    _seed(origin, tmp_path / "seed", {"pom.xml": "<project/>"})
    _stub(monkeypatch, origin)
    repo_root = tmp_path / "factory"
    repo_root.mkdir()
    (repo_root / "connections.yaml").write_text("dummy: {type: x}\n")
    mule_workspace.prepare_workspace(repo_root, CONN, "mule-app", "job1", branch="cdu/job1")

    facts = mule_workspace.deliver_workspace(repo_root, CONN, "mule-app", "job1")
    assert facts["pushed"] is False
    assert facts["changed_files"] == []


def test_deliver_rejects_malformed_xml(monkeypatch, origin, tmp_path):
    _seed(origin, tmp_path / "seed", {"pom.xml": "<project/>"})
    _stub(monkeypatch, origin)
    repo_root = tmp_path / "factory"
    repo_root.mkdir()
    (repo_root / "connections.yaml").write_text("dummy: {type: x}\n")
    info = mule_workspace.prepare_workspace(repo_root, CONN, "mule-app", "job1",
                                            branch="cdu/job1")
    (Path(info["workspace"]) / "src/main/mule/bad.xml").parent.mkdir(parents=True, exist_ok=True)
    (Path(info["workspace"]) / "src/main/mule/bad.xml").write_text("<mule><flow></mule>")

    with pytest.raises(mule_workspace.DeliveryError, match="not well-formed XML"):
        mule_workspace.deliver_workspace(repo_root, CONN, "mule-app", "job1")


def test_deliver_blocks_secret_value(monkeypatch, origin, tmp_path):
    _seed(origin, tmp_path / "seed", {"pom.xml": "<project/>"})
    _stub(monkeypatch, origin)
    repo_root = tmp_path / "factory"
    repo_root.mkdir()
    # connections.yaml declares a secret env var; set its value in the env.
    (repo_root / "connections.yaml").write_text(
        "mule:\n  type: git_repo\n  secrets: { token: WS_TEST_TOKEN }\n"
    )
    monkeypatch.setenv("WS_TEST_TOKEN", "super-secret-xyz-123")
    info = mule_workspace.prepare_workspace(repo_root, CONN, "mule-app", "job1",
                                            branch="cdu/job1")
    (Path(info["workspace"]) / "leak.txt").write_text("token=super-secret-xyz-123\n")

    with pytest.raises(mule_workspace.DeliveryError, match="value of secret WS_TEST_TOKEN"):
        mule_workspace.deliver_workspace(repo_root, CONN, "mule-app", "job1")
