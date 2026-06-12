# CDU Integration Factory — Architecture & Build Specification

**Status:** Design locked. Ready to build.
**Audience:** Claude Code (or any developer) implementing this system from scratch.
**Owner:** CDU Integration Team
**Last updated:** 2026-06-11

---

## 1. Purpose

Automate the full lifecycle of CDU file-transfer integrations. Today, an integration (e.g. "read Oracle data into a staging table, export it to a file, deliver it to an external system") is built by hand: SQL, ORDS REST wrapper, MuleSoft flow, tests. This system replaces that with a **manifest-driven factory**:

> A developer branches from main, fills in one folder (`job/`) with an intent file and supporting files, and pushes. The pipeline validates, generates all artifacts via GitHub Copilot, deploys to dev, runs tests, and reports — automatically.

The repo's main branch IS the factory. Feature branches are disposable job workspaces.

---

## 2. Locked architecture decisions

These were decided deliberately. Do not revisit during implementation.

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Main branch is a pristine template. Feature branches NEVER merge back. | Main = factory everyone clones. Branches = disposable jobs. |
| D2 | One feature branch = one integration job. | Isolation; parallel jobs never conflict. |
| D3 | All deployments target the DEV environment only. | Simplicity; prod promotion is out of scope for v1. |
| D4 | AI generation uses **GitHub Copilot CLI only** (institute-provided). No other LLM APIs. | Licensing constraint. |
| D5 | Regeneration is **skip-or-full-regen per artifact, never AI patching**. | AI diff-patching causes artifact drift; full regen is reproducible. |
| D6 | Human gate = the `mode` field in the intent file. `mode: generate` → generate only. `mode: deploy` → generate-if-stale + deploy + test. | Approval is a plain git commit; visible in history, no special GitHub config. |
| D7 | Per-branch state lives in `.cdu-lock.json`, committed to the branch by the pipeline. | Branch dies → state dies with it. No external state store. |
| D8 | Orchestrator = **GitHub Actions + a Python Typer CLI**. No Prefect/Airflow/Dagster. | State, gate, and trigger all live in git; an orchestrator framework adds dependency without value. CLI subcommands give local/CI parity. |
| D9 | Secrets live in GitHub Actions Secrets (repo Settings). `connections.yaml` holds metadata + secret NAMES only. Copilot never sees secret values; generated code uses placeholders. | Security wall. |
| D10 | Testing: if the intent has a `testing:` block (or files in `job/tests/`), build tests from those human assertions. Otherwise Copilot generates default tests, flagged "AI-authored" in the report. | Prevents AI grading its own homework when humans provide ground truth; stays low-friction when they don't. |
| D11 | All deployed objects are namespaced by `job_name` (ORDS endpoint prefix, staging table prefix, MuleSoft app name). | Concurrent branches share one dev environment without collisions. |
| D12 | No automated dev-environment cleanup (janitor) in v1. Lockfile records deployed object names so cleanup is possible later. | Team decision; option preserved at zero cost. |

---

## 3. System flow

### Phase 1 — generate (first push)
1. Developer branches from main: `git checkout -b feature/<job-name>`
2. Fills `job/`: edits `intent.md` (mode: generate), adds SQL / specs / samples / mappings, optionally `job/tests/`
3. Pushes. GitHub Actions fires (trigger: branches `feature/**`, paths `job/**`)
4. Pipeline: `validate` → `generate`
5. Generated artifacts + updated `.cdu-lock.json` are committed back to the branch with `[skip ci]`
6. Developer reviews the generated diff in the branch (the human gate)

### Phase 2 — deploy (second push)
7. Developer edits `intent.md`: `mode: deploy`. Pushes.
8. Pipeline: `validate` → `generate` (regenerates only if any input hash changed since last generation; otherwise skips) → `deploy` → `test`
9. Report written to `reports/`, posted as PR comment (and/or workflow summary)
10. Tests fail → developer fixes the right thing (intent, supporting file, or prompt template on main via PR) and pushes again. Hash logic ensures only affected artifacts regenerate.

