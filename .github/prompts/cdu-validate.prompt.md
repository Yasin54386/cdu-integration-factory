---
mode: agent
description: Validate the CDU intent and inputs; report problems clearly.
---

# /cdu-validate — Validate the job

Run the validator and report the result. This never generates or deploys.

## Steps

1. Run in the terminal:
   ```
   python pipeline/cdu.py validate
   ```
2. If it passes, report "validation OK" and list the job_name, mode, and the
   connections the job uses.
3. If it fails, read each error and explain it plainly:
   - **missing referenced file** → the path in `job/intent.md` doesn't exist
     under `job/`; either add the file or fix the reference.
   - **unknown connection** → the name isn't in `connections.yaml`.
   - **secret not configured** → the env var is missing (local `.env` or CI
     Actions Secret). Name the exact variable; never print its value.
   - **schema error** → a front-matter field is wrong; point at the field.
4. Fix only trivial, unambiguous issues in `job/intent.md` (e.g. a mistyped
   file path that clearly matches an existing file). For anything ambiguous,
   stop and ask the human. Do NOT touch files under `job/` other than
   `intent.md`, and never invent secret values.

Re-run `validate` until green or until you need a human decision.
