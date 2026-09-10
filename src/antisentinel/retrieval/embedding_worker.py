"""Process one durable embedding task, then reconcile its versioned projection."""
import hashlib
import json
import re
import time
import math
import grpc
from pymilvus.exceptions import MilvusException,ErrorCode

from .embedding_jobs import LeaseLost
from .models import CodeSearchDocument,CodeSearchScope,normalize_document_record
from .vector import VectorPoint


class EmbeddingWorker:
    def __init__(self,queue,index,embedder,*,owner,clock=time.time,lease_seconds=120):
        if not owner or lease_seconds<=0:raise ValueError('invalid worker lease configuration')
        if getattr(embedder,'max_retries',0)!=0:raise ValueError('worker must own embedding retries')
        if hasattr(index,'rpc_timeout') and index.rpc_timeout is None:raise ValueError('worker requires bounded Milvus RPC timeout')
        self.queue,self.index,self.embedder=queue,index,embedder
        self.owner,self.clock,self.lease_seconds=owner,clock,lease_seconds

    def _versions(self,run):
        return (run['model_revision'],run['dimension'],run['template_revision'],run['projection_revision'])

    def _point(self,doc,vector,run):
        if len(vector)!=run['dimension'] or any(type(v) not in (int,float) or not math.isfinite(v) for v in vector) or not any(vector):
            raise ValueError('invalid cached vector')
        return VectorPoint('derived',tuple(vector),doc.source_identity,doc.embedding_input_hash,
            run['model_revision'],run['dimension'],run['template_revision'],run['projection_revision'],doc.document_id)

    def run_once(self,rid):
        run=self.queue.run(rid)
        if self._versions(run)!=(self.index.model_revision,self.index.dimension,self.index.template_revision,self.index.projection_revision):
            raise ValueError('worker index version mismatch')
        if (self.embedder.model_name,self.embedder.dimension)!=(run['model_revision'],run['dimension']):
            raise ValueError('worker embedder version mismatch')
        if run['template_revision']!='path-symbol-source-v1':raise ValueError('unsupported embedding input template')
        try:
            lease=self.queue.claim(rid,self.owner,now=self.clock(),lease_seconds=self.lease_seconds)
        except ValueError as exc:
            if str(exc)!='embedding projection is not active':raise
            self.queue.set_run_state(rid,'blocked','projection_inactive')
            return {'worked':False,'status':'blocked','error_code':'projection_inactive'}
        if lease is None:
            return {'worked':False,'status':self.reconcile(rid)}
        try:
            doc=self.queue.document(lease,now=self.clock())
            if hasattr(self.index, 'source_ranges') and self.index.source_ranges != (doc.byte_start is not None):
                raise ValueError('source range schema does not match projection')
            text=f'{doc.path}\n{doc.symbol}\n{doc.text}'
            if hashlib.sha256(text.encode()).hexdigest()!=doc.embedding_input_hash or hashlib.sha256(doc.text.encode()).hexdigest()!=doc.source_hash:
                raise ValueError('embedding input hash mismatch')
            if lease['vector_json'] is None:
                values=self.embedder.embed_documents([text])
                if len(values)!=1:raise ValueError('embedding result count mismatch')
                vector=values[0]
            else:vector=json.loads(lease['vector_json'])
            self.queue.renew(lease,now=self.clock(),lease_seconds=self.lease_seconds)
            vector=self.queue.cache_vector(lease,vector,now=self.clock())
            point=self._point(doc,vector,run)
            if point.point_id!=lease['point_id']:raise ValueError('embedding point identity mismatch')
            if self.index.upsert([point])!=1:raise ConnectionError('vector upsert incomplete')
            self.queue.complete(lease,now=self.clock())
        except LeaseLost:
            return {'worked':True,'status':'lease_lost'}
        except Exception as exc:
            code,retryable,blocked=classify_failure(exc)
            try:self.queue.fail(lease,code,now=self.clock(),retryable=retryable,blocked=blocked)
            except LeaseLost:return {'worked':True,'status':'lease_lost'}
            return {'worked':True,'status':self.queue.run(rid)['status'],'error_code':code}
        return {'worked':True,'status':self.reconcile(rid)}

    def reconcile(self,rid):
        run=self.queue.run(rid)
        if run['status']=='blocked':return run['status']
        tasks=self.queue.task_rows(rid)
        if any(t['status']!='ready' for t in tasks):
            return 'degraded' if any(t['status']=='failed' for t in tasks) else run['status']
        try:
            points=[]
            for task in tasks:
                data=normalize_document_record(json.loads(task['document_json']));doc=CodeSearchDocument(**{k:data[k] for k in CodeSearchDocument.__dataclass_fields__})
                points.append(self._point(doc,json.loads(task['vector_json']),run))
            report=self.index.reconcile(points)
            return 'ready' if self.queue.publish(rid,report,now=self.clock()) else self.queue.run(rid)['status']
        except Exception as exc:
            code,retryable,blocked=classify_failure(exc)
            status='degraded' if retryable else 'blocked'
            self.queue.set_run_state(rid,status,code)
            return status


def classify_failure(error):
    if isinstance(error,PermissionError):return 'permission_denied',False,True
    rpc_code=error.code() if isinstance(error,grpc.RpcError) else (error.code if isinstance(error,MilvusException) else None)
    if rpc_code in (grpc.StatusCode.UNAVAILABLE,grpc.StatusCode.DEADLINE_EXCEEDED,grpc.StatusCode.RESOURCE_EXHAUSTED) or (isinstance(error,MilvusException) and error.code==ErrorCode.RATE_LIMIT):
        return 'milvus_transient',True,False
    if rpc_code in (grpc.StatusCode.PERMISSION_DENIED,grpc.StatusCode.UNAUTHENTICATED):return 'milvus_auth',False,True
    code=str(error)
    if code=='embedding projection is not active':return 'projection_inactive',False,True
    match=re.fullmatch(r'embedding_http_(\d{3})',code)
    if match:
        status=int(match[1])
        if status in (401,403):return 'embedding_auth',False,True
        if status in (408,429,500,502,503,504):return 'embedding_transient',True,False
        return 'embedding_configuration',False,True
    if isinstance(error,(TimeoutError,ConnectionError)) or code=='embedding_transport_error':
        return 'transport',True,False
    return 'embedding_or_index_error',False,True
