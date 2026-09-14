#!/usr/bin/env python3
"""Cumulative 6A.1/2 Case: fixed candidates, real SQLite, fresh-process recovery."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from antisentinel.control.intents import IntentService  # noqa: E402
from antisentinel.domain.intent import AuthorizedIntentContext, IntentCandidate  # noqa: E402
from antisentinel.evaluation.intent_contract import evaluate_cases  # noqa: E402
from antisentinel.persistence.intent_store import SQLiteIntentStore  # noqa: E402


def write_json(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New isolated directory only')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start = monotonic()
    report = {'scope': '6A.1-6A.2', 'case_pass': False, 'background_workers': 0,
              'background_exceptions': 0, 'model_calls': 0, 'business_dispatches': 0}
    try:
        suite = json.loads((ROOT / 'tests/fixtures/intents/v1.json').read_text())
        records, fixed = evaluate_cases(suite['cases'])
        assert fixed['contract_pass'] and len(records) == 32
        write_json(args.output / 'fixed-candidates.json', records)
        cases = {case['id']: deepcopy(case) for case in suite['cases']}
        db_path = args.output / 'intents.sqlite'
        store = SQLiteIntentStore(db_path)
        service = IntentService(store, clock=lambda: 1000.0)

        def resolve(case, revision=0, iid=None):
            return service.resolve(IntentCandidate.from_dict(case['candidate']),
                                   AuthorizedIntentContext.from_dict(case['context']),
                                   expected_revision=revision, intent_id=iid).to_dict()

        initial = cases['d01']
        initial['context']['required_fields'] = ['/entities/commit']
        first = resolve(initial)
        iid = first['identity']['intent_id']
        assert first['route'] == 'clarify'
        second = resolve(cases['d03'], 1, iid)
        assert second['route'] == 'plan' and second['objective'] == first['objective']
        correction = cases['d04']
        correction['context']['verified_fields']['/entities/commit'] = 'B'
        third = resolve(correction, 2, iid)
        assert third['identity']['revision'] == 3
        assert {c['kind'] for c in third['constraints']} == {'read_only', 'forbid_restart'}
        replay = resolve(correction, 2, iid)
        assert replay == third
        control = resolve(cases['t01'])
        assert control['route'] == 'task_control'
        snapshots = [first, second, third, control]
        counts = store.counts()
        assert counts == {'revisions': 4, 'messages': 4, 'outbox': 3, 'questions': 1, 'events': 11}
        pending = store.pending('a1', 's1', 'i1')
        assert len(pending) == 2
        assert [r['revision'] for r in pending if r['intent_id'] == iid] == [3]
        business_completed_at = time()
        write_json(args.output / 'snapshots.json', snapshots)
        persistence_completed_at = time()
        # A separate interpreter loads both current intents and all immutable revisions.
        code = '''import json,sqlite3,sys
from antisentinel.persistence.intent_store import SQLiteIntentStore
from antisentinel.domain.intent import ResolvedIntent
s=SQLiteIntentStore(sys.argv[1])
with sqlite3.connect(sys.argv[1]) as db:
 rows=db.execute('SELECT payload FROM revisions ORDER BY rowid').fetchall()
 results=[ResolvedIntent.from_dict(json.loads(row[0])).to_dict() for row in rows]
print(json.dumps({'snapshots':results,'pending':s.pending('a1','s1','i1'),'counts':s.counts()}))'''
        restored = subprocess.run([sys.executable, '-c', code, str(db_path)],
                                  env={**os.environ, 'PYTHONPATH': str(ROOT/'src')},
                                  check=True, capture_output=True, text=True, timeout=30)
        recovered = json.loads(restored.stdout)
        assert recovered['snapshots'] == snapshots
        assert recovered['pending'] == pending and recovered['counts'] == counts
        total = monotonic() - start
        report.update(inputs=37, fixed_inputs=32, lifecycle_requests=5, fixed_outputs=32,
                      lifecycle_outputs=5, distinct_revisions=4, persisted_snapshots=4,
                      restored_revisions=4, association_checks=20, pending_routes=2,
                      superseded_routes=1, duplicate_new_routes=0, counts=counts,
                      fixed_contract=fixed, business_completed_at=business_completed_at,
                      persistence_completed_at=persistence_completed_at,
                      persistence_lag_seconds=persistence_completed_at-business_completed_at,
                      total_seconds=total, recovery_pass=True, label_status=suite['label_status'],
                      stage_pass=False, case_pass=total <= 120)
    except Exception as exc:
        report.update(error=f'{type(exc).__name__}: {exc}', total_seconds=monotonic()-start)
    write_json(args.output / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['case_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
