import pytest
from antisentinel.retrieval.models import CodeSearchHit
from antisentinel.retrieval.vector import VectorSearchHit
from antisentinel.retrieval.fusion import ReciprocalRankFusion


def identity(node, **kwargs):
    return {'repository_id':'r','snapshot_id':'s','published_generation':1,'commit_sha':'c','node_id':node,'chunk_id':node,'path':'a.py','source_hash':'hash-'+node,**kwargs}


def test_representative_document_and_source_are_from_the_same_record():
    left=CodeSearchHit('z',1,'keyword',identity('z',byte_start=0,byte_end=20))
    right=VectorSearchHit('a',.9,identity('a',byte_start=10,byte_end=30))
    result=ReciprocalRankFusion().combine([left],[right],limit=5)
    assert len(result)==1 and result[0].document_id=='z'
    assert result[0].source_identity==left.source_identity
    assert result[0].score==2/61


def test_same_hash_without_ranges_does_not_merge_different_chunks():
    left=CodeSearchHit('a',1,'keyword',identity('a',source_hash='same'))
    right=VectorSearchHit('b',.9,identity('b',source_hash='same'))
    assert len(ReciprocalRankFusion().combine([left],[right],limit=5))==2


@pytest.mark.parametrize('change',[{'repository_id':'other'},{'commit_sha':'other'},{'byte_start':True},{'byte_start':-1},{'byte_end':0}])
def test_scope_or_invalid_ranges_cannot_be_merged(change):
    left=CodeSearchHit('a',1,'keyword',identity('a',byte_start=0,byte_end=20))
    right=VectorSearchHit('b',.9,identity('b',byte_start=10,byte_end=30)|change)
    assert len(ReciprocalRankFusion().combine([left],[right],limit=5))==2


def test_duplicate_document_with_conflicting_source_fails_closed():
    left=CodeSearchHit('a',1,'keyword',identity('a'))
    right=VectorSearchHit('a',.9,identity('a',path='wrong.py'))
    with pytest.raises(ValueError,match='conflicting source'):
        ReciprocalRankFusion().combine([left],[right],limit=5)


def test_overlap_chain_does_not_absorb_disjoint_candidate():
    first=CodeSearchHit('a',1,'keyword',identity('a',byte_start=0,byte_end=10))
    bridge=VectorSearchHit('b',.9,identity('b',byte_start=5,byte_end=15))
    separate=VectorSearchHit('c',.8,identity('c',byte_start=12,byte_end=20))
    result=ReciprocalRankFusion().combine([first],[bridge,separate],limit=5)
    assert [r.document_id for r in result]==['a','c']


def test_duplicate_without_ranges_cannot_erase_a_known_range():
    first=CodeSearchHit('a',1,'keyword',identity('a',byte_start=0,byte_end=10))
    duplicate=CodeSearchHit('a',2,'keyword',identity('a'))
    conflict=VectorSearchHit('a',.9,identity('a',byte_start=20,byte_end=30))
    with pytest.raises(ValueError,match='conflicting source range'):
        ReciprocalRankFusion().combine([first,duplicate],[conflict],limit=5)


def test_graph_candidate_can_be_read_without_manually_selecting_chunk(tmp_path):
    from tests.test_retrieval_graph_boundaries import setup_source
    from antisentinel.retrieval.graph import GraphExpander,GraphSeed
    from antisentinel.retrieval.evidence import CodeEvidenceAssembler
    store,scope,symbols,_,source=setup_source(tmp_path)
    expanded=GraphExpander(store).expand([GraphSeed(symbols[0].node_id,'seed',1)],scope=scope)
    candidate=expanded.items[0].source_candidates[0]
    context=CodeEvidenceAssembler(source).select('incident-a',[candidate])
    assert len(context.slices)==1
    evidence=source.evidence_store.get(context.slices[0].evidence_id)
    assert evidence.metadata['node_id']==candidate['node_id']
    assert evidence.metadata['chunk_id']==candidate['chunk_id']


