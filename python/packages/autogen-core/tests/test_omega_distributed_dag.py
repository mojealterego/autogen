import asyncio
from dataclasses import dataclass

import pytest

from autogen_core import (
    ClusterCoordinator, ClusterWorker, DurableWorkflowExecutor, Resources, TenantSpec,
    WorkerSpec, WorkflowPolicy, WorkflowStep, distributed_step_handler,
    OutcomeUnknownError, FabricTransportError,
)

Q = Resources(1, 128, 100)


def setup(tmp_path):
    c = ClusterCoordinator(tmp_path / 'cluster.db', Q,
                           {'tenant': TenantSpec(Q, frozenset({'remote'}))},
                           {'worker': WorkerSpec(Q, frozenset({'tenant'}), frozenset({'remote'}))},
                           lease_ms=3000)
    return c


@pytest.mark.asyncio
async def test_real_worker_two_step_dag_and_replay(tmp_path):
    c = setup(tmp_path)
    session = c.register('worker')
    invocations = []

    async def remote(data):
        invocations.append(data)
        return {'sum': data.get('a', {}).get('sum', 0) + 3}

    run = True
    async def pump():
        worker = ClusterWorker(c, session, {'remote': remote}, heartbeat_interval=.2)
        while run:
            await worker.run_once()
            await asyncio.sleep(.002)

    task = asyncio.create_task(pump())
    try:
        handler = distributed_step_handler(c, 'tenant', 'remote', Q, poll_interval=.002, poll_timeout=3)
        engine = DurableWorkflowExecutor(tmp_path/'journal.db', {'remote':handler}, WorkflowPolicy(frozenset({'remote'}), retry_base=0))
        steps = [WorkflowStep('a','remote'), WorkflowStep('b','remote',('a',))]
        args = dict(tenant_id='tenant', run_id='run', workflow_revision='v1')
        result = await engine.run(steps, **args)
        assert result.outputs == {'a': {'sum':3}, 'b':{'sum':6}}
        assert result.statuses == {'a':'succeeded','b':'succeeded'}
        assert len(invocations) == 2
        assert await engine.run(steps, **args, expected_head=result.journal_head) == result
        assert len(invocations) == 2
    finally:
        run=False
        await task


@pytest.mark.asyncio
async def test_committed_submit_lost_response_reconciled(tmp_path):
    c=setup(tmp_path)
    class LostAck:
        def __init__(self): self.calls=0
        def submit(self,*a,**kw):
            self.calls+=1
            value=c.submit(*a,**kw)
            raise FabricTransportError('lost response')
        def get(self,*a): return c.get(*a)
    backend=LostAck()
    handler=distributed_step_handler(backend, 'tenant','remote',Q,poll_interval=.005,poll_timeout=2)
    async def worker():
        await asyncio.sleep(.05)
        w=ClusterWorker(c,c.register('worker'), {'remote': lambda _: asyncio.sleep(0, result=7)})
        return await w.run_once()
    task=asyncio.create_task(worker())
    e=DurableWorkflowExecutor(tmp_path/'run.db', {'remote':handler}, WorkflowPolicy(frozenset({'remote'})))
    result=await e.run([WorkflowStep('x','remote')],tenant_id='tenant',run_id='run',workflow_revision='v')
    assert result.outputs=={'x':7}
    assert backend.calls==1
    assert (await task).state=='succeeded'


@pytest.mark.asyncio
async def test_unreachable_authority_is_uncertain_not_failed(tmp_path):
    class Offline:
        def submit(self,*a,**kw): raise FabricTransportError('offline')
        def get(self,*a): raise FabricTransportError('offline')
    handler=distributed_step_handler(Offline(),'tenant','remote',Q,poll_interval=.005,poll_timeout=.02)
    e=DurableWorkflowExecutor(tmp_path/'db',{'remote':handler},WorkflowPolicy(frozenset({'remote'})))
    args=dict(tenant_id='tenant',run_id='x',workflow_revision='v')
    result=await e.run([WorkflowStep('x','remote')],**args)
    assert result.statuses=={'x':'uncertain'}
    assert await e.run([WorkflowStep('x','remote')],**args,expected_head=result.journal_head)==result


