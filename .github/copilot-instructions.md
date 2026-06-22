# CDU Integration Factory — Copilot agent instructions

These instructions apply to **GitHub Copilot agent mode** when working in this
repository. They let Copilot act as the generation engine for the pipeline
**instead of an external model API** — the factory builds precise prompts,
Copilot produces the artifacts, and the factory validates and version-controls
them.

Read `CDU-INTEGRATION-FACTORY-SPEC.md` and `INTEGRATION-WORKFLOW.md` for the
full contract. The essentials are below.

## Your role in the pipeline

The factory generates artifacts in three sub-stages, in order:

| Sub-stage | Artifact | Output path |
|-----------|----------|-------------|
| `sql`      | Oracle ORDS module SQL | `generated/ords/<job_name>_module.sql` |
| `mulesoft` | MuleSoft flow XML      | `generated/mulesoft/<job_name>_flow.xml` |
| `tests`    | pytest file            | `generated/tests/test_<job_name>.py` |

For each sub-stage the factory writes a **prompt file** to
`generated/.prompts/<substage>.prompt.md`. Everything below the
`=== PASTE FROM HERE INTO COPILOT CHAT ===` marker is the full instruction
plus the machine-readable intent contract and supporting files. **Treat that
content as the authoritative spec for what to generate.**

## How to run the pipeline (the loop)

When asked to run the pipeline (or `/cdu-run`), do this autonomously for each
requested sub-stage (default order: sql → mulesoft → tests):

1. Run `python pipeline/cdu.py prompt --sub <substage>` to (re)build the prompt.
2. Open `generated/.prompts/<substage>.prompt.md` and read everything below
   the paste marker.
3. **Generate the artifact** exactly as the prompt instructs.
4. Write the generated content to the output path shown in the prompt header.
   - Output the file content ONLY. No markdown code fences, no commentary.
5. Run `python pipeline/cdu.py ingest --sub <substage>`.
   - This runs sanity checks, THE WALL secret scan, updates the lockfile and
     version history, and commits with `[skip ci]`.
   - If `ingest` fails (e.g. malformed XML, missing ORDS calls, a secret value
     detected), read the error, fix the generated file, and run `ingest` again.
6. Move to the next sub-stage.

Finish by running `python pipeline/cdu.py validate` and reporting the result.

## Hard rules (do not violate)

- **THE WALL (spec §7):** never put a real credential VALUE into any generated
  file. Use only the logical names from the intent / `connections.yaml`. The
  `ingest` step will reject any file containing a secret value — but do not rely
  on it; never emit one in the first place.
- **Full regeneration only (D5):** when regenerating an artifact, produce the
  whole file. Never patch or partially edit a previously generated artifact.
- **Respect the contract:** the YAML intent block in the prompt is the single
  source of truth. Do not invent fields, tables, or endpoints not implied by it.
- **Do not edit** files under `job/` (the human-authored intent) — only generate
  into `generated/`.
- **Deployment is gated:** generating does NOT deploy. Deployment happens only
  when `job/intent.md` has `mode: deploy` and a human runs
  `python pipeline/cdu.py run`. Do not attempt to deploy from agent mode.

## Editing an existing MuleSoft repo (the `/cdu-mule` flow)

Some integrations change an **existing** MuleSoft repository rather than emit a
single new flow. For those, do NOT use the single-file generation path — use the
workspace flow:

1. `python pipeline/cdu.py mule-checkout` clones the target repo into
   `mule_workspace/<repo>/` on the correct branch.
2. You edit files in that folder directly — update an existing flow, add new
   flows, change DataWeave / pom / properties, whatever the requirement needs.
3. `python pipeline/cdu.py mule-deliver` validates the changed files (secret
   scan + XML well-formedness), commits and pushes them.

Rules for in-workspace edits:
- Match the existing project's conventions and structure.
- Never hard-code a secret value — reuse the property placeholders the project
  already defines.
- Keep changes scoped to the requirement; do not reformat unrelated files.
- The workspace is gitignored; it is pushed to the MuleSoft remote, not the
  factory branch.

## Output format for generated artifacts

- **ORDS SQL:** a PL/SQL block using `ORDS.DEFINE_MODULE` / related calls; must
  mention the `job_name`. No `CREATE OR REPLACE` of arbitrary objects beyond
  what the prompt asks.
- **MuleSoft XML:** a single well-formed XML document (parseable by an XML
  parser), root `<mule>`, containing the flow named `<job_name>-main-flow`.
- **pytest:** a runnable test module building assertions from the intent's
  `testing` block; if no human assertions exist, keep tests conservative.
