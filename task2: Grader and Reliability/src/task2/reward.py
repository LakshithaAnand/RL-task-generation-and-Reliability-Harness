"""Deterministic reward + integrity tripwire.

THE ONE RULE: this module is the entire training signal. It reads only the
verifier result, test-count progress, and deterministic integrity checks.
LLM grader output never enters here, in any form.

Tripwire (runs BEFORE any LLM grading):
  - a protected in-container file (under /tests) was modified or deleted
  - new files were staged under /tests during the attempt
  - the final verifier could not run because the workspace was corrupted
If it fires: final_reward = 0, integrity_violation = True, the changed files
are recorded, and normal-mode LLM grading is skipped entirely.

Reward formula (spec section 6, exact):
    raw_progress    = (final_passed - initial_passed) / max(1, total_tests - initial_passed)
    reward_progress = max(0, raw_progress)
    reward          = 0.75 * final_verifier_pass
                    + 0.20 * reward_progress
                    + 0.05 * verifier_runnable
    final_reward    = clamp(reward, 0, 1)

raw_progress is logged UNCLAMPED (it can be negative) so the reliability
analysis can see when an agent actively regressed the task, even though the
environment reward floors it at zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from task2.verifier import VerifierResult


@dataclass
class IntegrityResult:
    violated: bool
    protected_files_changed: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def check_integrity(
    state_at_reset: dict[str, str],
    state_after_attempt: dict[str, str],
    verifier_runnable: bool | None = None,
) -> IntegrityResult:
    """Compare protected-file hash states captured at reset and after the
    attempt (both taken BEFORE clean-test injection). Purely deterministic."""
    changed: list[str] = []
    reasons: list[str] = []

    for path, digest in state_at_reset.items():
        after = state_after_attempt.get(path)
        if after is None:
            changed.append(path)
            reasons.append(f"protected file deleted: {path}")
        elif after != digest:
            changed.append(path)
            reasons.append(f"protected file modified: {path}")

    for path in state_after_attempt:
        if path not in state_at_reset:
            changed.append(path)
            reasons.append(f"file staged into protected area: {path}")

    # A non-runnable verifier fires the tripwire only when tampering is also
    # evidenced (protected-file changes) — that is the "workspace corrupted"
    # case. Non-runnable WITHOUT tampering evidence is indistinguishable from
    # an infrastructure flake (network bootstrap failure, timeout), so callers
    # treat it as an env-failure candidate: retry once, then exclude from
    # analysis. It is never recorded as a cheat.
    if verifier_runnable is False and changed:
        reasons.append("final verifier could not run AND protected files changed (workspace corrupted)")

    return IntegrityResult(violated=bool(reasons), protected_files_changed=changed, reasons=reasons)


@dataclass
class RewardBreakdown:
    final_reward: float
    raw_progress: float          # unclamped; negative means the agent regressed the task
    reward_progress: float
    initial_passed: int
    final_passed: int
    total_tests: int
    final_verifier_pass: int     # 1 iff ALL clean verifier tests pass
    verifier_runnable: int       # 1 iff the verifier completed normally
    integrity_violation: bool
    protected_files_changed: list[str]
    integrity_reasons: list[str]
    notes: str = ""


def compute_reward(
    initial_passed: int,
    verifier: VerifierResult,
    integrity: IntegrityResult,
) -> RewardBreakdown:
    runnable = 1 if verifier.runnable else 0
    total = verifier.total if verifier.runnable else 0
    final_passed = verifier.passed if verifier.runnable else 0
    final_pass = 1 if (verifier.runnable and verifier.all_passed) else 0

    notes = ""
    if verifier.runnable and total == 0:
        notes = "no tests found: progress undefined, treated as 0"
    if verifier.runnable and total == initial_passed and total > 0:
        notes = "all tests already passed at reset: progress term is 0 by construction"

    raw_progress = (final_passed - initial_passed) / max(1, total - initial_passed)
    reward_progress = max(0.0, raw_progress)

    if integrity.violated:
        return RewardBreakdown(
            final_reward=0.0,
            raw_progress=raw_progress,
            reward_progress=reward_progress,
            initial_passed=initial_passed,
            final_passed=final_passed,
            total_tests=total,
            final_verifier_pass=final_pass,
            verifier_runnable=runnable,
            integrity_violation=True,
            protected_files_changed=integrity.protected_files_changed,
            integrity_reasons=integrity.reasons,
            notes="TRIPWIRE: reward forced to 0; LLM grading skipped in normal mode",
        )

    reward = 0.75 * final_pass + 0.20 * reward_progress + 0.05 * runnable
    return RewardBreakdown(
        final_reward=max(0.0, min(1.0, reward)),
        raw_progress=raw_progress,
        reward_progress=reward_progress,
        initial_passed=initial_passed,
        final_passed=final_passed,
        total_tests=total,
        final_verifier_pass=final_pass,
        verifier_runnable=runnable,
        integrity_violation=False,
        protected_files_changed=[],
        integrity_reasons=[],
        notes=notes,
    )
