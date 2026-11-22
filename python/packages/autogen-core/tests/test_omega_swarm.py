import asyncio
import pytest

from autogen_core import (
    SwarmAgent, SwarmNode, SwarmPolicy, HierarchicalSwarm, SwarmDenied,
)


def roster(events):
    async def run(message):
        events.append(('work', message))
        return {'answer': message['task']['value'] + 1}
    async def review(message):
        events.append(('review', message))
        return {'approved': message['dependencies']['work']['answer'] == 3}
    return [
        SwarmAgent('chief','tenant-a','supervisor',None,frozenset({'plan'}),run),
        SwarmAgent('worker','tenant-a','worker','chief',frozenset({'compute'}),run),
        SwarmAgent('critic','tenant-a','verifier','chief',frozenset({'verify'}),review),
    ]


@pytest.mark.asyncio
async def test_hierarchy_executes_and_independent_verifier_approves():
    events=[]
    s=HierarchicalSwarm(roster(events),SwarmPolicy())
    r=await s.run('tenant-a',(
        SwarmNode('work','worker','compute',{'value':2},requires_verification=True),
        SwarmNode('review','critic','verify',{},('work',),verifies='work'),
    ))
    assert r.accepted and r.result.statuses == {'work':'succeeded','review':'succeeded'}
    assert r.result.outputs['work'] == {'answer':3}
    assert [x[0] for x in events] == ['work','review']


@pytest.mark.asyncio
async def test_verifier_veto_rejects_without_hidden_approval():
    s=HierarchicalSwarm(roster([]),SwarmPolicy())
    r=await s.run('tenant-a',(
        SwarmNode('work','worker','compute',{'value':5},requires_verification=True),
        SwarmNode('review','critic','verify',{},('work',),verifies='work'),
    ))
    assert not r.accepted and r.verifications == {'work':False}


@pytest.mark.asyncio
async def test_missing_independent_verifier_denied_before_side_effect():
    events=[]
    s=HierarchicalSwarm(roster(events),SwarmPolicy())
    with pytest.raises(SwarmDenied):
        await s.run('tenant-a',(SwarmNode('work','worker','compute',{'value':2},requires_verification=True),))
    assert events == []


@pytest.mark.asyncio
async def test_high_impact_requires_explicit_task_approval():
    events=[]
    s=HierarchicalSwarm(roster(events),SwarmPolicy(protected_capabilities=frozenset({'compute'})))
    tasks=(SwarmNode('work','worker','compute',{'value':2}),)
    with pytest.raises(SwarmDenied): await s.run('tenant-a',tasks)
    assert not events
    result=await s.run('tenant-a',tasks,approvals=frozenset({'work'}))
    assert result.accepted and len(events)==1


@pytest.mark.asyncio
async def test_unauthorized_tenant_and_capability_denied():
    s=HierarchicalSwarm(roster([]),SwarmPolicy())
    with pytest.raises(SwarmDenied): await s.run('tenant-b',(SwarmNode('work','worker','compute',{'value':2}),))
    with pytest.raises(SwarmDenied): await s.run('tenant-a',(SwarmNode('work','worker','deploy',{'value':2}),))


def test_invalid_hierarchy_cycle_and_orphan_fail_closed():
    async def task(_): return {}
    with pytest.raises(SwarmDenied): HierarchicalSwarm([
        SwarmAgent('s','a','supervisor','s',frozenset({'plan'}),task)
    ],SwarmPolicy())
    with pytest.raises(SwarmDenied): HierarchicalSwarm([
        SwarmAgent('s','a','supervisor',None,frozenset({'plan'}),task),
        SwarmAgent('w','a','worker','gone',frozenset({'compute'}),task)
    ],SwarmPolicy())


@pytest.mark.asyncio
async def test_verifier_cannot_verify_its_own_work_or_unrelated_task():
    s=HierarchicalSwarm(roster([]),SwarmPolicy())
    with pytest.raises(SwarmDenied): await s.run('tenant-a',(
        SwarmNode('work','worker','compute',{'value':2},requires_verification=True),
        SwarmNode('review','worker','compute',{},('work',),verifies='work'),
    ))


@pytest.mark.asyncio
async def test_task_failure_skips_verification_and_denies_report():
    events=[]
    async def fail(_): raise RuntimeError('internal secret')
    agents=roster(events)
    agents[1]=SwarmAgent('worker','tenant-a','worker','chief',frozenset({'compute'}),fail)
    s=HierarchicalSwarm(agents,SwarmPolicy())
    r=await s.run('tenant-a',(
        SwarmNode('work','worker','compute',{'value':2},requires_verification=True),
        SwarmNode('review','critic','verify',{},('work',),verifies='work'),
    ))
    assert not r.accepted
    assert r.result.statuses == {'work':'failed','review':'skipped'}
    assert events == []
