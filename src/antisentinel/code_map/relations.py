"""Conservative file and lexical scope resolution."""
import ast
import hashlib
from collections import defaultdict
from pathlib import PurePosixPath
from .models import CodeEdge


def module_name(path):
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def bindings(body):
    result = defaultdict(list)
    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result[node.name].append(node)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                result[alias.asname or alias.name.split(".")[0]].append((node, alias))
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            result[node.id].append(None)
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                result[name].append(None)
        for child in ast.iter_child_nodes(node):
            visit(child)
    for node in body:
        visit(node)
    return result


class RelationResolver:
    def resolve(self, files, snapshot_id):
        modules = defaultdict(list)
        for parsed in files:
            if not parsed.errors:
                modules[module_name(parsed.path)].append(parsed)
        trees = {p.path: p.tree if p.tree is not None else ast.parse(p.data.decode(p.encoding)) for ps in modules.values() for p in ps}
        tables = {path: bindings(tree.body) for path, tree in trees.items()}
        edges = []
        for parsed in files:
            if parsed.errors:
                continue
            tree = trees[parsed.path]
            local_symbols = {(n.qualified_name, n.end_line): n for n in parsed.symbols}
            def external(module, name):
                candidates = modules.get(module, [])
                if len(candidates) != 1:
                    return None
                p = candidates[0]
                found = [n for n in p.symbols if n.qualified_name == name]
                return found[0] if len(found) == 1 and len(tables[p.path].get(name, [])) == 1 else None
            def resolve(expr, scopes):
                base = expr.id if isinstance(expr, ast.Name) else expr.value.id if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) else None
                for table, prefix in reversed(scopes):
                    values = table.get(base, [])
                    if not values:
                        continue
                    if len(values) != 1:
                        return None, "ambiguous_binding"
                    value = values[0]
                    if isinstance(value, tuple):
                        statement, alias = value
                        if isinstance(statement, ast.ImportFrom) and isinstance(expr, ast.Name) and alias.name != "*":
                            module = statement.module or ""
                            if statement.level:
                                package = module_name(parsed.path).split(".")
                                if not parsed.path.endswith("/__init__.py"):
                                    package.pop()
                                package = package[:len(package) - statement.level + 1]
                                module = ".".join(package + ([module] if module else []))
                            return external(module, alias.name), "explicit_import_alias"
                        if isinstance(statement, ast.Import) and isinstance(expr, ast.Attribute):
                            return external(alias.name, expr.attr), "module_attribute"
                    if isinstance(expr, ast.Name) and isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        return local_symbols.get((".".join(filter(None, (prefix, base))), value.end_lineno)), "lexical_binding"
                    return None, "shadowed_or_dynamic"
                return None, "dynamic_or_unknown"
            def emit(owner, relation, expr, scopes, site):
                target, basis = resolve(expr, scopes)
                edges.append(_edge(snapshot_id, owner.node_id, relation, target.node_id if target else None, ast.unparse(expr), basis, site))
                if target and relation == "calls":
                    if basis == "explicit_import_alias":
                        edges.append(_edge(snapshot_id, owner.node_id, "imports", target.node_id, None, basis, site))
                    if PurePosixPath(parsed.path).name.startswith("test_") or "tests" in PurePosixPath(parsed.path).parts:
                        edges.append(_edge(snapshot_id, target.node_id, "tested_by", owner.node_id, None, "static_test_call", site))
            def visit(node, scopes, owner=None, prefix=""):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    qualified = ".".join(filter(None, (prefix, node.name)))
                    current = local_symbols.get((qualified, node.end_lineno))
                    if current is None:
                        return
                    if isinstance(node, ast.ClassDef):
                        for base in node.bases:
                            emit(current, "inherits", base, scopes, base)
                    table = bindings(node.body)
                    if hasattr(node, "args"):
                        args = node.args
                        for arg in [*args.args, *args.posonlyargs, *args.kwonlyargs, args.vararg, args.kwarg]:
                            if arg:
                                table[arg.arg].append(None)
                    nested = scopes if isinstance(node, ast.ClassDef) else [*scopes, (table, qualified)]
                    for child in node.body:
                        visit(child, nested, current, qualified)
                    return
                if isinstance(node, ast.Call) and owner:
                    emit(owner, "calls", node.func, scopes, node)
                for child in ast.iter_child_nodes(node):
                    visit(child, scopes, owner, prefix)
            visit(tree, [(tables[parsed.path], "")])
        return tuple(sorted(edges, key=lambda e: (e.call_start_line or 0, e.edge_id)))


def _edge(snapshot, source, relation, target, expression, basis, site):
    identity = f"{snapshot}|{source}|{relation}|{target}|{expression}|{site.lineno}|{site.col_offset}"
    return CodeEdge(hashlib.sha256(identity.encode()).hexdigest(), snapshot, source, relation, target,
                    expression if target is None else None, site.lineno, site.end_lineno,
                    "resolved" if target else "unresolved", basis)
