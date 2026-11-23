"""OMEGA 13: honest late remote outcomes with no remote side-effect replay."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from autogen_core import (
    CheckpointIntegrityError, ClusterCoordinator, ClusterDenied, ClusterWorker,
    DurableWorkflowExecutor, JobSnapshot, RemoteReconciliation, Resources,
    TenantSpec, WorkerSpec, WorkflowPolicy, WorkflowStep,
    distributed_step_handler, distributed_step_reconciler,
)

Q=Resources(1,128,100)


def environment(tmp_path, *, max_result_bytes=65536):
    c=ClusterCoordinator(tmp_path/'cluster.db',Q,
        {'tenant':TenantSpec(Q,frozenset({'remote'}))},
        {'worker':WorkerSpec(Q,frozenset({'tenant'}),frozenset({'remote'}))},lease_ms=3000)
    handler=distributed_step_handler(c,'tenant','remote',Q,poll_interval=.002,poll_timeout=.02)
    calls=[]
    async def next_step(inputs):
        calls.append(inputs)
        return {'double':inputs['remote']['value']*2}
    policy=WorkflowPolicy(frozenset({'remote','next'}),max_result_bytes=max_result_bytes,deadline=20)
    executor=DurableWorkflowExecutor(tmp_path/'workflow.db',{'remote':handler,'next':next_step},policy)
    steps=(WorkflowStep('remote','remote'),WorkflowStep('next','next',('remote',)))
    args=dict(tenant_id='tenant',run_id='op',workflow_revision='v1')
    return c,executor,steps,args,calls


async def worker_once(c, result):
    session=c.register('worker')
    async def remote(payload):
        return result
    return await ClusterWorker(c,session,{'remote':remote},heartbeat_interval=.2).run_once()


@pytest.mark.asyncio
async def test_late_remote_success_reopens_and_continues_without_submit(tmp_path):
    c,executor,steps,args,calls=environment(tmp_path)
    baseline=await executor.run(steps,**args)
    assert baseline.statuses=={'remote':'uncertain','next':'skipped'}
    assert (await worker_once(c,{'value':7})).state=='succeeded'
    class GetOnly:
        def __init__(self):self.gets=0
        def get(self,*a):self.gets+=1;return c.get(*a)
    reader=GetOnly()
    resolved=await executor.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(reader,'tenant','remote')},expected_head=baseline.journal_head)
    assert reader.gets==1
    assert resolved.statuses=={'remote':'succeeded'}
    assert resolved.outputs=={'remote':{'value':7}}
    final=await executor.run(steps,**args,expected_head=resolved.journal_head)
    assert final.statuses=={'remote':'succeeded','next':'succeeded'}
    assert final.outputs['next']=={'double':14}
    assert len(calls)==1
    with sqlite3.connect(c._path) as db:
        assert db.execute('SELECT COUNT(*) FROM omega_cluster_jobs').fetchone()[0]==1
    assert await executor.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(reader,'tenant','remote')},expected_head=final.journal_head)==final


@pytest.mark.asyncio
async def test_nonterminal_remote_does_not_change_journal_or_replay(tmp_path):
    c,e,steps,args,_=environment(tmp_path)
    baseline=await e.run(steps,**args)
    resolved=await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(c,'tenant','remote')},expected_head=baseline.journal_head)
    assert resolved==baseline


@pytest.mark.asyncio
async def test_authority_outage_keeps_uncertainty(tmp_path):
    c,e,steps,args,_=environment(tmp_path)
    baseline=await e.run(steps,**args)
    class Offline:
        def get(self,*_):raise ClusterDenied('offline')
    resolved=await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(Offline(),'tenant','remote')},expected_head=baseline.journal_head)
    assert resolved==baseline


@pytest.mark.asyncio
async def test_remote_failure_closes_step_without_restarting_dependants(tmp_path):
    c,e,steps,args,calls=environment(tmp_path)
    baseline=await e.run(steps,**args)
    s=c.register('worker');lease=c.claim(s)
    assert lease is not None
    assert c.fail(s,lease).state=='failed'
    resolved=await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(c,'tenant','remote')},expected_head=baseline.journal_head)
    assert resolved.statuses=={'remote':'failed','next':'skipped'}
    assert await e.run(steps,**args,expected_head=resolved.journal_head)==resolved
    assert not calls


@pytest.mark.asyncio
async def test_reconciliation_rejects_wrong_expected_head(tmp_path):
    c,e,steps,args,_=environment(tmp_path)
    baseline=await e.run(steps,**args)
    with pytest.raises(CheckpointIntegrityError):
        await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(c,'tenant','remote')},expected_head='0'*64)
    assert await e.run(steps,**args,expected_head=baseline.journal_head)==baseline


@pytest.mark.asyncio
async def test_remote_identity_spoof_rejected_without_journal_change(tmp_path):
    c,e,steps,args,_=environment(tmp_path)
    base=await e.run(steps,**args)
    class Corrupt:
        def get(self,*a):
            original=c.get(*a)
            return JobSnapshot('other',original.job_id,'succeeded',1,1,7)
    with pytest.raises(CheckpointIntegrityError):
        await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(Corrupt(),'tenant','remote')},expected_head=base.journal_head)
    assert await e.run(steps,**args,expected_head=base.journal_head)==base


@pytest.mark.asyncio
async def test_reader_forged_tenant_or_operation_denied(tmp_path):
    c,e,steps,args,_=environment(tmp_path)
    base=await e.run(steps,**args)
    async def wrong(ctx):return RemoteReconciliation('other',ctx.idempotency_key,'succeeded',{'value':1})
    with pytest.raises(CheckpointIntegrityError):
        await e.reconcile(steps,**args,readers={'remote':wrong},expected_head=base.journal_head)
    async def wrong_op(ctx):return RemoteReconciliation(ctx.tenant_id,'wrong','succeeded',{'value':1})
    with pytest.raises(CheckpointIntegrityError):
        await e.reconcile(steps,**args,readers={'remote':wrong_op},expected_head=base.journal_head)


@pytest.mark.asyncio
async def test_oversized_result_rejected_without_committing(tmp_path):
    c,e,steps,args,_=environment(tmp_path,max_result_bytes=256)
    base=await e.run(steps,**args)
    async def large(ctx):return RemoteReconciliation(ctx.tenant_id,ctx.idempotency_key,'succeeded',{'x':'Y'*400})
    with pytest.raises(CheckpointIntegrityError):
        await e.reconcile(steps,**args,readers={'remote':large},expected_head=base.journal_head)
    assert await e.run(steps,**args,expected_head=base.journal_head)==base


@pytest.mark.asyncio
async def test_replay_detects_tampered_late_result(tmp_path):
    c,e,steps,args,_=environment(tmp_path)
    base=await e.run(steps,**args)
    await worker_once(c,{'value':1})
    resolved=await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(c,'tenant','remote')},expected_head=base.journal_head)
    with sqlite3.connect(e._path) as db:
        db.execute("UPDATE omega_events SET output_json=? WHERE status='succeeded'",(json.dumps({'value':999}),))
    with pytest.raises(CheckpointIntegrityError):
        await e.run(steps,**args,expected_head=resolved.journal_head)


@pytest.mark.asyncio
async def test_wrong_revision_and_tenant_cannot_reconcile(tmp_path):
    c,e,steps,args,_=environment(tmp_path)
    base=await e.run(steps,**args)
    with pytest.raises(Exception):
        await e.reconcile(steps,**{**args,'workflow_revision':'other'},readers={'remote':distributed_step_reconciler(c,'tenant','remote')},expected_head=base.journal_head)
    with pytest.raises(CheckpointIntegrityError):
        await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(c,'other','remote')},expected_head=base.journal_head)

@pytest.mark.asyncio
async def test_transitive_reopen_three_step_chain_no_remote_replay(tmp_path):
    c,e,_,args,calls=environment(tmp_path)
    async def tail(inputs):
        return {'summary':inputs['middle']['value']+1}
    e=DurableWorkflowExecutor(tmp_path/'workflow.db',{
        'remote':distributed_step_handler(c,'tenant','remote',Q,poll_interval=.002,poll_timeout=.02),
        'next':lambda inputs: asyncio.sleep(0,result={'value':inputs['remote']['value']+3}),
        'tail':tail},WorkflowPolicy(frozenset({'remote','next','tail'}),deadline=20))
    steps=(WorkflowStep('remote','remote'),WorkflowStep('middle','next',('remote',)),
           WorkflowStep('grandchild','tail',('middle',)))
    first=await e.run(steps,**args)
    assert first.statuses=={'remote':'uncertain','middle':'skipped','grandchild':'skipped'}
    await worker_once(c,{'value':4})
    resolved=await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(c,'tenant','remote')},expected_head=first.journal_head)
    assert resolved.statuses=={'remote':'succeeded'}
    final=await e.run(steps,**args,expected_head=resolved.journal_head)
    assert final.outputs['grandchild']=={'summary':8}


@pytest.mark.asyncio
async def test_failed_one_of_two_reader_results_leaves_entire_journal_unchanged(tmp_path):
    c,e,_,args,_=environment(tmp_path)
    steps=(WorkflowStep('a','remote'),WorkflowStep('b','remote'))
    baseline=await e.run(steps,**args)
    assert all(s=='uncertain' for s in baseline.statuses.values())
    async def invalid_second(ctx):
        if ctx.step_id=='b':return RemoteReconciliation(ctx.tenant_id,'WRONG','succeeded',1)
        return RemoteReconciliation(ctx.tenant_id,ctx.idempotency_key,'succeeded',1)
    with pytest.raises(CheckpointIntegrityError):
        await e.reconcile(steps,**args,readers={'remote':invalid_second},expected_head=baseline.journal_head)
    assert await e.run(steps,**args,expected_head=baseline.journal_head)==baseline


@pytest.mark.asyncio
async def test_reconcile_timeout_leaves_head_untouched_and_does_not_block_second_attempt(tmp_path):
    c,e,steps,args,_=environment(tmp_path)
    baseline=await e.run(steps,**args)
    async def stalled(_):
        await asyncio.sleep(1)
    nochange=await e.reconcile(steps,**args,readers={'remote':stalled},expected_head=baseline.journal_head,read_timeout=.002)
    assert nochange==baseline
    await worker_once(c,{'value':2})
    late=await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(c,'tenant','remote')},expected_head=baseline.journal_head)
    assert late.statuses=={'remote':'succeeded'}


@pytest.mark.asyncio
async def test_reconciliation_survives_new_executor_and_keeps_original_output(tmp_path):
    c,e,steps,args,calls=environment(tmp_path)
    baseline=await e.run(steps,**args)
    await worker_once(c,{'value':5})
    fresh=DurableWorkflowExecutor(e._path,e._handlers,e._policy)
    late=await fresh.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(c,'tenant','remote')},expected_head=baseline.journal_head)
    final=await fresh.run(steps,**args,expected_head=late.journal_head)
    assert final.outputs['next']=={'double':10} and len(calls)==1
    with sqlite3.connect(fresh._path) as db:
        events=[s for (s,) in db.execute('SELECT status FROM omega_events ORDER BY seq')]
    assert events.count('running')==2 and events.count('succeeded')==2
    assert events.count('reopened')==1


@pytest.mark.asyncio
async def test_bad_remote_status_denied(tmp_path):
    c,e,steps,args,_=environment(tmp_path)
    baseline=await e.run(steps,**args)
    class Unexpected:
        def get(self,*a):
            o=c.get(*a)
            return JobSnapshot(o.tenant,o.job_id,'completed',1,1,{'value':10})
    with pytest.raises(CheckpointIntegrityError):
        await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(Unexpected(),'tenant','remote')},expected_head=baseline.journal_head)
    assert (await e.run(steps,**args,expected_head=baseline.journal_head))==baseline


@pytest.mark.asyncio
async def test_reconciled_after_elapsed_workflow_deadline_preserves_history_not_executes(tmp_path):
    c,e,steps,args,calls=environment(tmp_path)
    p=WorkflowPolicy(frozenset({'remote','next'}),deadline=.08)
    e=DurableWorkflowExecutor(tmp_path/'workflow.db',e._handlers,p)
    baseline=await e.run(steps,**args)
    assert baseline.statuses['remote']=='uncertain'
    await asyncio.sleep(.09)
    await worker_once(c,{'value':5})
    late=await e.reconcile(steps,**args,readers={'remote':distributed_step_reconciler(c,'tenant','remote')},expected_head=baseline.journal_head)
    final=await e.run(steps,**args,expected_head=late.journal_head)
    assert final.statuses=={'remote':'succeeded','next':'timed_out'}
    assert not calls


from test_omega_fabric import pki, serving  # noqa: E402,F401


@pytest.mark.asyncio
async def test_late_remote_result_is_read_over_actual_mtls(tmp_path,pki):
    with serving(pki,tmp_path/'coordinator.db',lease_ms=3000) as (_,connect,__):
        producer,worker_backend=connect('a'),connect('worker')
        q=Resources(1,100,100)
        h=distributed_step_handler(producer,'a','f',q,poll_interval=.002,poll_timeout=.03)
        calls=[]
        async def downstream(inputs):
            calls.append(1)
            return {'value':inputs['first']['value']+1}
        e=DurableWorkflowExecutor(tmp_path/'durable.db',{'r':h,'next':downstream},WorkflowPolicy(frozenset({'r','next'}),deadline=10))
        steps=(WorkflowStep('first','r'),WorkflowStep('second','next',('first',)))
        args=dict(tenant_id='a',run_id='r',workflow_revision='v')
        first=await e.run(steps,**args)
        assert first.statuses=={'first':'uncertain','second':'skipped'}
        worker=ClusterWorker(worker_backend,worker_backend.register('w'),{'f':lambda _:asyncio.sleep(0,result={'value':10})},heartbeat_interval=.1)
        assert (await worker.run_once()).state=='succeeded'
        reader=distributed_step_reconciler(producer,'a','f')
        recovered=await e.reconcile(steps,**args,readers={'r':reader},expected_head=first.journal_head)
        final=await e.run(steps,**args,expected_head=recovered.journal_head)
        assert final.outputs['second']=={'value':11}
        assert len(calls)==1


def test_standalone_integration_demo_is_executable_and_reproducible():
    import os
    import subprocess
    import sys
    from pathlib import Path
    source = Path(__file__).resolve().parents[4] / 'docs/omega/remote-reconcile/demo.py'
    p = subprocess.run([sys.executable, str(source)], capture_output=True, text=True,
                       env=os.environ.copy(), timeout=20, check=True)
    output = json.loads(p.stdout)
    assert output['initial'] == {'remote': 'uncertain', 'dependent': 'skipped'}
    assert output['reconciled'] == {'remote': 'succeeded'}
    assert output['dependent_value'] == 42
    assert output['remote_resubmissions'] == 0
    assert len(output['journal_head']) == 64
