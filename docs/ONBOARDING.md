# Developer onboarding — one page

You never touch the pipeline. You fill in one folder and push twice.

## 1. Branch

```bash
git checkout -b feature/<job_name>     # one branch = one integration job
```

The branch name doesn't have to equal `job_name`, but it must start with
`feature/` for the pipeline to trigger.

## 2. Fill `job/`

- `job/intent.md` — the contract. Start from the annotated template (or
  copy `examples/student_download/intent.md`). Keep `mode: generate`.
- `job/sql/` — your staging-load and export SQL (referenced from the intent).
- `job/specs/` — optional BRDs (.docx, text PDF).
- `job/samples/` — optional expected-output examples.
- `job/mappings/` — optional field-mapping spreadsheets (.xlsx).
- `job/tests/` — optional golden files for assertions.

Add a `testing:` block to the intent if you can state what "correct"
looks like — the pipeline then builds tests from *your* assertions instead
of AI-generated defaults.

## 3. Push #1 — generate

The pipeline validates your intent (clear errors if something's off),
generates the ORDS module, MuleSoft flow and tests, and **commits them
back to your branch**. Pull and review the diff under `generated/`.

## 4. Push #2 — deploy

Happy with the generated code? Edit `job/intent.md`:

```yaml
mode: deploy
```

Push. The pipeline deploys the ORDS module to Oracle **dev** and hands the
generated MuleSoft app off to git: by default it creates repo
`cdu-<job-name>` with a buildable Mule project; set `mulesoft_delivery.repo`
in the intent to instead push branch `cdu/<job_name>` into an existing Mule
repo (only the flow XML is replaced there). Your MuleSoft CI/CD deploys to
Anypoint from that push. The pipeline then runs the tests and writes
`reports/run_<timestamp>.md` (also posted to the PR if you opened one).

Note: the end-to-end test needs your MuleSoft CI/CD to have deployed the
pushed app — a test failure right after the first deploy may just mean
"not deployed yet"; re-push to re-run.

## 5. Iterate

Fix the *input* that's wrong (intent, SQL, spec), not the generated code —
generated files are overwritten on regeneration. Only artifacts affected
by your change regenerate; the rest are skipped. Re-pushing with no
changes is a safe idempotent redeploy.

When the job is done, the branch can simply be left or deleted — it never
merges to main.

Stuck? See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
