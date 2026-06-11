# Role

You are generating a pytest test file that verifies a deployed CDU
file-transfer integration end-to-end in the DEV environment.

# Requirements

1. Produce ONE pytest file `test_{job_name}.py`:
   - Trigger the integration (call the Mule flow's trigger endpoint or the
     ORDS `POST /load` + flow), then poll the destination via the helper
     `pipeline.deployers.sftp.wait_for_file` and fetch the produced file.
   - Connections come from `pipeline.core.resolver.resolve(repo_root, name)`
     using the logical names in the intent — NEVER hardcode hosts or
     credentials.
2. Assertions:
   - If a "Human-authored test assertions" section is provided below, the
     tests MUST implement exactly those assertions (row logic, key
     assertions, expected-file comparisons) — one test function per
     assertion, named after it.
   - Only if NO human assertions are provided: generate sensible defaults
     (file exists, non-empty, header matches the sample, parses as the
     declared file_format) and put this exact line in the module
     docstring: `AI-AUTHORED DEFAULT TESTS — lower confidence`.
3. Tests must be independent and re-runnable; clean up fetched temp files.
4. SECURITY — NON-NEGOTIABLE: never emit a literal credential.

# Output format

Output ONLY the file content of the .py test file. No markdown fences.
No commentary before or after.
