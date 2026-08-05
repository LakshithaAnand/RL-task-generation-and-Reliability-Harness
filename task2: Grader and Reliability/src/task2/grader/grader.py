"""The LLM grader: audit-only judge over recorded attempts.

Order of operations for every grade, in normal mode:
  1. Deterministic pre-screen: if the attempt's tripwire fired
     (integrity_violation), mark it "hacked" and DO NOT call the LLM.
     (audit_mode=True overrides this for adversarial analysis only.)
  2. Pipeline:
       single_pass — the full rendered trajectory goes straight to the judge.
       multi_hop   — stage 0: deterministic extraction (final diff, changed
                     files, verifier result, test commands, protected-file
                     changes); stage 1: LLM worker quotes decision-relevant
                     evidence (trajectory treated as untrusted); stage 2: judge
                     scores from the extracted evidence only.
  3. Strict JSON validation; ONE retry with a corrective message on malformed
     output; then the record is flagged malformed.

Modes: pointwise (one attempt, one rubric), pairwise (A vs B, order randomized
per call and logged), step-level (rate one action helped/neutral/hurt).

Cost: prompt caching marks the shared task/trajectory prefix as cacheable
(pays off across the 3 rubrics' calls, step-level sweeps, and retries);
BatchRunner sends bulk grading through the Batch API.

Length gating: trajectories longer than the config gate are graded multi_hop
in auto mode — extraction compresses what the judge must read.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from task2.grader import prompts
from task2.grader.rubrics import RUBRICS, Rubric
from task2.grader.schema import (
    MalformedGrade,
    extract_json,
    validate_extraction,
    validate_judge,
    validate_pairwise,
    validate_pointwise,
    validate_step,
)
from task2.tasks import Task
from task2.trajectory import AttemptRecord, utc_now

ATTEMPT_TEXT_CAP = 30000
DIFF_TEXT_CAP = 15000
PRIOR_STEPS_CAP = 20000


@dataclass
class GradeRecord:
    grade_id: str
    created_at: str
    task_name: str
    attempt_id: str
    rubric: str                 # rubric key, "all" for multi-hop judge, "step" for step-level
    mode: str                   # pointwise | pairwise | step
    pipeline: str               # single_pass | multi_hop | prescreen_only
    grader_model: str | None
    extractor_model: str | None = None
    attempt_b_id: str | None = None
    order_swapped: bool | None = None   # pairwise: True if B was shown as "A"
    step_index: int | None = None
    prescreen: str = "clean"            # clean | hacked
    llm_called: bool = True
    result: dict | None = None          # validated JSON (winner mapped back to true A/B)
    extraction: dict | None = None      # multi-hop: deterministic + worker evidence
    malformed: bool = False
    retried: bool = False
    raw_response: str = ""
    usage: dict = field(default_factory=dict)

    def save(self, grades_dir: Path) -> Path:
        grades_dir.mkdir(parents=True, exist_ok=True)
        path = grades_dir / f"{self.grade_id}.json"
        path.write_text(json.dumps(asdict(self), indent=2))
        return path

    @classmethod
    def load(cls, path: Path) -> "GradeRecord":
        return cls(**json.loads(Path(path).read_text()))


def load_all_grades(grades_dir: Path) -> list[GradeRecord]:
    return [GradeRecord.load(p) for p in sorted(Path(grades_dir).glob("*.json"))]


# -- rendering helpers ---------------------------------------------------------

def attempt_text(record: AttemptRecord, cap: int = ATTEMPT_TEXT_CAP) -> str:
    text = record.trajectory.render()
    if len(text) > cap:
        text = text[:cap] + f"\n[... trajectory truncated at {cap} chars ...]"
    return text


def verifier_text(record: AttemptRecord) -> str:
    v = record.verifier
    if not v:
        return "(no verifier result recorded)"
    if not v.get("runnable"):
        return f"verifier could not run: {v.get('error')}"
    lines = [f"{v['passed']}/{v['total']} tests passed; all_passed={v['all_passed']}"]
    lines += [f"  {t['status']}: {t['name']}" for t in v.get("per_test", [])]
    return "\n".join(lines)


def diff_text(record: AttemptRecord, cap: int = DIFF_TEXT_CAP) -> str:
    d = record.diff
    if not d:
        return "(no diff recorded)"
    text = d.get("diff_text", "")
    if not text.strip():
        return "(empty diff: no workspace changes)"
    if len(text) > cap:
        text = text[:cap] + f"\n[... diff truncated at {cap} chars ...]"
    return text


def deterministic_evidence(record: AttemptRecord) -> dict:
    """Stage 0 of multi-hop: facts extracted with no LLM involved."""
    reward = record.reward or {}
    test_cmds = [
        s.command for s in record.trajectory.steps
        if any(k in s.command for k in ("test", "pytest", "verify", "/tests"))
    ]
    return {
        "changed_files": (record.diff or {}).get("changed_files", []),
        "verifier_result": verifier_text(record),
        "final_diff_excerpt": diff_text(record, cap=8000),
        "test_related_commands": test_cmds[:20],
        "protected_files_changed": reward.get("protected_files_changed", []),
        "integrity_violation": reward.get("integrity_violation", False),
    }


# -- LLM transport ---------------------------------------------------------------

def _blocks(prompt: str, cache_split: str | None) -> list[dict]:
    """Split the filled prompt into [cacheable prefix][suffix], with the split
    placed just BEFORE the last occurrence of cache_split — so the bulky
    task/trajectory prefix is the cached part. Pays off on retries, re-grades
    of the same attempt (bias probes), and step-level sweeps whose context
    prefix grows monotonically."""
    if cache_split and cache_split in prompt:
        idx = prompt.rfind(cache_split)
        prefix, suffix = prompt[:idx], prompt[idx:]
        if prefix.strip() and suffix.strip():
            return [
                {"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": suffix},
            ]
    return [{"type": "text", "text": prompt}]


class DirectRunner:
    """Immediate messages.create calls (used for smokes and retries)."""

    def __init__(self, client):
        self.client = client

    def run(self, requests: list[dict]) -> dict[str, tuple[str, dict]]:
        out: dict[str, tuple[str, dict]] = {}
        for req in requests:
            resp = self.client.messages.create(**req["params"])
            text = "".join(b.text for b in resp.content if b.type == "text")
            out[req["custom_id"]] = (text, _usage(resp.usage))
        return out


class BatchRunner:
    """Bulk grading through the Batch API (50% cost, async). Batches may take
    hours under load — the poll timeout must be generous, and callers should
    chunk large phases so a timeout can't strand a whole phase's results."""

    def __init__(self, client, poll_sec: float = 10.0, timeout_sec: float = 14400):
        self.client = client
        self.poll_sec = poll_sec
        self.timeout_sec = timeout_sec

    def run(self, requests: list[dict]) -> dict[str, tuple[str, dict]]:
        if not requests:
            return {}
        batch = self.client.messages.batches.create(requests=requests)
        deadline = time.monotonic() + self.timeout_sec
        while True:
            batch = self.client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"batch {batch.id} still {batch.processing_status} after {self.timeout_sec}s")
            time.sleep(self.poll_sec)
        out: dict[str, tuple[str, dict]] = {}
        for entry in self.client.messages.batches.results(batch.id):
            if entry.result.type == "succeeded":
                msg = entry.result.message
                text = "".join(b.text for b in msg.content if b.type == "text")
                out[entry.custom_id] = (text, _usage(msg.usage))
            else:
                out[entry.custom_id] = (f"BATCH_ERROR: {entry.result.type}", {})
        return out


def _usage(usage) -> dict:
    return {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


# -- the grader ----------------------------------------------------------------

RETRY_NOTE = ("Your previous response was not valid strict JSON for the required schema "
              "({errors}). Respond again with ONLY the strict JSON object, no prose, no code fences.")


class Grader:
    def __init__(self, config: dict, tasks_by_name: dict[str, Task],
                 audit_mode: bool = False, use_batch: bool | None = None):
        import anthropic

        self.config = config
        self.tasks = tasks_by_name
        self.audit_mode = audit_mode
        gcfg = config.get("grader", {})
        self.grader_model = config["grader_model"]
        self.extractor_model = config["extractor_model"]
        # claude-opus-4-8 deprecates `temperature`; null in config omits it.
        t = gcfg.get("temperature")
        self.temperature = float(t) if t is not None else None
        self.length_gate = int(gcfg.get("multi_hop_length_gate_chars", 40000))
        self.caching = bool(gcfg.get("prompt_caching", True))
        self.seed = int(config.get("seed", 42))
        self.client = anthropic.Anthropic()
        batch = gcfg.get("use_batch_api", True) if use_batch is None else use_batch
        batch_timeout = float(gcfg.get("batch_timeout_sec", 14400))
        self.runner = (BatchRunner(self.client, timeout_sec=batch_timeout)
                       if batch else DirectRunner(self.client))
        self.direct = DirectRunner(self.client)  # retries always go direct

    # -- public API -------------------------------------------------------------

    def choose_pipeline(self, record: AttemptRecord) -> str:
        """Auto mode: long trajectories are gated to multi_hop."""
        return "multi_hop" if len(record.trajectory.render()) > self.length_gate else "single_pass"

    def prescreen(self, record: AttemptRecord) -> str:
        reward = record.reward or {}
        return "hacked" if reward.get("integrity_violation") else "clean"

    def grade_pointwise(self, record: AttemptRecord, rubric_key: str,
                        pipeline: str = "single_pass") -> GradeRecord:
        blocked = self._prescreen_record(record, rubric_key, "pointwise")
        if blocked:
            return blocked
        if pipeline == "multi_hop":
            return self.grade_multihop(record)
        rubric = RUBRICS[rubric_key]
        prompt = self._pointwise_prompt(record, rubric)
        return self._graded_call(
            record=record, rubric=rubric_key, mode="pointwise", pipeline="single_pass",
            model=self.grader_model, prompt=prompt,
            cache_split=self._attempt_split(record),
            validate=lambda obj: validate_pointwise(obj, rubric_key),
            max_tokens=1500,
        )

    def grade_pairwise(self, record_a: AttemptRecord, record_b: AttemptRecord,
                       rubric_key: str, force_swap: bool | None = None) -> GradeRecord:
        for r in (record_a, record_b):
            blocked = self._prescreen_record(r, rubric_key, "pairwise")
            if blocked:
                # The comparison record is keyed by the (A, B) pair: A's id
                # stays the primary attempt_id even when B is the tripwired one.
                blocked.attempt_id = record_a.trajectory.attempt_id
                blocked.attempt_b_id = record_b.trajectory.attempt_id
                blocked.result["reason"] += f" (tripwired attempt: {r.trajectory.attempt_id})"
                return blocked
        rubric = RUBRICS[rubric_key]
        # Presentation order is randomized per (pair, rubric) via a seeded RNG
        # and logged: unpredictable to the judge, but reproducible across
        # re-runs so stored grades stay reusable. The position-bias probe pins
        # both orders explicitly via force_swap.
        rng = random.Random(f"{self.seed}:{record_a.trajectory.attempt_id}:"
                            f"{record_b.trajectory.attempt_id}:{rubric_key}")
        swapped = rng.random() < 0.5 if force_swap is None else force_swap
        first, second = (record_b, record_a) if swapped else (record_a, record_b)
        prompt = self._pairwise_prompt(first, second, rubric)

        rec = self._graded_call(
            record=record_a, rubric=rubric_key, mode="pairwise", pipeline="single_pass",
            model=self.grader_model, prompt=prompt, cache_split=None,
            validate=lambda obj: validate_pairwise(obj, rubric_key),
            max_tokens=1500,
        )
        rec.attempt_b_id = record_b.trajectory.attempt_id
        rec.order_swapped = swapped
        if rec.result and not rec.malformed:
            raw_winner = rec.result["winner"]
            if raw_winner in ("A", "B") and swapped:
                rec.result["winner"] = "B" if raw_winner == "A" else "A"
            rec.result["winner_as_presented"] = raw_winner
        return rec

    def grade_step(self, record: AttemptRecord, step_index: int) -> GradeRecord:
        blocked = self._prescreen_record(record, "step", "step")
        if blocked:
            blocked.step_index = step_index
            return blocked
        task = self.tasks[record.trajectory.task_name]
        steps = record.trajectory.steps
        step = steps[step_index]
        prior = "\n".join(
            f"$ {s.command}\n[exit {s.exit_code}] {s.stdout[:400]}" for s in steps[:step_index]
        ) or "(this is the first action)"
        prior = prior[-PRIOR_STEPS_CAP:]
        obs = (f"exit_code: {step.exit_code}\nstdout:\n{step.stdout[:2000]}\n"
               f"stderr:\n{step.stderr[:1000]}")
        prompt = prompts.fill(
            prompts.STEP_POINTWISE,
            task=task.instruction, prior_steps=prior,
            action=step.command, observation=obs,
        )
        rec = self._graded_call(
            record=record, rubric="step", mode="step", pipeline="single_pass",
            model=self.grader_model, prompt=prompt,
            cache_split="\nAction:\n",  # prefix through prior_steps grows monotonically -> cache hits across a sweep
            validate=validate_step, max_tokens=800,
        )
        rec.step_index = step_index
        return rec

    def grade_multihop(self, record: AttemptRecord) -> GradeRecord:
        blocked = self._prescreen_record(record, "all", "pointwise")
        if blocked:
            # keep pipeline="prescreen_only": downstream label extraction and
            # store-reuse keys rely on prescreen records being identifiable
            return blocked
        task = self.tasks[record.trajectory.task_name]

        det = deterministic_evidence(record)
        worker_prompt = prompts.fill(
            prompts.MULTIHOP_WORKER,
            task=task.instruction, attempt=attempt_text(record),
        )
        worker_text, worker_usage, worker_retried, worker_err = self._call_validated(
            self.extractor_model, worker_prompt,
            cache_split="\nExtract:",
            validate=validate_extraction, max_tokens=2000,
        )
        if worker_err is not None:
            return GradeRecord(
                grade_id=self._gid(record, "all", "multi_hop"),
                created_at=utc_now(), task_name=record.trajectory.task_name,
                attempt_id=record.trajectory.attempt_id, rubric="all",
                mode="pointwise", pipeline="multi_hop",
                grader_model=self.grader_model, extractor_model=self.extractor_model,
                malformed=True, retried=worker_retried,
                raw_response=f"WORKER MALFORMED: {worker_err}", usage=worker_usage,
                extraction={"deterministic": det, "llm_extracted": None},
            )
        extraction = {"deterministic": det, "llm_extracted": worker_text}

        reference = task.solution_script.read_text(errors="replace")
        judge_prompt = prompts.fill(
            prompts.MULTIHOP_JUDGE,
            task=task.instruction,
            evidence_json=json.dumps(extraction, indent=2),
            reference_context=f"Oracle solution script (reference only):\n{reference}",
        )
        rec = self._graded_call(
            record=record, rubric="all", mode="pointwise", pipeline="multi_hop",
            model=self.grader_model, prompt=judge_prompt,
            cache_split="\nReturn strict JSON:",
            validate=validate_judge, max_tokens=2500,
        )
        rec.extractor_model = self.extractor_model
        rec.extraction = extraction
        rec.usage = {k: rec.usage.get(k, 0) + worker_usage.get(k, 0)
                     for k in set(rec.usage) | set(worker_usage)}
        rec.retried = rec.retried or worker_retried
        return rec

    def grade_pairwise_multihop(self, record_a: AttemptRecord, record_b: AttemptRecord,
                                rubric_key: str) -> GradeRecord:
        """Multi-hop pairwise, DERIVED: the spec appendix defines verbatim
        prompts only for the pointwise multi-hop judge, so A-vs-B is computed
        by running the evidence-only multi-hop judge on each attempt and
        comparing the rubric scores (tie on equal scores; confidence scales
        with the score gap). Pipeline is labeled multi_hop_derived to keep it
        distinguishable from a native pairwise judgment."""
        ga = self.grade_multihop(record_a)
        gb = self.grade_multihop(record_b)
        for g, rec in ((ga, record_a), (gb, record_b)):
            if g.pipeline == "prescreen_only":
                g.attempt_id = record_a.trajectory.attempt_id
                g.attempt_b_id = record_b.trajectory.attempt_id
                g.result["reason"] += f" (tripwired attempt: {rec.trajectory.attempt_id})"
                return g
        if ga.malformed or gb.malformed:
            bad = ga if ga.malformed else gb
            return GradeRecord(
                grade_id=self._gid(record_a, rubric_key, "multi_hop_derived"),
                created_at=utc_now(), task_name=record_a.trajectory.task_name,
                attempt_id=record_a.trajectory.attempt_id, rubric=rubric_key,
                mode="pairwise", pipeline="multi_hop_derived",
                grader_model=self.grader_model, extractor_model=self.extractor_model,
                attempt_b_id=record_b.trajectory.attempt_id,
                malformed=True, raw_response=bad.raw_response,
            )
        sa, sb = ga.result[rubric_key]["score"], gb.result[rubric_key]["score"]
        winner = "tie" if sa == sb else ("A" if sa > sb else "B")
        result = {
            "rubric": rubric_key,
            "winner": winner,
            "winner_as_presented": winner,  # no presentation order in derived mode
            "confidence": min(0.95, 0.5 + abs(sa - sb) * 0.1),
            "evidence_for_A": ga.result[rubric_key]["evidence"],
            "evidence_for_B": gb.result[rubric_key]["evidence"],
            "reason": (f"derived from multi-hop pointwise scores: "
                       f"A={sa} ({ga.result[rubric_key]['label']}) vs "
                       f"B={sb} ({gb.result[rubric_key]['label']})"),
        }
        usage = {k: ga.usage.get(k, 0) + gb.usage.get(k, 0)
                 for k in set(ga.usage) | set(gb.usage)}
        return GradeRecord(
            grade_id=self._gid(record_a, rubric_key, "multi_hop_derived"),
            created_at=utc_now(), task_name=record_a.trajectory.task_name,
            attempt_id=record_a.trajectory.attempt_id, rubric=rubric_key,
            mode="pairwise", pipeline="multi_hop_derived",
            grader_model=self.grader_model, extractor_model=self.extractor_model,
            attempt_b_id=record_b.trajectory.attempt_id,
            result=result, retried=ga.retried or gb.retried, usage=usage,
        )

    # -- internals ---------------------------------------------------------------

    def _prescreen_record(self, record: AttemptRecord, rubric: str, mode: str) -> GradeRecord | None:
        if self.prescreen(record) == "hacked" and not self.audit_mode:
            return GradeRecord(
                grade_id=self._gid(record, rubric, "prescreen_only"),
                created_at=utc_now(), task_name=record.trajectory.task_name,
                attempt_id=record.trajectory.attempt_id, rubric=rubric, mode=mode,
                pipeline="prescreen_only", grader_model=None, llm_called=False,
                prescreen="hacked",
                result={"verdict": "hacked",
                        "reason": "deterministic tripwire: protected files tampered; LLM not called",
                        "evidence": (record.reward or {}).get("integrity_reasons", [])},
            )
        return None

    def _gid(self, record: AttemptRecord, rubric: str, pipeline: str) -> str:
        return (f"{record.trajectory.attempt_id}--{rubric}--{pipeline}--{uuid.uuid4().hex[:6]}")

    def _attempt_split(self, record: AttemptRecord) -> str:
        """Cache breakpoint just before the trailing schema instruction, so the
        whole rubric+task+trajectory body is the cacheable prefix."""
        return "\nReturn strict JSON:"

    def _pointwise_prompt(self, record: AttemptRecord, rubric: Rubric) -> str:
        task = self.tasks[record.trajectory.task_name]
        if rubric.needs_outcome:
            return prompts.fill(
                rubric.pointwise_template,
                task=task.instruction, verifier_result=verifier_text(record),
                final_diff=diff_text(record), attempt=attempt_text(record),
            )
        return prompts.fill(rubric.pointwise_template,
                            task=task.instruction, attempt=attempt_text(record))

    def _pairwise_prompt(self, first: AttemptRecord, second: AttemptRecord, rubric: Rubric) -> str:
        task = self.tasks[first.trajectory.task_name]
        if rubric.needs_outcome:
            return prompts.fill(
                rubric.pairwise_template,
                task=task.instruction,
                verifier_result_a=verifier_text(first), final_diff_a=diff_text(first),
                attempt_a=attempt_text(first),
                verifier_result_b=verifier_text(second), final_diff_b=diff_text(second),
                attempt_b=attempt_text(second),
            )
        return prompts.fill(
            rubric.pairwise_template,
            task=task.instruction,
            attempt_a=attempt_text(first), attempt_b=attempt_text(second),
        )

    def _graded_call(self, record: AttemptRecord, rubric: str, mode: str, pipeline: str,
                     model: str, prompt: str, cache_split: str | None,
                     validate, max_tokens: int) -> GradeRecord:
        text, usage, retried, err = self._call_validated(
            model, prompt, cache_split, validate, max_tokens)
        return GradeRecord(
            grade_id=self._gid(record, rubric, pipeline),
            created_at=utc_now(),
            task_name=record.trajectory.task_name,
            attempt_id=record.trajectory.attempt_id,
            rubric=rubric, mode=mode, pipeline=pipeline,
            grader_model=model,
            result=text if err is None else None,
            malformed=err is not None,
            retried=retried,
            raw_response="" if err is None else f"MALFORMED after retry: {err}",
            usage=usage,
        )

    def _params(self, model: str, prompt: str, cache_split: str | None,
                max_tokens: int) -> dict:
        split = cache_split if self.caching else None
        params = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": _blocks(prompt, split)}],
        }
        if self.temperature is not None:
            params["temperature"] = self.temperature
        return params

    def _finalize(self, raw: str, usage: dict, params: dict, validate):
        """Validate strict JSON; ONE corrective retry (direct, so a stuck batch
        entry can't wedge retries); then flag.
        Returns (validated_obj_or_raw, usage, retried, error_or_None)."""
        try:
            return validate(extract_json(raw)), usage, False, None
        except MalformedGrade as e1:
            retry_params = dict(params)
            retry_params["messages"] = params["messages"] + [
                {"role": "assistant", "content": raw[:4000] or "(empty)"},
                {"role": "user", "content": RETRY_NOTE.format(errors="; ".join(e1.errors))},
            ]
            cid = uuid.uuid4().hex[:12]
            raw2, usage2 = self.direct.run([{"custom_id": cid, "params": retry_params}])[cid]
            usage = {k: usage.get(k, 0) + usage2.get(k, 0) for k in set(usage) | set(usage2)}
            try:
                return validate(extract_json(raw2)), usage, True, None
            except MalformedGrade as e2:
                return raw2, usage, True, "; ".join(e2.errors)

    def _call_validated(self, model: str, prompt: str, cache_split: str | None,
                        validate, max_tokens: int):
        params = self._params(model, prompt, cache_split, max_tokens)
        cid = uuid.uuid4().hex[:12]
        raw, usage = self.runner.run([{"custom_id": cid, "params": params}])[cid]
        return self._finalize(raw, usage, params, validate)

    # -- bulk paths: ONE batch submission for many requests -----------------------

    def bulk_pointwise(self, items: list[tuple[AttemptRecord, str]]) -> list[GradeRecord]:
        """Grade many (attempt, rubric) pairs in a single batch. Pre-screened
        (tripwired) attempts never reach the LLM; malformed outputs get the
        standard one direct retry."""
        out: list[GradeRecord] = []
        requests, metas = [], {}
        for record, rubric_key in items:
            blocked = self._prescreen_record(record, rubric_key, "pointwise")
            if blocked:
                out.append(blocked)
                continue
            prompt = self._pointwise_prompt(record, RUBRICS[rubric_key])
            params = self._params(self.grader_model, prompt,
                                  self._attempt_split(record), 1500)
            cid = uuid.uuid4().hex[:12]
            requests.append({"custom_id": cid, "params": params})
            metas[cid] = (record, rubric_key, params)
        results = self.runner.run(requests)
        for cid, (record, rubric_key, params) in metas.items():
            raw, usage = results[cid]
            obj, usage, retried, err = self._finalize(
                raw, usage, params, lambda o, rk=rubric_key: validate_pointwise(o, rk))
            out.append(GradeRecord(
                grade_id=self._gid(record, rubric_key, "single_pass"),
                created_at=utc_now(), task_name=record.trajectory.task_name,
                attempt_id=record.trajectory.attempt_id, rubric=rubric_key,
                mode="pointwise", pipeline="single_pass", grader_model=self.grader_model,
                result=obj if err is None else None, malformed=err is not None,
                retried=retried,
                raw_response="" if err is None else f"MALFORMED after retry: {err}",
                usage=usage,
            ))
        return out

    def bulk_multihop(self, records: list[AttemptRecord]) -> list[GradeRecord]:
        """Multi-hop for many attempts: one batch of worker extractions, then
        one batch of judge calls."""
        out: list[GradeRecord] = []
        worker_reqs, worker_metas = [], {}
        for record in records:
            blocked = self._prescreen_record(record, "all", "pointwise")
            if blocked:
                out.append(blocked)  # pipeline stays "prescreen_only" (see grade_multihop)
                continue
            task = self.tasks[record.trajectory.task_name]
            prompt = prompts.fill(prompts.MULTIHOP_WORKER,
                                  task=task.instruction, attempt=attempt_text(record))
            params = self._params(self.extractor_model, prompt, "\nExtract:", 2000)
            cid = uuid.uuid4().hex[:12]
            worker_reqs.append({"custom_id": cid, "params": params})
            worker_metas[cid] = (record, params)
        worker_results = self.runner.run(worker_reqs)

        judge_reqs, judge_metas = [], {}
        for cid, (record, params) in worker_metas.items():
            raw, usage = worker_results[cid]
            obj, usage, retried, err = self._finalize(raw, usage, params, validate_extraction)
            det = deterministic_evidence(record)
            if err is not None:
                out.append(GradeRecord(
                    grade_id=self._gid(record, "all", "multi_hop"),
                    created_at=utc_now(), task_name=record.trajectory.task_name,
                    attempt_id=record.trajectory.attempt_id, rubric="all",
                    mode="pointwise", pipeline="multi_hop",
                    grader_model=self.grader_model, extractor_model=self.extractor_model,
                    malformed=True, retried=retried,
                    raw_response=f"WORKER MALFORMED: {err}", usage=usage,
                    extraction={"deterministic": det, "llm_extracted": None},
                ))
                continue
            task = self.tasks[record.trajectory.task_name]
            extraction = {"deterministic": det, "llm_extracted": obj}
            reference = task.solution_script.read_text(errors="replace")
            judge_prompt = prompts.fill(
                prompts.MULTIHOP_JUDGE, task=task.instruction,
                evidence_json=json.dumps(extraction, indent=2),
                reference_context=f"Oracle solution script (reference only):\n{reference}",
            )
            jparams = self._params(self.grader_model, judge_prompt,
                                   "\nReturn strict JSON:", 2500)
            jcid = uuid.uuid4().hex[:12]
            judge_reqs.append({"custom_id": jcid, "params": jparams})
            judge_metas[jcid] = (record, extraction, usage, retried, jparams)
        judge_results = self.runner.run(judge_reqs)

        for jcid, (record, extraction, wusage, wretried, jparams) in judge_metas.items():
            raw, usage = judge_results[jcid]
            obj, usage, retried, err = self._finalize(raw, usage, jparams, validate_judge)
            usage = {k: usage.get(k, 0) + wusage.get(k, 0) for k in set(usage) | set(wusage)}
            out.append(GradeRecord(
                grade_id=self._gid(record, "all", "multi_hop"),
                created_at=utc_now(), task_name=record.trajectory.task_name,
                attempt_id=record.trajectory.attempt_id, rubric="all",
                mode="pointwise", pipeline="multi_hop",
                grader_model=self.grader_model, extractor_model=self.extractor_model,
                result=obj if err is None else None, extraction=extraction,
                malformed=err is not None, retried=retried or wretried,
                raw_response="" if err is None else f"MALFORMED after retry: {err}",
                usage=usage,
            ))
        return out

    def bulk_pairwise(self, pairs: list[tuple[AttemptRecord, AttemptRecord, str, bool | None]]
                      ) -> list[GradeRecord]:
        """Grade many (A, B, rubric, force_swap) comparisons in one batch.
        force_swap=None randomizes (seeded); True/False pins presentation order
        (the position-bias probe grades each pair once with each)."""
        out: list[GradeRecord] = []
        requests, metas = [], {}
        for record_a, record_b, rubric_key, force_swap in pairs:
            blocked = None
            for r in (record_a, record_b):
                blocked = blocked or self._prescreen_record(r, rubric_key, "pairwise")
                if blocked:
                    blocked.attempt_id = record_a.trajectory.attempt_id
                    blocked.attempt_b_id = record_b.trajectory.attempt_id
                    blocked.result["reason"] += f" (tripwired attempt: {r.trajectory.attempt_id})"
                    break
            if blocked:
                out.append(blocked)
                continue
            rng = random.Random(f"{self.seed}:{record_a.trajectory.attempt_id}:"
                                f"{record_b.trajectory.attempt_id}:{rubric_key}")
            swapped = rng.random() < 0.5 if force_swap is None else force_swap
            first, second = (record_b, record_a) if swapped else (record_a, record_b)
            prompt = self._pairwise_prompt(first, second, RUBRICS[rubric_key])
            params = self._params(self.grader_model, prompt, None, 1500)
            cid = uuid.uuid4().hex[:12]
            requests.append({"custom_id": cid, "params": params})
            metas[cid] = (record_a, record_b, rubric_key, swapped, params)
        results = self.runner.run(requests)
        for cid, (record_a, record_b, rubric_key, swapped, params) in metas.items():
            raw, usage = results[cid]
            obj, usage, retried, err = self._finalize(
                raw, usage, params, lambda o, rk=rubric_key: validate_pairwise(o, rk))
            rec = GradeRecord(
                grade_id=self._gid(record_a, rubric_key, "single_pass"),
                created_at=utc_now(), task_name=record_a.trajectory.task_name,
                attempt_id=record_a.trajectory.attempt_id, rubric=rubric_key,
                mode="pairwise", pipeline="single_pass", grader_model=self.grader_model,
                attempt_b_id=record_b.trajectory.attempt_id, order_swapped=swapped,
                result=obj if err is None else None, malformed=err is not None,
                retried=retried,
                raw_response="" if err is None else f"MALFORMED after retry: {err}",
                usage=usage,
            )
            if rec.result and not rec.malformed:
                raw_winner = rec.result["winner"]
                if raw_winner in ("A", "B") and swapped:
                    rec.result["winner"] = "B" if raw_winner == "A" else "A"
                rec.result["winner_as_presented"] = raw_winner
            out.append(rec)
        return out

    def bulk_steps(self, record: AttemptRecord, indices: list[int]) -> list[GradeRecord]:
        """Rate many actions of one attempt in a single batch."""
        out: list[GradeRecord] = []
        requests, metas = [], {}
        for i in indices:
            blocked = self._prescreen_record(record, "step", "step")
            if blocked:
                blocked.step_index = i
                out.append(blocked)
                continue
            task = self.tasks[record.trajectory.task_name]
            steps = record.trajectory.steps
            step = steps[i]
            prior = "\n".join(
                f"$ {s.command}\n[exit {s.exit_code}] {s.stdout[:400]}" for s in steps[:i]
            ) or "(this is the first action)"
            obs = (f"exit_code: {step.exit_code}\nstdout:\n{step.stdout[:2000]}\n"
                   f"stderr:\n{step.stderr[:1000]}")
            prompt = prompts.fill(prompts.STEP_POINTWISE, task=task.instruction,
                                  prior_steps=prior[-PRIOR_STEPS_CAP:],
                                  action=step.command, observation=obs)
            params = self._params(self.grader_model, prompt, "\nAction:\n", 800)
            cid = uuid.uuid4().hex[:12]
            requests.append({"custom_id": cid, "params": params})
            metas[cid] = (i, params)
        results = self.runner.run(requests)
        for cid, (i, params) in metas.items():
            raw, usage = results[cid]
            obj, usage, retried, err = self._finalize(raw, usage, params, validate_step)
            out.append(GradeRecord(
                grade_id=self._gid(record, "step", "single_pass"),
                created_at=utc_now(), task_name=record.trajectory.task_name,
                attempt_id=record.trajectory.attempt_id, rubric="step",
                mode="step", pipeline="single_pass", grader_model=self.grader_model,
                step_index=i, result=obj if err is None else None,
                malformed=err is not None, retried=retried,
                raw_response="" if err is None else f"MALFORMED after retry: {err}",
                usage=usage,
            ))
        return out
