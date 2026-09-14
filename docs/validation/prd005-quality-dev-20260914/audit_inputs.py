"""Read-only audit of development labels and active runtime projection identities."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from antisentinel.evaluation.code_corpus import load_corpus


def audit(repository, manifest, runtime_database):
    corpus = load_corpus(repository, manifest)
    preflight = corpus.inspect()
    key_fields = ('repository_id', 'snapshot_id', 'published_generation', 'commit_sha',
                  'node_id', 'chunk_id', 'path', 'source_hash')
    runtime = None
    if runtime_database:
        with sqlite3.connect(f'file:{runtime_database.resolve()}?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute('''SELECT d.* FROM code_search_documents d
                JOIN code_search_active_projections a USING
                (repository_id,snapshot_id,published_generation,commit_sha,projection_revision)''')]
        by_source = {}
        for row in rows:
            by_source.setdefault(tuple(row[k] for k in key_fields), []).append(row['document_id'])
        mapping = {doc.document_id: by_source.get(tuple(getattr(doc, k) for k in key_fields), [])
                   for doc in corpus.documents}
        runtime = {
            'active_documents': len(rows),
            'projection_revisions': sorted({row['projection_revision'] for row in rows}),
            'same_document_ids': len({doc.document_id for doc in corpus.documents} & {r['document_id'] for r in rows}),
            'exact_source_matches': sum(len(ids) == 1 for ids in mapping.values()),
            'missing_source_matches': sum(not ids for ids in mapping.values()),
            'ambiguous_source_matches': sum(len(ids) > 1 for ids in mapping.values()),
            'mapping_policy': 'same scope/node/chunk/path/source_hash only; never infer partial-slice relevance',
            'document_id_mapping': mapping,
        }
    questions = []
    for query in corpus.queries:
        relevant = sorted(set(query['relevant_ids']))
        questions.append({'id': query['id'], 'query': query['query'], 'category': query['category'],
                          'gold_count': len(relevant),
                          'precision_at_5_ceiling': min(len(relevant), 5) / 5 if relevant else None,
                          'old_relevant_ids': relevant, 'scope': query['scope']})
    return {'commit': subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip(),
            'source_python_files': len(list((repository / 'src').rglob('*.py'))),
            'test_files': len(list((repository / 'tests').glob('test_*.py'))),
            'preflight': preflight, 'gold_count_distribution': dict(Counter(q['gold_count'] for q in questions)),
            'questions': questions, 'runtime_projection': runtime,
            'external_model_calls': 0, 'runtime_database_writes': 0, 'business_quality_pass': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=ROOT)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'tests/fixtures/code_retrieval/v1/manifest.json')
    parser.add_argument('--runtime-db', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.repository, args.manifest, args.runtime_db), ensure_ascii=False, indent=2))
