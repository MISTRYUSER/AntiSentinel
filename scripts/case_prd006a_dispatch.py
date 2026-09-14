#!/usr/bin/env python3
"""Cumulative intent handoff Case; all model responses and receivers are contract-only."""
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from time import monotonic, time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from antisentinel.control.intents import IntentService  # noqa: E402
from antisentinel.control.intent_dispatch import IntentDispatcher  # noqa: E402
from antisentinel.domain.intent import AuthorizedIntentContext  # noqa: E402
from antisentinel.entry.intent_application import IntentApplication  # noqa: E402
from antisentinel.evaluation.intent_receiver import SQLiteIntentReceiver  # noqa: E402
from antisentinel.persistence.intent_store import SQLiteIntentStore  # noqa: E402
from antisentinel.ports.intent_store import IntentAccessDenied, IntentConflict  # noqa: E402


class FixedInput:
    def __init__(self):
        self.context = None
        self.candidate = None
        self.calls = 0

    def read(self, actor, session, incident):
        return AuthorizedIntentContext.from_dict(self.context)

    def extract(self, request, *, timeout_seconds):
        assert request.tools == () and 0 < timeout_seconds <= 30
        self.calls += 1
        return deepcopy(self.candidate)


def write(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New isolated directory only')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start = monotonic()
    report = {'case_pass': False, 'scope': '6A.1-6A.3-contract', 'real_model_calls': 0,
              'real_business_calls': 0, 'background_workers': 0, 'background_exceptions': 0}
    try:
        previous = subprocess.run([sys.executable, str(ROOT/'scripts/case_prd006a_lifecycle.py'),
                                   '--output', str(args.output/'lifecycle')],
                                  text=True, capture_output=True, timeout=60, check=True)
        lifecycle = json.loads(previous.stdout)
        assert lifecycle['case_pass']
        store = SQLiteIntentStore(args.output/'intents.sqlite')
        receiver = SQLiteIntentReceiver(args.output/'receiver.sqlite')
        inputs = FixedInput()
        service = IntentService(store)
        dispatcher = IntentDispatcher(store, inputs, answer=receiver, plan=receiver, control=receiver)
        entry = IntentApplication(service, dispatcher, inputs)
        cases = {c['id']: c for c in json.loads((ROOT/'tests/fixtures/intents/v1.json').read_text())['cases']}
        outputs = []

        def submit(cid, revision=0, iid=None, active=False):
            inputs.context = deepcopy(cases[cid]['context'])
            inputs.candidate = deepcopy(cases[cid]['candidate'])
            if active:
                task = {'task_id': 't1', 'run_id': 'r1', 'state_version': 'v1'}
                inputs.context.update(tasks=[task], active_write_task=task, active_write_intent_id=iid)
            result = entry.submit(AuthorizedIntentContext.from_dict(inputs.context), expected_revision=revision, intent_id=iid)
            outputs.append(result)
            return result

        question = submit('q01')
        assert submit('q01') == question
        plan = submit('d01')
        control = submit('t01')
        assert submit('u01')['delivery_status'] == 'clarify'
        assert submit('u03')['delivery_status'] == 'unsupported'
        iid = plan['intent']['identity']['intent_id']
        stopping = submit('d04', 1, iid, active=True)
        assert stopping['delivery_status'] == 'waiting_for_stop'
        assert len(receiver.records()) == 4
        receiver.complete(stopping['receipt']['key'])
        updated = submit('d04', 1, iid, active=True)
        assert updated['receipt']['route'] == 'plan'
        assert {c['kind'] for c in updated['intent']['constraints']} == {'read_only', 'forbid_restart'}
        try:
            dispatcher.dispatch(iid, 1, 'a1', 's1', 'i1')
            raise AssertionError('stale revision dispatched')
        except IntentConflict:
            pass
        inputs.context = deepcopy(cases['t01']['context'])
        inputs.context['tasks'] = []
        try:
            dispatcher.dispatch(control['intent']['identity']['intent_id'], 1, 'a1', 's1', 'i1')
            raise AssertionError('revoked task dispatched')
        except IntentAccessDenied:
            pass
        accepted = receiver.records()
        assert len(outputs) == 8 and len(accepted) == 5 and inputs.calls == 6
        business_completed_at = time()
        write(args.output/'outputs.json', outputs)
        write(args.output/'accepted.json', accepted)
        persistence_completed_at = time()
        code = '''import json,sqlite3,sys
from antisentinel.domain.intent import ResolvedIntent
with sqlite3.connect(sys.argv[1]) as db:
 assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 revisions=[ResolvedIntent.from_dict(json.loads(r[0])).to_dict() for r in db.execute('SELECT payload FROM revisions')]
 receipts=[json.loads(r[0]) for r in db.execute('SELECT payload FROM route_receipts')]
 events=[json.loads(r[0]) for r in db.execute("SELECT metadata FROM events WHERE type='intent.routed'")]
print(json.dumps({'revisions':revisions,'receipts':receipts,'events':events}))'''
        child = subprocess.run([sys.executable, '-c', code, str(store.path)],
                               env={**os.environ, 'PYTHONPATH': str(ROOT/'src')},
                               check=True, text=True, capture_output=True, timeout=30)
        recovered = json.loads(child.stdout)
        assert len(recovered['revisions']) == 6 and len(recovered['receipts']) == 5
        assert {r['receipt_id'] for r in recovered['receipts']} == {r['receipt']['receipt_id'] for r in accepted}
        assert all(r['receiver_kind'] == 'contract' for r in recovered['receipts'])
        assert any(e['plan_id'] for e in recovered['events'])
        write(args.output/'recovered.json', recovered)
        report.update(inputs=45, outputs=45, prior_lifecycle=lifecycle, stage3_requests=8,
                      stage3_revisions=6, receiver_acceptances=5, restored_receipts=5,
                      duplicate_acceptances=0, stale_revision_rejections=1, revoked_access_rejections=1,
                      fixed_model_calls=inputs.calls, model_tools=0, counts=store.counts(),
                      business_completed_at=business_completed_at, persistence_completed_at=persistence_completed_at,
                      persistence_lag_seconds=persistence_completed_at-business_completed_at,
                      total_seconds=monotonic()-start, stage_pass=False, recovery_pass=True)
        report['case_pass'] = report['total_seconds'] <= 120
    except Exception as exc:
        report.update(error=f'{type(exc).__name__}: {exc}', total_seconds=monotonic()-start)
    write(args.output/'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['case_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
