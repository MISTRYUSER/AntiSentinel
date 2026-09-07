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