Re-pushing `mode: deploy` with nothing changed = idempotent redeploy. Always safe.

---

## 4. Repository structure (main branch)

```
cdu-integration-factory/
├── README.md                             # onboarding: clone → branch → fill job/ → push
├── requirements.txt                      # typer, pydantic, tenacity, python-docx, openpyxl,
│                                         #   oracledb, paramiko, deepdiff, pyyaml, pytest
├── .gitignore                            # __pycache__, .venv, .env, *.pyc — NOT generated/
├── connections.yaml                      # connection metadata + secret NAMES (never values)
│
├── .github/workflows/
│   └── cdu-pipeline.yml                  # trigger + mode routing + secret→env mapping
│
├── pipeline/                             # the engine — developers never touch
│   ├── cdu.py                            # Typer entry: validate | generate | deploy | test
│   ├── stages/
│   │   ├── validate.py
│   │   ├── generate.py
│   │   ├── deploy.py
│   │   └── test.py
│   ├── core/
│   │   ├── intent.py                     # Pydantic models = intent schema (single source of truth)
│   │   ├── lockfile.py                   # read/write .cdu-lock.json
│   │   ├── resolver.py                   # connections.yaml + env secrets → merged at runtime
│   │   ├── preprocess.py                 # .docx→text, .xlsx→md table, .csv→head(20), .sql→verbatim
│   │   ├── hashing.py                    # per-file + combined hashes
│   │   └── gitops.py                     # commit-back [skip ci], PR comment, github/gitlab switch
│   └── deployers/
│       ├── ords.py                       # push ORDS module to Oracle dev
│       ├── mulesoft.py                   # push app to Anypoint dev
│       └── sftp.py                       # destination delivery checks (test stage)
│
├── prompts/                              # AI domain knowledge — versioned, improves over time
│   ├── ords_generator.prompt.md
│   ├── mulesoft_generator.prompt.md
│   ├── transform_generator.prompt.md
│   └── test_generator.prompt.md
│
├── job/                                  # THE SKELETON — only folder developers edit
│   ├── intent.md                         # fully annotated template (see §5)
│   ├── sql/.gitkeep
│   ├── specs/.gitkeep
│   ├── samples/.gitkeep
│   ├── mappings/.gitkeep
│   └── tests/.gitkeep
│
├── examples/
│   └── student_download/                 # one complete worked job; pipeline IGNORES examples/
│       ├── intent.md
│       ├── sql/load_staging.sql
│       ├── sql/export_query.sql
│       └── samples/expected_output.csv
│
├── docs/
│   ├── ONBOARDING.md                     # 1 page developer workflow
│   └── TROUBLESHOOTING.md
│
└── tests/                                # pytest for the PIPELINE itself (not job tests)
    ├── test_validate.py
    ├── test_hashing.py
    ├── test_lockfile.py
    └── test_resolver.py
```

**Feature branch additions after a run (never on main):**

```
├── job/            # filled in by developer
├── generated/      # committed back by pipeline [skip ci]
│   ├── ords/<job_name>_module.sql
│   ├── mulesoft/<job_name>_flow.xml
│   ├── transform/<job_name>_transform.py   (if needed by the job)
│   └── tests/test_<job_name>.py
├── .cdu-lock.json
└── reports/run_<timestamp>.md
```

**Repo settings (manual, one-time):** protect main (require PR for changes to `pipeline/`, `prompts/`, `connections.yaml`). Feature branches unrestricted.

---

## 5. The intent contract (`job/intent.md`)

Fixed name, fixed location: `job/intent.md`. YAML front-matter carries the machine-readable contract; markdown body below it is free-form human notes (pipeline ignores the body except to pass it to Copilot as extra context).

