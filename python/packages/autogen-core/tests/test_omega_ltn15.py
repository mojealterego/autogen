"""OMEGA 15: exercise original user-supplied LTNtorch 1.0.2, not a replica."""
from __future__ import annotations
import pytest
import torch
ltn=pytest.importorskip("ltn",reason="original LTNtorch is optional")
from autogen_core import SymbolicGate, SymbolicRule
from autogen_core._research_neural import LTNAxiomLearner
from autogen_core._research_upstream import LTNtorchBridge, LTNtorchRejected

def _dataset():
    # Conjunctive predicate: positive iff both features are positive.
    return (torch.tensor([[0.,0.],[0.,1.],[1.,0.],[1.,1.]],dtype=torch.float32),
            torch.tensor([0.,0.,0.,1.],dtype=torch.float32))


def test_ltn_uses_original_library_quantifier_and_updates_real_weights():
    assert ltn.__file__.endswith('ltn/__init__.py')
    learner=LTNAxiomLearner(2,seed=2026)
    hard=SymbolicGate({'approved':'bool'},[SymbolicRule('authorization','approved == True')])
    bridge=LTNtorchBridge(learner,hard,threshold=.7)
    x,y=_dataset()
    first=bridge.satisfaction(x,y)
    before=learner.predicate.weight.detach().clone()
    report=bridge.fit(x,y,steps=120,lr=.12)
    final=bridge.satisfaction(x,y)
    assert report.steps==120 and final>first+.15 and final>.9 and report.positive_count==1 and report.negative_count==3
    assert not torch.equal(before,learner.predicate.weight)
    assert bridge.require(torch.tensor([1.,1.]),{'approved':True}).accepted
    with pytest.raises(Exception):bridge.require(torch.tensor([1.,1.]),{'approved':False})


def test_ltn_fail_closed_for_bad_labels_single_class_and_nan():
    learner=LTNAxiomLearner(2)
    gate=SymbolicGate({'ok':'bool'},[SymbolicRule('ok','ok == True')])
    b=LTNtorchBridge(learner,gate)
    x,y=_dataset()
    with pytest.raises(LTNtorchRejected): b.fit(x,torch.ones(4),steps=1)
    with pytest.raises(LTNtorchRejected): b.fit(x,torch.tensor([0.,0.,0.,.25]),steps=1)
    with pytest.raises(LTNtorchRejected): b.fit(torch.full((4,2),float('nan')),y,steps=1)
    with pytest.raises(LTNtorchRejected): b.fit(x,y,steps=100000)


def test_ltn_independently_enforces_neural_confidence():
    learner=LTNAxiomLearner(2)
    with torch.no_grad():
        learner.predicate.weight.zero_()
        learner.predicate.bias.fill_(-15)
    gate=SymbolicGate({'ok':'bool'},[SymbolicRule('ok','ok == True')])
    bridge=LTNtorchBridge(learner,gate,threshold=.99)
    with pytest.raises(Exception): bridge.require(torch.tensor([1.,1.]),{'ok':True})


