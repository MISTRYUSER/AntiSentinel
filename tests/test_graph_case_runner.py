from scripts.run_code_retrieval_case import run_graph_case


def test_graph_case_runner_persists_expansion_and_evidence(tmp_path):
    report = run_graph_case(tmp_path / "graph-case")

    assert report["case_pass"] is True
    assert report["expanded_nodes"] == 1
    assert report["unresolved_edges"] == 1
    assert report["evidence_count"] == 2  # Resumed Runtime and normal application Session each store Evidence.
    assert report['application_runtime_completed'] is True
    assert report['application_evidence_count'] == 1
    assert report["hash_verified"] == 1
    assert report["scope_leaks"] == 0
    assert report["background_exceptions"] == 0
    assert report['runtime_completed'] is True
    assert report['checkpoint_restored'] == 1
    assert report['negative_checks'] >= 5
    assert report['budget_rejected_evidence'] == 0
    assert report['generation_isolated'] is True
    assert report['keyword_seed_count'] == 5
    assert report['graph_visible_with_full_seeds'] is True
    assert report['negative_results']['binding_published_generation'] is True
    assert report['negative_results']['binding_requested_commit'] is True
    assert report['typed_graph_tool_result'] is True