```yaml
---
# ============ REQUIRED ============
job_name: student_download_v1        # namespaces EVERYTHING deployed; [a-z0-9_]+ only
mode: generate                       # generate | deploy  ← THE HUMAN GATE
direction: download                  # download | upload

sources:
  sql:
    - file: sql/load_staging.sql     # paths relative to job/
      role: staging_load             # roles: staging_load | export | procedure
    - file: sql/export_query.sql
      role: export
  specs:                             # optional list
    - file: specs/BRD_student_export_v3.docx
      role: business_rules
  samples:                           # optional list
    - file: samples/expected_output_sample.csv
      role: output_example
  mappings:                          # optional list
    - file: mappings/field_map.xlsx
      role: field_mapping

destination:
  connection: sftp_dev               # logical name from connections.yaml
  path: /incoming/student/
  file_format: csv                   # csv | fixed | json | xml
  file_name_pattern: "student_{yyyymmdd}.csv"

connections:                         # which logical connections this job uses
  oracle: oracle_dev                 # defaults shown; override only if non-standard
  mulesoft: mule_dev

# ============ OPTIONAL ============
testing:                             # ABSENT → AI-default tests (flagged in report)
  expected_row_logic: "row count equals SELECT COUNT(*) FROM STG_STUDENT_EXPORT"
  key_assertions:
    - "header row matches samples/expected_output_sample.csv line 1 exactly"
    - "no null values in STUDENT_ID column"
  expected_files:                    # files in job/tests/ to compare against
    - file: tests/golden_output.csv
      compare: exact_header_and_first_5_rows
---

## Notes (free-form, optional)
Anything the developer wants Copilot to know: edge cases, business context, gotchas.
```

**Validation rules (stage 0 enforces):**
- `intent.md` must exist at `job/intent.md` with parseable YAML front-matter
- All required fields present; `job_name` matches `^[a-z][a-z0-9_]{2,40}$`; `mode` ∈ {generate, deploy}
- Every referenced file exists → missing file = **FAIL** with exact path
- Every `role` value is from the known set → unknown role = FAIL
- Files present in `job/*/` but not referenced = **WARNING** in report (possible junk or forgotten reference)
- Every connection named in `connections:` and `destination.connection` exists in `connections.yaml`
- For each used connection, every secret name it declares exists as an env var → missing = FAIL within seconds: `"Secret ORACLE_DEV_PASSWORD not configured in repo Settings → Secrets → Actions"` (name only, never value)

---

## 6. Supporting-file preprocessing (`core/preprocess.py`)

Copilot CLI consumes text. Preprocess before prompt assembly:

| File type | Treatment |
|-----------|-----------|
| `.sql` | Verbatim, full content |
| `.docx` | Extract text via python-docx (paragraphs + tables) |
| `.pdf` | Extract text (pypdf or similar); if extraction is empty, FAIL with "scanned PDF unsupported in v1" |
| `.xlsx` (mappings) | Parse via openpyxl → render as a markdown table |
| `.csv` / `.dat` / `.txt` (samples) | First 20 rows only + a line noting truncation and total row count |

Cap each preprocessed file's contribution (e.g. 8,000 chars) and the assembled prompt overall; if exceeded, FAIL with guidance to trim inputs rather than silently truncating business rules.

---

## 7. Connections & secrets (`connections.yaml` + `core/resolver.py`)

Three layers:
1. **Intent** names logical connections (`oracle_dev`) — zero secrets, zero hostnames.
2. **connections.yaml** (on main) maps logical names → metadata + secret NAMES.
3. **GitHub Actions Secrets** (repo Settings) holds the VALUES. Injected as env vars by the workflow. Resolver merges layers in memory at runtime only.

```yaml
# connections.yaml — metadata only, NO secret values ever
oracle_dev:
  type: oracle
  host: oradev.cdu.internal
  port: 1521
  service: CDUDEV
  schema: INTEGRATION
  auth: basic
  secrets: { user: ORACLE_DEV_USER, password: ORACLE_DEV_PASSWORD }

mule_dev:
  type: anypoint
  org_id: cdu-integration
  environment: DEV
  auth: connected_app
  secrets: { client_id: MULE_DEV_CLIENT_ID, client_secret: MULE_DEV_CLIENT_SECRET }

git_main:
  type: github                        # or gitlab — gitops.py branches on this
  secrets: { token: GH_PIPELINE_TOKEN }

sftp_dev:
  type: sftp
  host: sftpdev.cdu.internal
  port: 22
  secrets: { user: SFTP_DEV_USER, key: SFTP_DEV_PRIVATE_KEY }
```

