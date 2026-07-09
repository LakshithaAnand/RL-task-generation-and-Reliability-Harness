"""Terminal-Bench 2.0 task loader.

A TB2 task is a self-contained directory:

    <task>/
      instruction.md            # goal description given to the agent
      task.toml                 # metadata, timeouts, resource limits
      environment/Dockerfile    # initial filesystem state (we build this locally)
      tests/test.sh             # verifier entrypoint; writes /logs/verifier/ctrf.json
                                #   (per-test results) and /logs/verifier/reward.txt (0/1)
      solution/solve.sh         # oracle solution (guaranteed pass)

Everything under tests/, plus task.toml, is a *protected file*. Protection is
two-layered: (1) the canonical copies live host-side where the agent can't
reach them, and verification always injects a pristine copy; (2) the
environment hashes the in-container protected area (/tests) at reset and
after the attempt — any modification/deletion/staging there is an integrity
violation (reward 0, deterministic tripwire — see reward.py). task.toml never
enters the container at all.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COMPONENTS = [
    "instruction.md",
    "task.toml",
    "environment/Dockerfile",
    "tests/test.sh",
    "solution/solve.sh",
]

_TEST_DEF_RE = re.compile(r"^def (test_\w+)", re.MULTILINE)


class TaskFormatError(Exception):
    """Raised when a task directory is missing required TB2 components."""


@dataclass(frozen=True)
class Task:
    name: str
    path: Path
    instruction: str
    config: dict  # parsed task.toml

    @property
    def dockerfile(self) -> Path:
        return self.path / "environment" / "Dockerfile"

    @property
    def tests_dir(self) -> Path:
        return self.path / "tests"

    @property
    def test_script(self) -> Path:
        return self.tests_dir / "test.sh"

    @property
    def solution_script(self) -> Path:
        return self.path / "solution" / "solve.sh"

    @property
    def protected_files(self) -> list[Path]:
        """Files the agent must never modify: official tests + task metadata.

        Paths are relative to the task dir; these host-side copies are the
        canonical versions the verifier injects. In-container tampering is
        detected separately via /tests hash-state comparison (environment.py);
        task.toml is protected by never entering the container."""
        rel = [p.relative_to(self.path) for p in sorted(self.tests_dir.rglob("*")) if p.is_file()]
        rel.append(Path("task.toml"))
        return rel

    @property
    def test_names(self) -> list[str]:
        """Static list of test functions, parsed from tests/*.py.

        For display and sanity checks only — authoritative per-test results
        come from the verifier's CTRF JSON at verify time.
        """
        names: list[str] = []
        for py in sorted(self.tests_dir.glob("*.py")):
            names.extend(_TEST_DEF_RE.findall(py.read_text(errors="replace")))
        return names

    @property
    def task_hash(self) -> str:
        """SHA-256 over every file in the task dir — logged for reproducibility."""
        h = hashlib.sha256()
        for p in sorted(self.path.rglob("*")):
            if p.is_file():
                h.update(str(p.relative_to(self.path)).encode())
                h.update(p.read_bytes())
        return h.hexdigest()

    @property
    def verifier_timeout_sec(self) -> float:
        return float(self.config.get("verifier", {}).get("timeout_sec", 900.0))

    @property
    def agent_timeout_sec(self) -> float:
        return float(self.config.get("agent", {}).get("timeout_sec", 900.0))

    @property
    def build_timeout_sec(self) -> float:
        return float(self.config.get("environment", {}).get("build_timeout_sec", 600.0))

    @property
    def difficulty(self) -> str:
        return self.config.get("metadata", {}).get("difficulty", "unknown")


def load_task(path: Path | str) -> Task:
    path = Path(path).resolve()
    missing = [c for c in REQUIRED_COMPONENTS if not (path / c).is_file()]
    if missing:
        raise TaskFormatError(f"{path.name}: missing required components: {missing}")
    config = tomllib.loads((path / "task.toml").read_text())
    instruction = (path / "instruction.md").read_text()
    return Task(name=path.name, path=path, instruction=instruction, config=config)


def discover_tasks(tasks_dir: Path | str) -> dict[str, Task]:
    """Load every task directory under tasks_dir, sorted by name."""
    tasks_dir = Path(tasks_dir)
    tasks = {}
    for entry in sorted(tasks_dir.iterdir()):
        if entry.is_dir():
            tasks[entry.name] = load_task(entry)
    return tasks
