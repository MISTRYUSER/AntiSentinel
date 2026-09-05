import json
from types import SimpleNamespace
from antisentinel.memory.candidates import CandidateSource, RuleCandidateExtractor
from antisentinel.memory.recall import MemoryRecall
from antisentinel.memory.models import MemoryScope

extractor=RuleCandidateExtractor()
a=extractor.extract(CandidateSource('op-a','session-a',agent_conclusion='timeout'))
b=extractor.extract(CandidateSource('op-b','session-b',agent_conclusion='connection reset'))
base={'memory_id':'m1','operator_id':'op-a','status':'active','confidence':0.9,'content':'日志','source_refs':[]}
def read(record, tree_text='', budget=20):
    tree=SimpleNamespace(digest=lambda token_budget:{'summary':tree_text})
    return MemoryRecall({'s':tree},records_provider=lambda _: [record]).recall(MemoryScope('session','s'),query='日志',token_budget=budget,operator_id='op-a',incident_id='i')
text_record={k:v for k,v in base.items() if k!='content'}
text_record['text']='日志'
legacy={k:v for k,v in base.items() if k!='source_refs'}
legacy['source_ids']=['session-origin']
future={**base,'valid_from':'2099-01-01T00:00:00+00:00'}
budget_view=read({**base,'content':'记'*40}, tree_text='摘'*80,budget=20)
print(json.dumps({
 'candidate_id':{'inputs':2,'unique_ids':len({a[0].candidate_id,b[0].candidate_id}),'expected_unique':2},
 'text':{'input_chars':len(text_record['text']),'output_chars':len(read(text_record).digest),'expected_output_chars':2},
 'provenance':{'session_ids_promoted_to_evidence':len(read(legacy).evidence_refs),'expected':0},
 'validity':{'future_records_selected':len(read(future).memory_ids),'expected':0},
 'budget':{'requested_tokens':20,'digest_chars':len(budget_view.digest),'estimate_using_assembler_formula':(len(budget_view.digest)+1)//2,'expected_max_estimate':20}
},ensure_ascii=False,indent=2))
