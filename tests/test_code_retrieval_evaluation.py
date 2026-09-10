import pytest

from antisentinel.evaluation.code_retrieval import score_query, summarize, preflight, compare_performance


def test_precision_uses_fixed_five_and_duplicates_cannot_inflate_recall():
    row = score_query(['a', 'a', 'x'], {'a', 'b'})
    assert row['precision_at_5'] == 0.2
    assert row['recall_at_5'] == 0.5
    assert row['mrr'] == 1.0
    assert row['duplicates'] == 1
    assert row['ceiling_precision_at_5'] == 0.4


def test_first_relevant_rank_and_no_answer_false_positive_are_separate():
    answered = score_query(['x', 'b'], {'a', 'b'})
    empty = score_query(['x'], set())
    assert answered['mrr'] == 0.5
    assert empty['precision_at_5'] is None
    assert empty['false_positive'] is True
    metrics = summarize([answered, empty])
    assert metrics['answerable_count'] == 1
    assert metrics['no_answer_count'] == 1
    assert metrics['macro']['precision_at_5'] == 0.2
    assert metrics['false_positive_rate'] == 1.0
    assert metrics['min']['recall_at_5'] == 0.5


def test_preflight_counts_unique_queries_not_repeated_measurements():
    rows = [{'id': str(i), 'query': 'same query', 'relevant_ids': ['a']} for i in range(20)]
    check = preflight(rows)
    assert check['unique_queries'] == 1
    assert check['sample_sufficient'] is False
    assert 'duplicate_query_text' in check['errors']
    assert check['precision_goal_feasible'] is False


def test_unattainable_quality_goal_is_reported_before_execution():
    rows = [{'id': str(i), 'query': f'query {i}', 'relevant_ids': ['a']} for i in range(24)]
    check = preflight(rows)
    assert check['sample_sufficient'] is True
    assert check['precision_ceiling'] == pytest.approx(0.2)
    assert check['precision_goal_feasible'] is False
    assert check['errors'] == []


def test_performance_comparison_requires_same_scope_and_five_runs():
    assert compare_performance([1, 1, 1, 1, 1], [1, 1], same_workload=True)['comparable'] is False
    assert compare_performance([1] * 5, [1] * 5, same_workload=False)['comparable'] is False
    result = compare_performance([1.06] * 5, [1] * 5, same_workload=True)
    assert result['median_ratio'] == pytest.approx(1.06)
    assert result['pass'] is False


def test_empty_label_set_does_not_become_perfect_retrieval():
    metrics = summarize([score_query([], set())])
    assert metrics['macro']['mrr'] is None
    assert metrics['false_positive_rate'] == 0


def test_dataset_loader_uses_git_blobs_and_rejects_changed_source_hash(tmp_path):
    import json
    from pathlib import Path
    from antisentinel.evaluation.code_corpus import load_corpus
    root = Path(__file__).parents[1]
    path = root / 'tests/fixtures/code_retrieval/v1/manifest.json'
    corpus = load_corpus(root, path)
    assert len(corpus.queries) == 24
    assert len({q['query'] for q in corpus.queries}) == 24
    assert corpus.inspect()['precision_ceiling'] == 0.25
    manifest = json.loads(path.read_text())
    manifest['sources'][0]['sha256'] = '0' * 64
    changed = tmp_path / 'manifest.json'
    changed.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='source hash mismatch'):
        load_corpus(root, changed)


def test_evaluation_keeps_errors_and_unavailable_mode_out_of_success():
    from types import SimpleNamespace
    from antisentinel.evaluation.code_retrieval import evaluate
    from antisentinel.retrieval.models import CodeSearchDocument
    doc = CodeSearchDocument('repo', 'sid', 1, 'sha', 'node', 'chunk', 'a.py', 'run', 'python', 'hash', 'input', 'v1', 'code')
    query = {'id': 'q', 'query': 'run', 'category': 'exact', 'scope': {k: doc.source_identity[k] for k in ('repository_id','snapshot_id','published_generation','commit_sha')}, 'relevant_ids': [doc.document_id]}
    corpus = SimpleNamespace(documents=(doc,), queries=(query,), metadata={'corpus_sha256': 'abc'})

    def broken(mode, query):
        raise PermissionError('no access')

    report = evaluate(corpus, broken, modes=['keyword'], rounds=5)
    assert report['modes']['keyword']['errors'] == 5
    assert report['modes']['keyword']['metrics']['macro']['recall_at_5'] == 0
    assert report['modes']['hybrid_graph']['status'] == 'not_available'
    assert report['execution_pass'] is False
    assert report['case_pass'] is False


