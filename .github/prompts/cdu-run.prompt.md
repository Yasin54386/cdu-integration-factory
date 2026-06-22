---
mode: agent
description: Run the CDU Integration Factory generation loop using Copilot as the engine.
---

# /cdu-run — Generate all CDU artifacts with Copilot

You are running the CDU Integration Factory in **agent mode**. Copilot is the
generation engine (no external model API is used). Follow the loop below
autonomously and report progress after each sub-stage.

Read `.github/copilot-instructions.md` first for the hard rules (THE WALL,
full-regen, contract-only). Then:

## Steps

For each sub-stage in order — **sql**, **mulesoft**, **tests** (or only the
sub-stages the user named):

1. Run in the terminal:
   ```
   python pipeline/cdu.py prompt --sub <substage>
   ```
2. Open `generated/.prompts/<substage>.prompt.md`. Read everything below the
   `=== PASTE FROM HERE INTO COPILOT CHAT ===` marker — that is your full spec
   (instructions + intent contract + supporting files).
3. Generate the artifact exactly as instructed. Write **only the file content**
   (no code fences, no prose) to the output path named in the prompt header:
   - sql      → `generated/ords/<job_name>_module.sql`
   - mulesoft → `generated/mulesoft/<job_name>_flow.xml`
   - tests    → `generated/tests/test_<job_name>.py`
4. Run in the terminal:
   ```
   python pipeline/cdu.py ingest --sub <substage>
   ```
5. If `ingest` reports an error (malformed XML, missing `ORDS.DEFINE_`, a
   detected secret value, etc.), fix the generated file and re-run `ingest`
   until it succeeds. Do **not** move on with a failing sub-stage.

After all requested sub-stages succeed:

6. Run `python pipeline/cdu.py validate` and confirm it passes.
7. Summarize what was generated and committed (the sub-stages, output paths,
   and that each was committed with `[skip ci]`).

## Notes

- This loop GENERATES and commits artifacts for review. It does **not** deploy.
- To deploy later, the human sets `mode: deploy` in `job/intent.md` and runs
  `python pipeline/cdu.py run` (which deploys to Oracle / pushes Mule to GitLab
  and runs tests).
- If `validate` fails before you start, stop and report the validation errors —
  the intent must be valid before generation.
