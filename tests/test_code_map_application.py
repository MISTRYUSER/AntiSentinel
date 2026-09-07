def test_application_binds_code_map_tools_only_for_incident_with_server_scope():
    from antisentinel.code_map.query import QueryScope
    from antisentinel.entry.application import DiagnosisApplicationService
    from antisentinel.tools.registry import ToolRegistry

    class Store:
        def scope_for_incident(self, incident_id):
            return QueryScope(incident_id, frozenset({"repo-a"}))

    service = DiagnosisApplicationService.default_fake()
    service.code_map_store = Store()
    incident = service.create_incident(title="code map", summary=None, source="test")
    registry = ToolRegistry(auto_discover=False)

    service._bind_code_map_tools(registry, incident)

    assert registry.resolve("code_map.find_symbols") is not None
    assert len([item for item in registry.manifests() if item["name"].startswith("code_map.")]) == 5
