---
mode: agent
description: Generate the Oracle ORDS module SQL (sql sub-stage) with Copilot.
---

# /cdu-generate-sql — Generate the ORDS module

Generate ONLY the `sql` sub-stage (the Oracle ORDS module). Copilot is the
engine. Read `.github/copilot-instructions.md` first (THE WALL, full-regen,
contract-only).

## Steps

1. Confirm the job declares SQL sources. Run `python pipeline/cdu.py plan --json`
   and look at the `sql` step:
   - if its action is **skip** with "no SQL sources", report that this is a
     MuleSoft-only job and stop — there is no ORDS to generate.
   - otherwise continue.
2. Run:
   ```
   python pipeline/cdu.py prompt --sub sql
   ```
3. Open `generated/.prompts/sql.prompt.md` and read everything below the
   `=== PASTE FROM HERE INTO COPILOT CHAT ===` marker — that is your full spec.
4. Generate the ORDS module and write **only the file content** (no fences, no
   prose) to `generated/ords/<job_name>_module.sql`. It must:
   - be a PL/SQL script using `ORDS.DEFINE_MODULE` / `DEFINE_TEMPLATE` /
     `DEFINE_HANDLER`, namespaced by `job_name`;
   - expose exactly the endpoints the intent describes (e.g. a paginated GET
     and/or a POST), with no hard-coded credential values.
5. Run:
   ```
   python pipeline/cdu.py ingest --sub sql
   ```
   If it fails (missing `ORDS.DEFINE_`, a detected secret value, etc.), fix the
   file and re-run `ingest` until it passes.

Report the output path and that it was committed with `[skip ci]`.