```python
# resolver contract
def resolve(conn_name: str) -> dict:
    meta = load_connections_yaml()[conn_name]
    creds = {k: os.environ[v] for k, v in meta["secrets"].items()}
    return {**meta, **creds}   # in memory only; never logged, never written to disk
```

**THE WALL (non-negotiable):** the prompt assembler includes logical names + non-secret metadata only. Prompt templates instruct Copilot to emit placeholders — `${ORACLE_PASSWORD}` in ORDS config, MuleSoft secure properties (`${secure::...}`) in flow XML. Deployers inject real values at deploy time. Generated code committed to git must never contain a credential.

---

## 8. Lockfile (`.cdu-lock.json`)

Written/updated by the pipeline, committed to the branch with `[skip ci]`.

```json
{
  "schema_version": 1,
  "job_name": "student_download_v1",
  "last_run_id": "gh-run-9182736",
  "last_run_at": "2026-06-11T14:30:22Z",
  "last_mode": "generate",
  "input_hashes": {
    "job/intent.md": "sha256:ab12...",
    "job/sql/load_staging.sql": "sha256:cd34...",
    "job/sql/export_query.sql": "sha256:ef56...",
    "job/specs/BRD_student_export_v3.docx": "sha256:0a1b...",
    "job/samples/expected_output_sample.csv": "sha256:2c3d...",
    "job/mappings/field_map.xlsx": "sha256:4e5f..."
  },
  "combined_input_hash": "sha256:9988...",
  "artifacts": {
    "ords":      { "path": "generated/ords/student_download_v1_module.sql",  "input_hash_at_gen": "sha256:9988...", "generated_at": "..." },
    "mulesoft":  { "path": "generated/mulesoft/student_download_v1_flow.xml","input_hash_at_gen": "sha256:9988...", "generated_at": "..." },
    "tests":     { "path": "generated/tests/test_student_download_v1.py",    "input_hash_at_gen": "sha256:9988...", "generated_at": "..." }
  },
  "deployed": {
    "ords_endpoint": "/ords/cdu/student_download_v1/",
    "staging_table": "STG_STUDENT_DOWNLOAD_V1",
    "mulesoft_app": "cdu-student-download-v1",
    "deployed_at": "2026-06-11T14:34:01Z"
  },
  "last_test_result": { "status": "pass", "report": "reports/run_2026-06-11_143022.md" }
}
```

---

## 9. Hashing & regeneration rules (`core/hashing.py` + `stages/generate.py`)

- Hash scope = `intent.md` + **every referenced supporting file** (raw bytes, sha256).
- Per artifact, regenerate (FULL regen, from scratch — D5) when its relevant inputs changed:

| Changed input | Regenerate | Skip |
|---------------|-----------|------|
| Any `sql/*` referenced file, or sql-related intent fields | ORDS + tests | MuleSoft |
| `destination.*`, connection names, `file_format`, mapping files | MuleSoft + tests | ORDS |
| Any `specs/*` file (business rules can affect anything) | ALL artifacts | — |
| `testing:` block or `job/tests/*` only | tests only | ORDS + MuleSoft |
| `mode` field only | nothing | everything |
| Nothing (hash identical) | nothing | everything |

- Implementation: keep a simple, explicit field→artifact impact map in code (not string matching on diff output). `deepdiff` may be used on the parsed intent dict for change detection, but routing decisions come from the explicit map.
- If no lockfile exists (first run) → generate everything.

---

## 10. Pipeline stages — detailed contracts

### `cdu validate`
Inputs: `job/intent.md`, `job/` tree, `connections.yaml`, env vars.
Behavior: all rules in §5. Pydantic model in `core/intent.py` is the schema's single source of truth.
Output: exit 0 + parsed intent (also writes `reports/validate_<ts>.md` on warnings) | exit 1 with one clear, human-readable error per problem. Never retried.

