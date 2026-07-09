"""The attempt loop: drives an agent against a reset Environment and records
a full Trajectory.

The controller owns all budgets (steps, wall clock, agent output tokens) so
agents stay thin. It operates on an already-reset Environment; composing
reset -> run_attempt -> verify -> reward into a complete AttemptRecord is the
collect stage's job (later steps).
"""

from __future__ import annotations

import time

from task2.environment import Environment, Observation
from task2.trajectory import Trajectory, TrajectoryStep, new_attempt_id, utc_now


def run_attempt(
    env: Environment,
    agent,
    max_steps: int,
    attempt_timeout_sec: float,
    token_budget: int | None = None,
) -> Trajectory:
    task = env.task
    agent.setup(env, task)

    traj = Trajectory(
        attempt_id=new_attempt_id(task.name, agent.source),
        task_name=task.name,
        source=agent.source,
        model_id=agent.model_id,
        seed=env.seed,
        temperature=getattr(agent, "temperature", None),
        max_steps=max_steps,
        started_at=utc_now(),
    )

    last_obs: Observation | None = None
    deadline = time.monotonic() + attempt_timeout_sec

    while True:
        if len(traj.steps) >= max_steps:
            traj.done_reason = "max_steps"
            break
        if time.monotonic() > deadline:
            traj.done_reason = "timeout"
            break
        if token_budget is not None and agent.total_output_tokens >= token_budget:
            traj.done_reason = "token_budget"
            break

        try:
            action = agent.next_action(last_obs)
        except Exception as e:  # agent-side failure is a valid failed attempt
            traj.done_reason = "agent_error"
            traj.final_message = f"agent error: {e}"
            break

        if action.done or action.command is None:
            traj.done_reason = "agent_done"
            traj.final_message = action.final_message
            break

        last_obs = env.exec(action.command)
        traj.steps.append(TrajectoryStep(
            index=len(traj.steps),
            thought=action.thought,
            command=action.command,
            stdout=last_obs.stdout,
            stderr=last_obs.stderr,
            exit_code=last_obs.exit_code,
            cwd=last_obs.cwd,
            truncated=last_obs.truncated,
            elapsed_ms=last_obs.elapsed_ms,
        ))

    traj.total_output_tokens = agent.total_output_tokens
    traj.finished_at = utc_now()
    return traj
