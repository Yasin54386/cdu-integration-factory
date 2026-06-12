# Troubleshooting

## Validation failures (the workflow fails in seconds)

| Message | Fix |
|---------|-----|
| `intent.md has no YAML front-matter` | The file must start with `---`, YAML, `---`. Copy the template or the example. |
| `intent.md references job/... but that file does not exist` | The path in `sources:` is wrong or the file wasn't committed. Paths are relative to `job/`. |
| `unknown role '...'` | Allowed roles: sql → `staging_load`/`export`/`procedure`; specs → `business_rules`; samples → `output_example`; mappings → `field_mapping`. |
| `connection '...' is not defined in connections.yaml` | Use a logical name that exists in `connections.yaml` (e.g. `oracle_dev`, `sftp_dev`), or ask a maintainer to add one via PR to main. |
| `Secret XYZ not configured in repo Settings → Secrets → Actions` | A repo admin must add that Actions secret. The pipeline checks names only — it never sees or prints values. |
| `job/... exists but is not referenced in intent.md` (warning) | Either reference the file in the intent or delete it. The run still proceeds. |

## Generation issues

- **`copilot CLI exited ...` / empty output** — transient Copilot/network
  flakiness is retried 3× automatically. Persistent failure usually means
  `COPILOT_TOKEN` is missing/expired or Copilot CLI is disabled by org
  policy (spec §11 prerequisite).
- **`preprocessed content is over the per-file cap`** — trim the document
  or split it. The pipeline refuses to silently truncate business rules.
- **`output contains the value of secret ...`** — the security wall fired.
  Nothing was written. Re-run; if it persists, report it to the
  integration team (prompt template fix needed on main).
- **Wrong artifact regenerated / nothing regenerated** — regeneration
  follows the impact map in spec §9. `mode` changes alone never
  regenerate. Delete `.cdu-lock.json` on your branch to force a full
  regeneration of everything.

## Deploy/test issues

- **`mulesoft_delivery.repo '...' does not exist`** — the intent names an
  existing Mule repo that isn't under the connection's namespace (or the
  `MULE_REPO_TOKEN` can't see it). Fix the name, or omit `repo:` to let
  the factory create `cdu-<job-name>`.
- **Mule app pushed but nothing deployed to Anypoint** — the factory's job
  ends at the git push (branch `cdu/<job_name>`); deployment from there is
  your MuleSoft repo's own CI/CD. Check that repo's pipeline triggers on
  `cdu/*` branches.
- **Deploy fails reaching Oracle/Anypoint** — dev systems are
  internal-only from GitHub-hosted runners unless networking was set up
  (spec §14). Check with the platform team about the self-hosted runner.
- **Tests fail** — read `reports/run_<ts>.md`. Fix the *input* (intent,
  SQL, spec), never the files under `generated/` — they're overwritten on
  the next regeneration. If the report header says assertions are
  AI-authored, consider adding a `testing:` block with real assertions.
- **Redeploy collides with someone else's objects** — it shouldn't: all
  object names are namespaced by `job_name`. If two branches share a
  `job_name`, rename one.

## Pipeline didn't trigger

- Branch must match `feature/**` AND the push must touch `job/**`.
- Commits made by the pipeline itself contain `[skip ci]` — that's the
  loop guard, not a bug.
- Two quick pushes: the older run is auto-cancelled (newest wins).