### `cdu generate`
1. Run validate logic (or require it ran).
2. Compute hashes; compare to lockfile → decide per-artifact skip/regen (§9).
3. For each artifact to regen: preprocess inputs (§6) → assemble prompt = `prompts/<artifact>_generator.prompt.md` + intent front-matter + intent body notes + preprocessed file contents (labeled by role) → invoke Copilot CLI **programmatic/non-interactive mode** → write output under `generated/`.
4. Post-generation sanity checks (cheap, non-AI): ORDS output contains `ORDS.DEFINE_` calls and the `job_name`; MuleSoft output is well-formed XML (parse it); no secret-looking strings (regex for the known secret env var names) — any hit = FAIL.
5. Update lockfile; commit `generated/` + `.cdu-lock.json` to the branch, message `"cdu: generate <job_name> [skip ci]"`, push via `git_main` connection token.
Retries: wrap Copilot invocation with tenacity (3 attempts, exponential backoff) — CLI/network flakiness only; a sanity-check failure is NOT retried blindly more than once.

### `cdu deploy`  (only when `mode: deploy`)
1. If combined input hash ≠ lockfile's → run generate first (D6: "generate if not generated yet").
2. Resolve connections. Deploy ORDS module to Oracle dev (run the generated module SQL via oracledb); deploy MuleSoft app to Anypoint dev (Anypoint Platform API or anypoint-cli — pick during M5 based on what the institute allows).
3. All object names derive from `job_name` (D11). Redeploy = replace own namespaced objects only (idempotent).
4. Record deployed object names in lockfile.
Retries: tenacity, 3 attempts, exponential backoff per deployer call.

### `cdu test`
1. If `testing:` block or `job/tests/` present → run generated tests built from human assertions. Else → run AI-default tests; report header: `"⚠ assertions are AI-authored — lower confidence"`.
2. Typical flow for `direction: download`: invoke the MuleSoft endpoint (or trigger the flow) → poll destination via `sftp_dev` → fetch file → assert format/header/rows/key fields.
3. Write `reports/run_<ts>.md`: per-assertion pass/fail, skipped/regenerated artifacts this run, validation warnings, link to workflow run. Post as PR comment if a PR exists; always write to the GitHub Actions job summary.
4. Update lockfile `last_test_result`. Exit non-zero on any failure (fails the workflow visibly).

---

## 11. Copilot CLI integration (`stages/generate.py`)

- D4: GitHub Copilot CLI in **non-interactive/programmatic mode**, invoked as a subprocess from Python, running inside GitHub Actions.
- Auth: a token of a Copilot-licensed identity, stored as an Actions secret (e.g. `COPILOT_TOKEN`); exact mechanism per current Copilot CLI docs.
- Invocation shape (verify exact flags against current docs at build time):
  `copilot -p "<assembled prompt>" --allow-tool write` (or output-capture mode writing files ourselves — prefer capturing stdout and writing files from Python for determinism and easier sanity checks).
- Prompt templates end with strict output instructions: "Output ONLY the file content. No markdown fences. No commentary." Strip fences defensively anyway.
- **PREREQUISITE (blocking, verify before M4):** institute's GitHub org policy must have Copilot CLI enabled. Fallback if disabled: Copilot coding agent via issue assignment (async; requires workflow redesign of the generate stage — descope to a documented manual generate step in the interim rather than blocking the whole build).

---

## 12. GitHub Actions workflow (`.github/workflows/cdu-pipeline.yml`)

