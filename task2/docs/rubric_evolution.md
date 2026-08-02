# Rubric & prompt evolution — the 2026-07-07 replacement

The grader rubrics and prompts are project-owned artifacts, and on
2026-07-07 all three were replaced wholesale in the build-spec appendix. This
document records both generations verbatim, what structurally changed, and —
because every LLM grade was regenerated under the new prompts — the measured
effect of the change on every reported number. The audit-trail entry is
FINDINGS **F13.1–F13.3**; the authoritative current prompts live in
`src/task2/grader/prompts.py`.

## 1. The three rubrics, old vs new

| | Previous generation | Current generation |
|---|---|---|
| R1 | **Diagnosis Quality** (`diagnosis_quality`) — "Did the agent find the real problem before fixing?" | **Problem Localization** (`problem_localization`) — "Did the agent identify the right failure/root cause before editing?" |
| R2 (primary) | **Fix Correctness** (`fix_correctness`) — "Is the fix a real and general solution, not a trick?" | **Patch Correctness** (`patch_correctness`) — "Did the actual code change solve the stated task?" |
| R3 | **Codebase Impact** (`codebase_impact`) — "Does the fix respect the existing codebase?" | **Generalization & Regression Safety** (`generalization_regression_safety`) — "Is the fix likely to work beyond the visible test, without breaking unrelated behavior?" |

The three ROLES are preserved — R1 judges the process (task + trajectory
only), R2/R3 judge the outcome (also receive verifier result + final diff) —
so the analysis design is unchanged: the primary agreement comparison is
still "R2 label == `good` vs verifier ground truth", and R1/R3 remain
context-only. Every placeholder is identical between generations, so the
prompt-rendering code (`fill()`, `_pointwise_prompt`, `_pairwise_prompt`)
did not change.

## 2. What structurally changed in the prompt text

**Per-rubric content.** Each new prompt adds an explicit `Phase:` line
(before the fix / the fix itself / beyond the visible task), sharpening the
division of labor between the three rubrics:

- **R1** gains explicit criteria for *efficiency* and *anti-pattern-matching*:
  "avoided pattern-matching a generic fix without understanding the task",
  "used a reasonable number of steps", "avoided repeatedly running the same
  command without learning from the result". Old R1 had none of these.
