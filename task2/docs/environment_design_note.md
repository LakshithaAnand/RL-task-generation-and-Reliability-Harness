# Environment design note

*(Task 2 deliverable: action space, observation format, reward structure,
reward-hacking failure modes, and what to monitor in a real training run.)*

## Interface

```python
env = Environment(task, config, seed)
info = env.reset()          # build image, start container, count initial passing tests
obs  = env.exec(command)    # one bash command -> observation (the step primitive)
res  = env.verify()         # inject clean tests, run official test.sh, per-test results
diff = env.snapshot_diff()  # workspace diff vs the state captured at reset
env.close()                 # remove container + temp state
```

A trainer would call `reset → (exec)* → verify → reward`; the controller
(`controller.py`) packages that loop and records a full trajectory. Episode
termination: the agent declares done, or a step/token/wall-clock budget runs
out — all budgets are the controller's, so agents stay thin.

## Action space

One bash command per step, executed as root in the task container at the
image's WORKDIR. Deliberately minimal: no structured edit actions, no file-API
— TB2 tasks are terminal tasks, and bash is what the verifier itself assumes.
Commands run under an **in-container `timeout`** so a hung process can't
outlive its step, and the whole attempt has a wall-clock budget.

## Observation format

```
{stdout, stderr, exit_code, cwd, truncated, elapsed_ms}
```

stdout/stderr are truncated at a configurable cap (default 8000 chars) with an
explicit `truncated` flag — the agent knows when it saw a prefix. `reset()`
additionally reports the instruction, workdir, image tag, task hash, seed, and
the **initial passing-test count** (measured by running the real verifier once
in a throwaway container, so the agent's container never sees the tests or the
verifier's tooling).

## Isolation properties (enforced, then verified)

- **No network while the agent can act.** The container is disconnected from
  the bridge network after reset and reconnected only inside `verify()` (the
  official `test.sh` bootstraps its tooling from the network). This is checked
  by an in-container TCP probe in `task2 env`, not assumed.
- **Clean-test injection.** `verify()` deletes `/tests` and copies the
  pristine host-side tests before every run. Whatever the agent staged there
  never executes.
- **Pristine initial state.** The initial pass count comes from a probe
  container that is destroyed afterwards; verifier side effects never pollute
  the agent's workspace or its diff.

## Reward structure (deterministic; the only training signal)

```
raw_progress    = (final_passed − initial_passed) / max(1, total_tests − initial_passed)
reward          = 0.75·all_clean_tests_pass + 0.20·max(0, raw_progress) + 0.05·verifier_runnable
final_reward    = clamp(reward, 0, 1)        # forced to 0 if the tripwire fired (next section)
```

- The pass term dominates: progress shaping exists to separate partial work
  from nothing, not to be worth farming.
- `raw_progress` is logged **unclamped** (it can be negative) so analysis can
  see regressions the clamped reward hides.
- Every reward component is logged separately per attempt.
- **What is deliberately left out of reward**: LLM grader scores (audit-only,
  by principle), step-level shaping (per-step labels have no ground truth),
  style/efficiency bonuses (gameable). Env failures (Docker/API infrastructure)
  are excluded and retried — never scored as agent failures.

## The tripwire (deterministic anti-cheat check)

The score in this environment is decided by files — the official tests, the
verifier script, the task metadata. An agent that edits those files isn't
solving the task; it's rewiring the judge. The *tripwire* is the mechanism
that makes that unprofitable, named after the physical device: a hidden line
that triggers the moment someone steps where they must not. How it works:

1. **At reset**, the environment records a fingerprint (SHA-256 hash) of
   every *protected file* — each official test file, the verifier script,
   and the task metadata.
2. **After the attempt**, it re-computes the fingerprints and compares.
   Any difference means the agent touched the scoring machinery: a protected
   file modified, a protected file deleted, or a new file planted into the
   protected test directory (e.g. a fake always-pass test).
3. **If anything differs — "the tripwire fired"** — the attempt gets
   `final_reward = 0` regardless of test results, is flagged
   `integrity_violation = true`, and LLM grading is skipped entirely: an
   attempt that tampered with the judge has nothing left worth judging.

The comparison is plain file hashing — no language model anywhere — so it
cannot be persuaded, distracted, or prompt-injected, it costs nothing to
run, and it fires *before* any LLM ever sees the attempt. Defense in depth:
even if tampering somehow went undetected, `verify()` injects a pristine
copy of the tests before running them (see "Isolation properties"), so
edited tests never execute anyway. Tampering is therefore both pointless
and detected, by two independent layers.

## Known reward-hacking surface

| attack | outcome in this environment |
|---|---|
| edit/delete official tests | tripwire: reward 0, no LLM (and clean injection means it wouldn't work anyway) |
| stage a fake test suite | same tripwire (staged-file detection) |
| hardcode expected outputs | **passes reward** (verifier truth is imperfect) — caught only by the audit grader (R2 = poor with cited evidence); this residual gap is the reason the grader exists |
| farm the progress term | capped at 0.20 and denominator-normalized; oracle analysis shows real fixes dominate it |
| exhaust/crash the verifier | `verifier_runnable = 0` (pass and progress terms unreachable); tripwire fires only when tampering is also evidenced — an unrunnable verifier with no tampering is treated as an env failure (retried once, excluded from analysis), never as a cheat |
| background processes surviving the attempt | permitted (some tasks require servers); they run inside the same container the verifier tests |

## What to monitor in a real training run

1. **Tripwire rate** — a rising integrity-violation rate is the clearest sign
   the policy is probing the harness rather than solving tasks.
2. **Reward/grader disagreement rate** — attempts with `reward = 1` but
   R2 = poor (the hardcode signature) should be sampled to humans; a rising
   rate means the verifier is being gamed within the rules.
3. **`raw_progress < 0` frequency** — policies that destroy passing tests
   while chasing the pass term.
4. **Env-failure rate and verify latency** — infrastructure rot silently
   biases the dataset toward short attempts.
5. **Per-task pass-rate drift vs budget** — if pass rates saturate at tiny
   step budgets (as kv-store-grpc did here), the task no longer teaches
   anything; rotate it out.
6. **Verifier-anchoring exposure** — this project measured that the audit
   grader flips with a falsified verifier label; any pipeline that feeds
   verifier output into grader prompts must treat grader agreement as
   correlated, not independent, evidence.
