"""Docker lifecycle for running one attempt on one TB2 task.

Thin harness, no Harbor: we shell out to `docker` directly.

Lifecycle (Environment):
  reset()   build the task image locally from environment/Dockerfile, measure
            the initial passing-test count in a THROWAWAY container (so the
            agent's container stays pristine), then start the agent container,
            snapshot its initial workdir state, record protected-file state,
            and disconnect its network.
  exec()    run one bash command in the agent container (the `step` primitive;
            observations are truncated stdout/stderr/exit_code).
  verify()  reconnect the network (test.sh downloads uv), inject a clean copy
            of the official tests, run test.sh, parse per-test CTRF results.
  snapshot_diff()  diff of the workdir vs the state captured at reset.
  close()   remove the container and temp snapshot.

Network rule (spec principle #6): NO network while the agent acts. The
verifier needs network to bootstrap uv, so the sequence is: connected during
image build and verification, disconnected the whole time the agent can act.

Failure taxonomy: EnvFailure (docker build/start/harness errors) should be
retried or excluded from analysis; agent failures (bad commands, timeouts
inside the attempt) are valid failed attempts and are kept.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from task2.tasks import Task
from task2.verifier import VerifierResult, run_verifier

IMAGE_PREFIX = "task2"
DOCKER_NETWORK = "bridge"


class EnvFailure(Exception):
    """Infrastructure failure (docker build/start/copy). Retry or exclude —
    never record as an agent failure."""


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    elapsed_ms: int


@dataclass
class Observation:
    stdout: str
    stderr: str
    exit_code: int
    cwd: str
    truncated: bool
    elapsed_ms: int


@dataclass
class ResetInfo:
    task_name: str
    instruction: str
    workdir: str
    image_tag: str
    task_hash: str
    seed: int
    initial_passed: int
    total_tests: int
    initial_verifier_runnable: bool


@dataclass
class DiffSummary:
    changed_files: list[str]
    diff_text: str
    truncated: bool


def _docker(args: list[str], timeout: float | None = None, check: bool = False) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            ["docker", *args], capture_output=True, text=True, errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise EnvFailure(f"docker {' '.join(args[:2])} timed out after {timeout}s") from e
    if check and proc.returncode != 0:
        raise EnvFailure(f"docker {' '.join(args[:2])} failed: {proc.stderr[-1500:]}")
    return proc


class Container:
    """Handle to one running container; used by Environment and verifier."""

    def __init__(self, name: str, workdir: str):
        self.name = name
        self.workdir = workdir

    def exec_root(self, command: str, cwd: str | None = None, timeout: float = 300) -> ExecResult:
        """Run a bash command as root. In-container `timeout` bounds the process
        itself so a client-side timeout can't leave it running."""
        inner = f"timeout -k 5 {int(timeout)} bash -c {_shq(command)}"
        argv = ["exec"]
        if cwd:
            argv += ["-w", cwd]
        argv += [self.name, "bash", "-c", inner]
        start = time.monotonic()
        try:
            proc = subprocess.run(
                ["docker", *argv], capture_output=True, text=True, errors="replace", timeout=timeout + 15,
            )
            elapsed = int((time.monotonic() - start) * 1000)
            timed_out = proc.returncode == 124  # coreutils timeout exit code
            return ExecResult(proc.stdout, proc.stderr, proc.returncode, timed_out, elapsed)
        except subprocess.TimeoutExpired:
            elapsed = int((time.monotonic() - start) * 1000)
            return ExecResult("", "harness: command timed out", 124, True, elapsed)

    def read_file(self, path: str) -> str | None:
        res = self.exec_root(f"cat {_shq(path)}", timeout=30)
        return res.stdout if res.exit_code == 0 else None

    def cp_in(self, host_path: Path, container_path: str) -> None:
        _docker(["cp", str(host_path), f"{self.name}:{container_path}"], timeout=120, check=True)

    def cp_out(self, container_path: str, host_path: Path) -> None:
        _docker(["cp", f"{self.name}:{container_path}", str(host_path)], timeout=120, check=True)

    def set_network(self, connected: bool) -> None:
        verb = "connect" if connected else "disconnect"
        proc = _docker(["network", verb, DOCKER_NETWORK, self.name], timeout=30)
        already = "already exists" in proc.stderr or "is not connected" in proc.stderr
        if proc.returncode != 0 and not already:
            raise EnvFailure(f"network {verb} failed: {proc.stderr[-500:]}")

    def remove(self) -> None:
        _docker(["rm", "-f", self.name], timeout=60)


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def build_image(task: Task, build_timeout: float) -> str:
    """Build the task image locally from environment/Dockerfile. Tagged with the
    task-content hash so a task edit forces a rebuild and the tag is loggable."""
    tag = f"{IMAGE_PREFIX}/{task.name}:{task.task_hash[:12]}"
    if _docker(["image", "inspect", tag], timeout=30).returncode == 0:
        return tag
    proc = _docker(
        ["build", "-t", tag, str(task.path / "environment")],
        timeout=build_timeout,
    )
    if proc.returncode != 0:
        raise EnvFailure(f"docker build failed for {task.name}: {proc.stderr[-2000:]}")
    return tag


