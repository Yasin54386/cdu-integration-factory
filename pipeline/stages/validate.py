"""Stage 0 — validate (spec §5 rules, §10 contract).

Exit semantics (via ValidationResult): every problem is reported as one
clear, human-readable error; warnings never fail the run but are written
to reports/validate_<ts>.md and surfaced in the run report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pipeline.core.intent import Intent, IntentError, load_intent
from pipeline.core.resolver import (
    ResolverError,
    load_connections_yaml,
    missing_secrets,
)

JOB_DIRS = ("sql", "specs", "samples", "mappings", "tests")


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    intent: Optional[Intent] = None
    raw_intent: dict = field(default_factory=dict)
    body_notes: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(repo_root: Path) -> ValidationResult:
    result = ValidationResult()
    job_dir = repo_root / "job"
    intent_path = job_dir / "intent.md"

    # 1. intent.md exists, parses, and satisfies the schema (incl. job_name
    #    pattern, mode/direction/file_format enums, known roles).
    try:
        result.intent, result.raw_intent, result.body_notes = load_intent(intent_path)
    except IntentError as exc:
        result.errors.append(str(exc))
        return result
    intent = result.intent

    # 2. Every referenced file exists → missing = FAIL with the exact path.
    referenced = intent.referenced_files()
    for rel in referenced:
        if not (job_dir / rel).is_file():
            result.errors.append(
                f"intent.md references job/{rel} but that file does not exist"
            )

    # 3. Files present under job/*/ but never referenced = WARNING.
    referenced_set = {str(Path(rel).as_posix()) for rel in referenced}
    for sub in JOB_DIRS:
        directory = job_dir / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name == ".gitkeep":
                continue
            rel = path.relative_to(job_dir).as_posix()
            if sub == "tests":
                continue  # everything in job/tests/ is implicitly a test input
            if rel not in referenced_set:
                result.warnings.append(
                    f"job/{rel} exists but is not referenced in intent.md "
                    "(junk, or a forgotten reference?)"
                )

    # 4. Every named connection exists in connections.yaml; 5. every secret
    #    NAME each used connection declares exists as an env var.
    try:
        connections = load_connections_yaml(repo_root)
    except ResolverError as exc:
        result.errors.append(str(exc))
        return result
    for conn_name in intent.used_connections():
        if conn_name not in connections:
            result.errors.append(
                f"connection '{conn_name}' (named in intent.md) is not defined "
                f"in connections.yaml (known: {sorted(connections)})"
            )
            continue
        for secret_name in missing_secrets(connections[conn_name]):
            result.errors.append(
                f"Secret {secret_name} not configured in repo Settings → "
                "Secrets → Actions"
            )

    if result.warnings:
        _write_warning_report(repo_root, result)
    return result


def _write_warning_report(repo_root: Path, result: ValidationResult) -> None:
    reports_dir = repo_root / "reports"
    reports_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    lines = ["# Validation warnings", ""]
    lines += [f"- ⚠ {w}" for w in result.warnings]
    (reports_dir / f"validate_{timestamp}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
