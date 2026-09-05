"""BenchFlow session-factory agent backed by AntiSentinel RuntimeLoop."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import shlex
from typing import Any

from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.evaluation.skillsbench_adapter import TaskSandbox
from antisentinel.ports.model import ModelRequest
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.execution.tool_executor import ToolExecutionScope
from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine


ALLOWED_COMMANDS = frozenset({"bash", "sh", "python", "python3", "pip", "pip3", "pytest", "ls", "cat", "grep", "find", "sort", "head", "tail", "wc", "sed", "cp", "mv", "mkdir", "node", "npm", "npx", "java", "mvn", "gradle", "make", "gcc", "g++", "dot"})


class BenchmarkContextBuilder:
    def __init__(self) -> None:
        self._result_history: list[dict[str, Any]] = []

    def build(self, incident, session, turn, *, prior_turns, task_results, tools, memory_context=None, skill_context=None):
        messages: list[dict[str, Any]] = [{"role": "system", "content": "Complete the benchmark task inside the isolated workspace. Use sandbox tools to inspect, implement, test, and create every requested artifact. Do not merely describe file contents in the final response: write them with sandbox.write_file or sandbox.execute. Reuse prior results instead of repeating calls. Return the normal AntiSentinel final JSON only after the requested artifacts exist. Never access paths outside the workspace."}, {"role": "user", "content": incident.summary or incident.title}]
        if skill_context:
            messages.extend({"role": "skill", **item} for item in skill_context.get("active_skills", []))
        if task_results:
            self._result_history.extend(task_results)
        if self._result_history:
            messages.append({"role": "tool", "task_results": self._result_history})
        return ModelRequest(str(incident.incident_id), str(session.session_id), str(turn.turn_id), messages, tools)


@dataclass
class PreloadedBenchmarkSkills:
    skills: list[dict[str, Any]]

    def bind_registry(self, registry): return registry
    def visible_tool_names(self): return frozenset({"sandbox.read_file", "sandbox.write_file", "sandbox.execute"})
    def manifests(self, registry): return [item for item in registry.manifests() if item["name"] in self.visible_tool_names()]
    def context_payload(self): return {"active_skills": self.skills}
    def execution_scope(self): return ToolExecutionScope(self.visible_tool_names(), read_only_mode=False)
    def note_turn_results(self, successful_tool_names): return None
    def snapshot(self): return {"active_skills": [{"skill_id": item["skill_id"], "instruction_hash": item["instruction_hash"]} for item in self.skills]}
    def usage(self): return {"active_skill_ids": [item["skill_id"] for item in self.skills], "reference_hashes": {}}


class AntiSentinelSkillsBenchSession:
    def __init__(self, sandbox, *, exec_user=None):
        self.sandbox = sandbox
        self.exec_user = exec_user or "root"
        self.workspace = sandbox.agent_cwd or "/app"
        self.agent_env = sandbox.agent_env
        self.steps: list[dict[str, Any]] = []
        self.on_change = None
        self._cancelled = False

    async def prompt(self, text: str):
        loop = asyncio.get_running_loop()
        skills = await self._load_skills()

        def runner(request):
            future = asyncio.run_coroutine_threadsafe(self._sandbox_request(request), loop)
            return future.result(timeout=130)

        task_sandbox = TaskSandbox("skillsbench", "bound-by-benchflow", self.workspace, ALLOWED_COMMANDS, runner)
        registry = ToolRegistry(auto_discover=False)
        for definition in task_sandbox.tool_definitions(): registry.register(definition)
        base_url = self.agent_env["BENCHFLOW_PROVIDER_BASE_URL"].replace("host.docker.internal", "127.0.0.1")
        model = OpenAICompatibleModelAdapter(base_url=base_url, api_key=self.agent_env["BENCHFLOW_PROVIDER_API_KEY"], model=self.agent_env["BENCHFLOW_PROVIDER_MODEL"], timeout=120)
        runtime = PreloadedBenchmarkSkills(skills) if skills else None
        incident = Incident.create(title="SkillsBench task", source="benchflow", summary=text)
        session = Session.create(incident_id=incident.incident_id, participant_ids=["benchflow"])
        engine = RuntimeEngine(loop=__import__("antisentinel.worker.runtime.loop", fromlist=["RuntimeLoop"]).RuntimeLoop(context_builder=BenchmarkContextBuilder()))
        result = await asyncio.to_thread(engine.run, incident, session, model, registry=registry, config=RuntimeConfig(max_turns=16, max_tasks=8, max_tool_calls=8, max_total_tool_calls=48), skill_runtime=runtime)
        final_text = result.final.summary if result.final else str(result.error)
        self.steps.append({"type": "agent_message", "content": final_text, "status": result.status, "skill_usage": result.skill_usage})
        if self.on_change: self.on_change(self)
        from benchflow.acp.types import StopReason
        return StopReason.END_TURN

    async def cancel(self): self._cancelled = True
    def on_ask_user(self, handler): self._ask_user = handler

    async def _load_skills(self):
        command = f"find /skills {shlex.quote(self.workspace)}/skills -maxdepth 4 -type f -name SKILL.md -print 2>/dev/null || true"
        found = await self.sandbox.exec(command, user=self.exec_user, timeout_sec=20)
        paths = [line for line in str(getattr(found, "stdout", "") or "").splitlines() if line]
        skills = []
        for path in paths:
            result = await self.sandbox.exec(f"cat {shlex.quote(path)}", user=self.exec_user, timeout_sec=20)
            content = str(getattr(result, "stdout", "") or "")
            pure_path = PurePosixPath(path)
            skill_id = pure_path.parent.name or pure_path.stem
            skills.append({"skill_id": skill_id, "version": "skillsbench", "instruction_hash": hashlib.sha256(content.encode()).hexdigest(), "instructions": content, "references": []})
        return skills

    async def _sandbox_request(self, request):
        if request["op"] == "read":
            command = f"cat {shlex.quote(self.workspace + '/' + request['path'])}"
        elif request["op"] == "write":
            encoded = base64.b64encode(request["content"].encode()).decode()
            target = shlex.quote(self.workspace + "/" + request["path"])
            command = f"mkdir -p $(dirname {target}) && printf %s {shlex.quote(encoded)} | base64 -d > {target}"
        else:
            command = "cd " + shlex.quote(self.workspace) + " && " + " ".join(shlex.quote(item) for item in request["argv"])
        result = await self.sandbox.exec(command, user=self.exec_user, timeout_sec=120)
        return {"exit_code": int(getattr(result, "return_code", getattr(result, "exit_code", 0))), "stdout": str(getattr(result, "stdout", "") or "")[:60000], "stderr": str(getattr(result, "stderr", "") or "")[:4000]}


class AntiSentinelSkillsBenchAgent:
    def __init__(self, *, exec_user=None): self.exec_user = exec_user
    async def connect(self, sandbox, role): return AntiSentinelSkillsBenchSession(sandbox, exec_user=self.exec_user)
    def capabilities(self):
        from benchflow.agents.protocol import AgentCapabilities
        return AgentCapabilities(protocol="session-factory", nudges=True)


def build_agent(*, exec_user=None): return AntiSentinelSkillsBenchAgent(exec_user=exec_user)
