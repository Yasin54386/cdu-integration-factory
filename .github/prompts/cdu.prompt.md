---
mode: agent
description: One command — run the whole CDU integration, deciding the right action per sub-stage automatically.
---

# /cdu — Run the integration, Copilot decides what to do

This is the single entry point. You do NOT need to know whether each sub-stage
is a new flow, an edit to existing code, or already up to date — the factory's
`plan` command decides, and you execute it. Read
`.github/copilot-instructions.md` first for the hard rules (THE WALL, no secret
values, contract-only).

## Step 1 — get the plan

Run:
```
python pipeline/cdu.py plan --json
```
This prints an ordered list of steps. Each step has an `action`:

- **skip** — inputs unchanged; do nothing for this sub-stage.
- **generate** — produce the artifact: run the `prompt` command, generate the
  file content yourself, save it to the shown path, run the `ingest` command.
- **workspace-edit** — the integration changes an EXISTING MuleSoft repo. Run
  `mule-checkout`, then make the real change in `mule_workspace/<repo>/`, then
  run `mule-deliver`. The change may be **any** of:
    - adding a brand-new flow file,
    - editing an existing file,
    - **adding a new `<flow>` into an existing XML file**,
    - changing DataWeave / pom / properties,
    - or several of these together.
  Study the existing project first and make the minimal, idiomatic change.
- **generate-new-flow** — no existing repo named; generate one self-contained
  flow via `prompt` → save → `ingest` (the factory scaffolds a fresh repo on
  deploy).
- **run-tests** — run `python pipeline/cdu.py run --sub tests` (regenerate first
  if the step says inputs changed).

## Step 2 — execute each step in order

Follow the `commands` array in each step. For any step that fails validation
(malformed XML, a detected secret value, a failing test), fix the file and
re-run that step's final command before moving on. Never proceed past a failing
step.

## Step 3 — finish

Run `python pipeline/cdu.py validate` and report a short summary: for each
sub-stage say what you did (skipped / generated / edited which files / tested),
and the result.

## Notes

- Deploy actions (`workspace-edit` push, `run-tests`) require `mode: deploy` in
  `job/intent.md` (the human gate, D6). In `mode: generate` the plan still shows
  what would happen; you produce artifacts for review without pushing.
- The MuleSoft workspace (`mule_workspace/`) is gitignored and pushed to its own
  remote, never to the factory branch.
