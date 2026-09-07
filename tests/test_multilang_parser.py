def test_go_and_typescript_symbols_and_inheritance():
    from antisentinel.code_map.multilang_parser import MultiLanguageParser
    p=MultiLanguageParser('multilang-tree-sitter-v1')
    go=p.parse_file('main.go', b'type Child struct { Base }\nfunc (c Child) Run() {}\n', 's')
    assert {n.kind for n in go.symbols}=={'module','class','function'}
    ts=p.parse_file('main.ts', b'class Child extends Base {}\nfunction run() {}\n', 's')
    assert {n.kind for n in ts.symbols}=={'module','class','function'}
