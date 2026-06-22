"""Intent contract: Pydantic models for job/intent.md front-matter.

This module is the single source of truth for the intent schema (spec §5).
The markdown body below the front-matter is free-form human notes; the
pipeline ignores it except to pass it to Copilot as extra context.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

JOB_NAME_PATTERN = r"^[a-z][a-z0-9_]{2,40}$"

SQL_ROLES = {"staging_load", "export", "procedure"}
SPEC_ROLES = {"business_rules"}
SAMPLE_ROLES = {"output_example"}
MAPPING_ROLES = {"field_mapping"}
KNOWN_ROLES = SQL_ROLES | SPEC_ROLES | SAMPLE_ROLES | MAPPING_ROLES


class IntentError(ValueError):
    """Raised when job/intent.md cannot be parsed into a valid Intent."""


def _role_checker(allowed: set[str]):
    def check(value: str) -> str:
        if value not in allowed:
            raise ValueError(
                f"unknown role '{value}' — allowed roles here: {sorted(allowed)}"
            )
        return value

    return check


class SqlSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    role: str
    _check_role = field_validator("role")(_role_checker(SQL_ROLES))


class SpecSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    role: str
    _check_role = field_validator("role")(_role_checker(SPEC_ROLES))


class SampleSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    role: str
    _check_role = field_validator("role")(_role_checker(SAMPLE_ROLES))


class MappingSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    role: str
    _check_role = field_validator("role")(_role_checker(MAPPING_ROLES))


class Sources(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: list[SqlSource] = Field(default_factory=list)
    specs: list[SpecSource] = Field(default_factory=list)
    samples: list[SampleSource] = Field(default_factory=list)
    mappings: list[MappingSource] = Field(default_factory=list)

    def all_files(self) -> list[str]:
        """Every referenced supporting-file path, relative to job/."""
        return [
            s.file
            for group in (self.sql, self.specs, self.samples, self.mappings)
            for s in group
        ]


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection: str
    path: str
    file_format: Literal["csv", "fixed", "json", "xml"]
    file_name_pattern: str


class Connections(BaseModel):
    model_config = ConfigDict(extra="forbid")
    oracle: str = "oracle_dev"
    mulesoft: str = "mule_dev"


class MulesoftDelivery(BaseModel):
    """Where the generated Mule app is pushed when the mulesoft connection
    is a git repo (spec §17 amendment). Omit `repo` to have the factory
    create `cdu-<job-name>`; omit `branch` for the default `cdu/<job_name>`.
    """

    model_config = ConfigDict(extra="forbid")
    repo: Optional[str] = None
    branch: Optional[str] = None


class ExpectedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    compare: str


class Testing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_logic: Optional[str] = None
    key_assertions: list[str] = Field(default_factory=list)
    expected_files: list[ExpectedFile] = Field(default_factory=list)

    def all_files(self) -> list[str]:
        return [e.file for e in self.expected_files]


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str = Field(pattern=JOB_NAME_PATTERN)
    mode: Literal["generate", "deploy"]
    direction: Literal["download", "upload"]
    sources: Sources
    destination: Destination
    connections: Connections = Field(default_factory=Connections)
    mulesoft_delivery: Optional[MulesoftDelivery] = None
    testing: Optional[Testing] = None

    def referenced_files(self) -> list[str]:
        """All file paths referenced anywhere in the intent, relative to job/."""
        files = self.sources.all_files()
        if self.testing:
            files += self.testing.all_files()
        return files

    def used_connections(self) -> list[str]:
        """Logical connection names this job uses (intent + destination)."""
        names = [self.connections.oracle, self.connections.mulesoft,
                 self.destination.connection]
        return list(dict.fromkeys(names))  # dedupe, preserve order


_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def split_front_matter(text: str) -> tuple[dict, str]:
    """Split intent.md into (front-matter dict, markdown body)."""
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        raise IntentError(
            "job/intent.md has no YAML front-matter "
            "(expected a block delimited by '---' lines at the top of the file)"
        )
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise IntentError(f"job/intent.md front-matter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise IntentError("job/intent.md front-matter must be a YAML mapping")
    return data, match.group(2).strip()


def load_intent(intent_path: Path) -> tuple[Intent, dict, str]:
    """Parse job/intent.md → (validated Intent, raw front-matter dict, body notes)."""
    if not intent_path.is_file():
        raise IntentError(f"intent file not found at {intent_path}")
    raw, body = split_front_matter(intent_path.read_text(encoding="utf-8"))
    try:
        intent = Intent.model_validate(raw)
    except Exception as exc:
        raise IntentError(f"intent.md failed schema validation:\n{exc}") from exc
    return intent, raw, body
