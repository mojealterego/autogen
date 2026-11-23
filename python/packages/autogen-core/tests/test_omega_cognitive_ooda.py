"""OMEGA 11.0: real SQLite memory, deterministic world-state planning and HITL."""
import asyncio
from dataclasses import replace
import sqlite3

import pytest

from autogen_core import (
    CognitiveEvent, EpisodicStore, MemoryConflict, MemoryCorrupt,
    WorldAction, WorldModel, WorldPolicyDenied,
    ApprovalLedger, ApprovalDenied, CognitiveOODA, CognitiveDenied,
    SymbolicGate, SymbolicRule,
)


def test_lossless_persistent_memory_and_replay(tmp_path):
    path=tmp_path/'memory.sqlite'
    store=EpisodicStore(path, max_per_tenant=3)
    item=CognitiveEvent('tenant-a','id-1','alert',{'nested':{'a':[1, 'żółć', True]}, 'value':0})
    first=store.record(item)
    assert first.novelty_proxy==1.0 and first.occurrences==1
    assert store.record(item)==first
    assert EpisodicStore(path,max_per_tenant=3).recall('tenant-a','id-1')==item
    assert store.recall('tenant-b','id-1') is None
    with pytest.raises(MemoryConflict): store.record(CognitiveEvent('tenant-a','id-1','alert',{'value':2}))


def test_novelty_proxy_not_gradient_and_tenant_quotas(tmp_path):
    store=EpisodicStore(tmp_path/'m.sqlite',max_per_tenant=2)
    a=store.record(CognitiveEvent('a','1','event',{'v':1}))
    b=store.record(CognitiveEvent('a','2','event',{'v':2}))
    assert a.novelty_proxy==1.0 and b.novelty_proxy==0.5
    with pytest.raises(MemoryConflict):store.record(CognitiveEvent('a','3','other',{}))
    assert store.record(CognitiveEvent('b','1','event',{})).novelty_proxy==1.0
    assert [x.event_id for x in store.recent('a',limit=2)]==['2','1']


def test_integrity_detection_and_fail_closed(tmp_path):
    path=tmp_path/'mem.sqlite'
    store=EpisodicStore(path)
    store.record(CognitiveEvent('a','id','notice',{'abc':'test'}))
    with sqlite3.connect(path) as db:
        db.execute("UPDATE cognitive_events SET payload=? WHERE tenant='a'", (b'bad-stream',))
    with pytest.raises(MemoryCorrupt): store.recall('a','id')


def test_reject_invalid_untrusted_memory_payload_and_id(tmp_path):
    store=EpisodicStore(tmp_path/'m.sqlite')
    with pytest.raises(ValueError):CognitiveEvent('tenant','../etc','alert',{})
    with pytest.raises(ValueError):store.record(CognitiveEvent('a','1','alert',{'nan':float('nan')}))
    with pytest.raises(ValueError):store.record(CognitiveEvent('a','2','alert',{'huge':'x'*70000}))


def test_declarative_world_simulation_is_deterministic_and_pure():
    model=WorldModel(frozen_keys=frozenset({'system'}))
    before={'slots':4, 'system':1}
    actions=(WorldAction('increment','slots',-2),WorldAction('read','system'))
    a=model.simulate(before,actions)
    b=model.simulate(before,actions)
    assert a==b and a.final_state=={'slots':2,'system':1} and before['slots']==4
    assert len(a.digest)==64
    with pytest.raises(WorldPolicyDenied):model.simulate(before,(WorldAction('set','system',0),))
    with pytest.raises(WorldPolicyDenied):model.simulate(before,(WorldAction('increment','slots',-10),))


def test_world_model_rejects_injection_and_unbounded_actions():
    with pytest.raises(ValueError):WorldAction('exec','system32',0)
    with pytest.raises(ValueError):WorldAction('set','../etc/passwd',1)
    with pytest.raises(ValueError):WorldAction('increment','counter',True)
    with pytest.raises(WorldPolicyDenied):WorldModel(max_actions=2).simulate({'x':2},tuple(WorldAction('read','x') for _ in range(3)))
    with pytest.raises(WorldPolicyDenied):WorldModel().simulate({'x':10**15},(WorldAction('read','x'),))


