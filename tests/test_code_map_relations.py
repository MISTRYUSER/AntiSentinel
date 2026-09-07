def test_relation_resolver_records_unique_same_module_calls_and_inheritance_but_not_dynamic_calls():
    from antisentinel.code_map.python_parser import PythonAstParser
    from antisentinel.code_map.relations import RelationResolver

    source = b"""def target():
    return 1

def caller():
    return target()

class Base:
    pass

class Child(Base):
    def dynamic(self, value):
        return getattr(value, 'run')()
"""
    parser = PythonAstParser("python-ast-v1")
    parsed = parser.parse_file("app.py", source, "snapshot-a")
    edges = RelationResolver().resolve((parsed,), "snapshot-a")
    by_relation = {edge.relation: [] for edge in edges}
    for edge in edges:
        by_relation.setdefault(edge.relation, []).append(edge)
    symbols = {symbol.qualified_name: symbol.node_id for symbol in parsed.symbols}

    assert any(edge.source_node_id == symbols["caller"] and edge.target_node_id == symbols["target"] and edge.resolution == "resolved" for edge in by_relation["calls"])
    assert any(edge.source_node_id == symbols["Child"] and edge.target_node_id == symbols["Base"] for edge in by_relation["inherits"])
    assert any(edge.resolution == "unresolved" and "getattr" in (edge.unresolved_expression or "") for edge in by_relation["calls"])


def test_relation_resolver_resolves_explicit_import_alias_across_files():
    from antisentinel.code_map.python_parser import PythonAstParser
    from antisentinel.code_map.relations import RelationResolver

    parser = PythonAstParser("python-ast-v1")
    dependency = parser.parse_file("dep.py", b"def target():\n    return 1\n", "snapshot-a")
    caller = parser.parse_file("app.py", b"from dep import target as alias\n\ndef caller():\n    return alias()\n", "snapshot-a")
    edges = RelationResolver().resolve((dependency, caller), "snapshot-a")
    symbols = {symbol.qualified_name: symbol.node_id for parsed in (dependency, caller) for symbol in parsed.symbols}

    assert any(edge.relation == "calls" and edge.source_node_id == symbols["caller"] and edge.target_node_id == symbols["target"] and edge.resolution == "resolved" for edge in edges)


def test_relation_resolver_marks_test_call_and_import_evidence():
    from antisentinel.code_map.python_parser import PythonAstParser
    from antisentinel.code_map.relations import RelationResolver

    parser = PythonAstParser("python-ast-v1")
    implementation = parser.parse_file("service.py", b"def target():\n    return 1\n", "snapshot-a")
    test_file = parser.parse_file("test_service.py", b"from service import target\n\ndef test_target():\n    assert target() == 1\n", "snapshot-a")
    edges = RelationResolver().resolve((implementation, test_file), "snapshot-a")
    symbols = {symbol.qualified_name: symbol.node_id for parsed in (implementation, test_file) for symbol in parsed.symbols}

    assert any(edge.relation == "imports" and edge.source_node_id == symbols["test_target"] and edge.target_node_id == symbols["target"] for edge in edges)
    assert any(edge.relation == "tested_by" and edge.source_node_id == symbols["target"] and edge.target_node_id == symbols["test_target"] for edge in edges)
