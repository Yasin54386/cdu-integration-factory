# CDU Integration Factory

A manifest-driven factory for CDU file-transfer integrations. You describe
an integration in one folder (`job/`); the pipeline validates it, generates
all artifacts (ORDS module, MuleSoft flow, tests) via GitHub Copilot,
deploys to **dev**, runs tests, and reports.

**Main is the factory. Your branch is the job.** Feature branches never
merge back to main (spec D1/D2).

## Quick start

```bash
git clone <this repo> && cd cdu-integration-factory
git checkout -b feature/my_job_v1
```

1. Fill in `job/` — edit `job/intent.md` (keep `mode: generate`), add your
   SQL under `job/sql/`, optionally specs/samples/mappings/tests.
2. `git push -u origin feature/my_job_v1` → the pipeline validates and
   generates artifacts, committing them back to your branch.
3. **Review the generated diff** (`git pull`, look at `generated/`).
4. Edit `job/intent.md` → `mode: deploy`. Push again → the pipeline
   deploys to dev, runs the tests, and writes a report to `reports/`.

Tests fail? Fix the right input (intent, SQL, spec file) and push —
only the affected artifacts regenerate.

A complete worked job lives in `examples/student_download/`.
Full developer guide: [docs/ONBOARDING.md](docs/ONBOARDING.md).
Architecture & binding decisions: [CDU-INTEGRATION-FACTORY-SPEC.md](CDU-INTEGRATION-FACTORY-SPEC.md).

## Layout

| Path | What it is | Who touches it |
|------|------------|----------------|
| `job/` | Your integration's intent + inputs | **You** (on your branch) |
| `pipeline/` | The engine (validate/generate/deploy/test) | Maintainers, via PR to main |
| `prompts/` | Copilot prompt templates | Maintainers, via PR to main |
| `connections.yaml` | Connection metadata + secret *names* | Maintainers, via PR to main |
| `generated/`, `.cdu-lock.json`, `reports/` | Pipeline outputs on your branch | The pipeline |

## Running the pipeline locally

```bash
pip install -r requirements.txt
python pipeline/cdu.py validate     # fast, no external systems
python pipeline/cdu.py generate     # needs Copilot CLI + COPILOT_TOKEN
python pipeline/cdu.py deploy       # needs dev credentials; mode: deploy only
python pipeline/cdu.py test
pytest                              # the pipeline's own test suite
```

Secrets are never stored in the repo: `connections.yaml` holds secret
*names*; values come from GitHub Actions Secrets (or your local env vars).
