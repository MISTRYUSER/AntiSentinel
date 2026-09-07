import hashlib


def test_parser_extracts_decorated_async_nested_and_method_symbols_with_contains_edges():
    from antisentinel.code_map.python_parser import PythonAstParser

    source = b"""@decorator
async def outer():
    def nested():
        return 1
    return nested()

class Service:
    def run(self):
        return outer()
"""
    parser = PythonAstParser("python-3.13/ast-v1")
    parsed = parser.parse_file("pkg/service.py", source, "snapshot-1")

    symbols = {symbol.qualified_name: symbol for symbol in parsed.symbols}
    assert set(symbols) == {"outer", "outer.nested", "Service", "Service.run"}
    assert symbols["outer"].kind == "async_function"
    assert symbols["outer"].start_line == 1
    assert symbols["Service.run"].start_line == 8
    edges = parser.contains_edges((parsed,), "snapshot-1")
    assert {(edge.source_node_id, edge.target_node_id, edge.relation) for edge in edges} == {
        (symbols["outer"].node_id, symbols["outer.nested"].node_id, "contains"),
        (symbols["Service"].node_id, symbols["Service.run"].node_id, "contains"),
    }


def test_parser_preserves_crlf_bytes_and_splits_long_single_line_chunks():
    from antisentinel.code_map.python_parser import PythonAstParser

    source = b"def f():\r\n    value = '" + (b"x" * 40) + b"'\r\n    return value\r\n"
    parser = PythonAstParser("python-3.13/ast-v1", max_chunk_bytes=24, max_chunk_lines=2)
    parsed = parser.parse_file("pkg/a.py", source, "snapshot-1")

    assert parsed.errors == ()
    assert len(parsed.chunks) >= 2
    assert sum(chunk.byte_end - chunk.byte_start for chunk in parsed.chunks) >= len(source)
    assert any(chunk.partial_line for chunk in parsed.chunks)
    assert parsed.file_hash == hashlib.sha256(source).hexdigest()


def test_parser_reports_syntax_error_without_importing_source():
    from antisentinel.code_map.python_parser import PythonAstParser

    parsed = PythonAstParser("python-3.13/ast-v1").parse_file("pkg/broken.py", b"def broken(:\n", "snapshot-1")

    assert parsed.symbols == ()
    assert parsed.chunks == ()
    assert parsed.errors == ("syntax_error",)
