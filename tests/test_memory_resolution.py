from antisentinel.memory import operator_graph


def test_entity_resolver_deduplicates_aliases_before_preference_merge(tmp_path):
    graph = operator_graph.PreferenceGraph(operator_graph.FilePreferenceStore(tmp_path), operator_graph.InMemoryMemoryCache())
    graph.upsert(operator_graph.PreferenceCandidate(
        operator_id="operator-1", subject="diagnosis_order", predicate="prefers",
        object="先看日志再看 metrics", source_ids=("session-1",), confidence=0.8,
    ))
    graph.upsert(operator_graph.PreferenceCandidate(
        operator_id="operator-1", subject="diagnosis_order", predicate="prefers",
        object="logs_before_metrics", source_ids=("session-2",), confidence=0.9,
    ))

    active = graph.list_active("operator-1")
    assert len(active) == 1
    assert active[0].object == "logs_before_metrics"
    assert active[0].model_version == "rule-v1"


def test_low_confidence_conflict_keeps_active_value_and_audit_metadata(tmp_path):
    graph = operator_graph.PreferenceGraph(operator_graph.FilePreferenceStore(tmp_path), operator_graph.InMemoryMemoryCache())
    graph.upsert(operator_graph.PreferenceCandidate(
        operator_id="operator-1", subject="diagnosis_order", predicate="prefers",
        object="logs_before_metrics", source_ids=("session-1",), confidence=0.9,
    ))
    conflict = graph.upsert(operator_graph.PreferenceCandidate(
        operator_id="operator-1", subject="diagnosis_order", predicate="prefers",
        object="metrics_before_logs", source_ids=("session-2",), confidence=0.4,
        model_version="small-memory-v1",
    ))

    assert conflict.status == "conflict"
    assert conflict.conflict_with == graph.list_active("operator-1")[0].memory_id
    assert conflict.memory_id.startswith("mem_")
    assert conflict.source_ids == ("session-2",)
    assert conflict.model_version == "small-memory-v1"
    assert graph.list_active("operator-1")[0].object == "logs_before_metrics"
