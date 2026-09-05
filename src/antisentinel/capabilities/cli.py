"""Developer-only metadata validation and catalog inspection commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from antisentinel.tools.registry import ToolRegistry

from .bootstrap import CatalogBuildError, PluginConfig, assemble_plugins
from .loader import SkillLoadError, SkillLoader
from .package import PackageError, build_local_package, open_published_package


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="antisentinel-capabilities")
    parser.add_argument("command", choices=("validate", "inspect", "pack"))
    parser.add_argument("--root")
    parser.add_argument("--output")
    parser.add_argument("--release")
    parser.add_argument("--skill-id")
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            if not args.root or not args.output:
                return _error("invalid_input", "pack requires --root and --output")
            published = build_local_package(Path(args.root), Path(args.output))
            print(json.dumps({"release_id": published.release_id, "root": str(published.root),
                              "resource_hashes": published.resource_hashes, "lock": published.lock},
                             ensure_ascii=False, sort_keys=True))
            return 0
        if args.release:
            package = open_published_package(Path(args.release))
            if args.command != "inspect" or not args.skill_id:
                return _error("invalid_input", "--release requires inspect with --skill-id")
            loaded = SkillLoader(package).load(args.skill_id)
            print(json.dumps({"skill_id": loaded.skill_id, "version": loaded.version,
                              "release_id": loaded.release_id, "instruction": loaded.instruction,
                              "instruction_hash": loaded.instruction_hash,
                              "references": [item.__dict__ for item in loaded.references]}, ensure_ascii=False, sort_keys=True))
            return 0
        if not args.root:
            return _error("invalid_input", "validate and metadata inspect require --root")
        catalog = assemble_plugins([PluginConfig(Path(args.root), required=False)], ToolRegistry(auto_discover=False))
        print(json.dumps(catalog.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0 if not catalog.diagnostics else 2
    except (CatalogBuildError, PackageError, SkillLoadError) as exc:
        if isinstance(exc, CatalogBuildError):
            print(json.dumps({"diagnostics": [item.to_dict() for item in exc.diagnostics]}, ensure_ascii=False, sort_keys=True))
            return 2
        return _error(exc.code, str(exc))


def _error(code: str, message: str) -> int:
    print(json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
