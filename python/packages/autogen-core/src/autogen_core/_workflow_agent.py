"""Typed adapter from governed workflows to registered AutoGen agents."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from ._agent_id import AgentId
from ._agent_runtime import AgentRuntime
from ._cancellation_token import CancellationToken
from ._governed_workflow import Handler


def agent_workflow_handler(
    runtime: AgentRuntime,
    recipient: AgentId,
    encode: Callable[[Mapping[str, Any]], Any],
    decode: Callable[[Any], Any],
    *,
    sender: AgentId | None = None,
) -> Handler:
    """Bind a trusted fixed agent address and explicit JSON conversion to a workflow handler.

    The runtime must already be started. Cancellation is propagated to the agent's
    token; agents must cooperate with that token. No dynamic recipient lookup occurs.
    """
    async def dispatch(inputs: Mapping[str, Any]) -> Any:
        token = CancellationToken()
        try:
            response = await runtime.send_message(encode(inputs), recipient, sender=sender, cancellation_token=token)
            return decode(response)
        except asyncio.CancelledError:
            token.cancel()
            raise

    return dispatch
