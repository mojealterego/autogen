"""Bounded hierarchical delegation of trusted local agent handlers.

Agent identity, capabilities, approvals and DAG are application-supplied.
Model output can never register new capabilities, handlers or approvals.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ._governed_workflow import WorkflowExecutor, WorkflowPolicy, WorkflowResult, WorkflowStep, _canonical


class SwarmDenied(ValueError):
    """A tenant, hierarchy, capability or verification rule failed closed."""


AgentRole = Literal['supervisor', 'worker', 'verifier']
SwarmHandler = Callable[[Mapping[str, Any]], Awaitable[Any]]


def _id(name: Any) -> bool:
    return type(name) is str and 0 < len(name) <= 128 and all(ord(c) >= 32 for c in name)


@dataclass(frozen=True)
class SwarmAgent:
    name: str
    tenant: str
    role: AgentRole
    parent: str | None
    capabilities: frozenset[str]
    handler: SwarmHandler = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (not _id(self.name) or not _id(self.tenant) or self.role not in ('supervisor','worker','verifier')
                or (self.parent is not None and not _id(self.parent))
                or not isinstance(self.capabilities, frozenset) or not self.capabilities
                or not all(_id(x) for x in self.capabilities) or not callable(self.handler)):
            raise SwarmDenied('invalid agent registration')


@dataclass(frozen=True)
class SwarmNode:
    id: str
    agent: str
    capability: str
    payload: Mapping[str, Any]
    dependencies: tuple[str, ...] = ()
    verifies: str | None = None
    requires_verification: bool = False
    timeout: float = 30.0
    attempts: int = 1
    idempotent: bool = False

    def __post_init__(self) -> None:
        if not all(_id(v) for v in (self.id,self.agent,self.capability)) or not isinstance(self.payload,Mapping):
            raise SwarmDenied('invalid task registration')
        try:
            data=_canonical(dict(self.payload))
            if len(data)>65536: raise SwarmDenied('task input too large')
            object.__setattr__(self,'payload',json.loads(data))
        except (TypeError,ValueError,OverflowError,RecursionError) as exc:
            raise SwarmDenied('task input must be bounded JSON') from exc


@dataclass(frozen=True)
class SwarmPolicy:
    max_agents: int = 64
    max_nodes: int = 128
    max_parallel: int = 4
    deadline: float = 300.0
    protected_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if (any(type(x) is not int or not 1 <= x <= 1000 for x in (self.max_agents,self.max_nodes,self.max_parallel))
                or type(self.deadline) not in (int,float) or not 0 < self.deadline <= 3600
                or not isinstance(self.protected_capabilities,frozenset)
                or not all(_id(x) for x in self.protected_capabilities)):
            raise SwarmDenied('invalid swarm limits')


@dataclass(frozen=True)
class SwarmReport:
    accepted: bool
    verifications: dict[str, bool]
    result: WorkflowResult


class HierarchicalSwarm:
    """One trusted supervisor tree per tenant; execution is a validated finite DAG."""

    def __init__(self, agents: Sequence[SwarmAgent], policy: SwarmPolicy) -> None:
        if not isinstance(policy,SwarmPolicy) or not agents or len(agents)>policy.max_agents:
            raise SwarmDenied('swarm agent budget exceeded')
        self._policy=policy
        self._agents: dict[str,SwarmAgent]={}
        for agent in agents:
            if not isinstance(agent,SwarmAgent) or agent.name in self._agents:
                raise SwarmDenied('invalid or duplicate agent')
            self._agents[agent.name]=agent
        for agent in agents:
            seen=set()
            node=agent
            while node.parent is not None:
                if node.name in seen or node.parent not in self._agents:
                    raise SwarmDenied('agent hierarchy cycle or orphan')
                seen.add(node.name)
                parent=self._agents[node.parent]
                if parent.role!='supervisor' or parent.tenant!=agent.tenant:
                    raise SwarmDenied('cross-tenant or invalid supervision')
                node=parent
            if node.role!='supervisor':
                raise SwarmDenied('all agents must have a trusted supervisor root')
        tenants={a.tenant for a in agents}
        for tenant in tenants:
            if sum(a.parent is None for a in agents if a.tenant==tenant)!=1:
                raise SwarmDenied('exactly one root supervisor per tenant required')

    async def run(self, tenant: str, nodes: Sequence[SwarmNode], *,
                  approvals: frozenset[str] = frozenset()) -> SwarmReport:
        if (not _id(tenant) or not nodes or len(nodes)>self._policy.max_nodes
                or not isinstance(approvals,frozenset)):
            raise SwarmDenied('invalid or oversized swarm mission')
        mapped={}
        for node in nodes:
            if not isinstance(node,SwarmNode) or node.id in mapped:
                raise SwarmDenied('duplicate or invalid swarm node')
            agent=self._agents.get(node.agent)
            if (agent is None or agent.tenant!=tenant or node.capability not in agent.capabilities
                    or (node.capability in self._policy.protected_capabilities and node.id not in approvals)
                    or (node.verifies is not None and agent.role!='verifier')):
                raise SwarmDenied('tenant, role, capability or operator approval denied')
            mapped[node.id]=node
        if any(a not in mapped for a in approvals):
            raise SwarmDenied('approval for an unknown task')
        verified={}
        for node in nodes:
            if node.verifies is None: continue
            original=mapped.get(node.verifies)
            if (original is None or not original.requires_verification
                    or node.verifies not in node.dependencies
                    or node.agent==original.agent or node.verifies in verified):
                raise SwarmDenied('independent direct verifier required')
            verified[node.verifies]=node.id
        if any(node.requires_verification and node.id not in verified for node in nodes):
            raise SwarmDenied('required verifier missing')
        handlers={}
        steps=[]
        for node in nodes:
            async def invoke(inputs: Mapping[str,Any], task: SwarmNode=node) -> Any:
                # Serialize again to give a handler its own mutable copy.
                payload=json.loads(_canonical({'task':task.payload,'dependencies':inputs}))
                return await self._agents[task.agent].handler(payload)
            handlers[node.id]=invoke
            steps.append(WorkflowStep(node.id,node.id,node.dependencies,node.timeout,node.attempts,node.idempotent))
        p=WorkflowPolicy(frozenset(handlers),max_steps=self._policy.max_nodes,
                         max_parallel=self._policy.max_parallel,deadline=self._policy.deadline,
                         max_attempts=self._policy.max_nodes*32)
        executor=WorkflowExecutor(handlers,p)
        # Preflight DAG validation and full role+approval policy before any execution.
        try:
            executor._validate(steps)
        except ValueError as exc:
            raise SwarmDenied('invalid swarm mission DAG') from exc
        result=await executor.run(steps)
        verdict={target:result.statuses.get(reviewer)=='succeeded'
                 and isinstance(result.outputs.get(reviewer),dict)
                 and result.outputs[reviewer].get('approved') is True
                 for target,reviewer in verified.items()}
        accepted=all(x=='succeeded' for x in result.statuses.values()) and all(verdict.values())
        return SwarmReport(accepted,verdict,result)
