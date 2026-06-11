"""Connection resolution (spec §7).

Three layers, merged in memory at runtime only:
  1. intent names logical connections (zero secrets, zero hostnames)
  2. connections.yaml maps logical name → metadata + secret NAMES
  3. GitHub Actions Secrets hold the VALUES, injected as env vars

Resolved dicts are never logged and never written to disk.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

CONNECTIONS_FILE = "connections.yaml"


class ResolverError(ValueError):
    """Raised for unknown connections or missing secret env vars."""


def load_connections_yaml(repo_root: Path) -> dict:
    path = repo_root / CONNECTIONS_FILE
    if not path.is_file():
        raise ResolverError(f"{CONNECTIONS_FILE} not found at repo root ({path})")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ResolverError(f"{CONNECTIONS_FILE} must be a YAML mapping of connection names")
    return data


def get_connection_meta(repo_root: Path, conn_name: str) -> dict:
    connections = load_connections_yaml(repo_root)
    if conn_name not in connections:
        raise ResolverError(
            f"connection '{conn_name}' not defined in {CONNECTIONS_FILE} "
            f"(known: {sorted(connections)})"
        )
    return connections[conn_name]


def missing_secrets(meta: dict) -> list[str]:
    """Secret env-var NAMES declared by a connection that are not set."""
    return [name for name in meta.get("secrets", {}).values() if name not in os.environ]


def resolve(repo_root: Path, conn_name: str) -> dict:
    """Merge metadata with secret values from the environment.

    The returned dict contains live credentials: keep it in memory only —
    never log it, never write it to disk.
    """
    meta = get_connection_meta(repo_root, conn_name)
    secrets = meta.get("secrets", {})
    missing = missing_secrets(meta)
    if missing:
        raise ResolverError(
            "; ".join(
                f"Secret {name} not configured in repo Settings → Secrets → Actions"
                for name in missing
            )
        )
    creds = {key: os.environ[env_name] for key, env_name in secrets.items()}
    merged = {k: v for k, v in meta.items() if k != "secrets"}
    merged.update(creds)
    return merged
