"""Read/write .cdu-lock.json — per-branch pipeline state (spec §8, D7).

The lockfile is committed to the feature branch by the pipeline with
[skip ci]. Branch dies → state dies with it.

Note: in addition to the §8 fields, the lockfile stores `intent_snapshot`
(the parsed front-matter from the last run). §9 requires field-level change
detection on the intent ("which intent fields changed → which artifacts
regenerate"), which needs the previous parsed intent, not just its hash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

LOCKFILE_NAME = ".cdu-lock.json"
SCHEMA_VERSION = 1

ARTIFACTS = ("ords", "mulesoft", "tests")

# Canonical sub-stage names and their artifact mapping.
SUBSTAGES = ("sql", "mulesoft", "tests")
SUBSTAGE_TO_ARTIFACT: dict[str, str] = {
    "sql": "ords",
    "mulesoft": "mulesoft",
    "tests": "tests",
}


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    path: str
    input_hash_at_gen: str
    generated_at: str


class SubstageRecord(BaseModel):
    """Per-sub-stage state written after each generate + deploy/run phase."""
    model_config = ConfigDict(extra="allow")
    status: str = "pending"      # pending | done
    input_hash: str = ""         # combined_input_hash when this substage last ran
    generated_at: str = ""       # when the artifact was (re)generated
    deployed_at: str = ""        # when it was deployed (sql/mulesoft); empty for tests
    test_result: str = ""        # pass | fail | "" (tests substage only)


class StageSnapshot(BaseModel):
    """Immutable record of one successful generation of a sub-stage artifact.

    Written to stage_history[substage] BEFORE the next regeneration so there
    is always a trail of what the pipeline produced and which git commit
    holds that version. Used by `cdu rollback` to restore a prior version.
    """
    model_config = ConfigDict(extra="allow")
    generated_at: str               # ISO-8601 UTC timestamp
    input_hash: str                 # combined_input_hash that produced this version
    artifact_path: str              # repo-relative path to the generated file
    git_commit: str = ""            # HEAD SHA at commit-back time (filled by run.py)
    run_id: str = ""                # GitHub Actions run ID or "local"
    mode: str = ""                  # generate | deploy
    test_result: str = ""           # pass | fail | "" (tests substage only)


class Lockfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = SCHEMA_VERSION
    job_name: str = ""
    last_run_id: str = ""
    last_run_at: str = ""
    last_mode: str = ""
    input_hashes: dict[str, str] = Field(default_factory=dict)
    combined_input_hash: str = ""
    intent_snapshot: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, ArtifactRecord] = Field(default_factory=dict)
    substages: dict[str, SubstageRecord] = Field(default_factory=dict)
    stage_history: dict[str, list[StageSnapshot]] = Field(default_factory=dict)
    deployed: dict[str, Any] = Field(default_factory=dict)
    last_test_result: dict[str, Any] = Field(default_factory=dict)


def lockfile_path(repo_root: Path) -> Path:
    return repo_root / LOCKFILE_NAME


def read_lockfile(repo_root: Path) -> Optional[Lockfile]:
    """Return the branch lockfile, or None on first run."""
    path = lockfile_path(repo_root)
    if not path.is_file():
        return None
    return Lockfile.model_validate(json.loads(path.read_text(encoding="utf-8")))


def write_lockfile(repo_root: Path, lock: Lockfile) -> Path:
    path = lockfile_path(repo_root)
    path.write_text(
        json.dumps(lock.model_dump(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


def push_stage_snapshot(lock: Lockfile, substage: str, snapshot: StageSnapshot,
                        max_history: int = 10) -> None:
    """Prepend snapshot to stage_history[substage], capping at max_history entries."""
    history = lock.stage_history.setdefault(substage, [])
    history.insert(0, snapshot)
    if len(history) > max_history:
        lock.stage_history[substage] = history[:max_history]

