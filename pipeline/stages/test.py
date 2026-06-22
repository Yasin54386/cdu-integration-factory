"""Stage — test (spec §10, D10).

If the intent has a testing: block (or files in job/tests/), the generated
tests encode those human assertions. Otherwise the tests are AI-authored
defaults and the report is flagged accordingly — lower confidence.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.core import gitops
from pipeline.core.lockfile import LOCKFILE_NAME, read_lockfile, write_lockfile
from pipeline.stages.validate import ValidationResult

AI_AUTHORED_WARNING = "⚠ assertions are AI-authored — lower confidence"


class TestStageError(RuntimeError):
    pass


def has_human_assertions(repo_root: Path, validation: ValidationResult) -> bool:
    if validation.intent and validation.intent.testing:
        return True
    tests_dir = repo_root / "job" / "tests"
    return tests_dir.is_dir() and any(
        p.is_file() and p.name != ".gitkeep" for p in tests_dir.rglob("*")
    )


def run_tests(repo_root: Path, validation: ValidationResult,
              run_url: str = "") -> bool:
    intent = validation.intent
    assert intent is not None, "test requires a passing validation"
    lock = read_lockfile(repo_root)
    if lock is None or "tests" not in lock.artifacts:
        raise TestStageError("no generated tests found — run generate first")

    test_path = repo_root / lock.artifacts["tests"].path
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-v", "--tb=short"],
        cwd=repo_root, capture_output=True, text=True,
    )
    passed = result.returncode == 0

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    report_rel = f"reports/run_{timestamp}.md"
    report = build_report(
        repo_root, validation, passed, result.stdout, run_url, lock
    )
    report_path = repo_root / report_rel
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    lock.last_test_result = {
        "status": "pass" if passed else "fail",
        "report": report_rel,
    }
    write_lockfile(repo_root, lock)

    gitops.write_job_summary(report)
    gitops.post_pr_comment(repo_root, report)
    gitops.commit_back(
        repo_root,
        ["reports/", LOCKFILE_NAME],
        f"cdu: test report for {intent.job_name} [skip ci]",
    )
    return passed


def build_report(repo_root: Path, validation: ValidationResult, passed: bool,
                  pytest_output: str, run_url: str, lock) -> str:
    intent = validation.intent
    lines = [f"# CDU run report — {intent.job_name}", ""]
    if not has_human_assertions(repo_root, validation):
        lines += [f"**{AI_AUTHORED_WARNING}**", ""]
    lines += [
        f"**Result: {'✅ PASS' if passed else '❌ FAIL'}**",
        "",
        "## Artifacts",
    ]
    for name, record in lock.artifacts.items():
        lines.append(f"- {name}: `{record.path}` (generated {record.generated_at})")
    if validation.warnings:
        lines += ["", "## Validation warnings"]
        lines += [f"- ⚠ {w}" for w in validation.warnings]
    lines += ["", "## Test output", "```", pytest_output.strip()[-6000:], "```"]
    if run_url:
        lines += ["", f"[Workflow run]({run_url})"]
    return "\n".join(lines) + "\n"