```yaml
name: cdu-pipeline
on:
  push:
    branches: ['feature/**']
    paths: ['job/**']

concurrency:
  group: cdu-${{ github.ref }}          # one run per branch at a time; newest wins
  cancel-in-progress: true

jobs:
  pipeline:
    runs-on: ubuntu-latest
    env:
      ORACLE_DEV_USER:        ${{ secrets.ORACLE_DEV_USER }}
      ORACLE_DEV_PASSWORD:    ${{ secrets.ORACLE_DEV_PASSWORD }}
      MULE_DEV_CLIENT_ID:     ${{ secrets.MULE_DEV_CLIENT_ID }}
      MULE_DEV_CLIENT_SECRET: ${{ secrets.MULE_DEV_CLIENT_SECRET }}
      SFTP_DEV_USER:          ${{ secrets.SFTP_DEV_USER }}
      SFTP_DEV_PRIVATE_KEY:   ${{ secrets.SFTP_DEV_PRIVATE_KEY }}
      GH_PIPELINE_TOKEN:      ${{ secrets.GH_PIPELINE_TOKEN }}
      COPILOT_TOKEN:          ${{ secrets.COPILOT_TOKEN }}
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0, token: '${{ secrets.GH_PIPELINE_TOKEN }}' }
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r requirements.txt
      - id: mode
        run: echo "mode=$(python pipeline/cdu.py read-mode)" >> "$GITHUB_OUTPUT"
      - run: python pipeline/cdu.py validate
      - run: python pipeline/cdu.py generate
      - if: steps.mode.outputs.mode == 'deploy'
        run: python pipeline/cdu.py deploy
      - if: steps.mode.outputs.mode == 'deploy'
        run: python pipeline/cdu.py test
```

Notes:
- Loop guard is double: pipeline commits use `[skip ci]` AND the path filter (`job/**`) doesn't match `generated/**` or `.cdu-lock.json`.
- Add a tiny `read-mode` subcommand to `cdu.py` (prints `generate` or `deploy`) so routing stays in the CLI.
- `concurrency` prevents two pushes to the same branch racing each other.

---

## 13. Build milestones (execute in order; each independently testable)

**M1 — Skeleton & contract** *(no external systems needed)*
Repo scaffold per §4 · Pydantic intent models (`core/intent.py`) · annotated `job/intent.md` template · `connections.yaml` · `examples/student_download/` · pytest setup.
✅ Done when: `pytest` passes; example intent parses into the model.

**M2 — Validate stage** *(no external systems needed)*
`cdu validate` full implementation (§5 rules) incl. secret pre-flight (checks env var existence only) · unit tests for every rule.
✅ Done when: example passes; each broken-fixture case fails with the right message.

**M3 — Hashing + lockfile + preprocessing** *(no external systems needed)*
`core/hashing.py`, `core/lockfile.py`, `core/preprocess.py` · the impact map (§9) · unit tests (e.g. "sql change → ords+tests regen, mulesoft skip").
✅ Done when: hash/regen decisions match the §9 table for all fixture scenarios.

