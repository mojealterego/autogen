import asyncio
import pytest
from autogen_core import (
    SymbolicRule, SymbolicGate, SymbolicDenied, symbolically_guarded,
    SwarmAgent, SwarmNode, SwarmPolicy, HierarchicalSwarm,
)


def gate():
    return SymbolicGate({'tenant':'str','used':'int','requested':'int','approved':'bool'},(
        SymbolicRule('capacity','used + requested <= 10'),
        SymbolicRule('tenant','tenant == "a"'),
        SymbolicRule('consent','approved and requested > 0'),
    ))


def test_symbolic_guard_acceptance_and_deterministic_evidence():
    g=gate();context={'tenant':'a','used':5,'requested':4,'approved':True}
    result=g.evaluate(context)
    assert result.accepted and len(result.evidence_head)==64
    assert result==g.evaluate(context)
    assert g.verify_evidence(result)


def test_fail_closed_capacity_and_tenant():
    g=gate()
    assert not g.evaluate({'tenant':'a','used':8,'requested':4,'approved':True}).accepted
    with pytest.raises(SymbolicDenied): g.require({'tenant':'b','used':1,'requested':2,'approved':True})


@pytest.mark.parametrize('expr',[
    '__import__("os").system("id")',
    '().__class__.__mro__',
    '[1,2,3][0]',
    '2 ** 5000000',
    '(__builtins__)',
    '(x for x in [1])',
    'tenant.upper()',
    'used if approved else 3',
])
def test_attack_syntax_rejected(expr):
    with pytest.raises(SymbolicDenied): SymbolicGate({'tenant':'str','used':'int','approved':'bool'},(SymbolicRule('bad',expr),))


def test_type_confusion_and_unknown_inputs_fail_closed():
    g=gate()
    with pytest.raises(SymbolicDenied): g.evaluate({'tenant':'a','used':True,'requested':4,'approved':True})
    with pytest.raises(SymbolicDenied): g.evaluate({'tenant':'a','used':1,'requested':4,'approved':True,'admin':True})
    with pytest.raises(SymbolicDenied): g.evaluate({'tenant':'a','used':1,'requested':4})


def test_bound_oversized_expression_and_numeric_overflow():
    with pytest.raises(SymbolicDenied): SymbolicGate({'n':'int'},(SymbolicRule('bad','n+'*200+'1'),))
    with pytest.raises(SymbolicDenied): SymbolicGate({'n':'int'},(SymbolicRule('bad','n + 999999999999 + 999999999999'),))


def test_tampering_evidence_detected():
    from dataclasses import replace
    g=gate();v=g.evaluate({'tenant':'a','used':1,'requested':1,'approved':True})
    assert not g.verify_evidence(replace(v,evidence_head='0'*64))
    assert not g.verify_evidence(replace(v,rule_passes=(False,True,True)))


@pytest.mark.asyncio
async def test_guarded_worker_prevents_side_effect_when_symbolic_gate_denies():
    called=[]
    async def task(message): called.append(message);return {'ok':True}
    async def supervisor(message): return {'ok':True}
    guarded=symbolically_guarded(gate(),task,lambda _: {'tenant':'a','used':9,'requested':4,'approved':True})
    agents=[SwarmAgent('boss','a','supervisor',None,frozenset({'plan'}),supervisor),
            SwarmAgent('worker','a','worker','boss',frozenset({'write'}),guarded)]
    result=await HierarchicalSwarm(agents,SwarmPolicy()).run('a',(SwarmNode('effect','worker','write',{}),))
    assert not result.accepted and result.result.statuses['effect']=='failed' and not called
