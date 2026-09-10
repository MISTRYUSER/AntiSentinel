"""Read a reviewed evaluation manifest from immutable, local Git blobs."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess

from antisentinel.code_map.python_parser import PythonAstParser
from antisentinel.retrieval.models import CodeSearchDocument, CodeSearchScope
from .code_retrieval import preflight


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class EvaluationCorpus:
    documents: tuple[CodeSearchDocument, ...]
    queries: tuple[dict, ...]
    metadata: dict
    input_bytes: int
    graph_sources: tuple = ()

    def inspect(self):
        return {**preflight(self.queries), 'documents': len(self.documents), 'input_bytes': self.input_bytes,
                'fingerprints': self.metadata, 'label_status': self.metadata['label_status']}


def load_corpus(repository: Path, manifest_path: Path):
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get('schema_version') != 1 or manifest.get('top_k') != 5:
        raise ValueError('unsupported evaluation schema or top_k')
    documents, lookup, scopes = [], {}, {}
    graph_sources = []
    size = 0
    sources_seen = set()
    for source in manifest['sources']:
        revision = source['revision']
        commit = manifest['commits'][revision]
        if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
            raise ValueError('corpus commit must be an immutable full SHA')
        path = source['path']
        key = revision, path
        if key in sources_seen:
            raise ValueError('duplicate source')
        sources_seen.add(key)
        data = subprocess.run(['git', '-C', str(repository), 'show', f'{commit}:{path}'],
                              capture_output=True, check=True, timeout=10).stdout
        if hashlib.sha256(data).hexdigest() != source['sha256']:
            raise ValueError(f'corpus source hash mismatch: {path}')
        size += len(data)
        sid = digest({'repository': manifest['repository_id'], 'commit': commit, 'corpus': manifest['corpus_version']})
        scope = CodeSearchScope(manifest['repository_id'], sid, 1, commit)
        scopes[revision] = scope
        if path.endswith('.py'):
            language = 'python'
            parser = PythonAstParser('evaluation-python-ast-v1')
        elif path.endswith('.go'):
            from tree_sitter_language_pack import get_parser
            from antisentinel.code_map.multilang_parser import MultiLanguageParser
            language = 'go'
            if get_parser('go').parse(data).root_node.has_error:
                raise ValueError(f'corpus parsing failed: {path}')
            parser = MultiLanguageParser('evaluation-go-tree-sitter-v1')
        else:
            raise ValueError(f'unsupported evaluation language: {path}')
        parsed = parser.parse_file(path, data, sid)
        if parsed.errors:
            raise ValueError(f'corpus parsing failed: {path}')
        graph_sources.append((scope, parsed, parser.contains_edges((parsed,), sid)))
        symbols = {n.node_id: n for n in parsed.symbols}
        for chunk in parsed.chunks:
            text = data[chunk.byte_start:chunk.byte_end].decode(parsed.encoding)
            symbol = symbols[chunk.node_id].qualified_name
            embedding_text = f'{path}\n{symbol}\n{text}'
            document = CodeSearchDocument(scope.repository_id, sid, 1, commit, chunk.node_id, chunk.chunk_id,
                path, symbol, language, chunk.content_hash, hashlib.sha256(embedding_text.encode()).hexdigest(),
                'evaluation-projection-v1', text)
            documents.append(document)
            lookup.setdefault((revision, path, symbol), []).append(document.document_id)
    queries = []
    for item in manifest['queries']:
        revision = item['revision']
        if revision not in scopes:
            raise ValueError('query revision not in corpus')
        relevant = []
        for label in item['relevant']:
            key = revision, label['path'], label['symbol']
            if key not in lookup:
                raise ValueError(f'unknown label: {key}')
            relevant.extend(lookup[key])
        queries.append({'id': item['id'], 'query': item['query'], 'category': item['category'],
                        'scope': vars(scopes[revision]), 'relevant_ids': sorted(set(relevant)),
                        'rationale': item['rationale']})
    metadata = {'corpus_version': manifest['corpus_version'], 'query_version': manifest['query_version'], 'source_count': len(manifest['sources']),
                'commits': manifest['commits'], 'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                'corpus_sha256': digest(manifest['sources']), 'queries_sha256': digest(manifest['queries']),
                'resolved_labels_sha256': digest([{'id': q['id'], 'relevant_ids': q['relevant_ids']} for q in queries]),
                'label_status': manifest['label_status'], 'top_k': 5, 'random_seed': None}
    return EvaluationCorpus(tuple(documents), tuple(queries), metadata, size, tuple(graph_sources))


def publish_graph_corpus(corpus, database):
    """Publish the same verified Git bytes and parser identities used by scoring."""
    from datetime import datetime, timezone
    from antisentinel.code_map.models import MapSnapshot, RepositoryRegistration
    from antisentinel.code_map.store import SQLiteCodeMapStore, SourceBlob, SourceFile, StagedMapRows
    database.initialize()
    store = SQLiteCodeMapStore(database)
    grouped = {}
    for scope, parsed, edges in corpus.graph_sources:
        grouped.setdefault(scope, []).append((parsed, edges))
    for repo in {scope.repository_id for scope in grouped}:
        store.register(RepositoryRegistration(repo, 'file:///evaluation-fixture', 'local', 'frozen'))
    for scope, sources in grouped.items():
        job = store.enqueue(scope.repository_id, scope.commit_sha, 'manual', parser_revision='evaluation-graph-v1')
        lease = store.claim_job('evaluation', datetime.now(timezone.utc))
        if lease is None or lease.job_id != job.job_id:
            raise RuntimeError('evaluation graph lease mismatch')
        result = store.publish(lease, MapSnapshot(scope.snapshot_id, scope.repository_id, scope.commit_sha,
            'evaluation-graph-v1', job.rules_digest, file_count=len(sources)), StagedMapRows(
                nodes=tuple(n for parsed, _ in sources for n in parsed.symbols),
                chunks=tuple(c for parsed, _ in sources for c in parsed.chunks),
                edges=tuple(e for _, edges in sources for e in edges),
                blobs=tuple(SourceBlob(parsed.file_hash, parsed.file_hash, parsed.data, parsed.encoding) for parsed, _ in sources),
                files=tuple(SourceFile(digest([scope.snapshot_id, parsed.path]), scope.snapshot_id, parsed.path,
                    parsed.file_hash, parsed.file_hash, len(parsed.data)) for parsed, _ in sources)))
        if not result.ok:
            raise RuntimeError('evaluation graph publication failed')
    return store
