# Labelbox FDE/FDR Take-Home — Lakshitha Anand

Synthetic task generation, an RL environment, an LLM grader, and a reliability
analysis for Terminal-Bench 2.0 / Harbor. This repository contains both tasks,
their design write-ups, and (for Task 2) the collected attempts used in the
analysis.

## Layout

```
task1/   Synthetic task-generation pipeline
         - README.md            how to run (start here)
         - Design_Writeup.docx  Task 1 design write-up
         - pipeline/            one module + CLI per stage
         - tasks/generated/     the 5 validated Harbor tasks + assurance cards
         - artifacts/           stage reports, funnel report, cards (evidence)
         - scripts/             demo.sh (full pipeline), handcheck.sh
         - docs/                plan, curation, stage summaries

task2/   RL environment, LLM grader, and reliability analysis
         - README.md                   how to run (start here)
         - environment_design_note.docx / docs/  design notes
         - FINDINGS.md                 reliability findings summary
         - src/task2/                  environment + grader
         - data/attempts/              collected agent attempts (graded)
         - data/grades/, data/reports/ grader output + reliability report
         - tasks_real/, fixtures/      task set + adversarial fixtures
```

## Running

Each task is self-contained; follow the README inside `task1/` and `task2/`.

- **Task 1:** `cd task1 && bash scripts/demo.sh` — replays the full pipeline from
  committed state in ~7 minutes with **zero API keys**; regenerated task folders
  come out byte-identical to committed. `scripts/handcheck.sh` independently
  re-verifies the key security claim.
- **Task 2:** see `task2/README.md`. Model-calling steps (generating attempts,
  running the grader) require an `ANTHROPIC_API_KEY`; the committed attempts and
  reports in `data/` let the analysis be reviewed without re-running anything.

## Notes

- No API keys or virtual environments are included; set your own
  `ANTHROPIC_API_KEY` (via environment variable or a local, gitignored `.env`)
  only if you want to re-run the model-calling steps.
- Every accepted Task 1 task ships a machine-readable assurance card; every
  Task 2 grade and attempt is logged with the exact model IDs used, for
  reproducibility. Residual risks and limitations are stated on the cards, in
  each task's write-up, and in `task2/FINDINGS.md`.
