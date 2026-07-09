# Curation — 3 showcase tasks

All five validated tasks cleared every gate; the three below are curated for
**spread**: both mutation templates, both instruction-verbosity settings, and
the full structural-difficulty range (easy / medium / hard). Each task's
complete evidence is in its `assurance_card.json` (in the task folder and
`artifacts/cards/`).

## tbgen/tabulate-c07-inverted_condition-L2393 — inverted_condition · explicit · medium

The richest card in the set, chosen first. Its shortcut battery shows the
full harden loop on record: `spoof_reward_json` initially **failed**, the
verifier was hardened, the probe re-ran **blocked**, and broken=0/oracle=1
was re-confirmed. It also carries the set's one **open residual**: the
`assert_true` near-miss (assertion gaming) is accepted by the verifier and
the one-shot hardening attempt found no distinguishing edge test — stated on
the card, counted against the rejection rate (2/3), and independently
confirmed by the standalone `scripts/handcheck.sh`. This task is the honesty
demonstration: evidence of what the pipeline catches *and* what it doesn't.

## tbgen/tabulate-c03-boundary_flip-L1458 — boundary_flip · symptom_only · hard

The hardest structural profile (only 2 flipped tests to localize from, no
file/function hint in the instruction, a 3-test verifier) and the
**hardening success story**: its `off_by_one` near-miss initially slipped
(reward 1), Stage 8 admitted one distinguishing edge test
(`firstrow_only`), re-confirmed broken=0/oracle=1, and the re-run near-miss
was rejected — final rejection rate 3/3. The counterpart to c07: the same
mechanism that documents c07's open residual closed this one.

## tbgen/tabulate-c02-boundary_flip-L2446 — boundary_flip · explicit · easy

Completes the spread (easy tier, second boundary_flip) and best shows
Stage-5 verifier synthesis: 4 template-generated edge tests admitted under
the fail-on-broken/pass-on-oracle rule (the most in the set), on top of 18
regression copies. It is also covered by the independent handcheck
(`spoof_reward_json` confirmed closed by the standalone script), and its
near-miss battery rejected 3/3 with no hardening needed.

## The other two tasks

`c05-inverted_condition-L121` (easy) and `c06-inverted_condition-L846`
(medium) remain in `tasks/generated/` as additional fully validated output —
they cleared every gate and carry complete assurance cards; they are simply
redundant with the showcase three on template/verbosity/difficulty spread,
and deleting validated evidence would serve nothing.

## Note: reconciliation with empirical difficulty (n=5, artifacts/empirical_difficulty.json)

Under the measured labels the showcase set spans empirical easy (c02), easy
(c03), and medium (c07). c03 is retained deliberately: its structural-hard
vs empirical-easy disagreement is a documented finding on its card — a
2-test failure signature still localizes the bug for a capable agent. c07
remains the only empirically resistant task in the set at 3/5.
