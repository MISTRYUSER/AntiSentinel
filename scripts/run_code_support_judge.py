"""Execute an explicitly authorized frozen DeepSeek support-judge Case."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from antisentinel.evaluation.support_judge import strict_judge_request, parse_strict_judge_response, RANGE_SYSTEM_PROMPT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packets', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=600)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if not 0 < args.timeout <= 600:
        raise ValueError('timeout must be within 600 seconds')
    packets, fingerprints = [], {}
    for path in args.packets:
        data = path.read_bytes()
        fingerprints[str(path)] = hashlib.sha256(data).hexdigest()
        packets.extend(json.loads(data))
    if not packets or len(packets) > 48 or len({p['query_id'] for p in packets}) != len(packets):
        raise ValueError('expected 1-48 unique frozen queries')
    base = httpx.URL(os.environ['ANTISENTINEL_MODEL_BASE_URL'])
    if base.scheme != 'https' or base.host != 'api.deepseek.com' or base.userinfo or base.query or base.fragment:
        raise ValueError('expected the authorized DeepSeek endpoint')
    model, key = os.environ['ANTISENTINEL_MODEL_NAME'], os.environ['ANTISENTINEL_MODEL_API_KEY']
    if not model.strip() or not key.strip():
        raise ValueError('missing model configuration')
    requests = [strict_judge_request(p, model) for p in packets]
    args.output.mkdir()
    (args.output/'requests.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in requests))
    rows, started = [], time.monotonic()
    deadline_exceeded = False
    with httpx.Client(follow_redirects=False) as client:
        for packet, request in zip(packets, requests, strict=True):
            begin = time.monotonic()
            remaining = args.timeout - (begin-started)
            if remaining <= 0:
                deadline_exceeded = True
                break
            row = {'query_id': packet['query_id']}
            try:
                response = client.post('https://api.deepseek.com/beta/chat/completions',
                    headers={'Authorization': 'Bearer '+key}, json=request, timeout=min(30, remaining))
                row['http_status'] = response.status_code
                if response.status_code != 200:
                    raise ValueError('judge_http_failure')
                body = response.json()
                row.update(model=body.get('model'), usage=body.get('usage'), response_id=body.get('id'), raw_choices=body.get('choices'))
                row['range_verdict'], row['verdict'] = parse_strict_judge_response(packet, body)
            except Exception as exc:
                row['error_type'] = type(exc).__name__
                row['error'] = str(exc) if isinstance(exc, ValueError) else 'request_or_protocol_failure'
            row['elapsed_ms'] = (time.monotonic()-begin)*1000
            rows.append(row)
            with (args.output/'responses.jsonl').open('a') as stream:
                stream.write(json.dumps(row, ensure_ascii=False)+'\n')
            print(row['query_id'], row.get('verdict', {}).get('status', row.get('error')), flush=True)
            if 'error' in row:
                break
    elapsed = (time.monotonic()-started)*1000
    report = {'requested_model': model, 'planned': len(packets), 'attempted': len(rows),
        'valid': sum('verdict' in r for r in rows), 'errors': sum('error' in r for r in rows),
        'elapsed_ms': elapsed, 'deadline_exceeded': deadline_exceeded or elapsed > args.timeout*1000,
        'client_max_tokens': None, 'automatic_retries': 0, 'cost': None,
        'input_sha256': fingerprints, 'prompt_sha256': hashlib.sha256(RANGE_SYSTEM_PROMPT.encode()).hexdigest(),
        'usage': {k: sum((r.get('usage') or {}).get(k, 0) for r in rows) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')},
        'usage_responses': sum(bool(r.get('usage')) for r in rows)}
    report['execution_pass'] = report['valid'] == len(packets) and not report['deadline_exceeded']
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return 0 if report['execution_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
