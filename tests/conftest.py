"""Shared fixtures: a disposable factory repo with the example job filled in."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Every secret env var declared in connections.yaml, set to harmless values
# so validate's secret pre-flight passes in tests.
ALL_SECRET_ENV = {
    "ORACLE_DEV_USER": "test-user",
    "ORACLE_DEV_PASSWORD": "test-password",
    "MULE_DEV_CLIENT_ID": "test-client-id",
    "MULE_DEV_CLIENT_SECRET": "test-client-secret",
    "SFTP_DEV_USER": "test-sftp-user",
    "SFTP_DEV_PRIVATE_KEY": "test-sftp-key",
    "GH_PIPELINE_TOKEN": "test-token",
}


@pytest.fixture
def secrets_env(monkeypatch):
    for name, value in ALL_SECRET_ENV.items():
        monkeypatch.setenv(name, value)
    return ALL_SECRET_ENV


@pytest.fixture
def factory_repo(tmp_path: Path, secrets_env) -> Path:
    """A tmp repo root that mirrors a feature branch with the example job."""
    shutil.copy(REPO_ROOT / "connections.yaml", tmp_path / "connections.yaml")
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    example = REPO_ROOT / "examples" / "student_download"
    job_dir = tmp_path / "job"
    shutil.copytree(example, job_dir)
    for sub in ("specs", "mappings", "tests"):
        (job_dir / sub).mkdir(exist_ok=True)
    return tmp_path
