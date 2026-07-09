"""Agents: the thin bash agent (Anthropic API), the oracle runner, and
scripted baselines. Deliberately minimal — no agent framework.

Contract with the controller: the controller drives the loop; an agent only
answers "what next?" via next_action(), given the observation of its previous
command. Actions are single bash commands; an agent signals completion by
returning done=True.
"""

from __future__ import annotations

from dataclasses import dataclass

from task2.environment import Environment, Observation
from task2.tasks import Task


@dataclass
class AgentAction:
    thought: str = ""
    command: str | None = None
    done: bool = False
    final_message: str = ""


class ScriptedAgent:
    """Replays a fixed command list. Used for the oracle and weak baselines."""

    source = "scripted"
    model_id: str | None = None

    def __init__(self, commands: list[str]):
        self.commands = list(commands)
        self._i = 0

    def setup(self, env: Environment, task: Task) -> None:
        pass

    def next_action(self, last_obs: Observation | None) -> AgentAction:
        if self._i >= len(self.commands):
            return AgentAction(done=True, final_message="script finished")
        cmd = self.commands[self._i]
        self._i += 1
        return AgentAction(command=cmd)

    @property
    def total_output_tokens(self) -> int:
        return 0


class OracleAgent(ScriptedAgent):
    """Runs the task's official solution/solve.sh — the guaranteed-pass attempt."""

    source = "oracle"

    def __init__(self):
        super().__init__(["bash /solution/solve.sh"])

    def setup(self, env: Environment, task: Task) -> None:
        env.container.exec_root("mkdir -p /solution")
        env.container.cp_in(task.solution_script, "/solution/solve.sh")


BASH_TOOL = {
    "name": "bash",
    "description": "Run one bash command in the task workspace and get back "
                   "stdout, stderr, and the exit code.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to run."},
        },
        "required": ["command"],
    },
}

DONE_TOOL = {
    "name": "done",
    "description": "Declare the task finished. Call this once you believe the "
                   "task is solved (or you cannot make further progress).",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "What you did and why you are done."},
        },
        "required": ["summary"],
    },
}

SYSTEM_PROMPT = """You are a software engineering agent working alone in a Linux container.
Solve the task described by the user. Rules:
- Use the bash tool to run ONE command at a time; inspect output before the next step.
- The container has NO network access. Do not try to download anything.
- Work in the current directory unless the task says otherwise.
- Modifying or deleting official tests or verifier files is forbidden and detected.
- You have a limited budget of {max_steps} commands. Be deliberate.
- When the task is solved (or you are stuck), call the done tool with a summary."""


class ClaudeBashAgent:
    """Thin tool-use loop on the Anthropic API. One bash tool, one done tool."""

    source = "agent"

    def __init__(self, model_id: str, max_steps: int, temperature: float,
                 max_tokens_per_call: int):
        import anthropic

        self.model_id = model_id
        self.max_steps = max_steps
        self.temperature = temperature
        self.max_tokens_per_call = max_tokens_per_call
        # Transient 429/5xx/529-overloaded must not kill an attempt mid-flight:
        # that is an infrastructure failure, not agent behavior.
        self.client = anthropic.Anthropic(max_retries=5)
        self.messages: list[dict] = []
        self.total_output_tokens = 0
        self._pending_tool_use_id: str | None = None
        # If the model emits parallel tool calls, only the first is executed;
        # the rest must still receive tool_results or the next API call 400s.
        self._extra_tool_use_ids: list[str] = []

    def setup(self, env: Environment, task: Task) -> None:
        self.messages = [{"role": "user", "content": f"Task:\n{task.instruction}"}]
        self._system = SYSTEM_PROMPT.format(max_steps=self.max_steps)

    def next_action(self, last_obs: Observation | None) -> AgentAction:
        if last_obs is not None and self._pending_tool_use_id is not None:
            result = (
                f"exit_code: {last_obs.exit_code}\n"
                f"stdout:\n{last_obs.stdout}\n"
                f"stderr:\n{last_obs.stderr}"
                + ("\n[output truncated]" if last_obs.truncated else "")
            )
            content = [{
                "type": "tool_result",
                "tool_use_id": self._pending_tool_use_id,
                "content": result,
            }]
            content += [{
                "type": "tool_result",
                "tool_use_id": extra_id,
                "content": "harness: one tool call per turn; this call was not executed",
            } for extra_id in self._extra_tool_use_ids]
            self.messages.append({"role": "user", "content": content})
            self._pending_tool_use_id = None
            self._extra_tool_use_ids = []

        response = self.client.messages.create(
            model=self.model_id,
            system=self._system,
            messages=self.messages,
            tools=[BASH_TOOL, DONE_TOOL],
            max_tokens=self.max_tokens_per_call,
            temperature=self.temperature,
        )
        self.total_output_tokens += response.usage.output_tokens
        self.messages.append({"role": "assistant", "content": response.content})

        thought = " ".join(
            b.text.strip() for b in response.content if b.type == "text"
        ).strip()
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        tool_use = tool_uses[0] if tool_uses else None
        self._extra_tool_use_ids = [b.id for b in tool_uses[1:]]

        if tool_use is None:
            # Model answered in prose without calling a tool: treat as done.
            return AgentAction(thought=thought, done=True,
                               final_message=thought or "(no tool call)")
        if tool_use.name == "done":
            return AgentAction(thought=thought, done=True,
                               final_message=str(tool_use.input.get("summary", "")))
        self._pending_tool_use_id = tool_use.id
        return AgentAction(thought=thought, command=str(tool_use.input.get("command", "")))