def test_approval_ledger_survives_restart_and_denies_replay(tmp_path):
    path=tmp_path/'approve.sqlite'
    store=ApprovalLedger(path,{'a':frozenset({'admin'})},lifetime_seconds=60,clock=lambda:100.)
    ticket=store.stage('a','a'*64)
    with pytest.raises(ApprovalDenied):store.consume(ticket,'a','a'*64)
    with pytest.raises(ApprovalDenied):store.approve(ticket,'a','unauthorized')
    with pytest.raises(ApprovalDenied):store.approve(ticket,'b','admin')
    after=ApprovalLedger(path,{'a':frozenset({'admin'})},lifetime_seconds=60,clock=lambda:101.)
    after.approve(ticket,'a','admin')
    assert after.consume(ticket,'a','a'*64) is True
    with pytest.raises(ApprovalDenied):after.consume(ticket,'a','a'*64)
    with pytest.raises(ApprovalDenied):after.approve(ticket,'a','admin')


def test_approval_expiry_and_wrong_digest(tmp_path):
    now=[100.]
    s=ApprovalLedger(tmp_path/'s.sqlite',{'a':frozenset({'human'})},lifetime_seconds=2,clock=lambda:now[0])
    k=s.stage('a','0'*64)
    with pytest.raises(ApprovalDenied):s.consume(k,'a','1'*64)
    now[0]=103
    with pytest.raises(ApprovalDenied):s.approve(k,'a','human')


@pytest.mark.asyncio
async def test_ooda_observe_orient_decide_act_with_approval(tmp_path):
    memory=EpisodicStore(tmp_path/'memory.sqlite')
    approval=ApprovalLedger(tmp_path/'a.sqlite',{'t':frozenset({'operator'})})
    policy=SymbolicGate({'tenant':'str','steps':'int','mutations':'int','novelty_bp':'int'}, (
        SymbolicRule('tenant','tenant == "t"'),
        SymbolicRule('budget','steps <= 4 and mutations <= 2'),
    ))
    engine=CognitiveOODA(memory,WorldModel(),approval,policy)
    event=CognitiveEvent('t','evt-1','telemetry',{'disk':50})
    decision=await engine.assess(event,{'available':5},(WorldAction('increment','available',-1),))
    assert decision.status=='awaiting_approval' and decision.ticket is not None
    called=[]
    async def handler(actions,origin):called.append((actions,origin));return {'applied':True}
    with pytest.raises(ApprovalDenied):await engine.act(decision,handler)
    approval.approve(decision.ticket,'t','operator')
    assert await engine.act(decision,handler)=={'applied':True}
    assert len(called)==1
    with pytest.raises(ApprovalDenied):await engine.act(decision,handler)
    assert memory.recall('t','evt-1')==event


@pytest.mark.asyncio
async def test_ooda_read_only_requires_no_approval(tmp_path):
    m=EpisodicStore(tmp_path/'m.sqlite');a=ApprovalLedger(tmp_path/'a.sqlite',{'t':frozenset({'human'})})
    engine=CognitiveOODA(m,WorldModel(),a)
    decision=await engine.assess(CognitiveEvent('t','1','trace',{}),{'counter':1},(WorldAction('read','counter'),))
    assert decision.status=='simulated' and decision.ticket is None
    with pytest.raises(CognitiveDenied):await engine.act(decision,lambda a,b:None)


@pytest.mark.asyncio
async def test_ooda_symbolic_fail_closed_before_ticket_or_handler(tmp_path):
    m=EpisodicStore(tmp_path/'m.sqlite');a=ApprovalLedger(tmp_path/'a.sqlite',{'t':frozenset({'human'})})
    guard=SymbolicGate({'tenant':'str','steps':'int','mutations':'int','novelty_bp':'int'},(SymbolicRule('deny','steps < 1'),))
    engine=CognitiveOODA(m,WorldModel(),a,guard)
    with pytest.raises(CognitiveDenied):
        await engine.assess(CognitiveEvent('t','e','alert',{}),{'counter':1},(WorldAction('increment','counter',1),))


@pytest.mark.asyncio
async def test_ooda_tamper_and_failure_remain_uncertain_no_replay(tmp_path):
    m=EpisodicStore(tmp_path/'m.sqlite');a=ApprovalLedger(tmp_path/'a.sqlite',{'t':frozenset({'admin'})})
    eng=CognitiveOODA(m,WorldModel(),a)
    d=await eng.assess(CognitiveEvent('t','e','alert',{}),{'a':1},(WorldAction('set','a',2),))
    a.approve(d.ticket,'t','admin')
    with pytest.raises(CognitiveDenied):await eng.act(replace(d, digest='f'*64),lambda x,y:None)
    async def fail(actions,origin):raise RuntimeError('side effect outcome unknown')
    with pytest.raises(CognitiveDenied,match='unknown'):
        await eng.act(d,fail)
    with pytest.raises(ApprovalDenied):await eng.act(d,fail)

