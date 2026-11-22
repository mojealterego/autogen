import asyncio
import pytest
from autogen_core import (
    EvolutionCandidate, EvolutionCase, EvolutionPolicy, EvolutionObservation,
    WorkflowEvolutionEngine, EvolutionDenied,
)


def cases(n=12): return tuple(EvolutionCase(f'case-{i:02d}',{'n':i}) for i in range(n))


def candidate(name, parallel): return EvolutionCandidate(name,{'max_parallel':parallel,'retry_base_ms':50})


@pytest.mark.asyncio
async def test_candidate_better_latency_promotes_only_with_operator_gate():
    async def evaluate(model, case):
        return EvolutionObservation(True, 120 if model.name=='baseline' else 60, 3)
    engine=WorkflowEvolutionEngine(candidate('baseline',1),cases(),evaluate,EvolutionPolicy())
    decision=await engine.compare(candidate('optimized',2))
    assert decision.passed and decision.candidate_p95_ms == 60 and decision.baseline_p95_ms == 120
    assert len(decision.evidence_digest)==64
    with pytest.raises(EvolutionDenied): engine.promote(decision,operator_approved=False,expected_revision=0)
    snap=engine.promote(decision,operator_approved=True,expected_revision=0)
    assert snap.active.name=='optimized' and snap.revision==1


@pytest.mark.asyncio
async def test_correctness_regression_rejected_even_if_faster():
    async def evaluate(model, case):
        return EvolutionObservation(not (model.name=='bad' and case.id=='case-02'),1,1)
    engine=WorkflowEvolutionEngine(candidate('baseline',1),cases(),evaluate,EvolutionPolicy())
    decision=await engine.compare(candidate('bad',2))
    assert not decision.passed
    with pytest.raises(EvolutionDenied): engine.promote(decision,operator_approved=True,expected_revision=0)


@pytest.mark.asyncio
async def test_slow_or_expensive_candidates_fail():
    async def evaluate(model, case):
        return EvolutionObservation(True, 200 if model.name=='slow' else 100, 20 if model.name=='slow' else 5)
    engine=WorkflowEvolutionEngine(candidate('baseline',1),cases(),evaluate,EvolutionPolicy())
    assert not (await engine.compare(candidate('slow',2))).passed


@pytest.mark.asyncio
async def test_stale_revision_and_forged_decision_denied():
    async def evaluate(model, case):
        return EvolutionObservation(True,200 if model.name=='baseline' else 1,0)
    engine=WorkflowEvolutionEngine(candidate('baseline',1),cases(),evaluate,EvolutionPolicy())
    d=await engine.compare(candidate('good',2))
    with pytest.raises(EvolutionDenied):engine.promote(d,operator_approved=True,expected_revision=4)
    from dataclasses import replace
    with pytest.raises(EvolutionDenied):engine.promote(replace(d,evidence_digest='0'*64),operator_approved=True,expected_revision=0)
    engine.promote(d,operator_approved=True,expected_revision=0)
    with pytest.raises(EvolutionDenied):engine.promote(d,operator_approved=True,expected_revision=0)


@pytest.mark.asyncio
async def test_deterministic_evidence_fixture_order():
    trace=[]
    async def evaluate(model, case):
        trace.append((model.name,case.id))
        return EvolutionObservation(True,90 if model.name=='a' else 40,1)
    engine=WorkflowEvolutionEngine(candidate('a',1),tuple(reversed(cases())),evaluate,EvolutionPolicy())
    d=await engine.compare(candidate('b',2))
    first=trace.copy();trace.clear()
    again=await engine.compare(candidate('b',2))
    assert d.evidence_digest==again.evidence_digest and trace==first


@pytest.mark.asyncio
async def test_evaluator_error_fails_closed_without_promotable_decision():
    async def evaluate(model, case):
        if model.name=='new': raise RuntimeError('secrets')
        return EvolutionObservation(True,100,1)
    engine=WorkflowEvolutionEngine(candidate('base',1),cases(),evaluate,EvolutionPolicy())
    with pytest.raises(EvolutionDenied,match='evaluation failed'):
        await engine.compare(candidate('new',2))
    assert engine.snapshot().revision==0


@pytest.mark.asyncio
async def test_canary_rolls_back_on_accuracy_regression():
    async def evaluate(model, case): return EvolutionObservation(True,100 if model.name=='base' else 40,1)
    engine=WorkflowEvolutionEngine(candidate('base',1),cases(),evaluate,EvolutionPolicy())
    d=await engine.compare(candidate('new',2))
    engine.promote(d,operator_approved=True,expected_revision=0)
    assert engine.record_canary((EvolutionObservation(False,25,1),)*4,operator_approved=True,expected_revision=1).active.name=='base'
    assert engine.snapshot().revision==2


def test_invalid_unbounded_case_and_parameter_inputs():
    with pytest.raises(ValueError): EvolutionCandidate('evil',{'max_parallel':True})
    with pytest.raises(ValueError): EvolutionCase('case',{'bad':object()})
    with pytest.raises(ValueError): EvolutionObservation(True,-1,0)
    async def work(a,b): return EvolutionObservation(True,1,0)
    with pytest.raises(ValueError): WorkflowEvolutionEngine(candidate('a',1),cases(1),work,EvolutionPolicy(min_cases=8))
