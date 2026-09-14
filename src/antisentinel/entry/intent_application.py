"""Injectable message-to-intent entry. HTTP identity and real provider wiring are separate."""
from time import monotonic as system_monotonic

from antisentinel.domain.intent import IntentCandidate, IntentValidationError, content_hash
from antisentinel.ports.intent_model import IntentModelPort, IntentModelRequest


class IntentRecognitionError(RuntimeError):
    """Retryable recognition failure; not an unsupported business intent."""


class IntentApplication:
    def __init__(self, service, dispatcher, model: IntentModelPort, *, monotonic=system_monotonic):
        self.service = service
        self.dispatcher = dispatcher
        self.model = model
        self.monotonic = monotonic

    def submit(self, context, *, expected_revision=0, intent_id=None, clarification_id=None):
        ctx = context.to_dict()
        digest = content_hash({'message': ctx['messages'][ctx['message_id']]})
        value = self.service.store.replay(ctx, digest)
        if value is None:
            deadline = self.monotonic() + 30
            last_error = None
            for attempt in range(3):
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    self._failed(ctx, 'recognition_timeout', attempt)
                try:
                    response = self.model.extract(IntentModelRequest(context, attempt, last_error), timeout_seconds=remaining)
                except TimeoutError:
                    self._failed(ctx, 'recognition_timeout', attempt + 1)
                except IntentValidationError as exc:
                    last_error = '按IntentCandidate契约修复：'+str(exc)[:400]
                    continue
                except Exception:
                    self._failed(ctx, 'model_unavailable', attempt + 1)
                if self.monotonic() >= deadline:
                    self._failed(ctx, 'recognition_timeout', attempt + 1)
                try:
                    candidate = IntentCandidate.from_dict(response)
                    value = self.service.resolve(candidate, context, expected_revision=expected_revision,
                                                 intent_id=intent_id, clarification_id=clarification_id)
                    break
                except IntentValidationError as exc:
                    last_error = '按IntentCandidate契约修复：'+str(exc)[:400]
            if value is None:
                self._failed(ctx, 'invalid_model_output', 3)
        identity = value.to_dict()['identity']
        return self.dispatcher.dispatch(identity['intent_id'], identity['revision'],
                                        ctx['actor_id'], ctx['session_id'], ctx['incident_id'])

    def _failed(self, ctx, code, attempts):
        self.service.store.record_recognition_failure(ctx, code, attempts)
        raise IntentRecognitionError(code)
