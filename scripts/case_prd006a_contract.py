#!/usr/bin/env python3
"""Run the 6A.1 contract Case only; no model, business services or downstream dispatch."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic, time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from antisentinel.domain.intent import ResolvedIntent  # noqa: E402
from antisentinel.evaluation.intent_contract import evaluate_cases  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New isolated directory; existing paths are refused.')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = monotonic()
    fixture = ROOT / 'tests/fixtures/intents/v1.json'
    raw = fixture.read_bytes()
    suite = json.loads(raw)
    records, report = evaluate_cases(suite['cases'])
    business_completed_at = time()
    output_file = args.output / 'intents.jsonl'
    with output_file.open('x') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    persisted_at = time()
    loaded = [json.loads(line) for line in output_file.read_text().splitlines()]
    restored = sum(1 for item in loaded if 'intent' in item and
                   ResolvedIntent.from_dict(item['intent']).to_dict() == item['intent'])
    classes = Counter(case['expected']['intent_type'] for case in suite['cases'])
    report.update(
        stage='6A.1', receiver_kind='none_contract_only', dataset_sha256=hashlib.sha256(raw).hexdigest(),
        label_status=suite['label_status'], persisted_records=len(loaded), restored_records=restored,
        persistence_pass=loaded == records, class_counts=dict(classes),
        business_completed_at=business_completed_at, persistence_completed_at=persisted_at,
        persistence_lag_seconds=persisted_at-business_completed_at,
        total_seconds=monotonic()-started, background_exceptions=0,
        background_workers=0, business_dispatches=0,
        performance_regression='N/A: no comparable preimplementation contract baseline',
        model_effectiveness='N/A: no model calls',
    )
    # This local contract Case excludes later-stage effects and production model quality.
    report['case_pass'] = bool(report['contract_pass'] and report['persistence_pass'] and
                               restored == len(records) >= 30 and len(classes) == 5 and
                               min(classes.values()) >= 4 and report['total_seconds'] <= 120)
    report['stage_pass'] = False  # Human fixture review and later integration are not established here.
    report_path = args.output / 'report.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['case_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