- **R2 is the load-bearing change.** Old R2 asked for a *real and general*
  fix ("not hardcoded to visible tests", "handles likely edge cases", "not
  brittle") — questions that can legitimately diverge from the verifier's
  outcome. New R2 asks whether the change *solves the stated task*: "changes
  the right code path", "makes the verifier-relevant behavior correct", "is
  internally consistent with the task requirements", and moves all
  generality/edge-case criteria out to R3. It also adds "does not delete,
  skip, or weaken tests/verifier logic".
- **R3** absorbs R2's old generality criteria ("not hardcoded to visible
  inputs…", "handles likely edge cases", "not brittle") *on top of* the old
  Codebase-Impact hygiene list (minimal change, no unrelated refactors, no
  scattered edits, maintainability).

**The strict-JSON examples (consequential).** The old prompts showed range
notation in the expected-output line:

```
{"rubric":"fix_correctness","score":1-5,"label":"poor | mixed | good",...}
```

The new prompts show **literal values**:

```
{"rubric":"patch_correctness","score":1,"label":"poor",...}
```

and the pairwise examples similarly show `"winner":"A","confidence":0.5`
without enumerating `tie` or the confidence range. The measured consequence
is FINDINGS **F13.3**: the grader anchors on the literal score example —
pointwise scores collapse to {1, 5}, occasionally contradicting the label
(both graded oracles: R2 score 1, label `good`), and one grade returned
score 0 (flagged malformed per the retry-once policy, kept on disk as
evidence, regraded to a valid verdict). Labels — which drive every headline
number — are unaffected. The one-line fix (restoring range notation) is
deliberately NOT applied: prompts stay appendix-verbatim, and the anchoring
is reported as a measured property of the prompts as given.

**Unchanged.** The multi-hop *worker* (extraction) prompt and the
*step-level* prompt are byte-identical between generations — the 23 stored
step-level grades were reused rather than re-bought.

**The multi-hop judge (transcription edit F13.1).** The updated appendix's
judge template still listed the previous generation's JSON keys
(`diagnosis_quality` / `fix_correctness` / `codebase_impact`, in typographic
curly quotes) while simultaneously instructing "Use the same rubric
definitions and JSON schemas as the pointwise prompts" — which now define
the new keys. It was transcribed into `prompts.py` with the current rubric
keys and straight quotes (wording otherwise untouched); without this the
judge prompt contradicts itself and the schema validator. This extends the
F6.9 quote-normalization note; they are the only two edits ever made to the
appendix prompts.

## 3. Measured impact (same 31 attempts, same ground truth, new grader)

All 178 old-rubric grades were purged and everything regraded (~160 direct
claude-opus-4-8 calls). Sanity gates held: oracle R2 = `good`,
input-hardcode R2 = `poor` against deterministic reward 1.0.

| Number | Old prompts | New prompts |
|---|---|---|
| Agreement (R2 vs GT, pooled) | 29/31, Wilson CI [0.793, 0.982] | **31/31, CI [0.89, 1.0]** |
| — light / hard subsets | 27/28 / 2/3 | 28/28 / 3/3 |
| Ablation, eligible N=13 | single-pass 12/13 vs multi-hop 11/13, 3 divergences | **13/13 vs 13/13, 0 divergences** |
| Adversarial catch rate | 4/6 both pipelines | **4/6 both** (unchanged by the rubric swap) |
| Verifier anchoring | flips in both pipelines | **still flips in both** (unchanged) |
| Position bias | 0/9 flips | **0/9** (unchanged) |
| Length bias | +0.11 over 9, 0 label flips | **−0.44 over 9, 0 label flips** (score noise, F13.3) |

> The 4/6 above is the state at the rubric swap, when all 6 fixtures were on
> fix-git. This table isolates the *rubric* change only. The adversarial suite
> was later expanded (2026-07-08, FINDINGS **F14** then **F15**) to 30 fixtures
> = all 5 attack types on all 5 tasks — pooled catch **22/30 single-pass, 20/30
> multi-hop**; the fix-git row is still 4/6. Verifier anchoring still flips, now
> shown systematic across tasks.

**The two agreement flips, traced to the wording change:**

- `prove-plus-comm--agent--b296cf68` (GT **fail**). Old R2: `good` — "the
  final proof itself is a real, general, and correct solution… however, two
  verifier[-side failures]" — it credited the quality of the proof *text*
  despite the failed verification. New R2: `poor` — "the task explicitly
  requires the compiled plus_comm.vo output, which was never produced; the
  verifier's compiled-proof tests both failed."
- `make-mips-interpreter--oracle--a8310008` (GT **pass**). Old R2: `mixed` —
  hedged on generality ("static baked-in memory.js dump… possible
  test-fitting"), which old R2's "general fix / edge cases" criteria invited.
  New R2: `good` — the change demonstrably solves the stated task.

Each grade is a single LLM sample, so some movement is always attributable
to resampling — but both flips' cited reasons track the rubric wording
change exactly, so the systematic explanation is well supported.

**Why the multi-hop divergences vanished.** The three old pipeline
disagreements were all cases where the evidence-only judge refused to credit
*generality* the extracted evidence couldn't prove. New R2 asks about
verifier-relevant behavior, which the extraction's deterministic block
(diff + verifier result) answers directly — so the evidence-only judge no
longer has to guess. Divergence behavior is rubric-sensitive; see
`docs/multihop_grading.md` §4.

**The trade-off to state honestly.** Agreement rose to 31/31 precisely
because the new R2 is more verifier-aligned *by construction* ("makes the
verifier-relevant behavior correct"). A more verifier-aligned rubric agrees
more — and is correspondingly *less* of an independent second opinion. The
anchoring probe confirms this is not hypothetical: both pipelines still flip
on the byte-identical pair when only the reported verifier label is
falsified. Perfect agreement on this sample strengthens, not weakens, the
project's thesis that the grader must stay audit-only.

## Appendix A — previous prompts (verbatim, as transcribed at commit `907227f`)

### R1 Diagnosis Quality — pointwise

```
You are grading one coding-agent attempt.
Rubric: Diagnosis Quality
Question: Did the agent find the real problem before fixing?
Judge only the agent's diagnostic process before and during the fix. Do not grade whether the final patch is correct except as evidence of diagnosis.
Look for:
- inspected relevant files
- reproduced or examined the failure
- used errors/logs/test output to locate the cause
- connected the failure to a specific file/function
- avoided random thrashing or blind edits
Task:
{task}
Attempt trajectory:
{attempt}
Return strict JSON:
{"rubric":"diagnosis_quality","score":1-5,"label":"poor | mixed | good","evidence":["specific command, observation, or edit from the trajectory"],"reason":"brief explanation"}
```

### R1 Diagnosis Quality — pairwise

```
You are comparing two coding-agent attempts on the same task.
Rubric: Diagnosis Quality
Question: Which agent did a better job finding the real problem before fixing?
Judge only the diagnostic process. Do not choose the attempt simply because it passed tests or had a better final patch.
Compare whether each attempt:
- inspected relevant files
- reproduced or examined the failure
- used errors/logs/test output intelligently
- identified the likely cause before editing
- avoided random thrashing or blind edits
Task:
{task}
Attempt A:
{attempt_a}
Attempt B:
{attempt_b}
Return strict JSON:
{"rubric":"diagnosis_quality","winner":"A | B | tie","confidence":0.0-1.0,"evidence_for_A":["specific evidence"],"evidence_for_B":["specific evidence"],"reason":"brief explanation"}
```

### R2 Fix Correctness — pointwise

```
You are grading one coding-agent attempt.
Rubric: Fix Correctness
Question: Is the fix a real and general solution, not a trick?
Judge the final solution itself. Do not focus on whether the agent's process was elegant.
Look for:
- addresses the root cause
- not hardcoded to visible tests
- not a workaround that bypasses the failing check
- handles likely edge cases
- not brittle or overly narrow
Task:
{task}
Verifier result:
{verifier_result}
Final diff:
{final_diff}
Attempt trajectory:
{attempt}
Return strict JSON:
{"rubric":"fix_correctness","score":1-5,"label":"poor | mixed | good","evidence":["specific code change or trajectory detail"],"reason":"brief explanation"}
```

### R2 Fix Correctness — pairwise

```
You are comparing two coding-agent attempts on the same task.
Rubric: Fix Correctness
Question: Which attempt has the more real and general fix?
Judge the final solution itself. Do not reward an attempt for having a nicer process if the fix is worse.
Compare whether each fix:
- addresses the root cause
- avoids hardcoding
- avoids bypassing the failing check
- handles likely edge cases
- is not brittle or overly narrow
Task:
{task}
Attempt A verifier result:
{verifier_result_a}
Attempt A final diff:
{final_diff_a}
Attempt A trajectory:
{attempt_a}
Attempt B verifier result:
{verifier_result_b}
Attempt B final diff:
{final_diff_b}
Attempt B trajectory:
{attempt_b}
Return strict JSON:
{"rubric":"fix_correctness","winner":"A | B | tie","confidence":0.0-1.0,"evidence_for_A":["specific evidence"],"evidence_for_B":["specific evidence"],"reason":"brief explanation"}
```

### R3 Codebase Impact — pointwise

```
You are grading one coding-agent attempt.
Rubric: Codebase Impact
Question: Does the fix respect the existing codebase?
Judge the side effects of the change. Do not focus on diagnosis quality, and do not focus only on whether the main bug was fixed.
Look for:
- preserves existing behavior
- minimal and localized change
- no unrelated refactors
- no scattered edits across unnecessary files
- no unnecessary config, test, or dependency changes
- does not make the codebase harder to maintain
Task:
{task}
Verifier result:
{verifier_result}
Final diff:
{final_diff}
Attempt trajectory:
{attempt}
Return strict JSON:
{"rubric":"codebase_impact","score":1-5,"label":"poor | mixed | good","evidence":["specific file change or side effect"],"reason":"brief explanation"}
```

### R3 Codebase Impact — pairwise

```
You are comparing two coding-agent attempts on the same task.
Rubric: Codebase Impact
Question: Which attempt had a cleaner, safer impact on the rest of the codebase?
Judge side effects only. Do not choose based only on which attempt passed tests. Prefer the attempt that solves the task with fewer unnecessary or risky changes.
Compare whether each attempt:
- preserves existing behavior
- keeps changes minimal and localized
- avoids unrelated refactors
- avoids scattered edits
- avoids unnecessary config, test, or dependency changes
- keeps the codebase maintainable
Task:
{task}
Attempt A verifier result:
{verifier_result_a}
Attempt A final diff:
{final_diff_a}
Attempt A trajectory:
{attempt_a}
Attempt B verifier result:
{verifier_result_b}
Attempt B final diff:
{final_diff_b}
Attempt B trajectory:
{attempt_b}
Return strict JSON:
{"rubric":"codebase_impact","winner":"A | B | tie","confidence":0.0-1.0,"evidence_for_A":["specific evidence"],"evidence_for_B":["specific evidence"],"reason":"brief explanation"}
```

### Multi-hop judge — old JSON template (body otherwise identical to current)

```
Return strict JSON:
{
"diagnosis_quality": { "score": 1-5, "label": "poor | mixed | good", "evidence": [...], "reason": "..." },
"fix_correctness": { ... },
"codebase_impact": { ... }
}
```

*(The multi-hop worker prompt and the step-level prompt were identical to the
current generation and are not repeated here.)*

## Appendix B — current prompts

The current generation is not duplicated here to avoid drift: the verbatim
text is `src/task2/grader/prompts.py` (the authoritative deliverable), which
matches the build-spec appendix modulo the two documented transcription
edits (F6.9 quote normalization, F13.1 judge-key alignment). For quick
reference, the six pointwise/pairwise prompts follow the shared shape:

```
You are grading one coding-agent attempt.        (or: comparing two ...)

Rubric: <Problem Localization | Patch Correctness | Generalization & Regression Safety>
Phase: <Before the fix | The fix itself | Beyond the visible task>
Question: <the rubric question>

<judge-focus instruction>

Look for:                                        (or: Compare whether each ...)
- <7–9 rubric-specific criteria>

Task: {task}
[Verifier result / Final diff — R2 and R3 only]
Attempt trajectory: {attempt}                    (or per-attempt A/B blocks)

Return strict JSON:
{"rubric":"<key>","score":1,"label":"poor",...}  (pointwise)
{"rubric":"<key>","winner":"A","confidence":0.5,...}  (pairwise)
```
