"""Reuse the existing Flash adapter with evaluation-only HTTP accounting."""
from antisentinel.persistence.local_vector_memory import QwenFlashEmbedder


class FlashCodeEncoder:
    def __init__(self, embedder: QwenFlashEmbedder):
        self.embedder = embedder
        self.revision = embedder.model_name
        self.dimension = embedder.dimension
        self.calls = 0
        self.tokens = 0
        self.usage_responses = 0
        self.embedder.client.event_hooks['request'].append(self._request)
        self.embedder.client.event_hooks['response'].append(self._response)

    @classmethod
    def from_env(cls):
        return cls(QwenFlashEmbedder.from_env())

    def _request(self, request):
        self.calls += 1

    def _response(self, response):
        response.read()
        try:
            tokens = response.json().get('usage', {}).get('total_tokens')
        except (ValueError, AttributeError):
            return
        if type(tokens) is int and tokens >= 0:
            self.tokens += tokens
            self.usage_responses += 1

    def encode(self, text):
        return tuple(self.embedder.embed_query([text])[0])

    def encode_documents(self, texts):
        return self.embedder.embed_documents(texts)

    def metadata(self):
        return {'kind': 'remote_embedding', 'revision': self.revision,
                'dimension': self.dimension, 'external_calls': self.calls,
                'tokens': self.tokens if self.usage_responses else None,
                'usage_responses': self.usage_responses,
                'usage_complete': self.usage_responses == self.calls and self.calls > 0,
                'cost': None, 'reason': 'provider usage only; billed cost not available',
                'query_cache': False}

    def close(self):
        for name, hook in [('request', self._request), ('response', self._response)]:
            if hook in self.embedder.client.event_hooks[name]:
                self.embedder.client.event_hooks[name].remove(hook)
        self.embedder.close()
