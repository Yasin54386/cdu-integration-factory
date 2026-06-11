"""Per-file and combined input hashing (spec §9).

Hash scope = job/intent.md + every referenced supporting file + everything
under job/tests/ (test inputs influence the tests artifact). Raw bytes,
sha256, recorded as "sha256:<hex>".
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pipeline.core.intent import Intent


def hash_file(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def hash_inputs(repo_root: Path, intent: Intent) -> dict[str, str]:
    """Hash all pipeline inputs. Keys are repo-relative POSIX paths."""
    job_dir = repo_root / "job"
    hashes: dict[str, str] = {"job/intent.md": hash_file(job_dir / "intent.md")}
    for rel in intent.referenced_files():
        path = job_dir / rel
        hashes[f"job/{Path(rel).as_posix()}"] = hash_file(path)
    tests_dir = job_dir / "tests"
    if tests_dir.is_dir():
        for path in sorted(tests_dir.rglob("*")):
            if path.is_file() and path.name != ".gitkeep":
                rel = path.relative_to(repo_root).as_posix()
                hashes.setdefault(rel, hash_file(path))
    return hashes


def combined_hash(input_hashes: dict[str, str]) -> str:
    """Order-independent combined hash over (path, hash) pairs."""
    digest = hashlib.sha256()
    for path in sorted(input_hashes):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(input_hashes[path].encode("utf-8"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"
