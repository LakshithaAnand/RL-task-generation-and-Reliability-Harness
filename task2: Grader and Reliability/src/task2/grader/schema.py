"""Strict JSON validation for grader outputs.

Policy (spec): parse strictly; on malformed output the caller retries ONCE
with a corrective message; a second failure flags the grade record as
malformed — it is never silently coerced or dropped.
"""

from __future__ import annotations

import json

POINTWISE_LABELS = {"poor", "mixed", "good"}
STEP_LABELS = {"helped", "neutral", "hurt"}
WINNERS = {"A", "B", "tie"}


class MalformedGrade(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def extract_json(text: str) -> dict:
    """Parse the model's output as one JSON object. Tolerates a fenced code
    block or surrounding prose; everything else is an error."""
    s = text.strip()
    if "```" in s:
        # take the largest fenced block
        parts = [p for p in s.split("```") if "{" in p]
        if parts:
            s = max(parts, key=len)
            s = s.removeprefix("json").strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise MalformedGrade(["no JSON object found in response"])
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        raise MalformedGrade([f"JSON parse error: {e}"]) from e
    if not isinstance(obj, dict):
        raise MalformedGrade(["top-level JSON is not an object"])
    return obj


def _check_score(obj: dict, errors: list[str]) -> None:
    score = obj.get("score")
    if not (isinstance(score, (int, float)) and float(score).is_integer() and 1 <= score <= 5):
        errors.append(f"score must be an integer 1-5, got {score!r}")


def _check_label(obj: dict, allowed: set[str], errors: list[str]) -> None:
    if obj.get("label") not in allowed:
        errors.append(f"label must be one of {sorted(allowed)}, got {obj.get('label')!r}")


def _check_confidence(obj: dict, errors: list[str]) -> None:
    c = obj.get("confidence")
    if not (isinstance(c, (int, float)) and 0.0 <= c <= 1.0):
        errors.append(f"confidence must be a number in [0,1], got {c!r}")


def _check_str_list(obj: dict, field: str, errors: list[str], require_nonempty: bool) -> None:
    v = obj.get(field)
    if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
        errors.append(f"{field} must be a list of strings, got {v!r}")
    elif require_nonempty and not v:
        errors.append(f"{field} must cite at least one piece of evidence")


def validate_pointwise(obj: dict, rubric_key: str) -> dict:
    errors: list[str] = []
    if obj.get("rubric") != rubric_key:
        errors.append(f"rubric must be {rubric_key!r}, got {obj.get('rubric')!r}")
    _check_score(obj, errors)
    _check_label(obj, POINTWISE_LABELS, errors)
    _check_str_list(obj, "evidence", errors, require_nonempty=True)
    if not isinstance(obj.get("reason"), str) or not obj["reason"].strip():
        errors.append("reason must be a non-empty string")
    if errors:
        raise MalformedGrade(errors)
    obj["score"] = int(obj["score"])
    return obj


def validate_pairwise(obj: dict, rubric_key: str) -> dict:
    errors: list[str] = []
    if obj.get("rubric") != rubric_key:
        errors.append(f"rubric must be {rubric_key!r}, got {obj.get('rubric')!r}")
    if obj.get("winner") not in WINNERS:
        errors.append(f"winner must be one of {sorted(WINNERS)}, got {obj.get('winner')!r}")
    _check_confidence(obj, errors)
    _check_str_list(obj, "evidence_for_A", errors, require_nonempty=False)
    _check_str_list(obj, "evidence_for_B", errors, require_nonempty=False)
    if isinstance(obj.get("evidence_for_A"), list) and isinstance(obj.get("evidence_for_B"), list):
        if not obj["evidence_for_A"] and not obj["evidence_for_B"]:
            errors.append("at least one of evidence_for_A/evidence_for_B must be non-empty")
    if not isinstance(obj.get("reason"), str) or not obj["reason"].strip():
        errors.append("reason must be a non-empty string")
    if errors:
        raise MalformedGrade(errors)
    return obj


def validate_step(obj: dict) -> dict:
    errors: list[str] = []
    if obj.get("label") not in STEP_LABELS:
        errors.append(f"label must be one of {sorted(STEP_LABELS)}, got {obj.get('label')!r}")
    _check_confidence(obj, errors)
    if not isinstance(obj.get("evidence"), str) or not obj["evidence"].strip():
        errors.append("evidence must be a non-empty string quote")
    if not isinstance(obj.get("reason"), str) or not obj["reason"].strip():
        errors.append("reason must be a non-empty string")
    if errors:
        raise MalformedGrade(errors)
    return obj


def validate_extraction(obj: dict) -> dict:
    errors: list[str] = []
    fs = obj.get("fix_sites")
    if not (isinstance(fs, list) and all(isinstance(x, dict) for x in fs)):
        errors.append(f"fix_sites must be a list of objects, got {type(fs).__name__}")
    _check_str_list(obj, "test_interactions", errors, require_nonempty=False)
    for f in ("test_or_verifier_tampering", "diagnosis_before_edit"):
        v = obj.get(f, "MISSING")
        if v is not None and not isinstance(v, str):
            errors.append(f"{f} must be a string or null, got {v!r}")
        if v == "MISSING":
            errors.append(f"{f} is required (string or null)")
    if errors:
        raise MalformedGrade(errors)
    return obj


def validate_judge(obj: dict) -> dict:
    errors: list[str] = []
    for key in ("problem_localization", "patch_correctness", "generalization_regression_safety"):
        sub = obj.get(key)
        if not isinstance(sub, dict):
            errors.append(f"missing rubric object {key!r}")
            continue
        _check_score(sub, errors)
        _check_label(sub, POINTWISE_LABELS, errors)
        # A positive judgment must cite evidence (spec principle #4); a low
        # score may legitimately have none (the prompt tells the judge to
        # explain missing evidence in `reason` instead).
        _check_str_list(sub, "evidence", errors,
                        require_nonempty=(sub.get("label") == "good"))
        if not isinstance(sub.get("reason"), str) or not sub["reason"].strip():
            errors.append(f"{key}.reason must be a non-empty string")
    if errors:
        raise MalformedGrade(errors)
    for key in ("problem_localization", "patch_correctness", "generalization_regression_safety"):
        obj[key]["score"] = int(obj[key]["score"])
    return obj
