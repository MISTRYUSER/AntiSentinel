def test_preference_graph_is_scoped_and_preserves_source_and_conflict_history(tmp_path):
    from antisentinel.memory import operator_graph
    graph = operator_graph.PreferenceGraph(operator_graph.FilePreferenceStore(tmp_path), operator_graph.InMemoryMemoryCache())
    first = graph.upsert(operator_graph.PreferenceCandidate(
        operator_id="operator-1", subject="diagnosis_order", predicate="prefers",
        object="logs_before_metrics", source_ids=("session-1",), confidence=0.9,
    ))
    second = graph.upsert(operator_graph.PreferenceCandidate(
        operator_id="operator-1", subject="diagnosis_order", predicate="prefers",
        object="metrics_before_logs", source_ids=("session-2",), confidence=0.4,
    ))

    assert first.status == "active"
    assert second.status == "conflict"
    assert graph.list_active("operator-1")[0].object == "logs_before_metrics"
    assert graph.list_active("operator-2") == []
    assert len(graph.conflicts("operator-1")) == 1
