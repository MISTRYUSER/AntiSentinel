"""Registration must never grant partial packages or read skill bodies."""
import importlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from antisentinel.tools.manifest import ToolDefinition
from antisentinel.tools.registry import ToolRegistry


def api():
    name = "antisentinel.capabilities.bootstrap"
    assert importlib.util.find_spec(name) is not None, "skill catalog assembly is missing"
    return importlib.import_module(name)


def package(root, *, plugin_id="local", tools=(), skills=2):
    root.mkdir(parents=True)
    entries = []
    for index in range(skills):
        name = ("diagnosis", "runbook")[index]
        (root / f"{name}.md").write_text("PRIVATE BODY NOT FOR REGISTRATION")
        entries.append({
            "skill_id": f"{plugin_id}/{name}", "version": "1.0.0", "name": name,
            "description": f"Use {name}", "use_cases": ["health check"],
            "instructions_path": f"{name}.md", "required_tools": list(tools),
            "references": [],
        })
    value = {"schema_version": 1, "plugin_id": plugin_id, "version": "1.0.0",
             "skills": entries, "tool_exports": []}
    write_manifest(root, value)
    return value


def write_manifest(root, value):
    (root / "plugin.json").write_text(json.dumps(value))


def registry(*, name="health", read_only=True, requires_approval=False):
    result = ToolRegistry(auto_discover=False)
    result.register(ToolDefinition(name, "probe", {"type": "object"}, lambda args: {"ok": True},
                                   read_only=read_only, requires_approval=requires_approval))
    return result


def test_valid_catalog_does_not_read_bodies(tmp_path, monkeypatch):
    a = api()
    root = tmp_path / "pkg"
    package(root, tools=["health"])
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.suffix != ".md", "registration opened skill instructions"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    result = a.assemble_plugins([a.PluginConfig(root)], registry())
    assert [s.skill_id for s in result.skills.list()] == ["local/diagnosis", "local/runbook"]
    assert result.skills.get("local/unknown") is None
    assert not result.diagnostics
    assert "PRIVATE BODY" not in json.dumps(result.to_dict())
    assert result.to_dict()["executable"] is False  # full resource lock arrives in 4.2


@pytest.mark.parametrize("problem,code", [
    ("missing_tool", "missing_tool_dependency"), ("missing_resource", "resource_missing"),
    ("parent_path", "invalid_manifest"), ("absolute_path", "invalid_manifest"),
    ("extra_field", "invalid_manifest"), ("invalid_version", "invalid_manifest"),
    ("invalid_id", "invalid_manifest"), ("duplicate_skill", "invalid_manifest"),
    ("boolean_schema", "invalid_manifest"), ("too_large", "resource_too_large"),
    ("invalid_reference", "invalid_manifest"),
])
def test_invalid_optional_package_is_excluded_with_specific_diagnostic(tmp_path, problem, code):
    a = api()
    root = tmp_path / "pkg"
    value = package(root)
    skill = value["skills"][0]
    if problem == "missing_tool": skill["required_tools"] = ["missing"]
    elif problem == "missing_resource": (root / "diagnosis.md").unlink()
    elif problem == "parent_path": skill["instructions_path"] = "../outside.md"
    elif problem == "absolute_path": skill["instructions_path"] = str(root / "diagnosis.md")
    elif problem == "extra_field": value["unknown"] = True
    elif problem == "invalid_version": value["version"] = "latest"
    elif problem == "invalid_id": skill["skill_id"] = "other/diagnosis"
    elif problem == "duplicate_skill": value["skills"].append(dict(skill))
    elif problem == "boolean_schema": value["schema_version"] = True
    elif problem == "too_large": (root / "diagnosis.md").write_bytes(b"a" * (65536 + 1))
    elif problem == "invalid_reference": skill["references"] = [{"reference_id": "x", "path": "x.md"}]
    write_manifest(root, value)
    result = a.assemble_plugins([a.PluginConfig(root, required=False)], registry())
    assert result.skills.list() == ()
    assert result.diagnostics[0].code == code
    assert result.diagnostics[0].field
    assert result.diagnostics[0].message
    assert result.diagnostics[0].package_root == str(root)


def test_required_error_blocks_startup_with_diagnostics(tmp_path):
    a = api()
    root = tmp_path / "pkg"
    package(root, tools=["missing"])
    with pytest.raises(a.CatalogBuildError) as error:
        a.assemble_plugins([a.PluginConfig(root)], registry())
    assert error.value.diagnostics[0].code == "missing_tool_dependency"


def test_symbolic_link_resource_is_rejected(tmp_path):
    a = api()
    root = tmp_path / "pkg"
    package(root)
    target = root / "diagnosis.md"
    target.unlink()
    target.symlink_to(root / "runbook.md")
    result = a.assemble_plugins([a.PluginConfig(root, required=False)], registry())
    assert result.skills.list() == ()
    assert result.diagnostics[0].code == "invalid_manifest"


