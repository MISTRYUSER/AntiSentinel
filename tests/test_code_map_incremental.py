def test_ast_fact_cache_is_bound_to_blob_path_parser_and_rules():
    from antisentinel.code_map.incremental import AstFactCache, AstFactKey

    cache = AstFactCache()
    key = AstFactKey("blob-a", "app.py", "", "parser-v1", "rules-v1")
    cache.put(key, {"symbols": ["run"]})

    assert cache.get(key) == {"symbols": ["run"]}
    assert cache.get(AstFactKey("blob-b", "app.py", "", "parser-v1", "rules-v1")) is None
    assert cache.get(AstFactKey("blob-a", "app.py", "", "parser-v2", "rules-v1")) is None
    assert cache.get(AstFactKey("blob-a", "app.py", "", "parser-v1", "rules-v2")) is None


def test_snapshot_builder_reuses_cached_file_facts_without_reparsing(tmp_path):
    from antisentinel.code_map.incremental import AstFactCache
    from antisentinel.code_map.python_parser import PythonAstParser
    from antisentinel.code_map.snapshot_builder import SnapshotBuilder
    from antisentinel.code_map.store import JobLease
    from datetime import datetime, timezone

    class Entry:
        included = True
        path = "app.py"
        object_id = "object-a"

    class Reader:
        def list_tree(self, commit, budget): return (Entry(),)
        def read_blob(self, commit, object_id, max_bytes): return b"def run():\n    return 1\n"

    class CountingParser(PythonAstParser):
        calls = 0
        def parse_file(self, path, data, node_scope):
            self.calls += 1
            return super().parse_file(path, data, node_scope)

    parser = CountingParser("python-ast-v1")
    builder = SnapshotBuilder(Reader(), parser=parser, fact_cache=AstFactCache())
    lease = JobLease("job", "repo", "a" * 40, "python-ast-v1", "rules", "worker", "token", datetime(2026, 9, 7, tzinfo=timezone.utc), 1)

    first = builder.build(lease)
    second = builder.build(lease)

    assert parser.calls == 1
    assert first.rows.nodes == second.rows.nodes
    assert second.cache_hits == 1
