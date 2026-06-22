# CDU Integration Factory — End-to-End Workflow

This document is the operational map of the factory: how a single integration
goes from an idea to deployed Oracle ORDS + MuleSoft artifacts. It reflects the
sub-stage architecture (`cdu run --sub …`), the generate→deploy human gate
(D6), per-stage version history, and the GitHub Actions triggers.

For the binding architecture decisions and contracts, see
`CDU-INTEGRATION-FACTORY-SPEC.md`. This file describes the *flow*; the spec is
the source of truth for the *rules*.

---

## 1. Lifecycle at a glance

```mermaid
flowchart TD
    Start([New integration needed]) --> Boot

    subgraph Bootstrap["1 · Bootstrap"]
        Boot["cdu start-integration NAME"]
        Boot --> BranchCheck{Branch feature/NAME<br/>already exists?}
        BranchCheck -- yes --> BootErr[/Error: pick another name/]
        BranchCheck -- no --> MkBranch["git checkout -b feature/NAME<br/>git push -u origin"]
        MkBranch --> PlainCheck{job/docs/<br/>plain_text_intent.txt<br/>present?}
    end

    PlainCheck -- yes --> Draft
    PlainCheck -- no --> ManualIntent

    subgraph Intent["2 · Author the intent"]
        Draft["cdu draft-intent<br/>(GitHub Models API drafts intent.md)"]
        Draft --> Review1["Human reviews / edits<br/>job/intent.md"]
        ManualIntent["Hand-write job/intent.md<br/>(YAML front-matter + notes)"]
        ManualIntent --> Review1
    end

    Review1 --> Validate

    subgraph Run["3 · Run sub-stages (sql → mulesoft → tests)"]
        Validate["cdu validate"]
        Validate --> VOK{Valid?}
        VOK -- no --> VErr[/Fix intent / files / secrets/]
        VErr --> Validate
        VOK -- yes --> ModeGate{intent mode?}

        ModeGate -- generate --> GenOnly["cdu run<br/>generate artifacts for review<br/>(no external deploy)"]
        ModeGate -- deploy --> GenDeploy["cdu run<br/>generate + deploy + test"]
    end

    GenOnly --> Review2["Human reviews generated/<br/>flip mode: deploy when ready (D6)"]
    Review2 --> Validate

    GenDeploy --> Done([Deployed: ORDS live,<br/>Mule app pushed,<br/>tests green])

    Done --> Iterate{More changes?}
    Iterate -- yes --> Review1
    Iterate -- no --> Finish([Branch handed off /<br/>institute CI deploys])
```

---

## 2. Sub-stage decision logic

Each sub-stage is independent and skips work that is already current. Staleness
is decided by the impact map (`pipeline/core/impact.py`) comparing the intent
front-matter and input-file hashes against the lockfile.

```mermaid
flowchart TD
    RunStart["cdu run [--sub sql|mulesoft|tests]"] --> Resolve["resolve_substages()<br/>→ canonical order: sql, mulesoft, tests"]
    Resolve --> Loop{For each<br/>requested sub-stage}

    Loop --> Kind{Which<br/>sub-stage?}

    %% sql / mulesoft path
    Kind -- sql / mulesoft --> Stale1{Artifact stale?<br/>(impact map)}
    Stale1 -- no --> Skip1["skip — record nothing"]
    Stale1 -- yes --> Snap1["snapshot prior version<br/>→ stage_history"]
    Snap1 --> Gen1["generate via GitHub Models API<br/>sanity-check + THE WALL secret scan"]
    Gen1 --> Commit1["commit generated/ + lockfile [skip ci]<br/>backfill HEAD sha into snapshot"]
    Commit1 --> Mode1{mode = deploy?}
    Mode1 -- no --> Loop
    Mode1 -- yes --> Deploy1["sql → ORDS deploy (Oracle)<br/>mulesoft → git handoff / Anypoint"]
    Deploy1 --> CommitD["commit lockfile [skip ci]"]
    CommitD --> Loop

    %% tests path
    Kind -- tests --> Stale2{Test file stale?}
    Stale2 -- yes --> Snap2["snapshot + regenerate test file<br/>commit [skip ci]"]
    Stale2 -- no --> ModeT{mode = deploy?}
    Snap2 --> ModeT
    ModeT -- no (generate) --> Skip2["skip running"]
    ModeT -- yes --> Pytest["ALWAYS run pytest<br/>(inputs may change externally)"]
    Pytest --> TPass{Passed?}
    TPass -- no --> Fail[/RunError: tests FAILED<br/>completed work stays committed/]
    TPass -- yes --> Report["write report + lockfile [skip ci]"]
    Report --> Loop

    Skip1 --> Loop
    Skip2 --> Loop
    Loop -- done --> End([RunOutcome])
```

