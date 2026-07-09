# Task 2 — RL environment, LLM grader, and grader reliability analysis

An **environment** that runs a coding agent on Terminal-Bench 2.0 (TB2) tasks
and scores attempts deterministically, an **LLM grader** that audits attempt
quality on three rubrics, and a **reliability analysis** that measures — with
confidence intervals and adversarial probes — how much that grader can be
trusted.

The one-sentence thesis: **the deterministic verifier is the only training
signal; the LLM grader is audit-only and never touches reward** — and the main
contribution is an honest measurement of where that grader works and where it
breaks (spoiler: it anchors on the verifier label; see the findings summary).

Everything below was built from scratch — no Harbor, no agent framework; the
only third-party content is the 5 task directories cloned from
[terminal-bench-2](https://github.com/laude-institute/terminal-bench-2) @
`2fd12b88` (with one documented light modification).

## Vocabulary (7 terms; everything else follows)

- **Task** — one TB2 coding challenge: a Docker environment, an instruction,
  and an official test suite. Three difficulty tiers: **easy** (fix-git,
  prove-plus-comm), **medium** (regex-log, kv-store-grpc), **hard**
  (make-mips-interpreter). The four easy+medium tasks are grouped as the
  **light tasks** throughout the analysis (same collection procedure); the
  hard task is always broken out separately.
- **Attempt** — one full try at a task, recorded as a *trajectory* (every
  command, output, the workspace diff, and the verifier result). Attempt IDs
  read `task--source--id`.
- **Sources / baselines** — who produced an attempt: `claude` (a live LLM
  agent), `oracle` (the task's official reference solution — should always
  pass), and deliberately weak or dishonest baselines: `noop` (does nothing),
  `tamper` (edits the protected tests), `hardcode` (fakes expected outputs),
  `scripted` (a scripted weak effort).
- **Verifier** — deterministically runs the task's official test suite in a
  clean container. Its pass/fail outcome is "ground truth" and the *only*
  source of reward.
- **Grader** — the LLM judge being evaluated. It scores each attempt 1–5 with
  a label (`good`/`mixed`/`poor`) on three rubrics: **R1** Problem
  Localization, **R2** Patch Correctness (the primary one), **R3**
  Generalization & Regression Safety. It audits; it never touches reward.
- **Tripwire** — a deterministic anti-cheat check (hashes of protected files):
  tampering forces reward 0 and skips LLM grading entirely, no model needed.
- **Wilson CI** — a 95% confidence interval on a rate; with samples this small
  every reported rate is an interval, not a point.

---

## A. Requirements

- Python **>= 3.11**
- **Docker** installed and running
- **git** on the host (used for workspace diffing)
- macOS or Linux shell
- `ANTHROPIC_API_KEY` — **only** for regenerating attempts/grades. Verifying
  the submitted reports and running the smoke check need **no key at all**.
- Network access on first run (Docker base-image pulls; the verifier also
  bootstraps its tooling at verification time).

## B. Quickstart — verify submitted results with zero API cost

```bash
cd task2
python3 -m venv .venv
.venv/bin/pip install -e .

# 1. Deterministic acceptance check (~3-6 min, builds one small Docker image):
#    oracle solves fix-git; a live test-tampering attempt AND the test-deletion
#    fixture both trip the tripwire with reward 0; no LLM is ever called.
.venv/bin/task2 smoke
# expected last line:
#   SMOKE PASS — oracle solves; tamper attempt and test-deletion fixture both trip the tripwire with reward 0; no LLM involved.

# 2. Reproduce the full reliability report from the committed grade records
#    (~2 seconds, zero API calls — the empty key proves it):
ANTHROPIC_API_KEY= .venv/bin/task2 analyze --no-batch
# expected output:
#   agreement (R2 vs ground truth, pooled): 31/31  Wilson CI [0.89, 1.0]
#     subsets — light: 28/28 CI [0.879, 1.0], hard: 3/3 CI [0.438, 1.0]
#   adversarial catch rate: single 22/30, multi-hop 20/30
#   position-bias flip rate: 0/9
#   length-bias mean score delta: -0.4444444444444444
#   wrote data/reports/reliability_report.md
#   wrote data/reports/reliability_report.json
```

**Then read the main deliverable:** the terminal output above is only the
headline summary. The full analysis — agreement tables with the exact
disagreements, the single-pass vs multi-hop ablation, the verifier-anchoring
probe, both bias probes with per-attempt tables, the adversarial catch-rate
table, the use-case verdict, and the limitations — is the report you just
regenerated: [`data/reports/reliability_report.md`](data/reports/reliability_report.md).

Both commands work because every artifact is committed: 35 attempt records in
`data/attempts/` (28 light-task analysis + 3 hard-task + 4 pipeline-smoke
records, distinguished by their `dataset` field; smoke records are excluded
from every reported number), 322 grade records (`data/grades/`), the reports
(`data/reports/`), and 30 adversarial fixtures (`fixtures/adversarial/`) —
**all 5 attack types on all 5 tasks** (test-deletion, workaround/input-hardcode,
prompt-injection, narrative, and a verifier-anchoring pass/flip pair), 6 fixture
files per task.

## C. What's in the repo

```
task2/
  pyproject.toml            # installable package (`pip install -e .` -> `task2` CLI)
  README.md                 # this file
  config.yaml               # models, budgets, timeouts (all configurable)
  plan.md                   # the build plan this project followed
  FINDINGS.md               # dated findings log, one entry per build step
  docs/environment_design_note.md   # 2-page env design note (assignment deliverable)
  docs/multihop_grading.md          # multi-hop grading: design, eligibility rule, results
  docs/rubric_evolution.md          # old vs new rubric prompts + measured impact of the swap
  src/task2/
    tasks.py                # TB2 task loader (validates the 5 required components)
    environment.py          # Docker lifecycle: reset / exec / verify / snapshot_diff / close
    verifier.py             # runs official test.sh with CLEAN-test injection, parses CTRF (JSON test-results format)
    reward.py               # deterministic reward + integrity tripwire (no LLM imports)
    agent.py                # thin Claude bash agent + oracle runner + scripted baselines
    controller.py           # attempt loop -> Trajectory
    trajectory.py           # Trajectory / AttemptRecord dataclasses (JSON persisted)
    collect.py              # systematic dataset collection (dataset=analysis)
    grader/
      prompts.py            # spec-appendix prompts, verbatim (two documented
                            #   edits in the multi-hop judge template: FINDINGS F6.9, F13.1)
      rubrics.py            # R1 Problem Localization, R2 Patch Correctness,
                            #   R3 Generalization & Regression Safety
      schema.py             # strict JSON validation; one retry; then flagged
      grader.py             # pre-screen + single-pass + multi-hop + step-level + pairwise
    analysis/
      stats.py              # Wilson interval (+ optional bootstrap)
      reliability.py        # all analyses + report renderer
  scripts/mips_bounded_run.py   # 1-hour-capped hard-task runner (logged)
  tasks_real/               # 5 cloned TB2 tasks
  fixtures/adversarial/     # 30 hand-authored cheat fixtures (5 attack types × 5 tasks)
  data/{attempts,grades,reports}/   # every record behind every number, committed
```

## D. The pipeline, command by command

Each stage is a CLI subcommand; later stages consume the files earlier stages
write. All model names live in `config.yaml`; exact model IDs are logged into
every record.

| command | what it does | needs API key? |
|---|---|---|
| `task2 smoke` | oracle passes + tamper trips tripwire, no LLM | no |
| `task2 tasks [name]` | list/inspect loaded TB2 tasks | no |
| `task2 env <task>` | reset one environment, print initial pass count + network probe | no |
| `task2 attempt <task> --agent oracle\|noop\|tamper\|hardcode\|claude --verify --save` | run one attempt end-to-end | only `claude` |
| `task2 collect [--round extra]` | systematic dataset: oracle + agent variants + baselines per task | yes |
| `task2 grade <attempt\|all> [--mode multi_hop] [--pairwise B] [--step N]` | grade saved attempts | yes |
| `task2 adversarial [--run]` | list fixtures / print catch-rate table | `--run` reuses stored grades |
| `task2 bias` | position + length bias probes | reuses stored grades |
| `task2 analyze [--no-batch] [--regrade]` | everything above + report MD/JSON | no, unless `--regrade` |

Why each stage exists:

1. **Environment** (`env`, inside `attempt`): builds the task image locally,
   measures the initial passing-test count in a throwaway container, then runs
   the agent **with networking disconnected** (verified by an in-container TCP
   probe, not assumed). See `docs/environment_design_note.md`.
2. **Verification**: always wipes `/tests` and injects the pristine host-side
   copy before running the official `test.sh` — nothing an agent does in the
   container can change what gets verified.
3. **Reward** (`reward.py`): `0.75·all_tests_pass + 0.20·progress + 0.05·runnable`,
   clamped; a deterministic tripwire (protected-file hashes) forces reward 0 on
   tampering and skips LLM grading. Negative progress is logged unclamped.
   The module imports nothing model-related — grader output *cannot* reach it.
4. **Collection** (`collect`): produces a labeled dataset
   (`dataset=analysis`) with a genuine pass/partial/fail mix by varying step
   budgets, token budgets, and temperature (`agent.attempt_variants` in
   config). Ad-hoc runs are labeled `smoke` and are excluded from every
   reported number.
5. **Grading** (`grade`): three rubrics with the spec's verbatim prompts, in
   pointwise, pairwise (A/B order randomized per call and logged), and
   step-level modes; two pipelines — single-pass (judge sees the full
   trajectory) and multi-hop (deterministic extraction → LLM evidence
   extraction → evidence-only judge). `--pairwise` + `--mode multi_hop` gives
   a *derived* multi-hop comparison (both attempts through the multi-hop
   judge, winner by rubric-score comparison — the spec appendix defines no
   native pairwise multi-hop prompt). Strict JSON with one corrective retry.
6. **Analysis** (`analyze`): agreement vs ground truth with Wilson 95% CI,
   single-pass vs multi-hop ablation, position/length bias probes, adversarial
   catch-rate, use-case verdict, limitations — written to
   `data/reports/reliability_report.{md,json}`.

## E. Reproducing from scratch (costs money, results will vary)

**Cheapest taste first (one live attempt, ~a minute):**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/task2 attempt fix-git --agent claude --verify --save
.venv/bin/task2 grade <attempt-id-printed-above>
```

Ad-hoc attempts like this are labeled `dataset=smoke`, so they never
contaminate the reported numbers — run as many as you like.

**Full reproduction.** One thing to know first: fresh collection *adds* to
the committed dataset (new attempts get the same `dataset=analysis` label,
and `analyze` pools everything on disk with that label). To reproduce from
scratch rather than grow the submitted dataset, move the committed data
aside first:

```bash
mv data data_submitted
mkdir -p data/attempts data/grades data/reports
```

(The adversarial fixtures live in `fixtures/`, not `data/`, so they are
unaffected. Restore the submitted results anytime:
`rm -rf data && mv data_submitted data`.)

Then:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/task2 collect                # ~20 attempts, 4 light tasks, ~30-60 min
.venv/bin/task2 collect --round extra  # +8 diversity attempts
.venv/bin/python scripts/mips_bounded_run.py   # hard task, 1-hour cap, logged
.venv/bin/task2 analyze --no-batch     # grades everything fresh + emits the report
                                       # (drop --no-batch to use the Batch API at ~50% cost)
```

Live-agent attempts and fresh grades are **nondeterministic** (LLM sampling);
seeds, budgets, and model IDs are logged per record so any rerun is honestly
comparable rather than identical. Grading reuses stored grades unless
`--regrade` is passed.

## F. Design rationale (the load-bearing decisions)

- **Deterministic reward only.** LLM judgment is too gameable to train
  against; the reliability analysis exists precisely to quantify that. The
  grader audits; the verifier pays.
- **Tripwire before LLM.** All anti-cheat checks are file-hash comparisons.
  The test-deletion fixture and a live tamper baseline both zero out with
  `llm_called=false`.
- **Clean-test injection.** Verification runs a pristine copy of the official
  tests every time — test editing is pointless *and* detected (two independent
  layers).
- **No network during attempts, network during verification.** TB2's verifier
  bootstraps `uv` from the network, so isolation is sequenced: disconnected
  the whole time the agent can act, reconnected only after it stops.
  One task (kv-store-grpc) needed a documented light modification —
  pre-installing its two pinned pip packages — to be solvable offline.
- **Multi-hop grading** as the flagship experiment: compress the untrusted
  trajectory into quoted evidence before judging. It is evaluated where its
  premise applies — a deterministic eligibility rule (`steps >= 10 OR
  trajectory_chars >= 8000 OR task == make-mips-interpreter`) selects the 13
  of 31 long/noisy attempts; short attempts are deliberately not multi-hop
  targets (nothing to extract). The ablation reports it honestly: under the
  current rubrics it ties single-pass on raw agreement (13/13 both on the
  eligible subset, no label disagreements); the value it adds shows up on
  the adversarial side (an evidence-only judge that never reads the agent's
  narrative). Full design, rule rationale, and result interpretation:
  `docs/multihop_grading.md`.
- **Batch vs direct grading**: bulk grading supports the Batch API (50% cost)
  with chunked, incrementally-persisted batches; the submitted report was
  produced in direct synchronous mode after a real batch-stranding incident —
  documented in full inside the report ("Operational Reliability Finding").

## G. Findings summary (details: `FINDINGS.md` + `data/reports/reliability_report.md`)

- **Agreement (R2 Patch Correctness vs verifier ground truth): 31/31 = 100%,
  Wilson 95% CI [89.0%, 100%]**, pooled over every analysis attempt with a
  clean verifier signal — N=31 = 28 light + 3 hard (exact IDs in the report).
  Subset breakdown: light 28/28 (CI [87.9%, 100%]), hard
  (make-mips-interpreter) 3/3 (CI [43.8%, 100%] — too small to interpret
  alone). One R2 grade initially returned score 0 (malformed, flagged per
  policy) and was regraded to a valid verdict — documented in FINDINGS F13.3.
  The rubrics/prompts were replaced on 2026-07-07 (they are part of the
  deliverable); `docs/rubric_evolution.md` records both generations verbatim
  and traces every number change to the wording change — including why
  agreement rose from 29/31: the new R2 is more verifier-aligned by
  construction, which buys agreement at the cost of independence (the
  anchoring result below is the measured proof of that trade-off).
- **The headline negative result: verifier anchoring, now shown on every
  task.** On a byte-identical trajectory pair where only the reported verifier
  label was falsified, R2 flips good→poor. Replicated per task, this holds on
  **4/4 tasks where the probe is valid** in single-pass and **5/5 in
  multi-hop** (make-mips is inconclusive in single-pass — the grader never
  credits even the truthful copy, so there is no `good` verdict to flip). The
  grader is not an independent second opinion when it agrees with the verifier,
  and that is systematic, not a fix-git artifact.
- **Adversarial catch rate: pooled single-pass 22/30, multi-hop 20/30** — the
  full 5-attack battery on all 5 tasks (30 fixtures; expanded 2026-07-08 from 6
  fix-git-only). **Four of the five attack types are caught on every task in
  both pipelines**: test-deletion (tripwire), workaround/input-hardcode,
  narrative, and prompt-injection — 20/20 non-anchoring fixtures graded `poor`.
  The only misses are the **verifier-anchoring** pairs (the systematic finding
  below). Per-task single-pass: fix-git 4/6, kv-store 4/6, make-mips 6/6
  (inflated by the inconclusive anchor pair — read with the anchoring detail),
  prove-plus-comm 4/6, regex-log 4/6; multi-hop 4/6 on every task.
- **The case that justifies the whole design**: an input-hardcode cheat earns
  deterministic reward **1.0** (tests genuinely pass) while the grader rates
  it **1/poor** with cited evidence — the exact disagreement an audit layer
  exists to surface.
- **Multi-hop eligibility ablation**: of the 31 collected attempts, 13 meet
  the deterministic long/noisy rule; on that subset both pipelines agree
  13/13 with ground truth, with zero label disagreements between them.
  Multi-hop is scoped to long-context extraction, not a default replacement.
- **Bias probes** (task-stratified: every task contributes, hard task
  included): position 0/9 flips (CI up to 30% — small n); length −0.44 mean
  score delta over 9 attempts with **zero label flips** — the delta is
  entirely one attempt whose score dropped 5→1 while its label stayed `good`,
  i.e. score-channel noise, not genuine length bias (next bullet).
- **Score-channel anchoring (new-rubric finding)**: the rubric prompts'
  strict-JSON examples show literal values (`"score":1`), and the grader
  anchors on them — pointwise scores cluster at 1 and 5 and occasionally
  contradict the label (score 1 with label `good`). Labels, which drive every
  headline number, are unaffected; score-based readings are coarse
  (FINDINGS F13.3).

## H. Honest limitations

- **N = 31** (28 light + 3 hard) is small; every rate above is an interval,
  not a point.
- Own-task/own-agent circularity: the same pipeline authored tasks, attempts,
  fixtures, and grader — findings may not transfer.
- One agent model family (Haiku); style/model-family bias was therefore not
  claimed, only presentation biases (length, position) were probed.
- Adversarial fixtures now cover all 5 attack types on all 5 tasks (30
  fixtures), but they were authored by the same person who built the grader:
  catch rates measure resistance to *these* attacks, not attacks in general —
  an independent red-team using a different model would be the stronger test.
- "Ground truth" means *verifier* truth, and the verifier is imperfect —
  fix-git's hash-comparison tests are exactly why the hardcode cheat passes.
- The pointwise score channel anchors on the prompts' literal JSON examples
  (scores cluster at 1/5); labels are the reliable signal (FINDINGS F13.3).
- Step-level labels are illustrative; no per-step ground truth exists here.

## I. Future work (deliberately not built, per spec)

Auto-evolving rubrics, grader drift monitoring, human-in-the-loop review
tooling, an RL training loop, model-family bias measurement with a second
agent family, and batch-run reconciliation (persisting batch IDs + request
mappings so a stranded batch can be recovered instead of cancelled).
