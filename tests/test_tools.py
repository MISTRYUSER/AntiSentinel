import pytest

from antisentinel.domain.errors import InvalidInputError
from antisentinel.tools.manifest import ToolDefinition, tool
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.execution.tool_executor import ToolExecutor


def test_registry_discovers_explicit_tool_exports_from_package(tmp_path, monkeypatch):
    package = tmp_path / "dynamic_tool_package"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "_ignored.py").write_text("TOOL = object()", encoding="utf-8")
    (package / "health.py").write_text(
        "from antisentinel.tools.manifest import ToolDefinition\n"
        "def handler(arguments): return {'ok': True}\n"
        "TOOL = ToolDefinition(name='dynamic_health', description='health', "
        "argument_schema={'type': 'object'}, handler=handler)\n",
        encoding="utf-8",
    )
    (package / "storage.py").write_text(
        "from antisentinel.tools.manifest import ToolDefinition\n"
        "def handler(arguments): return {'ok': True}\n"
        "TOOLS = [ToolDefinition(name='dynamic_storage', description='storage', "
        "argument_schema={'type': 'object'}, handler=handler)]\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = ToolRegistry.discover("dynamic_tool_package")

    assert registry.resolve("dynamic_health") is not None
    assert registry.resolve("dynamic_storage") is not None
    assert registry.resolve("_ignored") is None


def test_registry_constructor_automatically_discovers_package_tools(tmp_path, monkeypatch):
    package = tmp_path / "automatic_tool_package"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "health.py").write_text(
        "from antisentinel.tools.manifest import ToolDefinition\n"
        "def handler(arguments): return {'ok': True}\n"
        "TOOL = ToolDefinition(name='automatic_health', description='health', "
        "argument_schema={'type': 'object'}, handler=handler)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = ToolRegistry(package_name="automatic_tool_package")

    assert registry.resolve("automatic_health") is not None


def test_tool_decorator_infers_schema_from_signature():
    @tool
    def inspect_service(service: str, limit: int = 20, verbose: bool = False):
        """Inspect one service."""
        return {"service": service}

    assert inspect_service.name == "inspect_service"
    assert inspect_service.description == "Inspect one service."
    assert inspect_service.argument_schema == {
        "type": "object",
        "properties": {
            "service": {"type": "string"},
            "limit": {"type": "integer"},
            "verbose": {"type": "boolean"},
        },
        "required": ["service"],
        "additionalProperties": False,
    }


def test_tool_decorator_rejects_untyped_variadic_arguments():
    with pytest.raises(InvalidInputError):
        tool(lambda *values: values)


def test_registry_discovers_decorated_tool_without_tool_constant(tmp_path, monkeypatch):
    package = tmp_path / "decorated_tool_package"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "health.py").write_text(
        "from antisentinel.tools.manifest import tool\n"
        "@tool\n"
        "def decorated_health(service: str):\n"
        "    return {'service': service}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = ToolRegistry.discover("decorated_tool_package")

    definition = registry.resolve("decorated_health")
    assert definition is not None
    assert definition.argument_schema["required"] == ["service"]


def test_registry_discovery_rejects_duplicate_tool_names(tmp_path, monkeypatch):
    package = tmp_path / "duplicate_tool_package"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    module = (
        "from antisentinel.tools.manifest import ToolDefinition\n"
        "def handler(arguments): return {}\n"
        "TOOL = ToolDefinition(name='duplicate', description='tool', "
        "argument_schema={'type': 'object'}, handler=handler)\n"
    )
    (package / "one.py").write_text(module, encoding="utf-8")
    (package / "two.py").write_text(module, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(InvalidInputError, match="duplicate_tool_package"):
        ToolRegistry.discover("duplicate_tool_package")


def make_tool(handler, *, requires_approval=False):
    return ToolDefinition(
        name="read_health",
        description="Read service health",
        argument_schema={
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": ["service"],
            "additionalProperties": False,
        },
        handler=handler,
        requires_approval=requires_approval,
    )


def test_registry_resolves_exact_names_and_exposes_manifest():
    registry = ToolRegistry()
    tool = make_tool(lambda arguments: {"ok": True})
    registry.register(tool)

    assert registry.resolve("read_health") is tool
    assert registry.resolve("READ_HEALTH") is None
    assert registry.manifests() == [
        {
            "name": "read_health",
            "description": "Read service health",
            "argument_schema": tool.argument_schema,
            "requires_approval": False,
            "read_only": True,
        }
    ]


def test_registry_rejects_duplicate_or_invalid_tools():
    registry = ToolRegistry()
    tool = make_tool(lambda arguments: {})
    registry.register(tool)
    with pytest.raises(InvalidInputError):
        registry.register(tool)


@pytest.mark.parametrize(
    "arguments",
    [{}, {"service": 1}, {"service": "api", "extra": True}],
)
def test_executor_rejects_invalid_arguments_without_calling_handler(arguments):
    calls = []
    registry = ToolRegistry()
    registry.register(make_tool(lambda value: calls.append(value)))

    result = ToolExecutor(registry).execute("read_health", arguments)

    assert result.status == "rejected"
    assert result.error is not None
    assert calls == []


def test_executor_rejects_unknown_tool_without_calling_handler():
    calls = []
    result = ToolExecutor(ToolRegistry()).execute("missing", {})

    assert result.status == "rejected"
    assert result.error == {"code": "unknown_tool", "message": "tool is not registered: missing"}
    assert calls == []


def test_executor_returns_approval_without_calling_handler():
    calls = []
    registry = ToolRegistry()
    registry.register(make_tool(lambda value: calls.append(value), requires_approval=True))

    result = ToolExecutor(registry).execute("read_health", {"service": "api"})

    assert result.status == "waiting_approval"
    assert result.error == {"code": "approval_required", "tool_name": "read_health"}
    assert calls == []


def test_executor_normalizes_success_and_handler_failure():
    registry = ToolRegistry()
    registry.register(make_tool(lambda value: {"status": "healthy", "raw": "secret"}))
    success = ToolExecutor(registry).execute("read_health", {"service": "api"})
    assert success.status == "succeeded"
    assert success.result["status"] == "healthy"

    def fail(value):
        raise RuntimeError("backend unavailable")

    failed_registry = ToolRegistry()
    failed_registry.register(make_tool(fail))
    failed = ToolExecutor(failed_registry).execute("read_health", {"service": "api"})
    assert failed.status == "failed"
    assert failed.error == {"code": "tool_execution_failed", "message": "backend unavailable"}