@pytest.mark.asyncio
async def test_unfinished_remote_job_stays_uncertain_on_timeout(tmp_path):
    c=setup(tmp_path)
    handler=distributed_step_handler(c,'tenant','remote',Q,poll_interval=.005,poll_timeout=.04)
    e=DurableWorkflowExecutor(tmp_path/'db',{'remote':handler},WorkflowPolicy(frozenset({'remote'})))
    result=await e.run([WorkflowStep('x','remote')],tenant_id='tenant',run_id='x',workflow_revision='v')
    assert result.statuses=={'x':'uncertain'}
    assert c.get('tenant',next(iter(_ids(c)))).state=='queued'


def _ids(c):
    import sqlite3
    with sqlite3.connect(c._path) as db:
        return [r[0] for r in db.execute('select job_id from omega_cluster_jobs')]


@pytest.mark.asyncio
async def test_tenant_identity_is_fixed(tmp_path):
    c=setup(tmp_path)
    handler=distributed_step_handler(c,'tenant','remote',Q,poll_interval=.001,poll_timeout=.05)
    e=DurableWorkflowExecutor(tmp_path/'db',{'remote':handler},WorkflowPolicy(frozenset({'remote'})))
    result=await e.run([WorkflowStep('x','remote')],tenant_id='other',run_id='r',workflow_revision='v')
    assert result.statuses=={'x':'failed'}
    assert not _ids(c)

@pytest.mark.asyncio
async def test_remote_failed_state_does_not_retry(tmp_path):
    c=setup(tmp_path)
    handler=distributed_step_handler(c,'tenant','remote',Q,poll_interval=.002,poll_timeout=2)
    task=None
    async def worker():
        await asyncio.sleep(.03)
        s=c.register('worker')
        lease=None
        for _ in range(100):
            lease=c.claim(s)
            if lease: break
            await asyncio.sleep(.002)
        assert lease
        return c.fail(s,lease)
    task=asyncio.create_task(worker())
    e=DurableWorkflowExecutor(tmp_path/'db',{'remote':handler},WorkflowPolicy(frozenset({'remote'})))
    result=await e.run([WorkflowStep('x','remote')],tenant_id='tenant',run_id='x',workflow_revision='v')
    assert result.statuses=={'x':'failed'}
    assert (await task).attempts==1


# The existing Fabric test PKI is intentionally reused. It creates ephemeral
# certificates and uses real loopback TLS, not a mock transport.
from test_omega_fabric import pki, serving  # noqa: E402,F401


@pytest.mark.asyncio
async def test_durable_dag_through_real_mtls(tmp_path,pki):
    with serving(pki,tmp_path/'coordinator.db',lease_ms=3000) as (_,connect,__):
        producer,worker_backend=connect('a'),connect('worker')
        session=worker_backend.register('w')
        active=True
        async def compute(data):
            return {'v': data.get('first',{}).get('v',1)+4}
        async def pump():
            worker=ClusterWorker(worker_backend,session,{'f':compute},heartbeat_interval=.1)
            while active:
                await worker.run_once()
                await asyncio.sleep(.003)
        task=asyncio.create_task(pump())
        try:
            handler=distributed_step_handler(producer,'a','f',Resources(1,100,100),poll_interval=.003,poll_timeout=3)
            engine=DurableWorkflowExecutor(tmp_path/'durable.db',{'r':handler},WorkflowPolicy(frozenset({'r'})))
            result=await engine.run([WorkflowStep('first','r'),WorkflowStep('second','r',('first',))],tenant_id='a',run_id='run',workflow_revision='v1')
            assert result.statuses=={'first':'succeeded','second':'succeeded'}
            assert result.outputs['second']=={'v':9}
        finally:
            active=False
            await task
