"""Conservative static relations for one Python snapshot."""

from __future__ import annotations

import ast
import hashlib

from .models import CodeEdge


class RelationResolver:
    def resolve(self, files, snapshot_id: str) -> tuple[CodeEdge, ...]:
        symbols = {symbol.qualified_name: symbol for parsed in files for symbol in parsed.symbols}
        edges: list[CodeEdge] = []
        for parsed in files:
            tree = ast.parse(parsed.data.decode(parsed.encoding), filename=parsed.path)
            aliases = {}
            for statement in tree.body:
                if isinstance(statement, ast.ImportFrom) and statement.module:
                    for imported in statement.names:
                        if imported.name != "*" and imported.name in symbols:
                            aliases[imported.asname or imported.name] = symbols[imported.name]
            owner = _Owner(symbols)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    source = symbols.get(node.name)
                    if source:
                        for base in node.bases:
                            if isinstance(base, ast.Name) and base.id in symbols:
                                edges.append(_edge(snapshot_id, source.node_id, "inherits", symbols[base.id].node_id, None, "resolved", "ast_base"))
                if isinstance(node, ast.Call):
                    source = owner.for_line(node.lineno)
                    if source is None:
                        continue
                    if isinstance(node.func, ast.Name) and (node.func.id in symbols or node.func.id in aliases):
                        target = symbols.get(node.func.id) or aliases[node.func.id]
                        basis = "same_module" if node.func.id in symbols else "explicit_import_alias"
                        edges.append(_edge(snapshot_id, source.node_id, "calls", target.node_id, None, "resolved", basis))
                    else:
                        edges.append(_edge(snapshot_id, source.node_id, "calls", None, ast.unparse(node.func), "unresolved", "dynamic_or_unknown"))
        return tuple(edges)


class _Owner:
    def __init__(self, symbols):
        self.symbols = list(symbols.values())

    def for_line(self, line: int):
        candidates = [item for item in self.symbols if item.kind in {"function", "async_function"} and item.start_line <= line <= item.end_line]
        return min(candidates, key=lambda item: item.end_line - item.start_line) if candidates else None


def _edge(snapshot_id, source_node_id, relation, target_node_id, expression, resolution, basis):
    identity = f"{snapshot_id}|{source_node_id}|{relation}|{target_node_id or expression}|{resolution}"
    return CodeEdge(hashlib.sha256(identity.encode()).hexdigest(), snapshot_id, source_node_id, relation, target_node_id, expression, resolution=resolution, basis=basis)
