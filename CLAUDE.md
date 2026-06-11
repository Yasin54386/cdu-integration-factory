# CLAUDE.md

This repo implements **CDU-INTEGRATION-FACTORY-SPEC.md** (repo root).
Read it before changing anything.

Binding sections — ask before deviating:
- §2 locked architecture decisions (D1–D12)
- §5 intent contract (`pipeline/core/intent.py` is the schema's single
  source of truth)
- §9 regeneration rules (the impact map lives in `pipeline/core/impact.py`)
- §10 pipeline stage contracts

Ground rules:
- Main is a pristine template; feature branches never merge back.
- AI generation = GitHub Copilot CLI only (D4); regeneration is always
  full-regen per artifact, never AI patching (D5).
- Secrets: `connections.yaml` holds secret NAMES only; values come from
  Actions secrets as env vars; nothing under `prompts/` or `generated/`
  may ever contain a credential value.
- Milestones M1–M3 are complete and covered by `tests/` (`pytest` must
  stay green). M4 needs Copilot CLI + token; M5 needs Oracle/Anypoint dev
  access; M6 needs the SFTP dev destination — verify §14 prerequisites
  before claiming them done.

Run the pipeline's own tests with `pytest` from the repo root.
