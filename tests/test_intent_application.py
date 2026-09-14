"""The message entry uses strict model candidates without enabling tools."""
from importlib import import_module
from importlib.util import find_spec
import sqlite3

import pytest

from antisentinel.domain.intent import AuthorizedIntentContext
from tests.test_intent_dispatch import ContextReader
from tests.test_intent_lifecycle import setup, request


def app_api():
    assert find_spec('antisentinel.entry.intent_application'), 'intent application missing'
    return import_module('antisentinel.entry.intent_application')


class FixedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def extract(self, request, *, timeout_seconds):
        self.requests.append(request)
        assert request.tools == ()
        assert 0 < timeout_seconds <= 30
        return next(self.responses)


def build(tmp_path, responses, clock=lambda: 0):
    module = app_api()
    from antisentinel.control.intent_dispatch import IntentDispatcher
    from antisentinel.evaluation.intent_receiver import SQLiteIntentReceiver
    store, service, _ = setup(tmp_path)
    c, ctx = request()
    model = FixedModel(responses)
    receiver = SQLiteIntentReceiver(tmp_path/'receiver.sqlite')
    dispatcher = IntentDispatcher(store,ContextReader(ctx),answer=receiver,plan=receiver,control=receiver)
    return module, module.IntentApplication(service,dispatcher,model,monotonic=clock), model, store, receiver, ctx


def test_entry_resolves_routes_and_replays_without_new_model_call(tmp_path):
    c, _ = request()
    _, app, model, store, receiver, ctx = build(tmp_path,[c])
    first = app.submit(AuthorizedIntentContext.from_dict(ctx))
    again = app.submit(AuthorizedIntentContext.from_dict(ctx))
    assert first == again
    assert len(model.requests)==1
    assert len(receiver.records())==1
    assert first['receipt']['status']=='accepted'


def test_two_repairs_then_failure_never_dispatches(tmp_path):
    module, app, model, store, receiver, ctx = build(tmp_path,[{}, {}, {}])
    with pytest.raises(module.IntentRecognitionError):
        app.submit(AuthorizedIntentContext.from_dict(ctx))
    assert len(model.requests)==3
    assert store.counts()['outbox']==0
    assert receiver.records()==[]
    with sqlite3.connect(store.path) as db:
        row=db.execute('SELECT code,attempts FROM recognition_failures').fetchone()
    assert row==('invalid_model_output',3)


def test_repaired_candidate_uses_remaining_budget_and_routes(tmp_path):
    c, _=request()
    _,app,model,_,receiver,ctx=build(tmp_path,[{},c])
    assert app.submit(AuthorizedIntentContext.from_dict(ctx))['receipt']['route']=='plan'
    assert len(model.requests)==2
    assert len(receiver.records())==1


def test_late_model_result_cannot_create_outbox(tmp_path):
    ticks=iter([0,0,31])
    c,_=request()
    module,app,model,store,receiver,ctx=build(tmp_path,[c],clock=lambda:next(ticks))
    with pytest.raises(module.IntentRecognitionError):
        app.submit(AuthorizedIntentContext.from_dict(ctx))
    assert store.counts()['revisions']==0
    assert receiver.records()==[]
