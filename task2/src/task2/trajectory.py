"""Trajectory and AttemptRecord: the data every later stage consumes.

A Trajectory is the full record of one attempt loop: every action the agent
took (with its stated reasoning), every observation the environment returned,
and why the loop ended. An AttemptRecord wraps a trajectory with everything
needed to score and audit it: reset metadata (image tag, task hash, initial
pass count, seed), verifier result, workspace diff, reward breakdown, and the
env-failure/agent-failure distinction.

Verifier/diff/reward are plain dicts filled in by later pipeline stages
(steps 4-5); None means "not yet computed". Records serialize to JSON, one
file per attempt, under data/attempts/.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_attempt_id(task_name: str, source: str) -> str:
    return f"{task_name}--{source}--{uuid.uuid4().hex[:8]}"


@dataclass
class TrajectoryStep:
    index: int
    thought: str          # agent's stated reasoning before the action ("" if none)
    command: str          # the bash action
    stdout: str
    stderr: str
    exit_code: int
    cwd: str
    truncated: bool
    elapsed_ms: int


@dataclass
class Trajectory:
    attempt_id: str
    task_name: str
    source: str                      # "oracle" | "agent" | "scripted" | "fixture"
    model_id: str | None             # exact model ID for agent runs, None otherwise
    seed: int
    temperature: float | None
    max_steps: int
    steps: list[TrajectoryStep] = field(default_factory=list)
    done_reason: str = ""            # agent_done | max_steps | timeout | token_budget | agent_error
    final_message: str = ""          # agent's closing summary, if it gave one
    total_output_tokens: int = 0     # LLM output tokens spent by the agent
    started_at: str = ""
    finished_at: str = ""

    def render(self, max_obs_chars: int = 1500) -> str:
        """Human/grader-readable rendering of the full trajectory."""
        lines = [f"# Attempt {self.attempt_id} on task {self.task_name} (source={self.source})"]
        for s in self.steps:
            lines.append(f"\n## Step {s.index}")
            if s.thought:
                lines.append(f"[agent reasoning] {s.thought}")
            lines.append(f"$ {s.command}")
            out = s.stdout[:max_obs_chars]
            err = s.stderr[:max_obs_chars]
            if out.strip():
                lines.append(out.rstrip())
            if err.strip():
                lines.append(f"[stderr] {err.rstrip()}")
            lines.append(f"[exit code: {s.exit_code}]")
        lines.append(f"\n[attempt ended: {self.done_reason}]")
        if self.final_message:
            lines.append(f"[agent final message] {self.final_message}")
        return "\n".join(lines)


@dataclass
class AttemptRecord:
    trajectory: Trajectory
    reset: dict          # image_tag, task_hash, workdir, initial_passed, total_tests, seed, ...
    verifier: dict | None = None   # VerifierResult fields (step 4)
    diff: dict | None = None       # DiffSummary fields (step 4)
    reward: dict | None = None     # reward breakdown (step 5)
    env_failure: bool = False      # infra broke -> retry/exclude, never an agent failure
    expected_verdict: str | None = None  # only for adversarial fixtures (step 7)
    # Provenance label. "smoke" = ad-hoc pipeline checks (never silently mixed
    # into reliability numbers); "analysis" = the systematic collect dataset;
    # "fixture" = hand-authored adversarial attempts. The reliability analysis
    # must state exactly which attempt IDs make up its N.
    dataset: str = "smoke"
    notes: str = ""

    # -- serialization ---------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "AttemptRecord":
        raw = json.loads(text)
        traj_raw = raw.pop("trajectory")
        steps = [TrajectoryStep(**s) for s in traj_raw.pop("steps")]
        trajectory = Trajectory(**traj_raw, steps=steps)
        return cls(trajectory=trajectory, **raw)

    def save(self, attempts_dir: Path) -> Path:
        attempts_dir.mkdir(parents=True, exist_ok=True)
        path = attempts_dir / f"{self.trajectory.attempt_id}.json"
        path.write_text(self.to_json())
        return path

    @classmethod
    def load(cls, path: Path) -> "AttemptRecord":
        return cls.from_json(Path(path).read_text())


def load_all_attempts(attempts_dir: Path) -> list[AttemptRecord]:
    return [AttemptRecord.load(p) for p in sorted(Path(attempts_dir).glob("*.json"))]
