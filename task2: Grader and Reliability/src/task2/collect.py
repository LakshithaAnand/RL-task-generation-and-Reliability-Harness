"""Systematic attempt collection: the `analysis` dataset.

Per task:
  - 1 oracle run (guaranteed pass; anchors ground truth)
  - 1 live-agent run per config `agent.attempt_variants` entry (varied
    temperature / step budget / token budget -> pass/partial/fail mix)
  - 1 weak scripted baseline (does something harmless, fails)

Every record: dataset="analysis", fresh deterministic seed, exact model ID,
budgets, and the full verifier/diff/reward blocks. Env failures (docker
breakage) are retried once, then excluded and reported — never recorded as
agent failures.

Attempts are internally sequential (agent loop = synchronous API by nature)
but independent of each other, so collection runs a small thread pool.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from task2.agent import ClaudeBashAgent, OracleAgent, ScriptedAgent
from task2.controller import run_attempt
from task2.environment import EnvFailure, Environment
from task2.reward import check_integrity, compute_reward
from task2.tasks import Task
from task2.trajectory import AttemptRecord

LIGHT_TASKS = ["fix-git", "prove-plus-comm", "regex-log", "kv-store-grpc"]

WEAK_BASELINE_COMMANDS = ["ls -la", "echo 'looks complicated, giving up'"]


def full_attempt(task: Task, agent, cfg: dict, seed: int,
                 max_steps: int, attempt_timeout: float,
                 token_budget: int | None, dataset: str) -> AttemptRecord:
    """reset -> attempt -> integrity -> verify -> diff -> reward, one record."""
    env = Environment(task, cfg, seed=seed)
    try:
        info = env.reset()
        traj = run_attempt(env, agent, max_steps=max_steps,
                           attempt_timeout_sec=attempt_timeout,
                           token_budget=token_budget)
        state_after = env.protected_container_state()
        # Diff BEFORE verify: test.sh may write artifacts (__pycache__, build
        # output) into the workdir, which must not be attributed to the agent.
        diff = env.snapshot_diff()
        vres = env.verify()
        integrity = check_integrity(env.protected_state_at_reset, state_after,
                                    verifier_runnable=vres.runnable)
        breakdown = compute_reward(info.initial_passed, vres, integrity)
        # Env failures to retry/exclude — never valid agent failures:
        # (a) attempt killed by transient API trouble; (b) verifier could not
        # run with no tampering evidence (infra flake vs corruption is
        # indistinguishable; see reward.check_integrity).
        transient_api_death = traj.done_reason == "agent_error" and any(
            marker in traj.final_message
            for marker in ("429", "529", "overloaded", "Internal server error", "500")
        )
        verifier_infra_failure = not vres.runnable and not integrity.violated
        return AttemptRecord(
            trajectory=traj,
            dataset=dataset,
            env_failure=transient_api_death or verifier_infra_failure,
            reset={
                "image_tag": info.image_tag, "task_hash": info.task_hash,
                "workdir": info.workdir, "initial_passed": info.initial_passed,
                "total_tests": info.total_tests,
                "initial_verifier_runnable": info.initial_verifier_runnable,
                "seed": info.seed,
            },
            verifier=asdict(vres),
            diff=asdict(diff),
            reward=asdict(breakdown),
        )
    finally:
        env.close()


def outcome(record: AttemptRecord) -> str:
    """pass / partial / fail / cheat / env_failure, for the collection summary."""
    r = record.reward or {}
    if record.env_failure:
        return "env_failure"
    if r.get("integrity_violation"):
        return "cheat"
    if r.get("final_verifier_pass"):
        return "pass"
    if r.get("final_passed", 0) > r.get("initial_passed", 0):
        return "partial"
    return "fail"


def build_jobs(task: Task, cfg: dict, base_seed: int) -> list[dict]:
    agent_cfg = cfg["agent"]
    model = agent_cfg.get("model") or cfg["agent_model"]
    jobs = [{
        "label": "oracle",
        "make_agent": lambda: OracleAgent(),
        "max_steps": 4,
        "token_budget": None,
        "seed": base_seed,
    }, {
        "label": "weak_baseline",
        "make_agent": lambda: ScriptedAgent(list(WEAK_BASELINE_COMMANDS)),
        "max_steps": 4,
        "token_budget": None,
        "seed": base_seed + 1,
    }]
    for i, var in enumerate(agent_cfg.get("attempt_variants", [])):
        steps = int(var.get("max_steps", agent_cfg["max_steps"]))
        jobs.append({
            "label": var.get("name", f"variant_{i}"),
            "make_agent": (lambda v=var, s=steps: ClaudeBashAgent(
                model_id=model,
                max_steps=s,
                temperature=float(v.get("temperature", agent_cfg["temperature"])),
                max_tokens_per_call=int(v.get("max_tokens_per_call",
                                              agent_cfg["max_tokens_per_call"])),
            )),
            "max_steps": steps,
            "token_budget": var.get("token_budget"),
            "seed": base_seed + 2 + i,
        })
    return jobs


def build_extra_jobs(task: Task, cfg: dict, base_seed: int) -> list[dict]:
    """Diversity round: 2 extra attempts per task, biased toward partial/fail
    (ultra-low budget + high temperature), never just more easy passes.
    Seed block offset +500 so it can never collide with the core round."""
    agent_cfg = cfg["agent"]
    model = agent_cfg.get("model") or cfg["agent_model"]
    return [{
        "label": "extra_ultra_low_budget",
        "make_agent": lambda: ClaudeBashAgent(
            model_id=model, max_steps=4, temperature=0.0, max_tokens_per_call=1024),
        "max_steps": 4,
        "token_budget": None,
        "seed": base_seed + 500,
    }, {
        "label": "extra_high_temperature",
        "make_agent": lambda: ClaudeBashAgent(
            model_id=model, max_steps=10, temperature=1.0, max_tokens_per_call=1536),
        "max_steps": 10,
        "token_budget": None,
        "seed": base_seed + 501,
    }]


def dataset_summary(attempts_dir: Path) -> str:
    """Updated counts over EVERYTHING on disk labeled dataset=analysis,
    with the exact N and the smoke/fixture separation stated."""
    from task2.trajectory import load_all_attempts

    recs = load_all_attempts(attempts_dir)
    analysis = [r for r in recs if r.dataset == "analysis" and not r.env_failure]
    excluded_env = [r for r in recs if r.dataset == "analysis" and r.env_failure]
    smoke = [r for r in recs if r.dataset == "smoke"]
    order = ["pass", "partial", "fail", "cheat"]
    lines = ["", "=== updated dataset=analysis totals (all attempts on disk) ==="]
    tasks = sorted({r.trajectory.task_name for r in analysis})
    totals = {k: 0 for k in order}
    for t in tasks:
        counts = {k: 0 for k in order}
        for r in analysis:
            if r.trajectory.task_name == t:
                counts[outcome(r)] += 1
                totals[outcome(r)] += 1
        lines.append(f"  {t:<18} " + "  ".join(f"{k}={counts[k]}" for k in order)
                     + f"  (n={sum(counts.values())})")
    lines.append(f"  {'TOTAL':<18} " + "  ".join(f"{k}={totals[k]}" for k in order))
    lines.append(f"\n  exact N (dataset=analysis): {len(analysis)}")
    lines.append(f"  env-failure records excluded from N: {len(excluded_env)}")
    lines.append(f"  smoke attempts on disk (excluded): {len(smoke)}")
    return "\n".join(lines)


def collect(cfg: dict, tasks: dict[str, Task], task_names: list[str],
            attempts_dir: Path, concurrency: int = 3, round_name: str = "core") -> list[dict]:
    agent_cfg = cfg["agent"]
    timeout = float(agent_cfg["attempt_timeout_sec"])
    base = int(cfg.get("seed", 42))
    results: list[dict] = []

    def run_job(task_name: str, job: dict) -> dict:
        task = tasks[task_name]
        last_err = None
        for attempt_round in (1, 2):  # env failures retried once
            try:
                rec = full_attempt(
                    task, job["make_agent"](), cfg, seed=job["seed"],
                    max_steps=job["max_steps"], attempt_timeout=timeout,
                    token_budget=job["token_budget"], dataset="analysis",
                )
                if rec.env_failure and attempt_round == 1:
                    continue  # transient infra: retry once before recording
                rec.notes = f"collect variant={job['label']}"
                path = rec.save(attempts_dir)
                return {"task": task_name, "label": job["label"],
                        "attempt_id": rec.trajectory.attempt_id,
                        "outcome": outcome(rec),
                        "reward": rec.reward["final_reward"],
                        "passed": f"{rec.reward['final_passed']}/{rec.reward['total_tests']}",
                        "steps": len(rec.trajectory.steps),
                        "model": rec.trajectory.model_id, "path": str(path)}
            except EnvFailure as e:
                last_err = e
        return {"task": task_name, "label": job["label"], "outcome": "env_failure",
                "attempt_id": None, "error": str(last_err)}

    builder = build_extra_jobs if round_name == "extra" else build_jobs
    jobs = []
    for ti, name in enumerate(task_names):
        for job in builder(tasks[name], cfg, base_seed=base + 1000 * (ti + 1)):
            jobs.append((name, job))

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(run_job, name, job): (name, job["label"])
                   for name, job in jobs}
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            print(f"  [{res['task']:<15}] {res['label']:<25} -> {res['outcome']:<11} "
                  f"reward={res.get('reward', '-')} passed={res.get('passed', '-')} "
                  f"steps={res.get('steps', '-')}", flush=True)
    return results


def summarize(results: list[dict], fixtures_count: int) -> str:
    tasks = sorted({r["task"] for r in results})
    lines = ["", "=== collection summary (dataset=analysis) ==="]
    order = ["pass", "partial", "fail", "cheat", "env_failure"]
    totals = {k: 0 for k in order}
    for t in tasks:
        counts = {k: 0 for k in order}
        for r in results:
            if r["task"] == t:
                counts[r["outcome"]] += 1
                totals[r["outcome"]] += 1
        lines.append(f"  {t:<18} " + "  ".join(f"{k}={counts[k]}" for k in order))
    lines.append(f"  {'TOTAL':<18} " + "  ".join(f"{k}={totals[k]}" for k in order))
    n_recorded = sum(v for k, v in totals.items() if k != "env_failure")
    lines.append(f"\n  analysis attempts recorded: N={n_recorded}")
    lines.append(f"  adversarial fixtures (dataset=fixture, all fix-git): {fixtures_count}")
    lines.append("  (smoke attempts from earlier steps are labeled dataset=smoke and are NOT counted here)")
    return "\n".join(lines)
