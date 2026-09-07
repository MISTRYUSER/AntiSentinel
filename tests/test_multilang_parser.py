def test_go_and_typescript_symbols_and_inheritance():
    from antisentinel.code_map.multilang_parser import MultiLanguageParser
    p=MultiLanguageParser('multilang-tree-sitter-v1')
    go=p.parse_file('main.go', b'type Child struct { Base }\nfunc (c Child) Run() {}\n', 's')
    assert {n.kind for n in go.symbols}=={'module','class','function'}
    ts=p.parse_file('main.ts', b'class Child extends Base {}\nfunction run() {}\n', 's')
    assert {n.kind for n in ts.symbols}=={'module','class','function'}

def test_calls_are_attributed_to_enclosing_function():
    from antisentinel.code_map.multilang_parser import MultiLanguageParser
    p=MultiLanguageParser('multilang-tree-sitter-v1')
    a=p.parse_file('a.ts', b'function target() {}\nfunction caller() {\n target();\n}\n', 's')
    edges=p.resolve_edges((a,), 's')
    assert any(e.relation=='calls' and e.source_node_id == a.symbols[2].node_id for e in edges)

def test_go_typescript_java_relations_are_resolved_or_explicitly_unresolved():
    from antisentinel.code_map.multilang_parser import MultiLanguageParser
    p=MultiLanguageParser('multilang-tree-sitter-v1')
    files=(p.parse_file('go.go', b'type Base struct{}\ntype Child struct { Base }\nfunc Run(){ Child{} }\n','s'), p.parse_file('web.ts', b'class Base {}\nclass Child extends Base {}\nfunction run(){ Child(); }\n','s'), p.parse_file('J.java', b'class Base {}\nclass Child extends Base {}\n','s'))
    edges=p.contains_edges(files,'s')+p.resolve_edges(files,'s')
    assert sum(e.relation=='contains' for e in edges) >= 3
    assert any(e.relation=='inherits' and e.resolution=='resolved' for e in edges)
    assert any(e.relation=='calls' for e in edges)
