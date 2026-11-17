import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from autogen_core import (
    DurableWorkflowExecutor, WorkflowPolicy, WorkflowStep, RetryableError,
    RunBusyError, CheckpointIntegrityError, CheckpointConflictError, current_attempt,
)


def engine(path, handlers, **kwargs):
    return DurableWorkflowExecutor(path, handlers, WorkflowPolicy(frozenset(handlers), retry_base=0, **kwargs))


@pytest.mark.asyncio
async def test_restart_reuses_completed_outputs_and_rejects_changed_revision(tmp_path):
    calls = []
    async def f(data):
        calls.append(current_attempt().step_id)
        return data.get('a', 0) + 1
    steps = [WorkflowStep('a','f'), WorkflowStep('b','f',('a',))]
    args = dict(tenant_id='tenant', run_id='run', workflow_revision='v1')
    first = await engine(tmp_path/'db', {'f':f}).run(steps, **args)
    second = await engine(tmp_path/'db', {'f':f}).run(steps, **args, expected_head=first.journal_head)
    assert first == second
    assert second.outputs == {'a':1,'b':2}
    assert calls == ['a','b']
    with pytest.raises(CheckpointConflictError):
        await engine(tmp_path/'db', {'f':f}).run(steps, **{**args,'workflow_revision':'v2'})
    with pytest.raises(CheckpointConflictError):
        await engine(tmp_path/'db', {'f':f}).run(steps[:1], **args)


@pytest.mark.asyncio
async def test_retry_attempts_and_stable_idempotency_key(tmp_path):
    contexts = []
    async def f(data):
        contexts.append(current_attempt())
        if len(contexts)==1: raise RetryableError('temporary')
        return 23
    result = await engine(tmp_path/'db', {'f':f}).run([WorkflowStep('a','f', attempts=2,idempotent=True)],tenant_id='t',run_id='r',workflow_revision='v1')
    assert result.outputs == {'a':23}
    assert [c.attempt for c in contexts] == [1,2]
    assert contexts[0].idempotency_key == contexts[1].idempotency_key
    assert current_attempt() is None


@pytest.mark.asyncio
async def test_concurrent_run_rejected_and_tenants_separated(tmp_path):
    gate = asyncio.Event()
    entered = asyncio.Event()
    async def f(data):
        entered.set()
        await gate.wait()
        return current_attempt().tenant_id
    e = engine(tmp_path/'db', {'f':f})
    steps = [WorkflowStep('a','f')]
    kwargs = dict(tenant_id='t',run_id='r',workflow_revision='v1')
    task = asyncio.create_task(e.run(steps,**kwargs))
    await entered.wait()
    try:
        with pytest.raises(RunBusyError):
            await engine(tmp_path/'db', {'f':f}).run(steps,**kwargs)
    finally:
        gate.set()
        await task
    other = await e.run(steps,**{**kwargs,'tenant_id':'u'})
    assert other.outputs == {'a':'u'}


@pytest.mark.asyncio
async def test_tampered_output_and_pinned_head(tmp_path):
    async def f(data): return 'original'
    e = engine(tmp_path/'db', {'f':f})
    args = dict(tenant_id='t',run_id='r',workflow_revision='v')
    steps = [WorkflowStep('a','f')]
    await e.run(steps,**args)
    with pytest.raises(CheckpointIntegrityError):
        await e.run(steps,**args,expected_head='0'*64)
    with sqlite3.connect(tmp_path/'db') as db:
        db.execute("UPDATE omega_events SET output_json='99' WHERE status='succeeded'")
    with pytest.raises(CheckpointIntegrityError):
        await e.run(steps,**args)


@pytest.mark.asyncio
@pytest.mark.parametrize('idempotent', [False, True])
async def test_real_process_crash_recovery(tmp_path, idempotent):
    db = tmp_path/'db'
    marker = tmp_path/'key'
    code = '''
import asyncio, os, sys
from pathlib import Path
from autogen_core import DurableWorkflowExecutor, WorkflowPolicy, WorkflowStep, current_attempt
async def f(data):
    Path(sys.argv[2]).write_text(current_attempt().idempotency_key)
    os._exit(71)
asyncio.run(DurableWorkflowExecutor(sys.argv[1], {'f':f}, WorkflowPolicy(frozenset({'f'}),retry_base=0)).run([WorkflowStep('a','f',attempts=2 if sys.argv[3]=='True' else 1,idempotent=sys.argv[3]=='True')],tenant_id='t',run_id='r',workflow_revision='v'))
'''
    proc = await asyncio.to_thread(subprocess.run,[sys.executable,'-c',code,str(db),str(marker),str(idempotent)], timeout=15, capture_output=True)
    assert proc.returncode == 71, proc.stderr.decode()
    calls = []
    async def f(data):
        calls.append(current_attempt())
        return 42
    result = await engine(db, {'f':f}).run([WorkflowStep('a','f',attempts=2 if idempotent else 1,idempotent=idempotent)],tenant_id='t',run_id='r',workflow_revision='v')
    if idempotent:
        assert result.outputs == {'a':42}
        assert calls[0].attempt == 2
        assert calls[0].idempotency_key == marker.read_text()
    else:
        assert result.statuses == {'a':'uncertain'}
        assert calls == []


