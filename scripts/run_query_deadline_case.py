"""Real local TLS/SQLite/Milvus deadline Case, no external model calls."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import subprocess
import sys
from threading import Event, Thread
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import httpx
from antisentinel.code_map.models import MapSnapshot, RepositoryRegistration
from antisentinel.code_map.python_parser import PythonAstParser
from antisentinel.code_map.source_context import SourceEvidenceService
from antisentinel.code_map.store import SQLiteCodeMapStore, SourceBlob, SourceFile, StagedMapRows
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore
from antisentinel.persistence.local_vector_memory import QwenFlashEmbedder
from antisentinel.retrieval.coordinator import RetrievalCoordinator
from antisentinel.retrieval.milvus_adapter import MilvusAdapter
from antisentinel.runtime.deadline import query_budget, QueryDeadlineExceeded


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    cert, key = output/'cert.pem', output/'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
        '-keyout',str(key),'-out',str(cert),'-subj','/CN=localhost',
        '-addext','subjectAltName=IP:127.0.0.1,DNS:localhost'], check=True, capture_output=True, timeout=20)
    key.chmod(0o600)
    release, finished = Event(), Event()
    state = {'slow': True, 'calls': 0, 'active': 0, 'errors': [], 'trickle_chunks': 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            state['calls'] += 1
            state['active'] += 1
            try:
                self.rfile.read(int(self.headers['Content-Length']))
                body = json.dumps({'model':QwenFlashEmbedder.model_name,
                    'data':[{'index':0,'embedding':[1.] + [0.]*255}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body[:1]); self.wfile.flush()
                offset = 1
                if state['slow']:
                    # Data keeps arriving faster than the socket idle timeout.
                    # Only the enclosing request deadline can bound this stream.
                    for _ in range(20):
                        if release.wait(.04): break
                        self.wfile.write(body[offset:offset+1]); self.wfile.flush()
                        offset += 1
                        state['trickle_chunks'] += 1
                    release.wait(2)
                self.wfile.write(body[offset:]); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass  # Expected when the timed-out client cancels the request.
            except Exception as error:
                state['errors'].append(type(error).__name__)
            finally:
                state['active'] -= 1
                finished.set()
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert,key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    trust = ssl.create_default_context(cafile=str(cert))
    class LocalProtocolEmbedder(QwenFlashEmbedder):
        def embed_documents(self, texts):
            return [[1.]+[0.]*255 for _ in texts]
    embedder = LocalProtocolEmbedder(base_url=f'https://127.0.0.1:{server.server_port}',
        api_key='local-fixture-only', dimension=256, max_retries=0,
        async_client_factory=lambda **kwargs: httpx.AsyncClient(verify=trust, **kwargs))
    database = SQLiteDatabase(output/'facts.sqlite')
    database.initialize()
    store = SQLiteCodeMapStore(database)
    store.register(RepositoryRegistration('repo','file:///fixture','local','main'))
    job = store.enqueue('repo','a'*40,'manual')
    lease = store.claim_job('case',datetime.now(timezone.utc))
    data = b'class Service:\n    def run(self):\n        return 42\n'
    parser = PythonAstParser('python-ast-v1')
    parsed = parser.parse_file('app.py',data,'snapshot')
    assert store.publish(lease, MapSnapshot('snapshot','repo','a'*40,'python-ast-v1',job.rules_digest,file_count=1),
        StagedMapRows(nodes=parsed.symbols,edges=parser.contains_edges((parsed,),'snapshot'),chunks=parsed.chunks,
            blobs=(SourceBlob(parsed.file_hash,'object',data,parsed.encoding),),
            files=(SourceFile('file','snapshot','app.py','object',parsed.file_hash,len(data)),))).ok
    store.bind_incident('incident','repo','snapshot')
    coordinator = RetrievalCoordinator(store, SourceEvidenceService(None,store,SQLiteEvidenceStore(database)),
        allowed_repositories=['repo'], embedder_factory=lambda:embedder,
        index_factory=lambda e:MilvusAdapter(output/'vectors.db','deadline',dimension=256,
            model_revision=e.model_name,template_revision='path-symbol-source-v1',projection_revision='p',rpc_timeout=5),
        poll_seconds=.02, query_timeout=.4)
    checks, timings = {}, {}
    try:
        coordinator.start()
        deadline = time.monotonic()+10
        while coordinator.health()['runs'] != {'ready':1}:
            if coordinator.errors or time.monotonic()>deadline:
                raise RuntimeError('projection did not become ready')
            time.sleep(.02)
        search = coordinator.for_incident('incident')[0]
        begin = time.monotonic()
        result = search.handler({'query':'Service.run','mode':'hybrid'})
        timings['slow_http_ms'] = (time.monotonic()-begin)*1000
        checks['keyword_fallback'] = result.status=='succeeded' and result.result['degraded'] and bool(result.result['hits']) and result.result['channel_statuses']['vector']=='unavailable'
        fallback_count = len(result.result['hits']) if result.status=='succeeded' else 0
        checks['http_budget'] = timings['slow_http_ms'] < 650
        release.set()
        checks['server_request_drained'] = finished.wait(2) and state['active']==0
        checks['continuous_response_was_cancelled'] = state['trickle_chunks'] >= 2
        state['slow'] = False
        coordinator.query_timeout = 1
        result = search.handler({'query':'Service.run','mode':'hybrid'})
        checks['next_query_healthy'] = result.status=='succeeded' and not result.result['degraded'] and len(result.result['hits'])==2
        normal_count = len(result.result['hits']) if result.status=='succeeded' else 0
        calls = state['calls']
        coordinator.query_timeout = .08
        pool = ThreadPoolExecutor(1)
        coordinator.lock.acquire()
        try:
            begin = time.monotonic()
            blocked = pool.submit(search.handler, {'query':'Service'}).result(timeout=1)
            timings['lock_wait_ms'] = (time.monotonic()-begin)*1000
            checks['lock_deadline'] = blocked.status=='failed' and blocked.error['code']=='query_deadline_exceeded' and timings['lock_wait_ms']<300
            checks['lock_timeout_no_http'] = state['calls']==calls
        finally:
            coordinator.lock.release()
            pool.shutdown()
        checks['no_evidence_side_effect'] = database.query('SELECT COUNT(*) FROM evidence')[0][0]==0
        checks['no_background_errors'] = not state['errors'] and not coordinator.errors
        business_done = time.monotonic()
    finally:
        release.set()
        coordinator.stop()
        server.shutdown(); server.server_close(); thread.join(2)
    checks['closed'] = not coordinator.health()['thread_alive'] and not thread.is_alive() and state['active']==0
    persisted_tasks = database.query('SELECT COUNT(*) FROM code_embedding_tasks')[0][0]
    verified = time.monotonic()
    report = {'case_pass':all(checks.values()),'checks':checks,'timings':timings,
        'input_files':1,'input_bytes':len(data),'persisted_documents':len(parsed.chunks),
        'persisted_tasks':persisted_tasks,'fallback_output_count':fallback_count,'normal_output_count':normal_count,
        'business_completed_ms':(business_done-started)*1000,'storage_verified_ms':(verified-started)*1000,
        'verification_lag_ms':(verified-business_done)*1000,
        'local_https_requests':state['calls'],'trickle_chunks':state['trickle_chunks'],
        'external_model_calls':0,'background_exceptions':len(state['errors']),
        'elapsed_ms':(time.monotonic()-started)*1000,'retries':0}
    (output/'report.json').write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    result=run(args.output);print(json.dumps(result,indent=2));raise SystemExit(0 if result['case_pass'] else 1)
