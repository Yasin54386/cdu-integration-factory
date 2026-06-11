---
# ╔══════════════════════════════════════════════════════════════════════╗
# ║ CDU INTEGRATION JOB — INTENT FILE                                      ║
# ║ This is the ONLY contract between you and the factory.                 ║
# ║ Fill it in on your feature branch; never edit this file on main.       ║
# ╚══════════════════════════════════════════════════════════════════════╝

# ============ REQUIRED ============

# Namespaces EVERYTHING deployed (ORDS endpoint, staging table, Mule app).
# Pattern: starts with a letter; lowercase letters, digits, underscores;
# 3–41 chars total. Example: student_download_v1
job_name: my_job_v1

# THE HUMAN GATE (spec D6):
#   generate → pipeline validates + generates artifacts, then STOPS so you
#              can review the generated diff on your branch.
#   deploy   → pipeline regenerates anything stale, deploys to DEV, runs
#              tests, posts the report. Flip this only after reviewing.
mode: generate

# download = Oracle → file → external destination. (upload is reserved for
# a later version; the schema accepts it but the pipeline targets download.)
direction: download

sources:
  sql:                               # at least one entry required
    - file: sql/load_staging.sql     # paths are relative to job/
      role: staging_load             # roles: staging_load | export | procedure
    - file: sql/export_query.sql
      role: export
  # specs:                           # optional: BRDs, business-rule docs
  #   - file: specs/my_brd.docx      # .docx and text-based .pdf supported
  #     role: business_rules
  # samples:                         # optional: example output files
  #   - file: samples/expected_output_sample.csv
  #     role: output_example
  # mappings:                        # optional: field-mapping spreadsheets
  #   - file: mappings/field_map.xlsx
  #     role: field_mapping

destination:
  connection: sftp_dev               # logical name from connections.yaml
  path: /incoming/my_job/
  file_format: csv                   # csv | fixed | json | xml
  file_name_pattern: "my_job_{yyyymmdd}.csv"

connections:                         # which logical connections this job uses
  oracle: oracle_dev                 # defaults shown; override only if non-standard
  mulesoft: mule_dev

# ============ OPTIONAL ============
# Provide a testing: block (and/or files in job/tests/) and the pipeline
# builds tests from YOUR assertions. Omit it entirely and Copilot generates
# default tests — the report flags them "AI-authored, lower confidence".
# testing:
#   expected_row_logic: "row count equals SELECT COUNT(*) FROM STG_MY_JOB"
#   key_assertions:
#     - "header row matches samples/expected_output_sample.csv line 1 exactly"
#     - "no null values in ID column"
#   expected_files:                  # files in job/tests/ to compare against
#     - file: tests/golden_output.csv
#       compare: exact_header_and_first_5_rows
---

## Notes (free-form, optional)

Anything you want the generator to know: edge cases, business context,
gotchas. The pipeline passes this section to Copilot as extra context.
