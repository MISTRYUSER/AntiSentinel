def test_ast_fact_cache_is_bound_to_blob_path_parser_and_rules():
    from antisentinel.code_map.incremental import AstFactCache, AstFactKey

    cache = AstFactCache()
    key = AstFactKey("blob-a", "app.py", "", "parser-v1", "rules-v1")
    cache.put(key, {"symbols": ["run"]})

    assert cache.get(key) == {"symbols": ["run"]}
    assert cache.get(AstFactKey("blob-b", "app.py", "", "parser-v1", "rules-v1")) is None
    assert cache.get(AstFactKey("blob-a", "app.py", "", "parser-v2", "rules-v1")) is None
    assert cache.get(AstFactKey("blob-a", "app.py", "", "parser-v1", "rules-v2")) is None
