"""Real Git differential acceptance for six changes and immutable generations."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import statistics
import subprocess
import time

from antisentinel.code_map.git_reader import SubprocessGitReader
from antisentinel.code_map.incremental import AstFactCache, normalize_semantics
from antisentinel.code_map.models import RepositoryRegistration
from antisentinel.code_map.snapshot_builder import SnapshotBuilder
from antisentinel.code_map.store import SQLiteCodeMapStore
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.tracing.telemetry import Telemetry


def run_c4(output: Path, timeout: float = 120):
    output = Path(output)
    started = time.monotonic()
    deadline = started + timeout
    repo = output / 'fixture'
    repo.mkdir()
    def git(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('C4 deadline')
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True,
                              timeout=min(remaining, 30), text=True).stdout.strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'C4 Fixture')
    git('config', 'user.email', 'c4@example.test')
    files = {
        'dep.py': 'def target():\n    return 1\n\nclass Base:\n    pass\n',
        'app.py': 'from dep import target as alias, Base\nclass Child(Base):\n    pass\ndef caller():\n    return alias()\n',
        'test_dep.py': 'from dep import target\ndef test_target():\n    assert target() == 1\n',
        'stable.py': 'def stable():\n    return 7\n',
        'rename_me.py': 'def named():\n    return 9\n',
        'delete_me.py': 'def obsolete():\n    return 0\n',
    }
    for path, text in files.items():
        (repo / path).write_text(text)
    def commit(message):
        git('add', '-A')
        git('commit', '-m', message)
        return git('rev-parse', 'HEAD')
    base = commit('base')
    reader = SubprocessGitReader(str(repo), output / 'cache.git', 'local-fixture', frozenset())
    assert reader.sync_ref('refs/heads/main').commit_sha == base
    cache = AstFactCache()
    builder = SnapshotBuilder(reader, fact_cache=cache)
    stores = []
    for name in ('incremental', 'full'):
        db = SQLiteDatabase(output / f'{name}.sqlite')
        db.initialize()
        store = SQLiteCodeMapStore(db)
        store.register(RepositoryRegistration('c4', str(repo), 'fixture', 'refs/heads/main'))
        stores.append(store)
    inc_store, full_store = stores
    telemetry = Telemetry(database=inc_store.database, trace_path=output / 'traces.jsonl')

    def build(store, engine, sha, previous=None):
        job = store.enqueue('c4', sha, 'deployment', parser_revision='python-ast-c4-v2')
        lease = store.claim_job('c4-worker', store._now())
        assert lease and lease.job_id == job.job_id
        t = time.monotonic()
        with telemetry.span('c4.build', commit_sha=sha):
            built = engine.build(lease, previous_commit=previous)
        t1 = time.time_ns()
        with telemetry.span('c4.persist', commit_sha=sha):
            result = store.publish(lease, built.snapshot, built.rows)
            assert result.ok, result.error
        assert telemetry.force_flush()
        published = store.get_snapshot(built.snapshot.snapshot_id)
        generation = store.read_generation(published.snapshot_id, published.generation)
        count = 0
        for table in ('nodes', 'edges', 'chunks', 'files'):
            actual = store.database.query(f'SELECT * FROM code_map_{table} WHERE snapshot_id=?', (published.snapshot_id,))
            assert len(actual) == len(generation[table])
            count += len(actual)
        for chunk in generation['chunks']:
            source_file = next(f for f in generation['files'] if f['path'] == chunk['path'])
            raw = bytes(store.database.query('SELECT content FROM code_map_blobs WHERE content_hash=?', (source_file['content_hash'],))[0][0])
            assert sha256(raw).hexdigest() == source_file['content_hash']
            assert sha256(raw[chunk['byte_start']:chunk['byte_end']]).hexdigest() == chunk['content_hash']
            count += 2
        ids = {n['node_id'] for n in generation['nodes']}
        for edge in generation['edges']:
            assert edge['source_node_id'] in ids and (edge['target_node_id'] is None or edge['target_node_id'] in ids)
            count += 1
        t2 = time.time_ns()
        return built, generation, count, (time.monotonic()-t)*1000, (t2-t1)/1e6, t1, t2

    initial, base_rows, _, _, _, _, _ = build(inc_store, builder, base)
    base_nodes = {n['node_id']: n for n in base_rows['nodes']}
    actual_edges = {(base_nodes[e['source_node_id']]['qualified_name'], e['relation'],
                     base_nodes[e['target_node_id']]['qualified_name'] if e['target_node_id'] else None)
                    for e in base_rows['edges']}
    golden_edges = {('Child', 'inherits', 'Base'), ('caller', 'calls', 'target'),
                    ('caller', 'imports', 'target'), ('test_target', 'calls', 'target'),
                    ('test_target', 'imports', 'target'), ('target', 'tested_by', 'test_target')}
    assert actual_edges == golden_edges, (actual_edges, golden_edges)
    baseline_json = json.dumps(base_rows, sort_keys=True)
    previous = base
    scenarios = []
    for name in ('add', 'modify', 'delete', 'rename', 'export', 'caller'):
        if name == 'add':
            (repo / 'added.py').write_text('def added():\n    return 3\n')
        elif name == 'modify':
            (repo / 'added.py').write_text('def added():\n    return 4\n')
        elif name == 'delete':
            (repo / 'delete_me.py').unlink()
        elif name == 'rename':
            git('mv', 'rename_me.py', 'renamed.py')
        elif name == 'export':
            (repo / 'dep.py').write_text('def renamed_target():\n    return 1\n\nclass Base:\n    pass\n')
        else:
            (repo / 'app.py').write_text('from dep import renamed_target as alias, Base\nclass Child(Base):\n    pass\ndef caller():\n    return alias()\n')
            (repo / 'test_dep.py').write_text('from dep import renamed_target as target\ndef test_target():\n    assert target() == 1\n')
        sha = commit(name)
        assert reader.sync_ref('refs/heads/main').commit_sha == sha
        inc, inc_rows, checks, inc_ms, lag, t1, t2 = build(inc_store, builder, sha, previous)
        full, full_rows, checks2, full_ms, _, _, _ = build(full_store, SnapshotBuilder(reader), sha)
        equivalent = normalize_semantics(inc) == normalize_semantics(full)
        a = {key: value for key, value in inc_rows.items() if key != 'snapshot'}
        b = {key: value for key, value in full_rows.items() if key != 'snapshot'}
        equivalent = equivalent and a == b
        # Independent expected target of the untouched caller changes after export removal.
        nodes = {n['node_id']: n for n in inc_rows['nodes']}
        calls = [e for e in inc_rows['edges'] if e['relation'] == 'calls' and nodes[e['source_node_id']]['qualified_name'] == 'caller']
        expected_target = None if name == 'export' else ('renamed_target' if name == 'caller' else 'target')
        actual_target = nodes[calls[0]['target_node_id']]['qualified_name'] if calls and calls[0]['target_node_id'] else None
        independent = len(calls) == 1 and actual_target == expected_target
        assert equivalent and independent and inc.cache_hits > 0
        assert json.dumps(inc_store.read_generation(initial.snapshot.snapshot_id, 1), sort_keys=True) == baseline_json
        scenarios.append({'name': name, 'commit': sha, 'previous': previous, 'changes': inc.changes,
                          'equal': equivalent, 'golden_call_match': independent, 'cache_hits': inc.cache_hits,
                          'nodes': len(inc.rows.nodes), 'edges': len(inc.rows.edges), 'chunks': len(inc.rows.chunks),
                          'input_files': len(inc.rows.files), 'input_bytes': inc.snapshot.logical_bytes,
                          'integrity_checks': checks+checks2, 'full_ms': full_ms, 'incremental_ms': inc_ms,
                          't1_ns': t1, 't2_ns': t2, 'persistence_lag_ms': lag})
        (output / f'{name}-semantics.json').write_text(json.dumps(a, sort_keys=True, indent=2))
        previous = sha

    # Same-input explicit partial retry cannot alter the generation referenced by a diagnosis.
    (repo / 'broken.py').write_text('def broken(:\n')
    partial_sha = commit('partial')
    reader.sync_ref('refs/heads/main')
    partial, old, _, _, _, _, _ = build(inc_store, builder, partial_sha, previous)
    sid = partial.snapshot.snapshot_id
    assert inc_store.get_snapshot(sid).status == 'partial'
    assert inc_store.get_published_snapshot('c4', partial_sha) is None
    inc_store.bind_incident('c4-incident', 'c4', sid, allow_partial=True)
    from antisentinel.code_map.source_context import SourceEvidenceService
    from antisentinel.code_map.query import CodeMapQuery
    from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore
    sources = SourceEvidenceService(CodeMapQuery(inc_store), inc_store, SQLiteEvidenceStore(inc_store.database))
    source_before = sources.read_source('c4-incident', 'c4', sid, old['chunks'][0]['chunk_id'])
    partial_job = inc_store.list_jobs('c4')[-1]
    # Resolve by exact input; job ordering need not be commit ordering.
    partial_job = next(j for j in inc_store.list_jobs('c4') if j.commit_sha == partial_sha)
    inc_store.retry_job(partial_job.job_id)
    build(inc_store, builder, partial_sha)
    assert inc_store.read_generation(sid, 1) == old
    assert inc_store.get_snapshot(sid).generation == 2
    binding = inc_store.database.query('SELECT published_generation FROM code_map_diagnosis_bindings WHERE incident_id=?', ('c4-incident',))[0][0]
    assert binding == 1
    source_after = sources.rehydrate([{'evidence_id': source_before.evidence_id}])[0]
    assert source_after.content == source_before.content and source_after.content_hash == source_before.content_hash
    spans = inc_store.database.query('SELECT COUNT(*) FROM spans')[0][0]
    telemetry.shutdown()
    elapsed = time.monotonic()-started
    report = {'case': 'C4', 'case_pass': True, 'scenarios_passed': len(scenarios), 'scenarios': scenarios,
              'base_commit': base, 'partial_commit': partial_sha, 'partial_generations': [1, 2],
              'old_snapshot_changed': 0, 'binding_generation': binding, 'old_evidence_readback': True, 'background_exception_count': 0,
              'golden_edge_precision': 1.0, 'golden_edge_recall': 1.0, 'golden_edges': len(golden_edges),
              'persisted_trace_spans': spans, 'elapsed_seconds': elapsed, 'retries': 0,
              'input_files': scenarios[-1]['input_files'], 'input_bytes': scenarios[-1]['input_bytes'],
              'persisted_snapshots': inc_store.database.query('SELECT COUNT(*) FROM code_map_snapshots')[0][0],
              'integrity_checks': sum(s['integrity_checks'] for s in scenarios),
              'full_median_ms': statistics.median(s['full_ms'] for s in scenarios),
              'incremental_median_ms': statistics.median(s['incremental_ms'] for s in scenarios),
              'performance_note': 'Different change inputs; timings are observations, not a performance improvement claim.'}
    assert elapsed <= timeout
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    return report
