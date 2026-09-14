"""Frozen expectations are independent of the resolver and are not model evals."""
from collections import Counter
from pathlib import Path
import hashlib
import json

from antisentinel.domain.intent import AuthorizedIntentContext, IntentCandidate, IntentIdentity
from antisentinel.control.intent_validation import resolve_candidate


FIXTURE = Path(__file__).parent / 'fixtures' / 'intents' / 'v1.json'


def test_frozen_bilingual_fixture_contract():
    assert FIXTURE.exists(), 'annotated fixture is not implemented'
    suite = json.loads(FIXTURE.read_text())
    assert suite['version'] == '1'
    cases = suite['cases']
    assert len(cases) >= 30
    counts = Counter(case['expected']['intent_type'] for case in cases)
    assert set(counts) == {'question', 'diagnose', 'execute', 'task_control', 'unknown'}
    assert min(counts.values()) >= 4
    assert len({c['id'] for c in cases}) == len(cases)
    assert {'zh', 'en'} == {c['language'] for c in cases}
    assert {'continuation', 'correction'} <= {c['candidate']['relation'] for c in cases}
    for case in cases:
        for field in ('message', 'context'):
            encoded = json.dumps(case[field], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
            assert hashlib.sha256(encoded).hexdigest() == case[field + '_hash']
        result = resolve_candidate(IntentCandidate.from_dict(case['candidate']),
                                   AuthorizedIntentContext.from_dict(case['context']),
                                   IntentIdentity.from_dict(case['identity'])).to_dict()
        assert result['intent_type'] == case['expected']['intent_type'], case['id']
        assert result['route'] == case['expected']['route'], case['id']
        for key, expected in case['expected']['required_fields'].items():
            assert result[key] == expected, case['id']
        assert result['constraints'] == case['candidate']['constraints'], case['id']
