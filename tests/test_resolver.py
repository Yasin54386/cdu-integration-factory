"""Resolver: metadata + env secrets merged in memory; clear failures (§7)."""

from __future__ import annotations

import pytest

from pipeline.core.resolver import (
    ResolverError,
    load_connections_yaml,
    missing_secrets,
    resolve,
)


def test_resolve_merges_metadata_and_secret_values(factory_repo):
    oracle = resolve(factory_repo, "oracle_dev")
    assert oracle["host"] == "oradev.cdu.internal"
    assert oracle["schema"] == "INTEGRATION"
    assert oracle["user"] == "test-user"
    assert oracle["password"] == "test-password"
    assert "secrets" not in oracle  # secret NAMES are replaced by values


def test_unknown_connection_raises(factory_repo):
    with pytest.raises(ResolverError, match="'nope' not defined"):
        resolve(factory_repo, "nope")


def test_missing_env_var_raises_with_secret_name_only(factory_repo, monkeypatch):
    monkeypatch.delenv("ORACLE_DEV_PASSWORD")
    with pytest.raises(ResolverError) as excinfo:
        resolve(factory_repo, "oracle_dev")
    assert "ORACLE_DEV_PASSWORD" in str(excinfo.value)
    assert "test-password" not in str(excinfo.value)  # never the value


def test_missing_secrets_lists_unset_names(factory_repo, monkeypatch):
    connections = load_connections_yaml(factory_repo)
    assert missing_secrets(connections["sftp_dev"]) == []
    monkeypatch.delenv("SFTP_DEV_USER")
    assert missing_secrets(connections["sftp_dev"]) == ["SFTP_DEV_USER"]


def test_all_spec_connections_present(factory_repo):
    connections = load_connections_yaml(factory_repo)
    assert {"oracle_dev", "mule_dev", "git_main", "sftp_dev"} <= set(connections)
