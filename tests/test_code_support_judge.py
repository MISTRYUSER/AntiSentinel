import json

import pytest

from antisentinel.evaluation.support_judge import make_packet, validate_verdict, messages_for_ranges, validate_range_verdict
from antisentinel.retrieval.models import CodeSearchDocument


def fixture():
    doc = CodeSearchDocument('repo', 'snap', 1, 'commit', 'node', 'chunk', 'a.py', 'f', 'python', 'hash', 'input', 'p', 'def f():\n    return 123\n')
    query = {'id': 'q', 'query': '返回123在哪里实现？', 'scope': {'repository_id': 'repo', 'snapshot_id': 'snap', 'published_generation': 1, 'commit_sha': 'commit'}, 'relevant_ids': [doc.document_id], 'rationale': 'SECRET_GOLD_LABEL'}
    return query, doc


def test_packet_hides_gold_labels_and_keeps_source_identity():
    query, doc = fixture()
    packet = make_packet(query, [doc.document_id], {doc.document_id: doc})
    serialized = json.dumps(packet)
    assert 'SECRET_GOLD_LABEL' not in serialized and 'relevant_ids' not in serialized
    assert packet['candidates'][0]['source_identity'] == doc.source_identity
    assert packet['candidates'][0]['text'] == doc.text


def test_packet_rejects_wrong_scope_and_duplicate_ids():
    query, doc = fixture()
    with pytest.raises(ValueError):
        make_packet(query, [doc.document_id] * 2, {doc.document_id: doc})
    query['scope']['commit_sha'] = 'wrong'
    with pytest.raises(ValueError):
        make_packet(query, [doc.document_id], {doc.document_id: doc})


def test_supported_verdict_requires_exact_disclosed_quote():
    query, doc = fixture()
    packet = make_packet(query, [doc.document_id], {doc.document_id: doc})
    verdict = {'status': 'supported', 'reason': 'return exists', 'citations': [{'document_id': doc.document_id, 'quote': 'return 123'}]}
    assert validate_verdict(packet, verdict) == verdict
    verdict['citations'][0]['quote'] = 'return 456'
    with pytest.raises(ValueError, match='quote'):
        validate_verdict(packet, verdict)


def test_truncated_context_cannot_prove_no_support():
    query, doc = fixture()
    packet = make_packet(query, [doc.document_id], {doc.document_id: doc}, byte_budget=8)
    assert packet['incomplete']
    with pytest.raises(ValueError, match='incomplete'):
        validate_verdict(packet, {'status': 'not_supported', 'reason': 'missing', 'citations': []})
    validate_verdict(packet, {'status': 'insufficient_evidence', 'reason': 'truncated', 'citations': []})


def test_range_protocol_resolves_exact_source_without_model_copying():
    query, doc = fixture()
    packet = make_packet(query, [doc.document_id], {doc.document_id: doc})
    message = json.loads(messages_for_ranges(packet)[1]['content'])
    assert message['candidates'][0]['source_lines'][1] == {'line': 2, 'text': '    return 123\n'}
    assert 'text' not in message['candidates'][0]
    verdict = {'status': 'supported', 'reason': 'return', 'citations': [{'document_id': doc.document_id, 'start_line': 2, 'end_line': 2}]}
    result = validate_range_verdict(packet, verdict)
    assert result['citations'][0]['quote'] == '    return 123\n'
    validate_verdict(packet, result)


@pytest.mark.parametrize('start,end', [(0, 1), (2, 1), (1, 3), (True, 1), ('1', 1)])
def test_range_protocol_rejects_invalid_offsets(start, end):
    query, doc = fixture()
    packet = make_packet(query, [doc.document_id], {doc.document_id: doc})
    with pytest.raises(ValueError, match='range'):
        validate_range_verdict(packet, {'status': 'supported', 'reason': 'x', 'citations': [{'document_id': doc.document_id, 'start_line': start, 'end_line': end}]})


def test_range_protocol_rejects_model_supplied_quote_and_unknown_document():
    query, doc = fixture()
    packet = make_packet(query, [doc.document_id], {doc.document_id: doc})
    for citation in [{'document_id': 'unknown', 'start_line': 1, 'end_line': 1}, {'document_id': doc.document_id, 'start_line': 1, 'end_line': 1, 'quote': '...'}]:
        with pytest.raises(ValueError):
            validate_range_verdict(packet, {'status': 'supported', 'reason': 'x', 'citations': [citation]})


def test_range_protocol_accepts_only_matching_query_id_metadata():
    query, doc = fixture()
    packet = make_packet(query, [doc.document_id], {doc.document_id: doc})
    verdict = {'query_id': 'q', 'status': 'supported', 'reason': 'x', 'citations': [{'document_id': doc.document_id, 'start_line': 1, 'end_line': 2}]}
    assert validate_range_verdict(packet, verdict)['status'] == 'supported'
    verdict['query_id'] = 'different'
    with pytest.raises(ValueError, match='query identity'):
        validate_range_verdict(packet, verdict)


def test_strict_request_requires_status_and_does_not_set_token_cap():
    from antisentinel.evaluation.support_judge import strict_judge_request, parse_strict_judge_response
    query, doc = fixture()
    packet = make_packet(query, [doc.document_id], {doc.document_id: doc})
    request = strict_judge_request(packet, 'deepseek-chat')
    assert 'max_tokens' not in request
    function = request['tools'][0]['function']
    assert function['strict'] and 'status' in function['parameters']['required']
    verdict = {'query_id': 'q', 'status': 'supported', 'reason': 'x', 'citations': [{'document_id': doc.document_id, 'start_line': 1, 'end_line': 2}]}
    body = {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [{'type': 'function', 'function': {'name': 'submit_support', 'arguments': json.dumps(verdict)}}]}}]}
    assert parse_strict_judge_response(packet, body)[1]['status'] == 'supported'
    body['choices'][0]['finish_reason'] = 'length'
    with pytest.raises(ValueError):
        parse_strict_judge_response(packet, body)
    verdict.pop('query_id')
    verdict['unrecognized'] = 'x'
    with pytest.raises(ValueError):
        validate_range_verdict(packet, verdict)


def test_explicit_contradiction_requires_traceable_source():
    query, doc = fixture()
    packet = make_packet(query, [doc.document_id], {doc.document_id: doc})
    verdict = {'status': 'contradicted', 'reason': 'returns 123 rather than the value assumed by the query', 'citations': [{'document_id': doc.document_id, 'start_line': 2, 'end_line': 2}]}
    assert validate_range_verdict(packet, verdict)['status'] == 'contradicted'
    verdict['citations'] = []
    with pytest.raises(ValueError, match='requires citations'):
        validate_range_verdict(packet, verdict)


def test_strict_schema_binds_line_limits_to_each_document():
    from antisentinel.evaluation.support_judge import strict_judge_request
    query, doc = fixture()
    packet = make_packet(query, [doc.document_id], {doc.document_id:doc})
    schema = strict_judge_request(packet,'deepseek-chat')['tools'][0]['function']['parameters']
    branch = schema['properties']['citations']['items']['anyOf'][0]
    assert branch['properties']['document_id']['enum'] == [doc.document_id]
    assert branch['properties']['start_line']['maximum'] == 2
    assert branch['properties']['end_line']['maximum'] == 2