@pytest.mark.asyncio
async def test_retry_of_same_observation_cannot_mint_second_approval(tmp_path):
    m=EpisodicStore(tmp_path/'m.sqlite');a=ApprovalLedger(tmp_path/'a.sqlite',{'t':frozenset({'admin'})})
    o=CognitiveOODA(m,WorldModel(),a)
    ev=CognitiveEvent('t','event-007','telemetry',{'count':1})
    actions=(WorldAction('increment','cpu',1),)
    one=await o.assess(ev,{'cpu':2},actions)
    two=await o.assess(ev,{'cpu':2},actions)
    assert one.ticket==two.ticket, 'same event and plan must not create a second approval'
    a.approve(one.ticket,'t','admin')
    called=[]
    async def effect(*args):called.append(True);return {'result':True}
    assert await o.act(one,effect)=={'result':True}
    with pytest.raises(ApprovalDenied):await o.act(two,effect)
    assert len(called)==1
    with pytest.raises(ApprovalDenied):
        await o.assess(ev,{'cpu':2},(WorldAction('set','cpu',99),))


def test_clock_rollback_does_not_extend_expired_ticket(tmp_path):
    clock=[100.]
    a=ApprovalLedger(tmp_path/'a.sqlite',{'t':frozenset({'admin'})},lifetime_seconds=5,clock=lambda:clock[0])
    t=a.stage('t','a'*64)
    clock[0]=108.
    with pytest.raises(ApprovalDenied):a.approve(t,'t','admin')
    clock[0]=101.
    with pytest.raises(ApprovalDenied):a.approve(t,'t','admin')


@pytest.mark.asyncio
async def test_parallel_duplicate_actuation_is_single_use(tmp_path):
    a=ApprovalLedger(tmp_path/'a.sqlite',{'t':frozenset({'operator'})})
    o=CognitiveOODA(EpisodicStore(tmp_path/'m.sqlite'),WorldModel(),a)
    d=await o.assess(CognitiveEvent('t','evt','status',{}),{'x':0},(WorldAction('set','x',1),))
    a.approve(d.ticket,'t','operator')
    effects=[]
    async def handler(*args): effects.append(1);await asyncio.sleep(.01);return {'ok':True}
    results=await asyncio.gather(o.act(d,handler),o.act(d,handler),return_exceptions=True)
    assert sum(type(x) is dict for x in results)==1
    assert sum(isinstance(x,ApprovalDenied) for x in results)==1
    assert len(effects)==1

@pytest.mark.asyncio
async def test_two_controllers_parallel_same_event_one_ticket(tmp_path):
    path_memory=tmp_path/'mem.sqlite';path_approval=tmp_path/'appr.sqlite'
    event=CognitiveEvent('org','same','anomaly',{'source':'agent'})
    async def run():
        engine=CognitiveOODA(EpisodicStore(path_memory),WorldModel(),
                             ApprovalLedger(path_approval,{'org':frozenset({'human'})}))
        return await engine.assess(event,{'q':4},(WorldAction('increment','q',1),))
    a,b=await asyncio.gather(run(),run())
    assert a.digest==b.digest and a.ticket==b.ticket


@pytest.mark.asyncio
async def test_real_swarm_invokes_ooda_without_exposing_actuator(tmp_path):
    from autogen_core import SwarmAgent, SwarmNode, SwarmPolicy, HierarchicalSwarm
    gate=CognitiveOODA(EpisodicStore(tmp_path/'mem.sqlite'),WorldModel(),
                       ApprovalLedger(tmp_path/'approval.sqlite',{'org':frozenset({'human'})}))
    async def supervisor(_):return {'supervised':True}
    async def observer(_):
        decision=await gate.assess(CognitiveEvent('org','e1','telemetry',{'temperature':42}),
                                   {'load':1},(WorldAction('increment','load',1),))
        return {'planned':decision.status,'has_ticket':bool(decision.ticket)}
    agents=(SwarmAgent('supervisor','org','supervisor',None,frozenset({'plan'}),supervisor),
            SwarmAgent('observer','org','worker','supervisor',frozenset({'observe'}),observer))
    result=await HierarchicalSwarm(agents,SwarmPolicy()).run('org',(
        SwarmNode('observe','observer','observe',{}),))
    assert result.accepted
    assert result.result.outputs['observe']=={'planned':'awaiting_approval','has_ticket':True}
    # No action was executed: the approved ticket has not yet been consumed.
