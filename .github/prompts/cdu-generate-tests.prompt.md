---
mode: agent
description: Generate the pytest file (tests sub-stage) with Copilot.
---

# /cdu-generate-tests — Generate the tests

Generate ONLY the `tests` sub-stage. Copilot is the engine. Read
`.github/copilot-instructions.md` first (THE WALL, full-regen, contract-only).

## Steps

1. Run:
   ```
   python pipeline/cdu.py prompt --sub tests
   ```
2. Open `generated/.prompts/tests.prompt.md` and read everything below the
   `=== PASTE FROM HERE INTO COPILOT CHAT ===` marker.
3. Generate a runnable pytest module and write **only the file content** (no
   fences, no prose) to `generated/tests/test_<job_name>.py`. Build assertions
   from the intent's `testing` block; if there are no human assertions, keep the
   tests conservative. Never embed a secret value.
4. Run:
   ```
   python pipeline/cdu.py ingest --sub tests
   ```
   Fix and re-run if `ingest` reports a problem.

Report the output path and that it was committed with `[skip ci]`.

## Note

This GENERATES the test file. Tests are actually RUN against the deployed job
in `mode: deploy` (via `/cdu-deploy` or `python pipeline/cdu.py run --sub tests`).