@pytest.mark.parametrize('field,value',[('node_id','wrong'),('path','wrong.py'),('byte_start',999)])
def test_candidate_identity_mismatch_is_rejected_before_evidence_write(tmp_path,field,value):
    from tests.test_retrieval_graph_boundaries import setup_source
    from antisentinel.retrieval.evidence import CodeEvidenceAssembler
    from antisentinel.domain.errors import DomainError
    store,scope,_,chunk,source=setup_source(tmp_path)
    candidate={**scope,'chunk_id':chunk['chunk_id'],'source_hash':chunk['content_hash'],field:value}
    with pytest.raises(DomainError,match='source_identity_mismatch'):
        CodeEvidenceAssembler(source).select('incident-a',[candidate])
    assert store.database.query('SELECT * FROM evidence')==[]


def test_multiple_graph_chunks_are_ordered_and_all_can_become_evidence(tmp_path):
    import hashlib,json
    from tests.test_retrieval_graph_boundaries import setup_source
    from antisentinel.retrieval.graph import GraphExpander,GraphSeed
    from antisentinel.retrieval.evidence import CodeEvidenceAssembler
    from antisentinel.retrieval.engine import _select_graph_hits
    from antisentinel.retrieval.fusion import FusedCodeSearchHit
    store,scope,symbols,_,source=setup_source(tmp_path)
    payload=store.read_generation(scope['snapshot_id'],1)
    child=next(c for c in payload['chunks'] if c['node_id']==symbols[1].node_id)
    original=store.read_generation_chunk(scope['repository_id'],scope['snapshot_id'],1,child['chunk_id'])['content']
    data=original.encode();split=len(data.splitlines(keepends=True)[0]);assert 0<split<len(data)
    head={**child,'byte_end':child['byte_start']+split,'content_hash':hashlib.sha256(data[:split]).hexdigest()}
    tail={**child,'chunk_id':child['chunk_id']+'-tail','byte_start':child['byte_start']+split,'content_hash':hashlib.sha256(data[split:]).hexdigest()}
    payload['chunks']=[c for c in payload['chunks'] if c['chunk_id']!=child['chunk_id']]+[tail,head]
    with store.database.transaction() as c:c.execute('UPDATE code_map_generations SET payload_json=?',(json.dumps(payload),))
    result=GraphExpander(store).expand([GraphSeed(symbols[0].node_id,'seed',1)],scope=scope)
    candidates=result.items[0].source_candidates
    assert [c['chunk_id'] for c in candidates]==[head['chunk_id'],tail['chunk_id']]
    hits=tuple(FusedCodeSearchHit(c['chunk_id'],1.,('graph',),{'graph':i},c) for i,c in enumerate(candidates,1))
    assert len(_select_graph_hits((),hits,5)[0])==2
    context=CodeEvidenceAssembler(source).select('incident-a',list(candidates))
    assert ''.join(s.content for s in context.slices)==original


def test_graph_chunk_candidates_are_bounded_and_invalid_paths_rejected(tmp_path):
    import json
    from tests.test_retrieval_graph_boundaries import setup_source
    from antisentinel.retrieval.graph import GraphExpander,GraphSeed
    store,scope,symbols,_,_=setup_source(tmp_path)
    payload=store.read_generation(scope['snapshot_id'],1)
    child=next(c for c in payload['chunks'] if c['node_id']==symbols[1].node_id)
    payload['chunks']=[c for c in payload['chunks'] if c['node_id']!=symbols[1].node_id]+[
        {**child,'chunk_id':f'part-{i}','byte_start':child['byte_start']+i,'byte_end':child['byte_start']+i+1} for i in range(6)]
    payload['chunks'].append({**child,'chunk_id':'bad','path':'foreign.py'})
    with store.database.transaction() as c:c.execute('UPDATE code_map_generations SET payload_json=?',(json.dumps(payload),))
    result=GraphExpander(store).expand([GraphSeed(symbols[0].node_id,'seed',1)],scope=scope)
    assert len(result.items[0].source_candidates)==5
    assert result.truncated and result.incomplete and result.degraded
    assert result.rejected_chunks==1 and result.unreadable_nodes==0
    assert all(c['path']==child['path'] for c in result.items[0].source_candidates)
