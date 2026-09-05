"""Explicit tool-module discovery for startup/bootstrap wiring."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from antisentinel.domain.errors import InvalidInputError

from .manifest import ToolDefinition
from .registry import ToolRegistry


class ToolDiscovery:
    @staticmethod
    def discover(package_name: str = "antisentinel.tools") -> ToolRegistry:
        package = importlib.import_module(package_name)
        registry = ToolRegistry(auto_discover=False)
        prefix = f"{package_name}."
        module_infos = sorted(pkgutil.iter_modules(package.__path__), key=lambda info: info.name)
        for module_info in module_infos:
            if module_info.name.startswith("_"):
                continue
            module = importlib.import_module(f"{prefix}{module_info.name}")
            for definition in _definitions_from(module):
                try:
                    registry.register(definition)
                except InvalidInputError as exc:
                    raise InvalidInputError(
                        f"failed to register tool from {module.__name__}: {exc}"
                    ) from exc
        return registry


def _definitions_from(module: ModuleType) -> list[ToolDefinition]:
    has_single = hasattr(module, "TOOL")
    has_many = hasattr(module, "TOOLS")
    if has_single and has_many:
        raise InvalidInputError(f"module {module.__name__} must export TOOL or TOOLS, not both")
    if has_single:
        definitions = [module.TOOL]
    elif has_many:
        definitions = list(module.TOOLS)
    else:
        return [
            value
            for value in vars(module).values()
            if isinstance(value, ToolDefinition)
            and getattr(value.handler, "__module__", None) == module.__name__
        ]
    if any(not isinstance(item, ToolDefinition) for item in definitions):
        raise InvalidInputError(f"module {module.__name__} exports a non-ToolDefinition value")
    return definitions
