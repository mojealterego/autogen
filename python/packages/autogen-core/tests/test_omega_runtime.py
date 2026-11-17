"""Regression coverage for queue bookkeeping and interceptor composition."""
import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from autogen_core import AgentId, DefaultInterventionHandler, DefaultTopicId, DropMessage, MessageContext, RoutedAgent, SingleThreadedAgentRuntime, message_handler
from autogen_core.exceptions import MessageDroppedException


@dataclass
class Payload:
    value: str


class Echo(RoutedAgent):
    @message_handler
    async def handle(self, message: Payload, ctx: MessageContext) -> Payload:
        return message


class Append(DefaultInterventionHandler):
    def __init__(self, suffix: str) -> None:
        self.suffix = suffix

    async def on_send(self, message: Any, *, message_context: MessageContext, recipient: AgentId) -> Any:
        return Payload(message.value + self.suffix)


class NoChange(DefaultInterventionHandler):
    async def on_send(self, message: Any, *, message_context: MessageContext, recipient: AgentId) -> Any:
        return None


class Drop(DefaultInterventionHandler):
    async def on_send(self, message: Any, *, message_context: MessageContext, recipient: AgentId) -> Any:
        return DropMessage

    async def on_publish(self, message: Any, *, message_context: MessageContext) -> Any:
        return DropMessage


class DropResponse(DefaultInterventionHandler):
    async def on_response(self, message: Any, *, sender: AgentId, recipient: AgentId | None) -> Any:
        return DropMessage


class Raise(DefaultInterventionHandler):
    async def on_send(self, message: Any, *, message_context: MessageContext, recipient: AgentId) -> Any:
        raise ValueError('denied')


async def runtime_with(*handlers: DefaultInterventionHandler) -> SingleThreadedAgentRuntime:
    runtime = SingleThreadedAgentRuntime(intervention_handlers=list(handlers))
    await Echo.register(runtime, 'echo', lambda: Echo('echo'))
    runtime.start()
    return runtime


@pytest.mark.asyncio
async def test_interceptors_compose_and_none_preserves_message() -> None:
    runtime = await runtime_with(Append('1'), NoChange(), Append('2'))
    try:
        with pytest.warns(RuntimeWarning, match='returned None'):
            result = await asyncio.wait_for(runtime.send_message(Payload('x'), AgentId('echo','a')), 1)
        assert result == Payload('x12')
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('handler,error', [(Drop(), MessageDroppedException), (DropResponse(), MessageDroppedException), (Raise(), ValueError)])
async def test_intervention_early_exit_does_not_block_idle(handler: DefaultInterventionHandler, error: type[Exception]) -> None:
    runtime = await runtime_with(handler)
    with pytest.raises(error):
        await runtime.send_message(Payload('x'), AgentId('echo','a'))
    await asyncio.wait_for(runtime.stop_when_idle(), .2)


@pytest.mark.asyncio
async def test_publish_drop_does_not_block_idle() -> None:
    runtime = await runtime_with(Drop())
    await runtime.publish_message(Payload('x'), DefaultTopicId())
    await asyncio.wait_for(runtime.stop_when_idle(), .2)


@pytest.mark.asyncio
async def test_governed_workflow_dispatches_real_registered_agent() -> None:
    from autogen_core import WorkflowExecutor, WorkflowPolicy, WorkflowStep, agent_workflow_handler, verify_audit

    runtime = await runtime_with()
    try:
        handler = agent_workflow_handler(runtime, AgentId('echo','a'),
                                         lambda inputs: Payload(str(inputs.get('first', 'root'))),
                                         lambda response: response.value + '!')
        engine = WorkflowExecutor({'echo': handler}, WorkflowPolicy(frozenset({'echo'})))
        result = await engine.run([WorkflowStep('first','echo'), WorkflowStep('second','echo',('first',))])
        assert result.outputs == {'first': 'root!', 'second': 'root!!'}
        assert verify_audit(result.audit, expected_head=result.audit_head)
    finally:
        await runtime.stop_when_idle()
