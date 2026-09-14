#!/usr/bin/env python3
"""Evaluate a user-reviewed frozen dataset against DeepSeek without dispatch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT))

from antisentinel.evaluation.intent_effectiveness import score,recognize_case  # noqa: E402


def dump(path,value):
    with path.open('w') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.flush();os.fsync(stream.fileno())


def load_approved(dataset,approval):
    raw=dataset.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    reviewed=json.loads(approval.read_text())
    if reviewed.get('approved') is not True or reviewed.get('dataset_sha256')!=sha:
        raise ValueError('dataset approval missing or hash mismatch')
    suite=json.loads(raw)
    for case in suite['cases']:
        for key in ('message','context'):
            encoded=json.dumps(case[key],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
            if hashlib.sha256(encoded).hexdigest()!=case[key+'_hash']:raise ValueError('fixture hash mismatch')
    return suite,sha


def worker(args):
    from scripts.case_prd006a_real import configuration
    from antisentinel.adapters.llm.deepseek_intent import DeepSeekIntentAdapter
    suite,sha=load_approved(args.dataset,args.approval)
    cfg=configuration()
    model=DeepSeekIntentAdapter(base_url=cfg['ANTISENTINEL_MODEL_BASE_URL'],api_key=cfg['ANTISENTINEL_MODEL_API_KEY'],model=cfg['ANTISENTINEL_MODEL_NAME'])
    started=time.monotonic();records=[];business_completed=time.time();persisted=business_completed
    with (args.output/'records.jsonl').open('x') as stream:
        for case in suite['cases']:
            record=recognize_case(case,model)
            business_completed=time.time()
            record.update(message_hash=case['message_hash'],context_hash=case['context_hash'])
            records.append(record)
            stream.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n');stream.flush();os.fsync(stream.fileno())
            persisted=time.time()
    report=score(suite['cases'],records)
    source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
        'src/antisentinel/adapters/llm/deepseek_intent.py','src/antisentinel/control/intent_validation.py',
        'src/antisentinel/domain/intent.py','src/antisentinel/evaluation/intent_effectiveness.py')}
    report.update(dataset_version=suite['version'],dataset_sha256=sha,label_review='user_approved',
                  source_hashes=source_hashes,
                  configured_model=model.model,provider='deepseek',total_seconds=time.monotonic()-started,
                  business_completed_at=business_completed,persistence_completed_at=persisted,
                  persistence_lag_seconds=persisted-business_completed,persisted_records=len(records),
                  background_exceptions=0,tool_handler_calls=0,business_dispatches=0,
                  model_calls=model.request_count,case_pass=report['effectiveness_pass'])
    dump(args.output/'report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['effectiveness_pass'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=ROOT/'tests/fixtures/intents/model-v2.json')
    parser.add_argument('--approval',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    suite,sha=load_approved(args.dataset,args.approval)
    if args.worker:return worker(args)
    args.output.mkdir(parents=True,exist_ok=False)
    env={**os.environ,'ANTISENTINEL_INTENTS_ENABLED':'0','ANTISENTINEL_MODEL_MODE':'fake',
         'ANTISENTINEL_STORAGE_ROOT':'','ANTISENTINEL_REDIS_URL':''}
    timed_out=False
    try:
        result=subprocess.run([sys.executable,__file__,'--worker','--dataset',str(args.dataset),
                               '--approval',str(args.approval),'--output',str(args.output)],
                              env=env,text=True,capture_output=True,timeout=120)
        if (args.output/'report.json').exists():
            print(result.stdout);return result.returncode
    except subprocess.TimeoutExpired:timed_out=True
    records=[]
    path=args.output/'records.jsonl'
    if path.exists():
        for line in path.read_text().splitlines():
            try:records.append(json.loads(line))
            except json.JSONDecodeError:break
    report=score(suite['cases'],records)
    report.update(dataset_sha256=sha,label_review='user_approved',case_pass=False,
                  run_error='case_timeout' if timed_out else 'worker_failed',
                  persisted_records=len(records),inflight_model_usage='unavailable',total_seconds=120 if timed_out else None)
    dump(args.output/'report.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))
    return 1


if __name__=='__main__':raise SystemExit(main())
