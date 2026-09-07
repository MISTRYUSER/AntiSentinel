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
