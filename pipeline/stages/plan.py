"""Stage — plan: decide the right action per sub-stage from the intent.

This is what lets a single command "just do the right thing": it inspects the
intent + lockfile and emits an ordered plan so Copilot agent mode (or a human)
knows, for each sub-stage, whether to generate, skip (already current), edit an
existing MuleSoft repo, or scaffold a new one — without anyone choosing a model.

Decision rules:
  sql   → generate if the ords artifact is stale, else skip.
  mulesoft → if not stale: skip.
            if stale and mulesoft_delivery.repo is set: edit the EXISTING repo
              (workspace flow — Copilot edits/adds files in a clone).
            if stale and no repo named: generate a new self-contained flow
              (the factory scaffolds a fresh repo on deploy).
  tests → in deploy mode: always run (generate first if stale).
          in generate mode: generate if stale, else skip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pipeline.core.hashing import hash_inputs
from pipeline.core.impact import decide_regeneration
from pipeline.core.lockfile import read_lockfile
from pipeline.stages.validate import ValidationResult


@dataclass
class PlanStep:
    substage: str          # sql | mulesoft | tests
    action: str            # generate | skip | workspace-edit | generate-new-flow | run-tests
    reason: str = ""
    commands: list[str] = field(default_factory=list)


@dataclass
class Plan:
    job_name: str
    mode: str
    steps: list[PlanStep] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "job_name": self.job_name,
            "mode": self.mode,
            "steps": [
                {"substage": s.substage, "action": s.action,
                 "reason": s.reason, "commands": s.commands}
                for s in self.steps
            ],
        }


def build_plan(repo_root: Path, validation: ValidationResult) -> Plan:
    intent = validation.intent
    assert intent is not None

    new_hashes = hash_inputs(repo_root, intent)
    lock = read_lockfile(repo_root)
    stale = decide_regeneration(
        repo_root, intent, validation.raw_intent, new_hashes, lock
    )
    deploy = intent.mode == "deploy"

    steps: list[PlanStep] = []

    # ── sql ───────────────────────────────────────────────────────────────────
    if not intent.sources.sql:
        steps.append(PlanStep(
            "sql", "skip",
            "no SQL sources declared → ORDS not generated (MuleSoft-only job)",
        ))
    elif "ords" in stale:
        steps.append(PlanStep(
            "sql", "generate",
            "ORDS inputs changed or first run",
            ["python pipeline/cdu.py prompt --sub sql",
             "# edit generated/ords/<job>_module.sql with Copilot",
             "python pipeline/cdu.py ingest --sub sql"],
        ))
    else:
        steps.append(PlanStep("sql", "skip", "ORDS inputs unchanged"))

    # ── mulesoft ──────────────────────────────────────────────────────────────
    delivery = intent.mulesoft_delivery
    has_target_repo = bool(delivery and delivery.repo)
    if "mulesoft" not in stale:
        steps.append(PlanStep("mulesoft", "skip", "MuleSoft inputs unchanged"))
    elif has_target_repo:
        steps.append(PlanStep(
            "mulesoft", "workspace-edit",
            f"target repo '{delivery.repo}' is set → edit the existing Mule "
            "project (Copilot updates/adds files in a clone)",
            ["python pipeline/cdu.py mule-checkout",
             "# edit files in mule_workspace/<repo>/ with Copilot",
             "python pipeline/cdu.py mule-deliver"],
        ))
    else:
        steps.append(PlanStep(
            "mulesoft", "generate-new-flow",
            "no target repo named → generate a new self-contained flow "
            "(factory scaffolds a fresh repo on deploy)",
            ["python pipeline/cdu.py prompt --sub mulesoft",
             "# edit generated/mulesoft/<job>_flow.xml with Copilot",
             "python pipeline/cdu.py ingest --sub mulesoft"],
        ))

    # ── tests ─────────────────────────────────────────────────────────────────
    if deploy:
        gen = "tests" in stale
        steps.append(PlanStep(
            "tests", "run-tests",
            "deploy mode → always run tests"
            + (" (regenerate first: inputs changed)" if gen else ""),
            ([
                "python pipeline/cdu.py prompt --sub tests",
                "# edit generated/tests/test_<job>.py with Copilot",
                "python pipeline/cdu.py ingest --sub tests",
            ] if gen else [])
            + ["python pipeline/cdu.py run --sub tests"],
        ))
    elif "tests" in stale:
        steps.append(PlanStep(
            "tests", "generate", "test inputs changed",
            ["python pipeline/cdu.py prompt --sub tests",
             "# edit generated/tests/test_<job>.py with Copilot",
             "python pipeline/cdu.py ingest --sub tests"],
        ))
    else:
        steps.append(PlanStep("tests", "skip", "test inputs unchanged"))

    return Plan(job_name=intent.job_name, mode=intent.mode, steps=steps)
