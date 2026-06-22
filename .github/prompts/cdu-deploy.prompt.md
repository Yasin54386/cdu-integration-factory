---
mode: agent
description: Deploy the CDU job (ORDS to Oracle, push Mule, run tests) — requires mode deploy.
---

# /cdu-deploy — Deploy the job

Deploys the generated artifacts. This crosses the human gate (D6), so it only
runs when `job/intent.md` has `mode: deploy`.

## Pre-flight

1. Read `job/intent.md`. If `mode` is not `deploy`, STOP and tell the human to
   review the generated artifacts first, then set `mode: deploy`. Do not flip it
   yourself.
2. Run `python pipeline/cdu.py validate` — must pass before deploying.

## Deploy

3. Run the deploy. To do everything in canonical order:
   ```
   python pipeline/cdu.py run
   ```
   Or one sub-stage at a time:
   ```
   python pipeline/cdu.py run --sub sql      # deploy ORDS module to Oracle
   python pipeline/cdu.py run --sub mulesoft # push Mule change to its repo
   python pipeline/cdu.py run --sub tests    # run pytest, write report
   ```
   - `sql` deploys the ORDS module to the Oracle connection (skipped if the job
     has no SQL).
   - `mulesoft` pushes the flow/edit to the target repo; the institute's CI/CD
     deploys it to Anypoint.
   - `tests` runs the generated pytest and writes a report.

4. If a step fails, read the error, report it clearly (which sub-stage, why),
   and stop — do not retry blindly. Never print secret values from a failure.

## Finish

5. Summarize what deployed: ORDS endpoint, the Mule repo/branch pushed, and the
   test result.
