"""Shared, non-executing support for PRD-005 real acceptance Cases."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from threading import Lock

from antisentinel.evaluation.code_corpus import EvaluationCorpus, load_corpus


@dataclass(frozen=True)
class FrozenCaseInput:
    repository: Path
    manifest: Path
    manifest_sha256: str
    corpus: EvaluationCorpus
    answerable_query: dict
    no_answer_query: dict


def freeze_case_input(repository, manifest, answerable_query_id, no_answer_query_id):
    repository = Path(repository).resolve()
    manifest = Path(manifest).resolve()
    corpus = load_corpus(repository, manifest)
    selected = {item['id']: item for item in corpus.queries}
    answerable = selected.get(answerable_query_id)
    no_answer = selected.get(no_answer_query_id)
    if answerable is None or not answerable['relevant_ids']:
        raise ValueError('answerable query must exist and have relevant IDs')
    if no_answer is None or no_answer['relevant_ids']:
        raise ValueError('no-answer query must exist and have zero relevant IDs')
    if answerable['scope'] != no_answer['scope']:
        raise ValueError('pilot queries must share one fixed scope')
    return FrozenCaseInput(
        repository=repository,
        manifest=manifest,
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        corpus=corpus,
        answerable_query=answerable,
        no_answer_query=no_answer,
    )


class HttpUsageAudit:
    """Count requests and provider usage without retaining request material."""

    def __init__(self, kind):
        self.kind = kind
        self._lock = Lock()
        self.requests = 0
        self.responses = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.usage_responses = 0

    def request(self, _request):
        with self._lock:
            self.requests += 1

    def response(self, response):
        response.read()
        try:
            usage = response.json().get('usage', {})
        except (ValueError, AttributeError):
            usage = {}
        with self._lock:
            self.responses += 1
            if isinstance(usage, dict) and type(usage.get('total_tokens')) is int:
                self.input_tokens += int(usage.get('prompt_tokens', usage.get('input_tokens', 0)))
                self.output_tokens += int(usage.get('completion_tokens', usage.get('output_tokens', 0)))
                self.total_tokens += usage['total_tokens']
                self.usage_responses += 1

    def snapshot(self):
        with self._lock:
            return {
                'kind': self.kind,
                'requests': self.requests,
                'responses': self.responses,
                'input_tokens': self.input_tokens,
                'output_tokens': self.output_tokens,
                'total_tokens': self.total_tokens,
                'usage_responses': self.usage_responses,
            }


@contextmanager
def case_environment(values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def assert_report_safe(report):
    encoded = json.dumps(report, ensure_ascii=False)
    forbidden = [
        'DASHSCOPE_API_KEY',
        'ANTISENTINEL_MODEL_API_KEY',
        'Authorization',
        'Bearer ',
        'source_text',
        'request_body',
        'response_body',
    ]
    forbidden.extend(value for value in (
        os.getenv('DASHSCOPE_API_KEY'),
        os.getenv('ANTISENTINEL_MODEL_API_KEY'),
    ) if value)
    if any(value in encoded for value in forbidden):
        raise ValueError('case report contains sensitive material')
