"""Stage — deploy (spec §10). Runs only when mode: deploy.

All object names derive from job_name (D11) so concurrent branches share
one dev environment without collisions. Redeploy replaces only this job's
namespaced objects — idempotent. DEV environment only (D3).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pipeline.core.hashing import combined_hash, hash_inputs
from pipeline.core.lockfile import LOCKFILE_NAME, read_lockfile, write_lockfile
from pipeline.core.resolver import resolve
from pipeline.deployers import mulesoft as mulesoft_deployer
from pipeline.deployers import ords as ords_deployer
from pipeline.stages import generate as generate_stage
from pipeline.stages.validate import ValidationResult


class DeployError(RuntimeError):
    pass


def deploy(repo_root: Path, validation: ValidationResult, run_id: str = "local") -> dict:
    intent = validation.intent
    assert intent is not None, "deploy requires a passing validation"

    # D6: generate-if-stale before deploying.
    lock = read_lockfile(repo_root)
    current = combined_hash(hash_inputs(repo_root, intent))
    if lock is None or lock.combined_input_hash != current:
        generate_stage.generate(repo_root, validation, run_id=run_id)
        lock = read_lockfile(repo_root)
    assert lock is not None

    oracle = resolve(repo_root, intent.connections.oracle)
    anypoint = resolve(repo_root, intent.connections.mulesoft)

    ords_path = repo_root / lock.artifacts["ords"].path
    mule_path = repo_root / lock.artifacts["mulesoft"].path
    if not ords_path.is_file() or not mule_path.is_file():
        raise DeployError("generated artifacts missing — run generate first")

    endpoint = ords_deployer.deploy_module(oracle, ords_path, intent.job_name)
    app_name = mulesoft_deployer.deploy_app(anypoint, mule_path, intent.job_name)

    lock.deployed = {
        "ords_endpoint": endpoint,
        "staging_table": f"STG_{intent.job_name.upper()}",
        "mulesoft_app": app_name,
        "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_lockfile(repo_root, lock)
    from pipeline.core import gitops

    gitops.commit_back(
        repo_root, [LOCKFILE_NAME], f"cdu: deploy {intent.job_name} [skip ci]"
    )
    return lock.deployed
