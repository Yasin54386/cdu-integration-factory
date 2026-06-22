---
mode: agent
description: Make MuleSoft changes in the real target repo (edit existing or add new files) and deliver them.
---

# /cdu-mule — Edit the target MuleSoft repo with Copilot, then deliver

Use this when the integration must change an **existing** MuleSoft repository —
updating an existing flow, adding a new flow, adjusting DataWeave / pom /
properties, or any mix. Copilot makes the real changes in a local clone; the
factory validates and pushes them.

Read `.github/copilot-instructions.md` first for the hard rules (THE WALL, no
secret values, full-regen for generated artifacts, contract-only).

## Steps

1. Run in the terminal:
   ```
   python pipeline/cdu.py mule-checkout
   ```
   This clones the repo named in `job/intent.md` → `mulesoft_delivery.repo`
   into `mule_workspace/<repo>/`, on the branch from `mulesoft_delivery.branch`
   (existing branch → your changes go on top; absent/omitted → a fresh branch
   off the default is created). The command prints the workspace path and the
   existing flows.

2. Open `mule_workspace/<repo>/` and study the existing project structure
   (flows under `src/main/mule/`, DataWeave under `src/main/resources/`,
   `pom.xml`, properties).

3. Make the changes the dev requirement needs — guided by `job/intent.md` and
   its supporting files under `job/`. This may be:
   - editing an existing file (e.g. a flow or a `.dwl` transform),
   - adding new files,
   - or both.
   Keep edits minimal and consistent with the existing code style. **Never
   write a real credential value** — use property placeholders the project
   already uses.

4. Run in the terminal:
   ```
   python pipeline/cdu.py mule-deliver
   ```
   The factory stages your changes, scans every changed file for secret values
   (THE WALL), checks changed `.xml` is well-formed, commits, and pushes to the
   target repo/branch. It prints the list of changed files and the URL.
   - If it reports a problem (malformed XML, a detected secret), fix the file in
     the workspace and run `mule-deliver` again.

## Notes

- `mule-deliver` requires `mode: deploy` in `job/intent.md` (the human gate, D6).
- The workspace (`mule_workspace/`) is gitignored — it is NOT committed to the
  factory feature branch; it is pushed to its own MuleSoft remote.
- The institute's existing CI/CD picks up the pushed branch and deploys it to
  Anypoint.
