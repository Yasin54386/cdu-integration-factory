---
mode: agent
description: Generate or edit the MuleSoft flow (mulesoft sub-stage) with Copilot.
---

# /cdu-generate-mule — Build the MuleSoft change

Handle ONLY the `mulesoft` sub-stage. The factory decides whether this is an
edit to an existing repo or a brand-new flow — you don't choose. Read
`.github/copilot-instructions.md` first (THE WALL, no secret values, minimal
idiomatic edits).

## Step 1 — ask the plan what to do

```
python pipeline/cdu.py plan --json
```
Look at the `mulesoft` step's `action`:

- **workspace-edit** — the integration changes an EXISTING Mule repo. Follow
  the `/cdu-mule` flow:
  1. `python pipeline/cdu.py mule-checkout` (clones the repo from
     `mulesoft_delivery.repo`/`branch` into `mule_workspace/<repo>/`).
  2. Study the existing project, then make the real change in
     `mule_workspace/<repo>/` — this may be **adding a new `<flow>` into an
     existing XML file**, editing a flow, changing DataWeave/pom/properties, or
     a mix. Match the existing style; use the project's property placeholders,
     never raw secrets.
  3. `python pipeline/cdu.py mule-deliver` (secret scan + XML well-formedness,
     commit, push). Fix and re-run if it reports a problem.

- **generate-new-flow** — no existing repo named. Generate a self-contained
  flow:
  1. `python pipeline/cdu.py prompt --sub mulesoft`
  2. Read `generated/.prompts/mulesoft.prompt.md` below the paste marker; write
     **only** the XML to `generated/mulesoft/<job_name>_flow.xml` (one
     well-formed `<mule>` document, flow named `<job_name>-main-flow`).
  3. `python pipeline/cdu.py ingest --sub mulesoft` (fix malformed XML / secret
     hits and re-run until it passes).

- **skip** — MuleSoft inputs unchanged; do nothing and report.

## Notes

- `mule-deliver` (the workspace push) requires `mode: deploy` in `job/intent.md`
  (the human gate). In `mode: generate` you prepare/edit the workspace for
  review but do not push.
- `mule_workspace/` is gitignored and pushed to its own remote, not the factory
  branch.
