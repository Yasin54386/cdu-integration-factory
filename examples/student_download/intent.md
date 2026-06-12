---
job_name: student_download_v1
mode: generate
direction: download

sources:
  sql:
    - file: sql/load_staging.sql
      role: staging_load
    - file: sql/export_query.sql
      role: export
  samples:
    - file: samples/expected_output.csv
      role: output_example

destination:
  connection: sftp_dev
  path: /incoming/student/
  file_format: csv
  file_name_pattern: "student_{yyyymmdd}.csv"

connections:
  oracle: oracle_dev
  mulesoft: mule_repo_dev   # generated Mule app is pushed to a git repo;
                            # existing CI/CD deploys it to Anypoint

testing:
  expected_row_logic: "row count equals SELECT COUNT(*) FROM STG_STUDENT_DOWNLOAD_V1"
  key_assertions:
    - "header row matches samples/expected_output.csv line 1 exactly"
    - "no null values in STUDENT_ID column"
---

## Notes

Nightly extract of active students for the external reporting system.
STUDENT_ID is the natural key. Dates are exported as YYYY-MM-DD.
Empty extract (zero data rows) is valid — the file must still be
delivered with the header row.
