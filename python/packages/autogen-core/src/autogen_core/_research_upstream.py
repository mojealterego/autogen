"""Opt-in bridge to the actual LTNtorch library (tested with source release 1.0.2).

This module never vendors third-party code and never runs expressions from an
agent. Fuzzy truth is an optimization score, NOT a formal proof: authorization
always requires the independent hard SymbolicGate to pass first.
"""
from __future__ import annotations

import hashlib
import importlib
import math
from dataclasses import dataclass
from typing import Any, Mapping

import torch
from torch import Tensor, nn

from ._governed_workflow import _canonical
from ._neurosymbolic_gate import SymbolicDenied, SymbolicGate, SymbolicVerdict
from ._research_neural import LTNAxiomLearner


class LTNtorchRejected(ValueError):
    """LTN package or tensor evidence failed the trusted bounded contract."""


@dataclass(frozen=True)
class LTNtorchReport:
    satisfaction_before: float
    satisfaction_after: float
    steps: int
    positive_count: int
    negative_count: int
    evidence_digest: str


@dataclass(frozen=True)
class LTNtorchDecision:
    accepted: bool
    neural_score: float
    symbolic: SymbolicVerdict


class LTNtorchBridge:
    """Real `ltn.Variable/Predicate/Quantifier/Connective/SatAgg` based training.

    Two FOL rules are evaluated on separate, explicitly labeled sets:
       forall x in Pos: Authorized(x)
       forall x in Neg: not Authorized(x)
    The labels and training data come only from trusted fixtures.
    """

    def __init__(self, learner: LTNAxiomLearner, hard: SymbolicGate, *, threshold: float = .8, p: int = 2):
        if not isinstance(learner, LTNAxiomLearner) or not isinstance(hard, SymbolicGate):
            raise ValueError('learned predicate and hard gate required')
        if (type(threshold) not in (int,float) or not math.isfinite(threshold) or
                not 0 < threshold <= 1 or type(p) is not int or not 1 <= p <= 8):
            raise ValueError('invalid bounded fuzzy semantics')
        try:
            ltn = importlib.import_module('ltn')
            # Do not claim a package is present merely because a lookalike module
            # can be imported. Require original LTNtorch primitives.
            for name in ('Variable', 'Predicate', 'Quantifier', 'Connective', 'fuzzy_ops'):
                getattr(ltn,name)
            for name in ('AggregPMeanError', 'NotStandard', 'SatAgg'):
                getattr(ltn.fuzzy_ops,name)
        except (ImportError, AttributeError) as exc:
            raise LTNtorchRejected('original LTNtorch package not available') from exc
        self.ltn = ltn
        self.learner = learner
        self.hard = hard
        self.threshold = float(threshold)
        self.p = p
        self._not = ltn.Connective(ltn.fuzzy_ops.NotStandard())
        self._forall = ltn.Quantifier(ltn.fuzzy_ops.AggregPMeanError(p=p), quantifier='f')
        self._sat = ltn.fuzzy_ops.SatAgg(ltn.fuzzy_ops.AggregPMeanError(p=p))
        self._predicate = ltn.Predicate(model=nn.Sequential(learner.predicate, nn.Sigmoid()))

    def _validate(self, features: Tensor, labels: Tensor) -> tuple[Tensor, Tensor]:
        if (not isinstance(features, Tensor) or not isinstance(labels, Tensor) or
                features.device.type != 'cpu' or labels.device.type != 'cpu' or
                features.shape != (labels.numel(), self.learner.features) or
                features.ndim != 2 or labels.ndim != 1 or not 2 <= len(labels) <= 2048 or
                not torch.is_floating_point(features) or not torch.is_floating_point(labels) or
                not bool(torch.isfinite(features).all()) or not bool(torch.isfinite(labels).all()) or
                not bool(((labels==0)|(labels==1)).all()) or not bool((labels==0).any()) or not bool((labels==1).any())):
            raise LTNtorchRejected('expected finite CPU feature vectors with both binary label classes')
        return features.float().detach(), labels.detach()

    def _sat_value(self, features: Tensor, labels: Tensor) -> Tensor:
        x,y=self._validate(features,labels)
        positive=self.ltn.Variable('positive',x[y==1].contiguous())
        negative=self.ltn.Variable('negative',x[y==0].contiguous())
        yes=self._forall(positive,self._predicate(positive))
        no=self._forall(negative,self._not(self._predicate(negative)))
        return self._sat(yes,no)

    def satisfaction(self, features: Tensor, labels: Tensor) -> float:
        with torch.no_grad():
            value=float(self._sat_value(features,labels).item())
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise LTNtorchRejected('nonfinite fuzzy satisfaction')
        return value

    def fit(self, features: Tensor, labels: Tensor, *, steps: int = 100, lr: float = .1) -> LTNtorchReport:
        x,y=self._validate(features,labels)
        if (type(steps) is not int or not 1 <= steps <= 512 or
                type(lr) not in (int,float) or not math.isfinite(lr) or not 0 < lr <= .5):
            raise LTNtorchRejected('invalid learning budget')
        before=self.satisfaction(x,y)
        optimizer=torch.optim.Adam(self.learner.parameters(),lr=float(lr))
        for _ in range(steps):
            optimizer.zero_grad(set_to_none=True)
            score=self._sat_value(x,y)
            if not bool(torch.isfinite(score)):
                raise LTNtorchRejected('nonfinite LTN loss')
            (1-score).backward()
            torch.nn.utils.clip_grad_norm_(self.learner.parameters(), 10.)
            optimizer.step()
        after=self.satisfaction(x,y)
        return LTNtorchReport(before,after,steps,int((y==1).sum()),int((y==0).sum()),
                             hashlib.sha256(_canonical(['LTNtorch-evidence-v1',x.tolist(),y.tolist(),steps,float(lr)])).hexdigest())

    def require(self, features: Tensor, context: Mapping[str,Any]) -> LTNtorchDecision:
        # A fuzzy approval is never allowed to override a hard authorization veto.
        verified=self.hard.require(context)
        if (not isinstance(features,Tensor) or features.device.type != 'cpu' or
                features.shape != (self.learner.features,) or not torch.is_floating_point(features) or
                not bool(torch.isfinite(features).all())):
            raise LTNtorchRejected('untrusted inference features')
        with torch.no_grad():
            prob=float(self.learner(features.float()).item())
        if not math.isfinite(prob) or prob < self.threshold:
            raise SymbolicDenied('LTNtorch learned probability not sufficient for authorization')
        return LTNtorchDecision(True,prob,verified)
