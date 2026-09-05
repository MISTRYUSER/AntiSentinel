"""Small, infrastructure-free domain primitives."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .errors import InvalidInputError


def new_stable_id() -> str:
    return str(uuid4())


def require_non_empty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"{field_name} must be non-empty")
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise InvalidInputError(f"{field_name} must be timezone-aware UTC")
    return value


def require_json(value: Any, field_name: str) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidInputError(f"{field_name} must be JSON serializable") from exc
    return value


def encode_datetime(value: datetime) -> str:
    return require_utc(value, "datetime").isoformat().replace("+00:00", "Z")


def decode_datetime(value: str, field_name: str) -> datetime:
    try:
        decoded = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"{field_name} must be a valid ISO-8601 datetime") from exc
    return require_utc(decoded, field_name)