**Key rule:** `sql` and `mulesoft` are skipped entirely when not stale; `tests`
are *always executed* in deploy mode even when the test file itself was not
regenerated, because the deployed system they probe can change independently.

---

## 3. Triggers — how `cdu run` gets invoked

```mermaid
flowchart LR
    subgraph Auto["Automatic (push)"]
        Push["git push to feature/**<br/>touching job/**"] --> WF1["cdu-pipeline workflow"]
    end

    subgraph Manual["Manual (workflow_dispatch)"]
        UI["Actions UI → Run workflow<br/>inputs: substages, reason"] --> WF2["cdu-pipeline workflow"]
    end

    WF1 --> Steps
    WF2 --> Steps

    subgraph Steps["Workflow steps"]
        S1["validate"] --> S2["resolve --sub flags<br/>(blank = all)"]
        S2 --> S3["cdu run [flags]"]
        S3 --> S4["job summary"]
    end

    subgraph Local["Local (CLI)"]
        L1["cdu run --sub sql"]
    end
```

The loop guard is double (spec §12): pipeline commit-backs carry `[skip ci]`
**and** the push trigger's `paths: ['job/**']` filter ignores `generated/**`
and `.cdu-lock.json`. `workflow_dispatch` runs are intentionally exempt — they
are explicitly human-initiated.

---

## 4. Version history & rollback

Every regeneration archives the prior version into `stage_history[substage]`
(newest first, capped at 10) with the git commit SHA that holds that file.

```mermaid
flowchart LR
    Inspect["cdu history [--sub sql]"] --> List["list versions:<br/>timestamp · sha · run-id · result"]
    List --> Choose{Restore an<br/>older version?}
    Choose -- yes --> Roll["cdu rollback --sub sql --version N"]
    Roll --> Show["git show SHA:path<br/>→ write to working tree"]
    Show --> ReviewR["Review restored file<br/>(NOT auto-committed)"]
    ReviewR --> Recommit["commit manually<br/>or re-run pipeline"]
    Choose -- no --> Done([keep current])
```

---

## 5. Existing MuleSoft repos

When `mulesoft_delivery.repo` names an existing institute repo, the factory
inspects it before touching anything.

```mermaid
flowchart TD
    PreFlight["cdu inspect-mule-repo (optional pre-flight)"] --> CloneI["clone → inspect_repo_structure()<br/>pom.xml? mule-artifact.json? src/main/mule/?"]
    CloneI --> ReportI["report: looks_like_mule, version,<br/>existing flows, our flow present?"]

    DeployM["mulesoft sub-stage (deploy)"] --> RepoExists{Repo exists?}
    RepoExists -- no, named --> ErrN[/Error: repo does not exist/]
    RepoExists -- no, unnamed --> Create["create cdu-NAME + full scaffold"]
    RepoExists -- yes --> IsMule{looks_like_mule_project?}
    IsMule -- no --> ErrM[/Error: not a MuleSoft project/]
    IsMule -- yes --> Replace["replace ONLY<br/>src/main/mule/JOB_flow.xml"]
    Create --> PushB["commit + force-push cdu/JOB branch"]
    Replace --> PushB
    PushB --> Handoff([Institute CI/CD deploys to Anypoint])
```

---

## 6. The three layers of a run

| Layer | Lives in | Crosses to external systems? |
|-------|----------|------------------------------|
| **Intent** | `job/intent.md` (+ supporting `job/` files) | No — logical names only |
| **Generate** | `generated/` (SQL, Mule XML, pytest) | No — reviewable artifacts |
| **Deploy** | Oracle ORDS, MuleSoft repo/Anypoint, test run | Yes — gated behind `mode: deploy` (D6) |

**THE WALL (spec §7):** secret *values* never enter prompts or generated
output. `connections.yaml` holds secret *names*; values arrive as Actions
secrets / env vars at deploy time only; every generated file is scanned and
refused if a credential value is found.

---

## 7. Command reference

| Command | Purpose |
|---------|---------|
| `cdu start-integration NAME` | Create + push `feature/NAME`; detect plain-text intent |
| `cdu draft-intent` | AI-draft `job/intent.md` from `job/docs/plain_text_intent.txt` |
| `cdu validate` | Check intent, referenced files, connections, secrets |
| `cdu run [--sub …]` | Run sub-stages (sql → mulesoft → tests); respects mode |
| `cdu inspect-mule-repo` | Pre-flight inspection of an existing MuleSoft repo |
| `cdu history [--sub …]` | Show per-stage version trail |
| `cdu rollback --sub S [--version N]` | Restore a prior artifact version from git |
| `cdu generate` / `deploy` / `test` | Legacy single-phase commands (sub-stages preferred) |

---

*Generated as the operational companion to `CDU-INTEGRATION-FACTORY-SPEC.md`.
When the flow changes, update this diagram in the same PR.*