@pytest.mark.asyncio
async def test_cancellation_leaves_uncertain_intent_and_skips_dependants(tmp_path):
    entered = asyncio.Event()
    async def f(data):
        entered.set()
        await asyncio.sleep(60)
    e = engine(tmp_path/'db', {'f':f})
    steps = [WorkflowStep('a','f'),WorkflowStep('b','f',('a',))]
    args = dict(tenant_id='t',run_id='r',workflow_revision='v')
    task = asyncio.create_task(e.run(steps,**args))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    result = await e.run(steps,**args)
    assert result.statuses == {'a':'uncertain','b':'skipped'}


@pytest.mark.asyncio
async def test_unpinned_truncation_is_detected(tmp_path):
    async def f(data): return 1
    e = engine(tmp_path/'db', {'f':f})
    args = dict(tenant_id='t',run_id='r',workflow_revision='v')
    steps = [WorkflowStep('a','f')]
    await e.run(steps, **args)
    with sqlite3.connect(tmp_path/'db') as db:
        db.execute('DELETE FROM omega_events WHERE seq=1')
    with pytest.raises(CheckpointIntegrityError):
        await e.run(steps, **args)


@pytest.mark.asyncio
async def test_budget_expires_while_executor_is_offline(tmp_path):
    calls = []
    async def f(data):
        calls.append(1)
        raise asyncio.CancelledError
    args = dict(tenant_id='t',run_id='r',workflow_revision='v')
    steps = [WorkflowStep('a','f',attempts=2,idempotent=True),WorkflowStep('b','f',('a',))]
    e = engine(tmp_path/'db', {'f':f}, deadline=.1)
    with pytest.raises(asyncio.CancelledError):
        await e.run(steps, **args)
    await asyncio.sleep(.15)
    result = await e.run(steps, **args)
    assert result.statuses == {'a':'timed_out','b':'skipped'}
    assert result.attempts['a'] == 1
    assert calls == [1]


@pytest.mark.asyncio
async def test_parallel_completed_step_survives_sibling_cancel(tmp_path):
    entered = asyncio.Event()
    released = asyncio.Event()
    calls = []
    async def f(data):
        step = current_attempt().step_id
        calls.append(step)
        if step == 'a':
            released.set()
            return 1
        entered.set()
        await asyncio.sleep(60)
    e = engine(tmp_path/'db', {'f':f}, max_parallel=2)
    steps = [WorkflowStep('a','f'),WorkflowStep('b','f')]
    args = dict(tenant_id='t',run_id='r',workflow_revision='v')
    task = asyncio.create_task(e.run(steps, **args))
    await entered.wait()
    await released.wait()
    # Wait for the committed outcome, without relying on a fixed delay.
    async def committed():
        while True:
            with sqlite3.connect(tmp_path/'db') as db:
                if db.execute("SELECT 1 FROM omega_events WHERE step='a' AND status='succeeded'").fetchone():
                    return
            await asyncio.sleep(0)
    await asyncio.wait_for(committed(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    result = await e.run(steps, **args)
    assert result.statuses == {'a':'succeeded','b':'uncertain'}
    assert result.outputs == {'a':1}
    assert calls == ['a','b']


@pytest.mark.asyncio
async def test_cross_process_run_lock(tmp_path):
    db = tmp_path/'db'
    code = '''
import asyncio, sys
from autogen_core import DurableWorkflowExecutor, WorkflowPolicy, WorkflowStep
async def f(data):
    print('locked', flush=True)
    await asyncio.sleep(60)
asyncio.run(DurableWorkflowExecutor(sys.argv[1], {'f':f}, WorkflowPolicy(frozenset({'f'}),retry_base=0)).run([WorkflowStep('a','f')],tenant_id='t',run_id='r',workflow_revision='v'))
'''
    child = await asyncio.create_subprocess_exec(sys.executable,'-c',code,str(db),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        assert child.stdout is not None
        assert await asyncio.wait_for(child.stdout.readline(), 10) == b'locked\n'
        async def f(data): raise AssertionError('must not execute')
        e = engine(db, {'f':f})
        with pytest.raises(RunBusyError):
            await e.run([WorkflowStep('a','f')],tenant_id='t',run_id='r',workflow_revision='v')
    finally:
        if child.returncode is None:
            child.kill()
        await child.communicate()
    recovered = await e.run([WorkflowStep('a','f')],tenant_id='t',run_id='r',workflow_revision='v')
    assert recovered.statuses == {'a':'uncertain'}


@pytest.mark.asyncio
async def test_real_agent_workflow_restart(tmp_path):
    from test_omega_runtime import runtime_with, Payload
    from autogen_core import AgentId, agent_workflow_handler
    runtime = await runtime_with()
    try:
        handler = agent_workflow_handler(runtime, AgentId('echo','a'),
                                         lambda inputs: Payload(str(inputs.get('a','root'))),
                                         lambda response: response.value+'!')
        e = engine(tmp_path/'db', {'echo':handler})
        steps = [WorkflowStep('a','echo'),WorkflowStep('b','echo',('a',))]
        args = dict(tenant_id='t',run_id='r',workflow_revision='v')
        first = await e.run(steps, **args)
        assert first.outputs == {'a':'root!','b':'root!!'}
    finally:
        await runtime.stop_when_idle()
    # The runtime has stopped: success must come exclusively from storage.
    assert await e.run(steps, **args) == first
