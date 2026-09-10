"""Thread/context-local monotonic deadlines shared across query adapters."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math
import time


class QueryDeadlineExceeded(TimeoutError):
    pass


@dataclass(frozen=True)
class Deadline:
    expires_at: float

    def remaining(self):
        value = self.expires_at - time.monotonic()
        if value <= 0:
            raise QueryDeadlineExceeded('query_deadline_exceeded')
        return value


_active = ContextVar('query_deadline', default=None)


def current_deadline():
    return _active.get()


def check_deadline():
    if _active.get() is not None:
        _active.get().remaining()


def remaining_timeout(default):
    deadline = _active.get()
    return default if deadline is None else min(default, deadline.remaining())


@contextmanager
def query_budget(seconds):
    if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('invalid query budget')
    previous = _active.get()
    expires = time.monotonic() + seconds
    if previous is not None:
        expires = min(expires, previous.expires_at)
    deadline = Deadline(expires)
    token = _active.set(deadline)
    try:
        yield deadline
    finally:
        _active.reset(token)