class Environment:
    def __init__(self, task: Task, config: dict, seed: int = 42):
        self.task = task
        self.config = config
        self.seed = seed
        self.container: Container | None = None
        self.image_tag: str | None = None
        self._snapshot_dir: Path | None = None
        self._protected_state_at_reset: dict[str, str] = {}
        self.reset_info: ResetInfo | None = None

    # -- lifecycle ------------------------------------------------------------

    def reset(self) -> ResetInfo:
        docker_cfg = self.config.get("docker", {})
        build_timeout = float(docker_cfg.get("build_timeout_sec", 3600))
        self.image_tag = build_image(self.task, build_timeout)
        workdir = self._image_workdir(self.image_tag)

        # Initial passing-test count, measured in a throwaway container so the
        # agent's container never sees injected tests or verifier tooling.
        probe = self._start_container(workdir, suffix="probe")
        try:
            initial = run_verifier(probe, self.task)
        finally:
            probe.remove()

        self.container = self._start_container(workdir, suffix="agent")
        self._snapshot_workdir()
        self._protected_state_at_reset = self._protected_container_state()
        self.container.set_network(False)  # no network while the agent acts

        self.reset_info = ResetInfo(
            task_name=self.task.name,
            instruction=self.task.instruction,
            workdir=workdir,
            image_tag=self.image_tag,
            task_hash=self.task.task_hash,
            seed=self.seed,
            initial_passed=initial.passed if initial.runnable else 0,
            total_tests=initial.total if initial.runnable else 0,
            initial_verifier_runnable=initial.runnable,
        )
        return self.reset_info

    def exec(self, command: str) -> Observation:
        """The step primitive: one bash command from the agent."""
        if self.container is None:
            raise EnvFailure("exec() before reset()")
        agent_cfg = self.config.get("agent", {})
        timeout = float(agent_cfg.get("command_timeout_sec", 120))
        cap = int(agent_cfg.get("max_output_chars", 8000))
        res = self.container.exec_root(command, cwd=self.container.workdir, timeout=timeout)
        out, err = res.stdout, res.stderr
        truncated = len(out) > cap or len(err) > cap
        return Observation(
            stdout=out[:cap], stderr=err[:cap], exit_code=res.exit_code,
            cwd=self.container.workdir, truncated=truncated, elapsed_ms=res.elapsed_ms,
        )

    def verify(self) -> VerifierResult:
        if self.container is None:
            raise EnvFailure("verify() before reset()")
        self.container.set_network(True)  # agent is done acting; test.sh needs network
        return run_verifier(self.container, self.task)

    def snapshot_diff(self) -> DiffSummary:
        """Diff the container workdir now vs the snapshot taken at reset.

        changed_files is computed by comparing the two trees in Python:
        parsing `git diff --name-only` output silently drops deletions (git
        prints /dev/null for the removed side) and mangles non-ASCII names
        with quote-escaping. git is used only for the human-readable diff
        text."""
        if self.container is None or self._snapshot_dir is None:
            raise EnvFailure("snapshot_diff() before reset()")
        current = Path(tempfile.mkdtemp(prefix="task2-cur-"))
        try:
            self.container.cp_out(self.container.workdir, current / "w")

            def tree_hashes(root: Path) -> dict[str, str]:
                out: dict[str, str] = {}
                for p in root.rglob("*"):
                    if p.is_file() and not p.is_symlink():
                        try:
                            digest = hashlib.sha256(p.read_bytes()).hexdigest()
                        except OSError:
                            digest = "<unreadable>"
                        out[str(p.relative_to(root))] = digest
                return out

            before = tree_hashes(self._snapshot_dir / "w")
            after = tree_hashes(current / "w")
            changed = sorted(
                {p for p in before if p not in after}          # deleted
                | {p for p in after if p not in before}        # added
                | {p for p in before.keys() & after.keys() if before[p] != after[p]}
            )

            proc_full = subprocess.run(
                ["git", "diff", "--no-index",
                 str(self._snapshot_dir / "w"), str(current / "w")],
                capture_output=True, text=True, errors="replace", timeout=120,
            )
            cap = 60000
            text = proc_full.stdout
            return DiffSummary(
                changed_files=changed,
                diff_text=text[:cap],
                truncated=len(text) > cap,
            )
        finally:
            shutil.rmtree(current, ignore_errors=True)

    def protected_container_state(self) -> dict[str, str]:
        """Current hash-state of protected in-container paths (for the tripwire)."""
        return self._protected_container_state()

    @property
    def protected_state_at_reset(self) -> dict[str, str]:
        return dict(self._protected_state_at_reset)

    def close(self) -> None:
        if self.container is not None:
            self.container.remove()
            self.container = None
        if self._snapshot_dir is not None:
            shutil.rmtree(self._snapshot_dir, ignore_errors=True)
            self._snapshot_dir = None

    # -- internals --------------------------------------------------------------

    def _start_container(self, workdir: str, suffix: str) -> Container:
        docker_cfg = self.config.get("docker", {})
        name = f"task2-{self.task.name}-{suffix}-{uuid.uuid4().hex[:8]}"
        _docker(
            ["run", "-d", "--name", name,
             "--cpus", str(docker_cfg.get("cpus", 2)),
             "--memory", str(docker_cfg.get("memory", "4g")),
             self.image_tag, "sleep", "infinity"],
            timeout=120, check=True,
        )
        return Container(name, workdir)

    def _image_workdir(self, tag: str) -> str:
        proc = _docker(["image", "inspect", "-f", "{{.Config.WorkingDir}}", tag],
                       timeout=30, check=True)
        wd = proc.stdout.strip()
        if not wd or wd == "/":
            raise EnvFailure(f"{self.task.name}: image has no usable WORKDIR")
        return wd

    def _snapshot_workdir(self) -> None:
        self._snapshot_dir = Path(tempfile.mkdtemp(prefix="task2-snap-"))
        self.container.cp_out(self.container.workdir, self._snapshot_dir / "w")

    def _protected_container_state(self) -> dict[str, str]:
        """sha256 of every file under protected in-container paths (/tests).

        In normal TB2 flow /tests does not exist during the attempt (tests are
        injected only at verification), so this is usually empty at reset; any
        difference later means the agent staged something there."""
        state: dict[str, str] = {}
        res = self.container.exec_root(
            "if [ -d /tests ]; then find /tests -type f -exec sha256sum {} \\; ; fi",
            timeout=60,
        )
        for line in res.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                state[parts[1]] = parts[0]
        return state
