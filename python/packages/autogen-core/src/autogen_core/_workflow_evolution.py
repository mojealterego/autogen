"""Deterministic, approval-gated workflow policy experiments.

Evaluations are supplied by a trusted evaluator; acceptance metrics are not
proof of real-world model quality. No autonomous production deployment.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ._governed_workflow import _canonical


class EvolutionDenied(RuntimeError):
    """An evaluation or promotion failed its evidence/approval gates."""


def _name(x: Any) -> bool:
    return type(x) is str and 0 < len(x) <= 128 and all(ord(c)>=32 for c in x)


@dataclass(frozen=True)
class EvolutionCandidate:
    name: str
    parameters: Mapping[str, int]

    def __post_init__(self) -> None:
        allowed={'max_parallel':(1,64),'retry_base_ms':(0,10000),'step_timeout_ms':(1,600000)}
        if (not _name(self.name) or not isinstance(self.parameters,Mapping) or not self.parameters
                or any(k not in allowed or type(v) is not int or not allowed[k][0]<=v<=allowed[k][1]
                       for k,v in self.parameters.items())):
            raise ValueError('invalid or unbounded candidate')
        object.__setattr__(self,'parameters',dict(sorted(self.parameters.items())))


@dataclass(frozen=True)
class EvolutionCase:
    id: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not _name(self.id) or not isinstance(self.payload,Mapping):
            raise ValueError('invalid evolution fixture')
        try:
            data=_canonical(dict(self.payload))
            if len(data)>65536: raise ValueError('fixture exceeds size budget')
            object.__setattr__(self,'payload',json.loads(data))
        except (ValueError,OverflowError,RecursionError,TypeError) as exc:
            raise ValueError('invalid evolution fixture JSON') from exc


@dataclass(frozen=True)
class EvolutionObservation:
    correct: bool
    latency_ms: int
    cost_micro_usd: int

    def __post_init__(self) -> None:
        if (type(self.correct) is not bool or type(self.latency_ms) is not int
                or not 0<=self.latency_ms<=3600000 or type(self.cost_micro_usd) is not int
                or not 0<=self.cost_micro_usd<=10**12):
            raise ValueError('invalid observation')


@dataclass(frozen=True)
class EvolutionPolicy:
    min_cases: int = 8
    max_cases: int = 128
    required_gain: float = .05
    max_latency_regression: float = .05
    max_cost_regression: float = .05
    evaluation_timeout: float = 15.0
    seed: int = 0

    def __post_init__(self) -> None:
        if (type(self.min_cases) is not int or type(self.max_cases) is not int
                or not 2<=self.min_cases<=self.max_cases<=1024
                or type(self.seed) is not int or not 0<=self.seed<2**32
                or any(type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1
                       for x in (self.required_gain,self.max_latency_regression,self.max_cost_regression))
                or type(self.evaluation_timeout) not in (int,float)
                or not math.isfinite(self.evaluation_timeout)
                or not 0<self.evaluation_timeout<=3600):
            raise ValueError('invalid evolution policy')


@dataclass(frozen=True)
class EvolutionDecision:
    candidate: EvolutionCandidate
    baseline: EvolutionCandidate
    passed: bool
    candidate_accuracy: float
    baseline_accuracy: float
    candidate_p95_ms: int
    baseline_p95_ms: int
    candidate_cost: int
    baseline_cost: int
    evidence_digest: str
    revision: int


@dataclass(frozen=True)
class EvolutionSnapshot:
    active: EvolutionCandidate
    revision: int
    pending_evidence: str | None
    previous: EvolutionCandidate | None


Evaluator = Callable[[EvolutionCandidate, EvolutionCase], Awaitable[EvolutionObservation]]


def _p95(obs: Sequence[EvolutionObservation]) -> int:
    values=sorted(x.latency_ms for x in obs)
    return values[math.ceil(len(values)*.95)-1]


class WorkflowEvolutionEngine:
    """Trusted offline evaluator plus optimistic, operator-approved policy switch."""

    def __init__(self, baseline: EvolutionCandidate, cases: Sequence[EvolutionCase],
                 evaluator: Evaluator, policy: EvolutionPolicy) -> None:
        if (not isinstance(baseline,EvolutionCandidate) or not isinstance(policy,EvolutionPolicy)
                or not callable(evaluator) or not policy.min_cases<=len(cases)<=policy.max_cases
                or any(not isinstance(c,EvolutionCase) for c in cases)
                or len({c.id for c in cases})!=len(cases)):
            raise ValueError('invalid evolution evaluation suite')
        self._policy=policy
        self._baseline=baseline
        self._active=baseline
        self._previous: EvolutionCandidate | None = None
        self._revision=0
        self._evaluator=evaluator
        self._cases=tuple(sorted(cases,key=lambda c:(hashlib.sha256(_canonical([policy.seed,c.id])).hexdigest(),c.id)))
        self._pending: EvolutionDecision | None = None
        self._active_decision: EvolutionDecision | None = None

    def snapshot(self) -> EvolutionSnapshot:
        return EvolutionSnapshot(self._active,self._revision,
                                 self._pending.evidence_digest if self._pending else None,self._previous)

    async def compare(self, candidate: EvolutionCandidate) -> EvolutionDecision:
        if not isinstance(candidate,EvolutionCandidate) or candidate.name==self._active.name:
            raise EvolutionDenied('candidate must differ from active policy')
        baseline=self._active
        records: list[list[EvolutionObservation]]=[[],[]]
        try:
            # Fixed ordering, same held-out fixtures and no hidden unbounded parallel calls.
            for fixture in self._cases:
                for index,model in enumerate((baseline,candidate)):
                    result=await asyncio.wait_for(self._evaluator(model,fixture),self._policy.evaluation_timeout)
                    if not isinstance(result,EvolutionObservation):
                        raise EvolutionDenied('invalid evaluator result')
                    records[index].append(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._pending=None
            raise EvolutionDenied('evaluation failed; candidate not promotable') from exc
        b,c=records
        b_accuracy=sum(x.correct for x in b)/len(b)
        c_accuracy=sum(x.correct for x in c)/len(c)
        b_p95,c_p95=_p95(b),_p95(c)
        b_cost,c_cost=sum(x.cost_micro_usd for x in b),sum(x.cost_micro_usd for x in c)
        correct_ok=c_accuracy>=b_accuracy
        latency_ok=c_p95<=b_p95*(1+self._policy.max_latency_regression)
        cost_ok=c_cost<=b_cost*(1+self._policy.max_cost_regression)
        improvement=(c_accuracy>b_accuracy or
                     c_p95<=b_p95*(1-self._policy.required_gain) and b_p95>0 or
                     c_cost<=b_cost*(1-self._policy.required_gain) and b_cost>0)
        passed=bool(correct_ok and latency_ok and cost_ok and improvement)
        sample_digest=hashlib.sha256(_canonical([
            self._revision,[(x.name,dict(x.parameters)) for x in (baseline,candidate)],
            [(fixture.id,fixture.payload) for fixture in self._cases],
            [[(x.correct,x.latency_ms,x.cost_micro_usd) for x in group] for group in records],
            passed])).hexdigest()
        decision=EvolutionDecision(candidate,baseline,passed,c_accuracy,b_accuracy,c_p95,b_p95,
                                   c_cost,b_cost,sample_digest,self._revision)
        self._pending=decision
        return decision

    def promote(self, decision: EvolutionDecision, *, operator_approved: bool,
                expected_revision: int) -> EvolutionSnapshot:
        if (type(operator_approved) is not bool or not operator_approved
                or type(expected_revision) is not int or expected_revision!=self._revision
                or decision is not self._pending or not decision.passed
                or decision.revision!=self._revision or decision.baseline!=self._active):
            raise EvolutionDenied('approval, revision, or evaluation evidence rejected')
        self._previous=self._active
        self._active=decision.candidate
        self._active_decision=decision
        self._pending=None
        self._revision+=1
        return self.snapshot()

    def record_canary(self, observations: Sequence[EvolutionObservation], *,
                      operator_approved: bool, expected_revision: int) -> EvolutionSnapshot:
        d=self._active_decision
        if (not operator_approved or type(operator_approved) is not bool
                or type(expected_revision) is not int or self._revision!=expected_revision
                or d is None or self._previous!=d.baseline or not 3<=len(observations)<=self._policy.max_cases
                or any(not isinstance(x,EvolutionObservation) for x in observations)):
            raise EvolutionDenied('invalid canary approval or measurement')
        observed=sum(x.correct for x in observations)/len(observations)
        if (observed<d.baseline_accuracy or
                _p95(observations)>d.baseline_p95_ms*(1+self._policy.max_latency_regression)):
            self._active=self._previous
            self._previous=None
            self._active_decision=None
            self._revision+=1
        return self.snapshot()
