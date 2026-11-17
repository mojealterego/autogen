import asyncio
from pathlib import Path

import pytest

from autogen_core import (
    DurableWorkflowExecutor, ProcessWorker, ResourceScheduler, Resources,
    WorkerLimits, WorkflowPolicy, WorkflowStep, scheduled_handler,
)


@pytest.mark.asyncio
async def test_separate_durable_executors_share_worker_capacity(tmp_path: Path):
    script = tmp_path / 'worker.py'
    script.write_text('import json,sys\nprint(json.dumps(json.load(sys.stdin).get("a", 0) + 1))')
    demand = Resources(1, 128 * 1024**2, 1000)
    scheduler = ResourceScheduler(demand, {'a': demand, 'b': demand})
    worker = ProcessWorker(script, WorkerLimits(memory_bytes=demand.memory_bytes))
    first_started = asyncio.Event()
    continue_first = asyncio.Event()
    seen = []

    async def first(data):
        seen.append('a')
        first_started.set()
        await continue_first.wait()
        return await worker(data)

    async def second(data):
        seen.append('b')
        return await worker(data)

    a = DurableWorkflowExecutor(tmp_path / 'a.db', {'f': scheduled_handler(scheduler, 'a', demand, first)}, WorkflowPolicy(frozenset({'f'})))
    b = DurableWorkflowExecutor(tmp_path / 'b.db', {'f': scheduled_handler(scheduler, 'b', demand, second)}, WorkflowPolicy(frozenset({'f'})))
    steps = [WorkflowStep('a', 'f')]
    args = dict(run_id='r', workflow_revision='scheduled-worker-v1')
    t1 = asyncio.create_task(a.run(steps, tenant_id='a', **args))
    await first_started.wait()
    t2 = asyncio.create_task(b.run(steps, tenant_id='b', **args))
    try:
        for _ in range(100):
            if scheduler.snapshot().queued == 1: break
            await asyncio.sleep(0)
        assert scheduler.snapshot().queued == 1
        assert seen == ['a']
    finally:
        continue_first.set()
        x, y = await asyncio.gather(t1, t2)
    assert x.outputs == y.outputs == {'a': 1}
    assert seen == ['a', 'b']
    script.write_text('raise AssertionError("replay must not execute")')
    assert await a.run(steps, tenant_id='a', **args, expected_head=x.journal_head) == x
    assert scheduler.snapshot().granted == 2 and scheduler.snapshot().active == 0
