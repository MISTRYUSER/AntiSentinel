import grpc
from scripts.run_standalone_case import is_auth_rejection


class RpcFailure(grpc.RpcError):
    def __init__(self, status):
        self.status = status
    def code(self):
        return self.status


def test_wrapped_auth_rejection_is_recognized():
    error = RuntimeError('generic connection error')
    error.__context__ = RpcFailure(grpc.StatusCode.UNAUTHENTICATED)
    assert is_auth_rejection(error)


def test_outage_is_not_misreported_as_auth_rejection():
    error = RuntimeError('generic connection error')
    error.__cause__ = RpcFailure(grpc.StatusCode.UNAVAILABLE)
    assert not is_auth_rejection(error)


def test_exception_cycle_does_not_loop_forever():
    error = RuntimeError('connection error')
    error.__context__ = error
    assert not is_auth_rejection(error)