**M4 — Generate stage with Copilot CLI** *(needs Copilot CLI enabled + token)*
Prompt assembly · Copilot subprocess invocation with tenacity · sanity checks · write `generated/` · `core/gitops.py` commit-back with `[skip ci]` · first real drafts of all four prompt templates.
✅ Done when: running `cdu generate` on the example produces plausible ORDS SQL, well-formed MuleSoft XML, and a runnable test file. (Expect prompt-template iteration here — that's the work.)

**M5 — Deploy stage** *(needs Oracle dev + Anypoint dev access)*
`deployers/ords.py` (oracledb) · `deployers/mulesoft.py` (Anypoint API or anypoint-cli) · namespacing · idempotent redeploy · lockfile `deployed` section.
✅ Done when: example deploys to dev twice in a row cleanly; endpoint responds.

**M6 — Test stage + reporting**
Human-assertion path and AI-default path · SFTP destination verification (`deployers/sftp.py`) · `reports/run_<ts>.md` · PR comment + job summary · lockfile result.
✅ Done when: end-to-end local run (`validate → generate → deploy → test`) passes on the example with a readable report.

**M7 — CI wiring + docs**
`cdu-pipeline.yml` (§12) · `read-mode` subcommand · branch protection on main · `README.md`, `docs/ONBOARDING.md`, `docs/TROUBLESHOOTING.md` seeded.
✅ Done when: pushing the example on a `feature/**` branch runs the whole flow in Actions; flipping mode to `deploy` deploys and tests; second push regenerates only what changed.

**Definition of done (v1):** a developer who has never seen the pipeline code can clone main, branch, fill `job/` from the example, push twice (generate, then deploy), and get a passing test report — touching nothing but `job/`.

---

## 14. Prerequisites checklist (humans, before/while building)

- [ ] Confirm with institute GitHub admin: **Copilot CLI enabled** in org policy (blocking for M4; see §11 fallback)
- [ ] Create repo + protect main (PRs required for `pipeline/`, `prompts/`, `connections.yaml`)
- [ ] Add Actions secrets: `ORACLE_DEV_USER/PASSWORD`, `MULE_DEV_CLIENT_ID/SECRET`, `SFTP_DEV_USER/PRIVATE_KEY`, `GH_PIPELINE_TOKEN` (repo-write PAT for commit-back), `COPILOT_TOKEN`
- [ ] Confirm Oracle dev reachability from Actions runners (network/VPN — if dev is internal-only, plan a self-hosted runner; affects M5/M7)
- [ ] Anypoint dev environment + connected-app credentials
- [ ] SFTP dev destination + test path
- [ ] One real (sanitized) SQL job to replace/validate the example

---

## 15. Working with Claude Code on this spec

Place this file at the repo root. Suggested kickoff prompt:

> Read CDU-INTEGRATION-FACTORY-SPEC.md fully. Build milestone M1 exactly as specified. Do not start M2 until M1's done-criteria pass. Sections 2 (locked decisions), 5 (intent contract), 9 (regen rules), and 10 (stage contracts) are binding — ask before deviating.

Work one milestone per session/PR. M1–M3 need no credentials or external systems — build and fully test them first. Keep a short `CLAUDE.md` in the repo root pointing at this spec and stating the binding sections, so every future session loads the context automatically. (Claude Code docs: https://docs.claude.com/en/docs/claude-code/overview)

---

## 16. Out of scope for v1 (explicitly)

- Production/staging promotion (dev only — D3)
- Automated dev cleanup janitor (D12 — lockfile preserves the option)
- Upload-direction jobs (design supports `direction: upload`; implement after the download path is proven)
- Scanned-PDF OCR, GitLab-hosted variant (resolver supports the switch; test when needed), multi-job branches

---

## 17. Amendment (2026-06-12, owner-directed): MuleSoft delivery via git handoff

The institute's MuleSoft applications live in GitHub/GitLab repositories and
existing CI/CD deploys them to Anypoint. The factory therefore does NOT push
to Anypoint itself by default; "deploying" the mulesoft artifact means a
**git handoff**:

- The intent's `connections.mulesoft` points at a connection of
  `type: git_repo` (provider github|gitlab, host, namespace, token secret).
- Deploy stage pushes the generated app to branch `cdu/<job_name>`
  (force-pushed; factory-owned), in one of two modes selected by the
  optional `mulesoft_delivery` intent block:
  - `repo:` named → existing repo; ONLY `src/main/mule/<job_name>_flow.xml`
    is replaced on the branch (build files untouched).
  - no `repo:` → the factory creates `cdu-<job-name>` under the connection's
    namespace and pushes a full minimal Mule 4 project scaffold
    (pom.xml, mule-artifact.json, flow XML, README).
- The institute's CI/CD takes over from the push. The lockfile `deployed`
  section records `mulesoft_repo`, `mulesoft_branch`, `mulesoft_commit`,
  `mulesoft_url` instead of `mulesoft_app`.
- The original direct-Anypoint path (§10, M5) remains available via a
  `type: anypoint` connection (e.g. `mule_dev`).
- Consequence for `cdu test`: end-to-end verification requires the
  institute's CI/CD to have deployed the pushed app; the SFTP poll timeout
  absorbs short delays, but test failures may mean "not deployed yet".

Implementation: `pipeline/deployers/mule_git.py`; routing in
`pipeline/stages/deploy.py` by connection type. `mulesoft_delivery` changes
route to the MuleSoft+tests artifacts in the §9 impact map.
