"""Versioned long-source slices through real SQLite/Milvus and Evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from antisentinel.code_map.models import MapSnapshot, RepositoryRegistration
from antisentinel.code_map.python_parser import PythonAstParser
from antisentinel.code_map.source_context import SourceEvidenceService
from antisentinel.code_map.store import SQLiteCodeMapStore, SourceBlob, SourceFile, StagedMapRows
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore
from antisentinel.retrieval.coordinator import RetrievalCoordinator
from antisentinel.retrieval.fusion import _overlapping_source
from antisentinel.retrieval.milvus_adapter import MilvusAdapter
from antisentinel.retrieval.models import CodeSearchDocument
from antisentinel.retrieval.slicing import SLICE_REVISION, MAX_SLICE_BYTES


class Encoder:
    model_name, dimension, max_retries = 'slicing-fixture', 3, 0
    def __init__(self): self.calls = 0
    def embed_documents(self, texts):
        self.calls += len(texts)
        return [[1.,0.,0.] for _ in texts]
    def embed_query(self, texts): return [[1.,0.,0.] for _ in texts]
    def close(self): pass


def run(output, *, milvus_uri=None, token=None):
    if milvus_uri:
        logging.disable(logging.CRITICAL)
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    database = SQLiteDatabase(output/'facts.sqlite'); database.initialize()
    store = SQLiteCodeMapStore(database)
    store.register(RepositoryRegistration('repo','file:///fixture','local','main'))
    job = store.enqueue('repo','a'*40,'manual')
    lease = store.claim_job('case',datetime.now(timezone.utc))
    sources = {
        'long.py': ('class Large:\n    def run(self):\n        payload = "'+'0123456789'*10000+'"\n        return payload\n').encode(),
        'unicode.py': ('def greet():\n    value = "'+ '中文🌍'*5000+'"\n    return value\n').encode(),
    }
    parser = PythonAstParser('python-ast-v1')
    parsed = [parser.parse_file(path,data,'snapshot') for path,data in sources.items()]
    assert all(not item.errors for item in parsed)
    assert store.publish(lease,MapSnapshot('snapshot','repo','a'*40,'python-ast-v1',job.rules_digest,file_count=2),
        StagedMapRows(nodes=tuple(n for p in parsed for n in p.symbols),
            chunks=tuple(c for p in parsed for c in p.chunks), edges=parser.contains_edges(tuple(parsed),'snapshot'),
            blobs=tuple(SourceBlob(p.file_hash,p.file_hash,p.data,p.encoding) for p in parsed),
            files=tuple(SourceFile(p.path,'snapshot',p.path,p.file_hash,p.file_hash,len(p.data)) for p in parsed))).ok
    store.bind_incident('incident','repo','snapshot')
    original_archive = store.read_generation('snapshot',1)
    checks, encoded, counts = {}, [], {}
    collection_base = 'slice_case_' + hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:12]
    refs = []
    for iteration in range(2):
        database = SQLiteDatabase(output/'facts.sqlite')
        store = SQLiteCodeMapStore(database)
        evidence = SourceEvidenceService(None,store,SQLiteEvidenceStore(database))
        encoder = Encoder()
        coordinator = RetrievalCoordinator(store,evidence,allowed_repositories=['repo'],
            embedder_factory=lambda:encoder,
            index_factory=lambda e:MilvusAdapter(milvus_uri or output/'vectors.db',collection_base,dimension=3,
                model_revision=e.model_name,template_revision='path-symbol-source-v1',projection_revision=SLICE_REVISION,
                source_ranges=True,rpc_timeout=5,token=token),poll_seconds=.01)
        try:
            coordinator.start()
            collection_name = coordinator.index.collection_name
            while coordinator.health()['runs'] != {'ready':1}:
                if coordinator.errors or time.monotonic()-start > 90:
                    raise RuntimeError('slice projection did not become ready')
                time.sleep(.02)
            tools = {t.name:t for t in coordinator.for_incident('incident')}
            rows = database.query('SELECT * FROM code_search_documents')
            docs = [CodeSearchDocument(**{key:row[key] for key in CodeSearchDocument.__dataclass_fields__}) for row in rows]
            counts['documents'] = len(docs)
            checks['slice_size'] = all(len(d.text.encode()) <= MAX_SLICE_BYTES for d in docs)
            coverage = []
            for parent in store.read_generation('snapshot',1)['chunks']:
                pieces = sorted((d for d in docs if d.chunk_id==parent['chunk_id']),key=lambda d:d.byte_start)
                original = store.read_generation_utf8_chunk('repo','snapshot',1,parent['chunk_id'])
                coverage.append(bool(pieces) and pieces[0].byte_start==original['byte_start'] and pieces[-1].byte_end==original['byte_end']
                    and all(a.byte_end==b.byte_start for a,b in zip(pieces,pieces[1:]))
                    and ''.join(d.text for d in pieces)==original['content'])
            checks['complete_parent_coverage'] = all(coverage)
            node_coverage=[]
            for item in parsed:
                lines=item.data.splitlines(keepends=True)
                for node in item.symbols:
                    pieces=sorted((d for d in docs if d.node_id==node.node_id),key=lambda d:d.byte_start)
                    begin=sum(map(len,lines[:node.start_line-1]));end=sum(map(len,lines[:node.end_line]))
                    node_coverage.append(bool(pieces) and pieces[0].byte_start==begin and pieces[-1].byte_end==end
                        and all(a.byte_end==b.byte_start for a,b in zip(pieces,pieces[1:]))
                        and ''.join(d.text for d in pieces).encode()==item.data[begin:end])
            checks['complete_node_coverage']=all(node_coverage)
            checks['archive_unchanged']=store.read_generation('snapshot',1)==original_archive
            undecodable = 0
            for parent in original_archive['chunks']:
                try:store.read_generation_chunk('repo','snapshot',1,parent['chunk_id'])
                except UnicodeDecodeError:undecodable += 1
            counts['original_undecodable_chunks'] = undecodable
            checks['split_character_fixture'] = undecodable > 0
            checks['more_slices_than_parents'] = len(docs)>len(coverage)
            payloads = coordinator.index.client.query(collection_name=coordinator.index.collection_name,filter='',
                output_fields=coordinator.index.output_fields,limit=1000,consistency_level='Strong',timeout=5)
            counts['vectors'] = len(payloads)
            expected = {d.document_id:d for d in docs}
            checks['range_identity_in_both_stores'] = len(payloads)==len(docs) and all(
                all(row.get(k)==v for k,v in expected[row['document_id']].source_identity.items()) for row in payloads)
            if iteration==0:
                result = tools['code_retrieval.search'].handler({'query':'Large.run','mode':'hybrid'})
                hits = result.result['hits']
                counts['hybrid_hits'] = len(hits)
                checks['hybrid_range_dedup'] = bool(hits) and not any(_overlapping_source(a['source_identity'],b['source_identity']) for i,a in enumerate(hits) for b in hits[i+1:])
                graph = tools['code_retrieval.search'].handler({'query':'Large','mode':'graph'})
                checks['graph_uses_projected_ids'] = bool(graph.result['hits']) and all(h['document_id'] in expected for h in graph.result['hits'])
                candidates = [h['source_identity'] for h in hits]
                read = tools['code_retrieval.read_evidence'].handler({'candidates':candidates})
                refs = [{'evidence_id':s['evidence_id']} for s in read.result['slices']]
                checks['evidence_budget'] = 0 < len(refs) <= 4 and read.result['total_bytes'] <= 32768
                (output/'refs.json').write_text(json.dumps(refs,indent=2))
                business = time.monotonic()
            else:
                restored = evidence.rehydrate(json.loads((output/'refs.json').read_text()))
                checks['rehydrated_slices'] = len(restored)==len(refs) and all(hashlib.sha256(s.content.encode()).hexdigest()==s.content_hash for s in restored)
        finally:
            coordinator.stop()
        encoded.append(encoder.calls)
        checks[f'closed_{iteration}'] = not coordinator.health()['thread_alive'] and not coordinator.errors
    checks['no_reembedding'] = encoded == [counts['documents'],0]
    counts['evidence'] = database.query('SELECT COUNT(*) FROM evidence')[0][0]
    counts['tasks'] = database.query('SELECT COUNT(*) FROM code_embedding_tasks')[0][0]
    counts['embedding_attempts'] = database.query('SELECT COUNT(*) FROM code_embedding_attempts')[0][0]
    checks['persistence_counts'] = counts['tasks']==counts['documents'] and counts['evidence']==len(refs)
    end = time.monotonic()
    report = {'case_pass':all(checks.values()),'checks':checks,'input_files':2,'input_bytes':sum(map(len,sources.values())),
        'parent_chunks':sum(len(p.chunks) for p in parsed),'counts':counts,'embedding_calls_per_start':encoded,
        'elapsed_ms':(end-start)*1000,'business_completed_ms':(business-start)*1000,'storage_verified_ms':(end-start)*1000,
        'verification_lag_ms':(end-business)*1000,'external_model_calls':0,'background_exceptions':0,
        'retries':sum(max(0,row[0]-1) for row in database.query('SELECT attempt FROM code_embedding_tasks')),
        'storage_kind':'standalone' if milvus_uri else 'lite','collection_name':collection_name}
    (output/'report.json').write_text(json.dumps(report,indent=2)); return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--milvus-uri');args=parser.parse_args()
    result=run(args.output,milvus_uri=args.milvus_uri,token=os.getenv('ANTISENTINEL_CODE_RETRIEVAL_MILVUS_TOKEN') if args.milvus_uri else None)
    print(json.dumps(result,indent=2));raise SystemExit(0 if result['case_pass'] else 1)
