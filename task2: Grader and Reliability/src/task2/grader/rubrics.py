"""The 3 rubrics and their prompt templates.

R1 Problem Localization — did it identify the right failure/root cause before editing?
R2 Patch Correctness — did the actual code change solve the stated task?
R3 Generalization & Regression Safety — robust beyond the visible test, no collateral damage?

R1 judges the process, so its prompts take only task + trajectory.
R2/R3 judge the outcome, so their prompts also take verifier result + diff.
"""

from __future__ import annotations

from dataclasses import dataclass

from task2.grader import prompts


@dataclass(frozen=True)
class Rubric:
    key: str                 # JSON "rubric" field value the model must return
    title: str
    pointwise_template: str
    pairwise_template: str
    needs_outcome: bool      # True -> prompts include verifier_result + final_diff


RUBRICS: dict[str, Rubric] = {
    "problem_localization": Rubric(
        key="problem_localization",
        title="R1 Problem Localization",
        pointwise_template=prompts.R1_POINTWISE,
        pairwise_template=prompts.R1_PAIRWISE,
        needs_outcome=False,
    ),
    "patch_correctness": Rubric(
        key="patch_correctness",
        title="R2 Patch Correctness",
        pointwise_template=prompts.R2_POINTWISE,
        pairwise_template=prompts.R2_PAIRWISE,
        needs_outcome=True,
    ),
    "generalization_regression_safety": Rubric(
        key="generalization_regression_safety",
        title="R3 Generalization & Regression Safety",
        pointwise_template=prompts.R3_POINTWISE,
        pairwise_template=prompts.R3_PAIRWISE,
        needs_outcome=True,
    ),
}

ALL_RUBRIC_KEYS = list(RUBRICS)
