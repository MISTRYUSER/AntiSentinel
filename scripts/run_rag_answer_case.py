"""Frozen Git -> retrieval -> persisted Evidence -> real answer -> Ragas metrics."""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'
os.environ['LANGSMITH_TRACING'] = 'false'

from antisentinel.evaluation.code_corpus import load_corpus
from antisentinel.evaluation.code_embedding import FlashCodeEncoder
from antisentinel.evaluation.rag_answers import prepare_context, answer_request, validate_answer, disclosed_contexts, select_answer_style, verify_index_corpus
from antisentinel.evaluation.support_judge import validate_verdict
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore
from antisentinel.retrieval.keyword import KeywordRetriever
from antisentinel.retrieval.vector import EmbeddingTaskStore
from antisentinel.retrieval.milvus_adapter import MilvusAdapter
from antisentinel.retrieval.engine import CodeRetrievalService
from antisentinel.retrieval.models import CodeSearchScope


async def run(args):
    if importlib.metadata.version('ragas') != '0.4.3':
        raise ValueError('expected ragas 0.4.3')
    from ragas import SingleTurnSample
    from ragas.metrics import Faithfulness, ResponseRelevancy
    from ragas.llms.base import BaseRagasLLM
    from ragas.embeddings import BaseRagasEmbeddings
    from ragas.run_config import RunConfig
    from langchain_core.outputs import Generation, LLMResult

    if args.output.exists():
        raise FileExistsError(args.output)
    corpus = load_corpus(args.repository, args.manifest)
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 600:
        raise ValueError('Case timeout must be within 600 seconds')
    queries = [q for q in corpus.queries if not args.query_ids or q['id'] in args.query_ids]
    if not queries or len(queries)>24 or (args.query_ids and set(args.query_ids)!={q['id'] for q in queries}):
        raise ValueError('expected 1-24 known query IDs')
    source_report = json.loads((args.index/'report.json').read_text())
    verify_index_corpus(source_report, corpus.metadata)
    model, key = os.environ['ANTISENTINEL_MODEL_NAME'], os.environ['ANTISENTINEL_MODEL_API_KEY']
    base = httpx.URL(os.environ['ANTISENTINEL_MODEL_BASE_URL'])
    if base.host != 'api.deepseek.com' or base.scheme != 'https':
        raise ValueError('unexpected judge destination')
    args.output.mkdir()
    started = time.monotonic()
    calls, rows = [], []
    phase = {'query_id': None, 'stage': None}
    client = httpx.Client(follow_redirects=False)

    def call(payload, *, strict=False):
        remaining = args.timeout - (time.monotonic()-started)
        if remaining <= 0 or len(calls) >= 200:
            raise TimeoutError('RAG Case budget exceeded')
        record = {**phase, 'request': payload}
        calls.append(record)
        begin = time.monotonic()
        try:
            response = client.post('https://api.deepseek.com'+('/beta' if strict else '')+'/chat/completions',
                headers={'Authorization':'Bearer '+key}, json=payload, timeout=min(30, remaining))
            record['http_status'] = response.status_code
            if response.status_code != 200:
                raise ValueError('model_http_failure')
            body = response.json()
            record['response'] = body
            return body
        finally:
            record['elapsed_ms'] = (time.monotonic()-begin)*1000
            with (args.output/'model-calls.jsonl').open('a') as stream:
                stream.write(json.dumps(record, ensure_ascii=False)+'\n')

    class JudgeLLM(BaseRagasLLM):
        def generate_text(self, prompt, n=1, temperature=.01, stop=None, callbacks=None):
            generations = []
            for _ in range(n):
                body = call({'model':model, 'messages':[{'role':'user','content':prompt.to_string()}],
                    'response_format':{'type':'json_object'}, 'temperature':temperature or 0,
                    'thinking':{'type':'disabled'}})
                choice = body['choices'][0]
                if choice['finish_reason'] != 'stop':
                    raise ValueError('unfinished_ragas_output')
                generations.append(Generation(text=choice['message']['content']))
            return LLMResult(generations=[generations])

        async def agenerate_text(self, prompt, n=1, temperature=.01, stop=None, callbacks=None):
            return await asyncio.to_thread(self.generate_text, prompt, n, temperature, stop, callbacks)

        def is_finished(self, response):
            return True  # Each HTTP finish_reason was checked above.

    encoder = FlashCodeEncoder.from_env()

    class Embeddings(BaseRagasEmbeddings):
        def embed_query(self, text):
            return list(encoder.encode(text))

        def embed_documents(self, texts):
            return encoder.encode_documents(texts)

        async def aembed_query(self, text):
            return await asyncio.to_thread(self.embed_query, text)

        async def aembed_documents(self, texts):
            return await asyncio.to_thread(self.embed_documents, texts)

    config = RunConfig(timeout=60, max_retries=1, max_workers=1)
    llm = JudgeLLM(run_config=config)
    embeddings = Embeddings(); embeddings.set_run_config(config)
    faith = Faithfulness(llm=llm)
    relevance = ResponseRelevancy(llm=llm, embeddings=embeddings, strictness=3)
    facts = SQLiteDatabase(args.index/'facts.sqlite')
    tasks = EmbeddingTaskStore(facts)
    adapter = MilvusAdapter(args.index/'vectors.db', 'evaluation_vectors', dimension=encoder.dimension,
        model_revision=encoder.revision, template_revision='path-symbol-source-v1', projection_revision='evaluation-projection-v1')
    service = CodeRetrievalService(KeywordRetriever(SQLiteCodeSearchStore(facts)), adapter, channel_store=tasks,
        model_revision=encoder.revision, template_revision='path-symbol-source-v1', projection_revision='evaluation-projection-v1')
    database = SQLiteDatabase(args.output/'evidence.sqlite');database.initialize()
    evidence_store = SQLiteEvidenceStore(database)
    documents = {d.document_id:d for d in corpus.documents}
    saved = {}
    if args.resume_answers:
        for line in args.resume_answers.read_text().splitlines():
            old = json.loads(line)
            if old['query_id'] in saved:
                raise ValueError('duplicate saved answer')
            saved[old['query_id']] = old
    try:
        for query in queries:
            phase.update(query_id=query['id'], stage='retrieval')
            row = {'query_id':query['id'],'user_input':query['query'],'label_answerable':bool(query['relevant_ids'])}
            begin = time.monotonic()
            try:
                if query['id'] in saved:
                    old = saved[query['id']]
                    if old['user_input'] != query['query']:
                        raise ValueError('saved query mismatch')
                    packet = prepare_context(query, [c['document_id'] for c in old['context']['candidates']], documents, args.output, evidence_store)
                    if packet['candidates'] != old['context']['candidates']:
                        raise ValueError('saved disclosure mismatch')
                    packet['incomplete'] = old['context']['incomplete']
                    answer = old['answer']
                    by_id = {c['document_id']:c['evidence_id'] for c in packet['candidates']}
                    if answer['status'] not in ('answered','insufficient_evidence') or answer['query_id'] != query['id'] or any(by_id.get(c['document_id'])!=c['evidence_id'] for c in answer['citations']):
                        raise ValueError('saved evidence binding mismatch')
                    validate_verdict(packet, {'status':'supported' if answer['status']=='answered' else 'insufficient_evidence',
                        'reason':answer['response'],'citations':[{k:c[k] for k in ('document_id','quote')} for c in answer['citations']]})
                    row['context_reused_from'] = str(args.resume_answers)
                    if not args.regenerate_saved:
                        row['answer'] = answer
                        row['resumed_from'] = str(args.resume_answers)
                else:
                    result = service.search(query['query'], CodeSearchScope(**query['scope']), mode='hybrid', query_vector=encoder.encode(query['query']))
                    if result.error_code or result.degraded:
                        raise ValueError('retrieval_degraded')
                    packet = prepare_context(query, [h.document_id for h in result.hits], documents, args.output, evidence_store)
                if 'answer' not in row:
                    phase['stage'] = 'answer'
                    body = call(answer_request(packet, model, style=args.answer_style), strict=True)
                    choice = body['choices'][0]
                    toolcalls = choice['message'].get('tool_calls', [])
                    if choice['finish_reason'] != 'tool_calls' or len(toolcalls)!=1 or toolcalls[0]['function']['name']!='submit_answer':
                        raise ValueError('invalid_answer_tool_output')
                    row['answer'] = validate_answer(packet, json.loads(toolcalls[0]['function']['arguments']))
                    row['answer_style'] = args.answer_style
                    row['resolved_answer_style'] = select_answer_style(packet) if args.answer_style=='routed' else args.answer_style
                row['context'] = packet
                row['ragas_contexts'] = disclosed_contexts(packet)
                sample = SingleTurnSample(user_input=query['query'], response=row['answer']['response'],
                    retrieved_contexts=row['ragas_contexts'])
                # Persist the actual answer/disclosure before any judge request.
                with (args.output/'answers.jsonl').open('a') as stream:
                    stream.write(json.dumps(row, ensure_ascii=False)+'\n')
                for name, metric in [('faithfulness',faith),('answer_relevancy',relevance)]:
                    phase['stage'] = name
                    value = await metric.single_turn_ascore(sample)
                    row[name] = float(value) if math.isfinite(value) else None
            except Exception as exc:
                row['error_type'] = type(exc).__name__
                row['error_stage'] = phase['stage']
            row['elapsed_ms'] = (time.monotonic()-begin)*1000
            rows.append(row)
            with (args.output/'evaluated.jsonl').open('a') as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            print(query['id'], row.get('faithfulness'), row.get('answer_relevancy'), row.get('error_type'), flush=True)
            if row.get('error_type'):
                break
    finally:
        adapter.close();encoder.close();client.close()
    verified = 0
    for record in database.query('SELECT evidence_id FROM evidence'):
        evidence = SQLiteEvidenceStore(SQLiteDatabase(args.output/'evidence.sqlite')).get(record['evidence_id'])
        if hashlib.sha256(Path(evidence.content_ref).read_bytes()).hexdigest() != evidence.content_hash:
            raise ValueError('persisted evidence hash mismatch')
        verified += 1
    report = {'ragas_version':'0.4.3','fingerprints':corpus.metadata,'planned':len(queries),'observations':len(rows),
        'errors':sum('error_type' in r for r in rows),'execution_pass':len(rows)==len(queries) and all('error_type' not in r for r in rows),
        'quality_pass':False,'case_pass':False,'embedding':encoder.metadata(),'chat_calls':len(calls),
        'chat_usage':{k:sum((r.get('response',{}).get('usage')or{}).get(k,0) for r in calls) for k in ['prompt_tokens','completion_tokens','total_tokens']},
        'returned_models':sorted({r['response']['model'] for r in calls if 'response' in r}),
        'requested_model':model,'same_generator_and_judge':True,'cost':None,
        'persisted_evidence':verified,'elapsed_ms':(time.monotonic()-started)*1000,'groups':{}}
    report['deadline_exceeded'] = report['elapsed_ms'] > args.timeout*1000
    report['execution_pass'] = report['execution_pass'] and not report['deadline_exceeded']
    report['citation_count'] = sum(len(r.get('answer',{}).get('citations',[])) for r in rows)
    report['resumed_answers'] = sum('resumed_from' in r for r in rows)
    report['answer_style'] = args.answer_style
    report['index_fingerprints'] = source_report['fingerprints']
    report['regenerate_saved'] = args.regenerate_saved
    report['reused_contexts'] = sum('context_reused_from' in r for r in rows)
    report['resume_input_sha256'] = hashlib.sha256(args.resume_answers.read_bytes()).hexdigest() if args.resume_answers else None
    report['context_occurrences'] = sum(len(r.get('context',{}).get('candidates',[])) for r in rows)
    report['source_bytes_disclosed'] = sum(r.get('context',{}).get('disclosed_bytes',0) for r in rows)
    report['implementation_sha256'] = {str(p.relative_to(Path(__file__).resolve().parents[1])):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(__file__).resolve().parents[1]/'src/antisentinel/evaluation/rag_answers.py']}
    for answerable in [True,False]:
        group = [r for r in rows if r['label_answerable']==answerable]
        report['groups']['answerable' if answerable else 'no_answer'] = {'count':len(group)}
        for metric in ['faithfulness','answer_relevancy']:
            values = [r[metric] for r in group if r.get(metric) is not None and not r.get('error_type')]
            report['groups']['answerable' if answerable else 'no_answer'][metric] = {'mean_defined':statistics.mean(values) if values else None,'defined':len(values),'undefined_or_error':len(group)-len(values)}
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['execution_pass'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['repository','manifest','index','output']:
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--query-ids', nargs='*')
    parser.add_argument('--resume-answers', type=Path)
    parser.add_argument('--regenerate-saved', action='store_true')
    parser.add_argument('--answer-style', choices=['baseline','focused','routed'], default='baseline')
    parser.add_argument('--timeout', type=float, default=600)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
