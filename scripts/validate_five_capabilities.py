#!/usr/bin/env python3
"""Offline five-capability regression entry; nonzero exit for incomplete regression."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from antisentinel.evaluation.five_capabilities import GROUPS, build_report, run_group, source_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--group', action='append', choices=GROUPS, help='Partial diagnostic run; cannot pass the full suite.')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('--timeout must be positive')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    fingerprint = source_fingerprint(ROOT)
    results = {}
    for name in dict.fromkeys(args.group or GROUPS):
        results[name] = run_group(name, GROUPS[name], root=ROOT, output=args.output, timeout=args.timeout)
        report = build_report(results, source_fingerprint=fingerprint)
        (args.output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        result = results[name]
        print(f"{name}: tests={result['tests']} failures={result['failures']} skipped={result['skipped']} pass={result['passed']}", flush=True)
    if source_fingerprint(ROOT) != fingerprint:
        report['regression_pass'] = False
        report['source_changed_during_run'] = True
        (args.output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return 0 if report['regression_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
