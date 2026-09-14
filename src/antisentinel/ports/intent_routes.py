"""Explicit routing ports. Implementations must deduplicate and recheck before acceptance."""
from typing import Callable, ContextManager, Protocol

from antisentinel.domain.intent import AuthorizedIntentContext, canonical, object_fields, require, text


class IntentContextReader(Protocol):
    def read(self, actor: str, session: str, incident: str) -> AuthorizedIntentContext: ...


class RouteGateway(Protocol):
    def lookup(self, key: str) -> dict | None: ...
    def submit(self, request: dict, validate_current: Callable[[], ContextManager[None]]) -> dict:
        """Hold validate_current() through bounded acceptance commit, not execution."""
        ...


def validate_receipt(receipt: dict, request: dict) -> dict:
    object_fields(receipt, {'receipt_id', 'intent_ref', 'route', 'step', 'status', 'receiver_kind',
                            'downstream_id', 'key'}, 'receipt')
    require(receipt['status'] in ('accepted', 'completed', 'failed'), 'invalid receipt status')
    require(receipt['receiver_kind'] in ('contract', 'real'), 'invalid receiver kind')
    require(text(receipt['receipt_id']) and text(receipt['downstream_id']), 'receipt IDs required')
    for key in ('intent_ref', 'route', 'step', 'key'):
        require(receipt[key] == request[key], f'receipt {key} mismatch')
    canonical(receipt)
    return receipt
