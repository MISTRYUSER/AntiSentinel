"""Benchmark-only Docker task workspace tools for SkillsBench adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import subprocess
from typing import Any, Callable

from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult


@dataclass(frozen=True)
class DockerTaskRunner:
    """No-shell Docker bridge; container identity comes from BenchFlow, never model input."""

    timeout_seconds: int = 120

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        container = request["container_id"]
        workspace = request["workspace"]
        if request["op"] == "read":
            command = ["docker", "exec", container, "cat", f"{workspace}/{request['path']}"]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout_seconds)
        elif request["op"] == "write":
            command = ["docker", "exec", "-i", container, "tee", f"{workspace}/{request['path']}"]
            completed = subprocess.run(command, input=request["content"], capture_output=True, text=True, timeout=self.timeout_seconds)
        else:
            command = ["docker", "exec", "-w", workspace, container, *request["argv"]]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout_seconds)
        return {"exit_code": completed.returncode, "stdout": completed.stdout[:16000], "stderr": completed.stderr[:4000]}


@dataclass(frozen=True)
class TaskSandbox:
    task_id: str
    container_id: str
    workspace: str
    allowed_commands: frozenset[str]
    runner: Callable[[dict[str, Any]], Any]

    def tool_definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition("sandbox.read_file", "Read a file inside the bound benchmark workspace.", {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}, self.read_file),
            ToolDefinition("sandbox.write_file", "Write a file inside the bound benchmark workspace.", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}, self.write_file, read_only=False),
            ToolDefinition("sandbox.execute", "Execute an allowlisted command inside the bound benchmark workspace.", {"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}}}, "required": ["argv"], "additionalProperties": False}, self.execute, read_only=False),
        ]

    def read_file(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        path = self._path(arguments["path"])
        if path is None:
            return self._reject("sandbox_path_escape", "path escapes task workspace")
        return self._result(self.runner({"op": "read", "container_id": self.container_id, "workspace": self.workspace, "path": path}))

    def write_file(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        path = self._path(arguments["path"])
        if path is None:
            return self._reject("sandbox_path_escape", "path escapes task workspace")
        return self._result(self.runner({"op": "write", "container_id": self.container_id, "workspace": self.workspace, "path": path, "content": arguments["content"]}))

    def execute(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        argv = arguments["argv"]
        if not isinstance(argv, list) or not argv or not isinstance(argv[0], str) or argv[0] not in self.allowed_commands:
            return self._reject("sandbox_command_not_allowed", "command is not allowlisted for this task")
        if any(not isinstance(item, str) for item in argv):
            return self._reject("invalid_arguments", "argv must contain strings")
        return self._result(self.runner({"op": "execute", "container_id": self.container_id, "workspace": self.workspace, "argv": list(argv)}))

    def _path(self, value: str) -> str | None:
        path = PurePosixPath(value)
        if path.is_absolute():
            try:
                path = path.relative_to(PurePosixPath(self.workspace))
            except ValueError:
                return None
        if ".." in path.parts or not path.parts:
            return None
        return str(path)

    @staticmethod
    def _result(value: Any) -> ToolExecutionResult:
        if isinstance(value, dict) and int(value.get("exit_code", 0)) != 0:
            return ToolExecutionResult(status="failed", error={"code": "sandbox_command_failed", "message": str(value.get("stderr") or "command failed")[:1000]}, result_summary=str(value)[:1000])
        return ToolExecutionResult(status="succeeded", result=value, result_summary=str(value)[:60000])

    @staticmethod
    def _reject(code: str, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(status="rejected", error={"code": code, "message": message}, result_summary=message)
