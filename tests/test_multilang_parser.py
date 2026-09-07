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

def test_supported_language_registry_is_shared():
    from antisentinel.code_map.languages import is_supported_path
    assert all(is_supported_path('x'+ext) for ext in ('.py','.go','.ts','.java','.kt','.rs','.cpp'))

def test_string_tokens_do_not_create_calls():
    from antisentinel.code_map.multilang_parser import MultiLanguageParser
    p=MultiLanguageParser('multilang-tree-sitter-v1')
    files=(p.parse_file('charge.ts', b'function charge() {}\n', 's'), p.parse_file('show.ts', b'function show() { console.log("charge"); }\n', 's'))
    edges=p.resolve_edges(files,'s')
    assert not any(e.relation=='calls' and e.target_node_id == files[0].symbols[1].node_id for e in edges)

def test_call_parser_failure_is_empty_mapping(monkeypatch):
    import antisentinel.code_map.multilang_parser as m
    monkeypatch.setattr(m, 'language_for_path', lambda _: 'typescript')
    assert isinstance(m._tree_sitter_calls_by_line('x.ts', b'bad'), dict)

def test_parameter_shadowing_does_not_resolve_cross_file_call():
    from antisentinel.code_map.multilang_parser import MultiLanguageParser
    p=MultiLanguageParser('multilang-tree-sitter-v1')
    a=p.parse_file('a.ts', b'function charge() {}\n','s')
    b=p.parse_file('b.ts', b'function caller(charge: () => void) {\n charge();\n}\n','s')
    edges=p.resolve_edges((a,b),'s')
    assert not any(e.relation=='calls' and e.target_node_id == a.symbols[1].node_id for e in edges)
