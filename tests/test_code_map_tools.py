def test_code_map_tool_factory_exposes_five_read_only_tools_with_bound_scope():
    from antisentinel.code_map.query import QueryEnvelope, QueryScope
    from antisentinel.code_map.tools import build_code_map_tools
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.execution.tool_executor import ToolExecutor

    class Query:
        def find_symbols(self, scope, repository_id, snapshot_id, qualified_name, **kwargs):
            assert scope.allowed_repositories == frozenset({"repo-a"})
            return QueryEnvelope(items=({"qualified_name": qualified_name},), snapshot_id=snapshot_id)

    definitions = build_code_map_tools(Query(), lambda _: QueryScope("incident-a", frozenset({"repo-a"})))
    registry = ToolRegistry(auto_discover=False)
    for definition in definitions:
        registry.register(definition)

    result = ToolExecutor(registry).execute("code_map.find_symbols", {"repository_id": "repo-a", "snapshot_id": "snap", "qualified_name": "Service.run"})
    assert len(definitions) == 5
    assert all(definition.read_only for definition in definitions)
    assert result.status == "succeeded"
    assert result.result["items"] == [{"qualified_name": "Service.run"}]