def test_nested_registered_resource_keeps_its_package_relative_path(tmp_path):
    a = api()
    root = tmp_path / "pkg"
    value = package(root)
    reference_dir = root / "references"
    reference_dir.mkdir()
    (reference_dir / "diagnosis.md").write_text("reference")
    value["skills"][0]["references"] = [{
        "reference_id": "diagnosis-guide", "description": "guide", "path": "references/diagnosis.md",
    }]
    write_manifest(root, value)
    result = a.assemble_plugins([a.PluginConfig(root)], registry())
    assert result.skills.get("local/diagnosis").references[0].path == "references/diagnosis.md"


@pytest.mark.parametrize("different_version", [False, True])
def test_duplicate_plugin_conflicts_reject_both_independent_of_order(tmp_path, different_version):
    a = api()
    roots = [tmp_path / "a", tmp_path / "b"]
    package(roots[0])
    value = package(roots[1])
    if different_version:
        value["version"] = "2.0.0"
        write_manifest(roots[1], value)
    configs = [a.PluginConfig(root, required=False) for root in roots]
    for ordered in (configs, list(reversed(configs))):
        result = a.assemble_plugins(ordered, registry())
        assert result.skills.list() == ()
        assert len(result.diagnostics) == 2
        assert all(d.code == "invalid_manifest" for d in result.diagnostics)


def test_bad_plugin_does_not_leak_exported_tools(tmp_path):
    a = api()
    root = tmp_path / "pkg"
    value = package(root, tools=["missing"])
    value["tool_exports"] = ["trusted.probe"]
    write_manifest(root, value)
    module = ModuleType("trusted.probe")
    module.TOOL = registry(name="private_probe").resolve("private_probe")
    result = a.assemble_plugins([a.PluginConfig(root, required=False,
                              trusted_tool_modules={"trusted.probe": module})], registry())
    assert result.tool_registry.resolve("private_probe") is None
    assert result.tool_registry.resolve("health") is not None


def test_untrusted_module_is_not_imported(tmp_path):
    a = api()
    root = tmp_path / "pkg"
    value = package(root)
    value["tool_exports"] = ["os"]
    write_manifest(root, value)
    result = a.assemble_plugins([a.PluginConfig(root, required=False)], registry())
    assert result.skills.list() == ()
    assert result.diagnostics[0].field == "tool_exports"


def test_explicit_tools_and_decorated_exports_reuse_discovery(tmp_path):
    a = api()
    root = tmp_path / "pkg"
    value = package(root, tools=["owned_probe"])
    value["tool_exports"] = ["trusted.probe"]
    write_manifest(root, value)
    module = ModuleType("trusted.probe")
    exec("from antisentinel.tools.manifest import tool\n@tool\ndef owned_probe(service: str): return service", module.__dict__)
    result = a.assemble_plugins([a.PluginConfig(root, trusted_tool_modules={"trusted.probe": module})], registry())
    assert result.tool_registry.resolve("owned_probe") is not None
    assert len(result.skills.list()) == 2


def test_catalog_is_defensively_frozen(tmp_path):
    a = api()
    root = tmp_path / "pkg"
    package(root)
    base = registry()
    result = a.assemble_plugins([a.PluginConfig(root)], base)
    base.resolve("health").argument_schema["required"] = ["injected"]
    result.tool_registry.resolve("health").argument_schema["required"] = ["mutated"]
    assert "required" not in result.tool_registry.resolve("health").argument_schema
    with pytest.raises(TypeError):
        result.tool_registry.register(base.resolve("health"))
    assert len(result.snapshot_id) == 64


def test_snapshot_is_independent_of_package_order(tmp_path):
    a = api()
    roots = [tmp_path / "a", tmp_path / "b"]
    package(roots[0], plugin_id="alpha")
    package(roots[1], plugin_id="beta")
    configs = [a.PluginConfig(root) for root in roots]
    assert a.assemble_plugins(configs, registry()).snapshot_id == a.assemble_plugins(configs[::-1], registry()).snapshot_id


def test_duplicate_json_keys_are_rejected(tmp_path):
    a = api()
    root = tmp_path / "pkg"
    package(root)
    (root / "plugin.json").write_text('{"schema_version":1,"schema_version":1}')
    result = a.assemble_plugins([a.PluginConfig(root, required=False)], registry())
    assert result.diagnostics[0].code == "invalid_manifest"
    assert "duplicate" in result.diagnostics[0].message


@pytest.mark.parametrize("read_only,approval,allowed,disabled,expected", [
    (True, False, True, False, ()),
    (True, False, False, False, ("tool_not_allowed",)),
    (False, False, True, False, ("read_only_required",)),
    (True, True, True, False, ("approval_not_allowed",)),
    (True, False, True, True, ("skill_disabled",)),
])
def test_availability_filters_run_catalog(tmp_path, read_only, approval, allowed, disabled, expected):
    a = api()
    root = tmp_path / "pkg"
    package(root, tools=["health"])
    catalog = a.assemble_plugins([a.PluginConfig(root)], registry(read_only=read_only, requires_approval=approval))
    policy = a.RunPolicy(allowed_tools=frozenset(["health"] if allowed else []),
                         disabled_skills=frozenset(["local/diagnosis"] if disabled else []))
    outcome = a.check_availability(catalog, "local/diagnosis", policy)
    assert outcome.reason_codes == expected
    assert outcome.available is (not expected)
    visible = [s.skill_id for s in catalog.available_skills(policy)]
    assert ("local/diagnosis" in visible) is (not expected)
