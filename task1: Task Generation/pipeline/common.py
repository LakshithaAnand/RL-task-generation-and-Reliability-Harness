"""Shared helpers for pipeline stages: config, paths, funnel log, docker, pytest parsing.

Everything here is deterministic and model-free.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SEEDS_CONFIG_PATH = ROOT / "pipeline" / "seeds.toml"
ARTIFACTS_DIR = ROOT / "artifacts"
FUNNEL_PATH = ARTIFACTS_DIR / "funnel.jsonl"
REPOS_DIR = ARTIFACTS_DIR / "repos"        # gitignored: reproducible clones
BUILD_DIR = ARTIFACTS_DIR / "build"        # gitignored: generated Dockerfiles
CANDIDATES_DIR = ARTIFACTS_DIR / "candidates"
ELIGIBILITY_REPORT_PATH = ARTIFACTS_DIR / "eligibility_report.json"
FAILURE_REPORT_PATH = ARTIFACTS_DIR / "failure_report.json"
ASSEMBLY_REPORT_PATH = ARTIFACTS_DIR / "assembly_report.json"
SOLVABILITY_REPORT_PATH = ARTIFACTS_DIR / "solvability_report.json"
GENERATED_TASKS_DIR = ROOT / "tasks" / "generated"


@dataclasses.dataclass(frozen=True)
class Seed:
    name: str
    url: str
    tag: str
    commit: str
    source_dir: str
    test_dir: str
    expected_license: str

    @property
    def repo_dir(self) -> Path:
        return REPOS_DIR / self.name

    @property
    def image_tag(self) -> str:
        return f"tb-seed-{self.name}:{self.commit[:12]}"


@dataclasses.dataclass(frozen=True)
class Limits:
    max_repo_mb: float
    max_test_seconds: float
    docker_build_timeout_seconds: float
    container_run_timeout_seconds: float


@dataclasses.dataclass(frozen=True)
class MutationSettings:
    n_candidates: int
    per_template: dict[str, int]


@dataclasses.dataclass(frozen=True)
class AssemblySettings:
    org: str
    verbosity_cycle: list[str]
    agent_timeout_sec: float
    verifier_timeout_sec: float
    build_timeout_sec: float


@dataclasses.dataclass(frozen=True)
class Config:
    rng_seed: int
    limits: Limits
    mutation: MutationSettings
    assembly: AssemblySettings
    seeds: list[Seed]


def load_config(path: Path = SEEDS_CONFIG_PATH) -> Config:
    raw = tomllib.loads(path.read_text())
    return Config(
        rng_seed=raw["rng_seed"],
        limits=Limits(**raw["limits"]),
        mutation=MutationSettings(
            n_candidates=raw["mutation"]["n_candidates"],
            per_template=dict(raw["mutation"]["per_template"]),
        ),
        assembly=AssemblySettings(**raw["assembly"]),
        seeds=[Seed(**s) for s in raw["seeds"]],
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def funnel_log(
    stage: str,
    item: str,
    verdict: str,
    reason: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one verdict line to the funnel log. Rejects always carry a reason."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "ts": utc_now_iso(),
        "stage": stage,
        "item": item,
        "verdict": verdict,
        "reason": reason,
    }
    if detail:
        entry["detail"] = detail
    with FUNNEL_PATH.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def run(
    cmd: list[str],
    timeout: float,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing text output. Never raises on nonzero exit."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def docker_run(
    image: str,
    script: str,
    timeout: float,
    ro_mounts: dict[Path, str] | None = None,
    name: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a bash script in a fresh container from `image`. Mounts are read-only.

    `name` lets callers `docker rm -f` the container if the host-side timeout
    fires (killing the docker CLI does not kill the container).
    """
    cmd = ["docker", "run", "--rm"]
    if name:
        cmd += ["--name", name]
    for host, cont in (ro_mounts or {}).items():
        cmd += ["-v", f"{host}:{cont}:ro"]
    cmd += [image, "bash", "-c", script]
    return run(cmd, timeout=timeout)


def build_task_image(task_dir: Path, timeout: float = 600) -> str:
    """Build a generated task's environment/ into a local image; return its tag.

    Layers on the cached Stage-1 seed image, so this is cheap. The image is the
    exact broken state an agent would see (mutation applied, .git and patches
    scrubbed, flipped tests pruned from the visible dev suite).
    """
    tag = f"tb-task-{task_dir.name}".lower()
    env_dir = task_dir / "environment"
    res = run(
        ["docker", "build", "-q", "-t", tag, str(env_dir)],
        timeout=timeout,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"docker build for {task_dir.name} failed: {res.stderr.strip()[-600:]}"
        )
    return tag


_SUMMARY_TOKEN_RE = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)")
_DURATION_RE = re.compile(r"in ([0-9.]+)s")
_FAILED_LINE_RE = re.compile(r"^FAILED (\S+)", re.MULTILINE)


def parse_pytest_summary(output: str) -> dict[str, Any]:
    """Parse counts and duration from `pytest -q` output (last summary line wins)."""
    counts: dict[str, int] = {}
    duration: float | None = None
    for line in output.splitlines():
        tokens = _SUMMARY_TOKEN_RE.findall(line)
        if tokens and _DURATION_RE.search(line):
            counts = {kind.rstrip("s"): int(n) for n, kind in tokens}
            counts = {("error" if k == "error" else k): v for k, v in counts.items()}
            m = _DURATION_RE.search(line)
            assert m is not None
            duration = float(m.group(1))
    return {"counts": counts, "duration_seconds": duration}


def parse_failed_tests(output: str) -> list[str]:
    """Extract 'FAILED <nodeid>' lines from `pytest -rf` output."""
    return _FAILED_LINE_RE.findall(output)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
