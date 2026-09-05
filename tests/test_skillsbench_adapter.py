from pathlib import Path


def test_task_sandbox_rejects_escape_and_unapproved_command(tmp_path):
    from antisentinel.evaluation.skillsbench_adapter import TaskSandbox

    seen = []
    sandbox = TaskSandbox("dialogue-parser", "container-1", "/app", frozenset({"python3"}), lambda args: seen.append(args) or {"stdout": "ok"})
    assert sandbox.read_file({"path": "../secret"}).error["code"] == "sandbox_path_escape"
    assert sandbox.execute({"argv": ["curl", "https://example.com"]}).error["code"] == "sandbox_command_not_allowed"
    assert sandbox.execute({"argv": ["python3", "solution.py"]}).status == "succeeded"
    assert seen[0]["argv"] == ["python3", "solution.py"]


def test_task_sandbox_tools_are_benchmark_only():
    from antisentinel.evaluation.skillsbench_adapter import TaskSandbox

    tools = TaskSandbox("task", "container", "/app", frozenset({"python3"}), lambda _: {}).tool_definitions()
    assert [tool.name for tool in tools] == ["sandbox.read_file", "sandbox.write_file", "sandbox.execute"]


def test_docker_runner_uses_no_shell(monkeypatch):
    from antisentinel.evaluation.skillsbench_adapter import DockerTaskRunner
    seen = {}
    class Result: returncode=0; stdout="ok"; stderr=""
    monkeypatch.setattr("subprocess.run", lambda command, **kwargs: (seen.update(command=command, kwargs=kwargs) or Result()))
    result = DockerTaskRunner()({"op": "execute", "container_id": "c1", "workspace": "/app", "argv": ["python3", "solution.py"]})
    assert seen["command"] == ["docker", "exec", "-w", "/app", "c1", "python3", "solution.py"]
    assert result["exit_code"] == 0
