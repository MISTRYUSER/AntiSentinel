"""Worker execution seam."""

from typing import Protocol

from antisentinel.protocol.execute_toolcall import (
    ExecuteToolCallRequest,
    ExecuteToolCallResponse,
)


class WorkerGateway(Protocol):
    def execute_toolcall(
        self,
        request: ExecuteToolCallRequest,
    ) -> ExecuteToolCallResponse:
        """Execute one serialized ToolCall request."""

