---
mode: agent
description: Draft job/intent.md from the documents in job/docs/ using Copilot (no external API).
---

# /cdu-draft — Draft the intent contract from job/docs/

Copilot is the engine here — do NOT call any external model API and do NOT run
`python pipeline/cdu.py draft-intent` (that path uses the Models API). You draft
`job/intent.md` yourself from the job's documents.

Read `.github/copilot-instructions.md` first for the hard rules (THE WALL, no
secret values, contract-only).

## Steps

1. Read the drafting spec `prompts/intent_drafter.prompt.md` — it defines the
   exact intent schema, allowed fields, enums, and roles. Treat it as
   authoritative for the file you produce.
2. Read the **contents of every file** under `job/docs/` (the human's
   description, BRDs, endpoint specs, etc.). This is the source of truth for
   what the integration must do.
3. List the supporting files present under `job/sql/`, `job/specs/`,
   `job/samples/`, `job/mappings/`, `job/tests/` — reference them by their real
   paths in the intent (do not invent files that aren't there).
4. Read `connections.yaml` and use only logical connection names defined there
   (e.g. `oracle_dev`, `mule_repo_dev`, `sftp_dev`). Never put secret values.
5. Write `job/intent.md` with valid YAML front-matter per the schema:
   - `job_name` (lowercase/digits/underscores, 3–41 chars), `mode: generate`,
     `direction`, `sources` (omit/empty `sql: []` if the job has no SQL),
     `destination`, `connections`, and `mulesoft_delivery` (set `repo`/`branch`
     when editing an existing Mule repo).
   - Put anything that doesn't fit a structured field into the `## Notes` body.

## Finish

6. Run `python pipeline/cdu.py validate` and fix any schema/reference errors in
   `job/intent.md` until it passes.
7. Show the drafted `job/intent.md` and a one-line summary so the human can
   review before generating. Do not generate artifacts in this command.
