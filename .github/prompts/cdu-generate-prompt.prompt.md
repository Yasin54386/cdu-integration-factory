---
mode: agent
description: Author per-job generator prompts (ORDS/MuleSoft/tests) into job/prompts/ from the finalised intent.
---

# /cdu-generate-prompt — Tailor the generator prompts to THIS job

The static templates in `prompts/` are deliberately generic. Once `job/intent.md`
is finalised, this command has Copilot author **job-specific** generator prompts
into `job/prompts/`, derived from the actual intent — so the later generate steps
produce exactly the right shape instead of the default load/export/SFTP scenario.

This is optional polish: if a tailored prompt exists, the generate steps use it;
otherwise they fall back to the static `prompts/` default. Run it AFTER the intent
is final (`/cdu-draft` → `/cdu-validate`) and BEFORE the generate steps.

Read `.github/copilot-instructions.md` first (THE WALL, full-regen, contract-only).

## Steps

1. Run `python pipeline/cdu.py validate` — the intent must be valid and final
   before tailoring prompts. Stop and report if it fails.

2. Run:
   ```
   python pipeline/cdu.py prompt-targets --json
   ```
   This lists, for each artifact this job actually needs (ORDS is dropped for a
   MuleSoft-only / no-SQL job), the `default_template` to base the tailoring on
   and the `job_template` path under `job/prompts/` to write.

3. Read the inputs that define this job's shape:
   - `job/intent.md` (front-matter is the contract — `direction`, every
     `sources` role present, `destination.file_format` / `file_name_pattern`,
     `testing`),
   - the supporting files under `job/` referenced by the intent,
   - `connections.yaml` (logical names only),
   - and, for each target, its `default_template` as the baseline to specialise.

4. For EACH target, author a tailored generator prompt and write it to its
   `job_template` path (e.g. `job/prompts/ords_generator.prompt.md`). Specialise
   the generic template to the real job — examples of what "tailored" means:
   - **ORDS:** expose one endpoint per SQL source, choosing the HTTP method from
     each source's `role` (e.g. `staging_load` → POST, `export` → GET,
     `procedure` → POST), instead of hardcoding `/load` + `/export`. Name the
     staging table(s) and columns from the actual SQL.
   - **MuleSoft:** build the flow from `direction` — `download` =
     ORDS GET → transform → write to the destination; `upload` = read the source
     → transform → ORDS POST. Use the real `file_format` and `file_name_pattern`.
   - **tests:** turn the intent's `testing.key_assertions` /
     `expected_row_logic` / `expected_files` into concrete, specific test
     guidance.

5. Keep every tailored prompt **structure-only**: logical names and
   intent-derived shape, never a credential value, hostname, or key (THE WALL,
   spec §7). Keep the "Output ONLY the file content, no fences" rule intact.

## Finish

Report which `job/prompts/` files you wrote. They are committed with the job
(part of the human-reviewed intent record); review them, then run the per-stage
generate commands — `/cdu-generate-sql`, `/cdu-generate-mule`,
`/cdu-generate-tests` — which now prefer these tailored prompts automatically.