def test_wrong_scope_cannot_receive_relevance_credit():
    from types import SimpleNamespace
    from antisentinel.evaluation.code_retrieval import evaluate
    from antisentinel.retrieval.models import CodeSearchDocument
    from antisentinel.retrieval.engine import RetrievalResult
    from antisentinel.retrieval.fusion import FusedCodeSearchHit
    doc = CodeSearchDocument('repo', 'sid', 1, 'sha', 'node', 'chunk', 'a.py', 'run', 'python', 'hash', 'input', 'v1', 'code')
    query = {'id': 'q', 'query': 'run', 'category': 'exact', 'scope': {k: doc.source_identity[k] for k in ('repository_id','snapshot_id','published_generation','commit_sha')}, 'relevant_ids': [doc.document_id]}
    corpus = SimpleNamespace(documents=(doc,), queries=(query,), metadata={'corpus_sha256': 'abc'})
    hit = FusedCodeSearchHit(doc.document_id, 1, ('keyword',), {'keyword': 1}, {**doc.source_identity, 'repository_id': 'foreign'})
    report = evaluate(corpus, lambda m, q: RetrievalResult((hit,), m, {'keyword':'ready'}), modes=['keyword'], rounds=5)
    assert report['modes']['keyword']['scope_errors'] == 5
    assert report['modes']['keyword']['metrics']['macro']['precision_at_5'] == 0
    assert report['execution_pass'] is False


def test_cli_preflight_runs_from_other_directory_without_creating_stores(tmp_path):
    import json
    from pathlib import Path
    import os
    import subprocess
    import sys
    script = Path(__file__).parents[1] / 'scripts/evaluate_code_retrieval.py'
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, str(script), '--preflight'], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['unique_queries'] == 24
    assert report['precision_goal_feasible'] is False
    assert list(tmp_path.iterdir()) == []


def test_cli_refuses_unqualified_acceptance_run_before_creating_output(tmp_path):
    from pathlib import Path
    import subprocess
    import sys
    output = tmp_path / 'unapproved'
    script = Path(__file__).parents[1] / 'scripts/evaluate_code_retrieval.py'
    result = subprocess.run([sys.executable, str(script), '--output', str(output)], capture_output=True,
                            text=True, timeout=10)
    assert result.returncode != 0
    assert '--diagnostic' in result.stderr
    assert not output.exists()


def test_empty_successful_response_reports_zero_measured_output():
    from types import SimpleNamespace
    from antisentinel.evaluation.code_retrieval import evaluate
    from antisentinel.retrieval.engine import RetrievalResult
    query = {'id': 'q', 'query': 'missing', 'category': 'semantic', 'scope': {}, 'relevant_ids': ['a']}
    corpus = SimpleNamespace(documents=(), queries=(query,), metadata={})
    report = evaluate(corpus, lambda m, q: RetrievalResult((), m, {'keyword': 'ready'}), modes=['keyword'])
    measured = report['modes']['keyword']
    assert measured['observations'] == 5
    assert all(r['result_count'] == 0 for r in measured['rows'])
    assert measured['metrics']['macro'] == {'precision_at_5': 0.0, 'recall_at_5': 0.0, 'mrr': 0.0}
    assert report['execution_pass'] is True  # Empty retrieval is a measured result, not an exception.
    assert report['quality_pass'] is False
    assert report['case_pass'] is False


def test_go_corpus_uses_multilanguage_parser_and_preserves_source_hash(tmp_path):
    import hashlib
    import json
    import subprocess
    from antisentinel.evaluation.code_corpus import load_corpus
    data = b'package demo\n\nfunc Run() int {\n    return 7\n}\n'
    (tmp_path/'main.go').write_bytes(data)
    def git(*args):
        return subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false','-C',str(tmp_path),*args],check=True,capture_output=True,text=True).stdout.strip()
    git('init','-q')
    git('add','main.go')
    git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture')
    manifest = {'schema_version':1,'top_k':5,'corpus_version':'go-fixture-v1','query_version':'q1','repository_id':'test',
        'commits':{'current':git('rev-parse','HEAD')},'label_status':'proposed_for_user_review',
        'sources':[{'revision':'current','path':'main.go','sha256':hashlib.sha256(data).hexdigest()}],
        'queries':[{'id':'q1','query':'Run','category':'exact','revision':'current',
                    'relevant':[{'path':'main.go','symbol':'Run'}],'rationale':'declared function'}]}
    path=tmp_path/'manifest.json'; path.write_text(json.dumps(manifest))
    corpus=load_corpus(tmp_path,path)
    document=next(d for d in corpus.documents if d.symbol=='Run')
    assert document.language=='go'
    assert document.source_hash==hashlib.sha256(document.text.encode()).hexdigest()
    assert corpus.queries[0]['relevant_ids']==[document.document_id]
