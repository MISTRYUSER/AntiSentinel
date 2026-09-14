import asyncio
from importlib import import_module
from importlib.util import find_spec
import json

import httpx
import pytest

from antisentinel.domain.intent import AuthorizedIntentContext, IntentValidationError
from antisentinel.ports.intent_model import IntentModelRequest
from tests.test_intent_lifecycle import request


def adapter(transport):
    assert find_spec('antisentinel.adapters.llm.deepseek_intent'), 'DeepSeek adapter missing'
    module=import_module('antisentinel.adapters.llm.deepseek_intent')
    return module.DeepSeekIntentAdapter(base_url='https://api.deepseek.com/v1',api_key='test-secret',
                                      model='deepseek-chat',transport=transport)


def test_deepseek_json_payload_has_no_tools_or_authority_override():
    candidate,context=request()
    def respond(req):
        body=json.loads(req.content)
        assert body['response_format']=={'type':'json_object'}
        assert 'tools' not in body and 'tool_choice' not in body
        assert body['thinking']=={'type':'disabled'}
        assert 'json' in body['messages'][0]['content'].lower()
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(candidate)}}],'usage':{'total_tokens':100}})
    model=adapter(httpx.MockTransport(respond))
    assert model.extract(IntentModelRequest(AuthorizedIntentContext.from_dict(context),0),timeout_seconds=2)==candidate


@pytest.mark.parametrize('message', [{'content':''},{'content':'{}','tool_calls':[{}]}])
def test_invalid_or_tool_output_never_becomes_candidate(message):
    _,context=request()
    model=adapter(httpx.MockTransport(lambda req:httpx.Response(200,json={'choices':[{'message':message}]})))
    with pytest.raises(IntentValidationError):
        model.extract(IntentModelRequest(AuthorizedIntentContext.from_dict(context),0),timeout_seconds=2)


def test_total_deadline_cancels_slow_response():
    _,context=request()
    async def slow(req):
        await asyncio.sleep(2)
        return httpx.Response(200,json={})
    model=adapter(httpx.MockTransport(slow))
    with pytest.raises(TimeoutError):
        model.extract(IntentModelRequest(AuthorizedIntentContext.from_dict(context),0),timeout_seconds=0.02)


def test_provider_error_does_not_echo_secret_or_body():
    _,context=request()
    model=adapter(httpx.MockTransport(lambda req:httpx.Response(401,text='test-secret')))
    with pytest.raises(RuntimeError) as error:
        model.extract(IntentModelRequest(AuthorizedIntentContext.from_dict(context),0),timeout_seconds=2)
    assert 'test-secret' not in str(error.value)
