"""Fixed-candidate contract evaluation; never a measurement of model classification."""
from collections import Counter
import hashlib
import json
from time import perf_counter

from antisentinel.control.intent_validation import resolve_candidate
from antisentinel.domain.intent import (
    AuthorizedIntentContext, IntentCandidate, IntentIdentity, IntentValidationError, ResolvedIntent,
)


def evaluate_cases(cases: list[dict]) -> tuple[list[dict], dict]:
    start = perf_counter()
    records, routes = [], Counter()
    errors = mismatches = associations = 0
    for case in cases:
        try:
            for key in ('message', 'context'):
                raw = json.dumps(case[key], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                if hashlib.sha256(raw).hexdigest() != case[key + '_hash']:
                    raise IntentValidationError(f'fixture {key} hash mismatch')
            result = resolve_candidate(IntentCandidate.from_dict(case['candidate']),
                                       AuthorizedIntentContext.from_dict(case['context']),
                                       IntentIdentity.from_dict(case['identity'])).to_dict()
            expected = case['expected']
            matched = (result['intent_type'] == expected['intent_type'] and result['route'] == expected['route']
                       and all(result[key] == val for key, val in expected['required_fields'].items())
                       and result['constraints'] == case['candidate']['constraints'])
            mismatches += int(not matched)
            if ResolvedIntent.from_dict(result).to_dict() == result:
                associations += 1
            routes[result['route']] += 1
            records.append({'case_id': case['id'], 'matched': matched, 'intent': result})
        except (IntentValidationError, KeyError, TypeError) as exc:
            errors += 1
            records.append({'case_id': case.get('id'), 'matched': False, 'error': str(exc)})
    return records, {
        'evaluation_kind': 'fixed_candidate_contract_not_model_classification',
        'inputs': len(cases), 'outputs': len(records) - errors,
        'mismatches': mismatches, 'fixture_errors': errors, 'associations_checked': associations,
        'routes': dict(routes), 'elapsed_seconds': perf_counter() - start,
        'model_calls': 0, 'contract_pass': bool(cases) and errors == mismatches == 0 and associations == len(cases),
    }
