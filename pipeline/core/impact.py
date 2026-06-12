"""Explicit field→artifact impact map and regeneration routing (spec §9).

Routing decisions come from this explicit map — never from string-matching
diff output. Regeneration is always FULL regen per artifact (D5).

§9 table:
  sql files / sql intent fields        → regen ords + tests
  destination.*, connections, mappings → regen mulesoft + tests
  specs files                          → regen ALL
  testing block / job/tests/*          → regen tests only
  mode field only / nothing changed    → regen nothing

Inputs not listed in the table:
  samples (output examples) describe the produced file → mulesoft + tests
  job_name / direction renames every deployed object   → ALL
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.core.intent import Intent
from pipeline.core.lockfile import ARTIFACTS, Lockfile

ALL = frozenset(ARTIFACTS)

IMPACT_MAP: dict[str, frozenset[str]] = {
    "sql": frozenset({"ords", "tests"}),
    "destination": frozenset({"mulesoft", "tests"}),
    "specs": ALL,
    "samples": frozenset({"mulesoft", "tests"}),
    "testing": frozenset({"tests"}),
    "identity": ALL,
    "mode": frozenset(),
}

# Intent front-matter field → impact category. `sources` is split per list.
_FIELD_CATEGORIES: dict[str, str] = {
    "job_name": "identity",
    "direction": "identity",
    "mode": "mode",
    "destination": "destination",
    "connections": "destination",
    "mulesoft_delivery": "destination",
    "testing": "testing",
}

_SOURCE_LIST_CATEGORIES: dict[str, str] = {
    "sql": "sql",
    "specs": "specs",
    "samples": "samples",
    "mappings": "destination",
}


def _changed_intent_categories(old: dict, new: dict) -> set[str]:
    categories: set[str] = set()
    for field, category in _FIELD_CATEGORIES.items():
        if old.get(field) != new.get(field):
            categories.add(category)
    old_sources = old.get("sources") or {}
    new_sources = new.get("sources") or {}
    for list_name, category in _SOURCE_LIST_CATEGORIES.items():
        if old_sources.get(list_name) != new_sources.get(list_name):
            categories.add(category)
    return categories


def _classify_path(path: str, intent: Intent) -> str:
    """Map a changed repo-relative input path to an impact category."""
    rel = path.removeprefix("job/")
    for source, category in (
        (intent.sources.sql, "sql"),
        (intent.sources.specs, "specs"),
        (intent.sources.samples, "samples"),
        (intent.sources.mappings, "destination"),
    ):
        if any(s.file == rel for s in source):
            return category
    if rel.startswith("tests/"):
        return "testing"
    # Fall back on directory convention for files no longer referenced.
    top = rel.split("/", 1)[0]
    return _SOURCE_LIST_CATEGORIES.get(top, "specs")  # unknown → conservative: ALL


def _changed_file_categories(
    old_hashes: dict[str, str], new_hashes: dict[str, str], intent: Intent
) -> set[str]:
    changed = {
        path
        for path in set(old_hashes) | set(new_hashes)
        if old_hashes.get(path) != new_hashes.get(path)
    }
    changed.discard("job/intent.md")  # intent changes are routed field-by-field
    return {_classify_path(path, intent) for path in changed}


def decide_regeneration(
    repo_root: Path,
    intent: Intent,
    new_raw_intent: dict,
    new_hashes: dict[str, str],
    lock: Optional[Lockfile],
) -> set[str]:
    """Return the set of artifacts to FULLY regenerate this run."""
    if lock is None:
        return set(ALL)  # first run → generate everything

    categories = _changed_intent_categories(lock.intent_snapshot, new_raw_intent)
    categories |= _changed_file_categories(lock.input_hashes, new_hashes, intent)

    regen: set[str] = set()
    for category in categories:
        regen |= IMPACT_MAP[category]

    # An artifact missing from the lockfile or deleted from disk is stale
    # regardless of input hashes (e.g. someone removed generated/).
    for artifact in ARTIFACTS:
        record = lock.artifacts.get(artifact)
        if record is None or not (repo_root / record.path).is_file():
            regen.add(artifact)
    return regen
