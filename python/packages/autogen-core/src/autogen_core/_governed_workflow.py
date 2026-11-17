"""Bounded, opt-in orchestration of trusted asynchronous handlers.

The executor is an application-level guardrail, not a process sandbox.
Handlers must respect cancellation and implement their own authorization.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

Handler = Callable[[Mapping[str, Any]], Awaitable[Any]]
_GENESIS = '0' * 64


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _positive_int(value: int) -> bool:
    return type(value) is int and value > 0


def _positive_number(value: float) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


class RetryableError(Exception):
    """Handler explicitly signals a transient failure safe for idempotent retry."""


@dataclass(frozen=True)
class WorkflowStep:
    id: str
    handler: str
    dependencies: tuple[str, ...] = ()
    timeout: float = 30.0
    attempts: int = 1
    idempotent: bool = False


@dataclass(frozen=True)
class WorkflowPolicy:
    allowed_handlers: frozenset[str]
    max_steps: int = 128
    max_parallel: int = 4
    max_attempts: int = 256
    max_result_bytes: int = 65536
    max_step_timeout: float = 120.0
    deadline: float = 300.0
    retry_base: float = 0.1
    retry_cap: float = 2.0

    def __post_init__(self) -> None:
        if not isinstance(self.allowed_handlers, frozenset) or not all(type(n) is str and n for n in self.allowed_handlers):
            raise ValueError('allowed_handlers must be a frozenset of nonempty names')
        for value in (self.max_steps, self.max_parallel, self.max_attempts, self.max_result_bytes):
            if not _positive_int(value):
                raise ValueError('resource limits must be positive integers')
        for value in (self.max_step_timeout, self.deadline, self.retry_cap):
            if not _positive_number(value):
                raise ValueError('timeouts must be finite and positive')
        if type(self.retry_base) not in (int, float) or not math.isfinite(self.retry_base) or not 0 <= self.retry_base <= self.retry_cap:
            raise ValueError('invalid retry backoff')


@dataclass(frozen=True)
class AuditRecord:
    sequence: int
    step: str
    status: str
    attempts: int
    previous: str
    digest: str


def _audit_digest(sequence: int, step: str, status: str, attempts: int, previous: str) -> str:
    return hashlib.sha256(_canonical([sequence, step, status, attempts, previous])).hexdigest()


def verify_audit(records: Sequence[AuditRecord], *, expected_head: str | None = None) -> bool:
    """Check event order and digest linkage; pin a trusted head to detect truncation/rewrite."""
    previous = _GENESIS
    for sequence, record in enumerate(records):
        if record.sequence != sequence or record.previous != previous:
            return False
        if record.digest != _audit_digest(sequence, record.step, record.status, record.attempts, previous):
            return False
        previous = record.digest
    return expected_head is None or previous == expected_head


@dataclass(frozen=True)
class WorkflowResult:
    outputs: dict[str, Any]
    statuses: dict[str, str]
    audit: tuple[AuditRecord, ...]

    @property
    def audit_head(self) -> str:
        return self.audit[-1].digest if self.audit else _GENESIS

    def audit_json(self) -> str:
        """Export metadata only, without handler inputs, outputs or error messages."""
        return _canonical([asdict(record) for record in self.audit]).decode('utf-8')


class WorkflowExecutor:
    """Validate a finite DAG before dispatch; execute ready nodes in sorted bounded batches."""

    def __init__(self, handlers: Mapping[str, Handler], policy: WorkflowPolicy) -> None:
        self._handlers = dict(handlers)
        self._policy = policy
        if any(not callable(handler) for handler in self._handlers.values()):
            raise ValueError('handlers must be callable')

    def _validate(self, steps: Sequence[WorkflowStep]) -> dict[str, WorkflowStep]:
        p = self._policy
        if not steps or len(steps) > p.max_steps:
            raise ValueError('workflow size outside policy')
        graph: dict[str, WorkflowStep] = {}
        for step in steps:
            if type(step.id) is not str or not 0 < len(step.id) <= 128 or step.id in graph:
                raise ValueError('invalid or duplicate step id')
            if type(step.handler) is not str or step.handler not in p.allowed_handlers or step.handler not in self._handlers:
                raise ValueError('handler not permitted or registered')
            if type(step.dependencies) is not tuple or any(type(dep) is not str for dep in step.dependencies) or len(set(step.dependencies)) != len(step.dependencies):
                raise ValueError('invalid dependencies')
            if not _positive_int(step.attempts) or type(step.idempotent) is not bool or (step.attempts > 1 and not step.idempotent):
                raise ValueError('retry requires explicit idempotency')
            if not _positive_number(step.timeout) or step.timeout > p.max_step_timeout:
                raise ValueError('step timeout outside policy')
            graph[step.id] = step
        if sum(step.attempts for step in graph.values()) > p.max_attempts:
            raise ValueError('worst-case attempt budget exceeded')
        remaining = set(graph)
        resolved: set[str] = set()
        while remaining:
            ready = {key for key in remaining if set(graph[key].dependencies) <= resolved}
            if not ready:
                raise ValueError('cycle or unknown dependency')
            remaining -= ready
            resolved |= ready
        return graph

    async def _execute(self, step: WorkflowStep, inputs: dict[str, Any], deadline: float) -> tuple[str, int, Any]:
        loop = asyncio.get_running_loop()
        delay = self._policy.retry_base
        for attempt in range(1, step.attempts + 1):
            remaining = deadline - loop.time()
            if remaining <= 0:
                return 'timed_out', attempt - 1, None
            try:
                # JSON round trip prevents one handler mutating another's results.
                isolated = json.loads(_canonical(inputs))
                attempt_deadline = min(deadline, loop.time() + step.timeout)
                value = await asyncio.wait_for(self._handlers[step.handler](isolated), min(step.timeout, remaining))
                if loop.time() > attempt_deadline:
                    return 'timed_out', attempt, None
                data = _canonical(value)
                if len(data) > self._policy.max_result_bytes:
                    return 'failed', attempt, None
                return 'succeeded', attempt, json.loads(data)
            except RetryableError:
                if attempt == step.attempts:
                    return 'failed', attempt, None
                if delay >= deadline - loop.time():
                    return 'timed_out', attempt, None
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._policy.retry_cap)
            except asyncio.TimeoutError:
                # Timeout does not prove absence of external side effects; never retry it automatically.
                return 'timed_out', attempt, None
            except Exception:
                return 'failed', attempt, None
        raise AssertionError('validated attempt count exhausted')

    async def run(self, steps: Sequence[WorkflowStep]) -> WorkflowResult:
        graph = self._validate(steps)
        deadline = asyncio.get_running_loop().time() + self._policy.deadline
        outputs: dict[str, Any] = {}
        statuses: dict[str, str] = {}
        audit: list[AuditRecord] = []
        remaining = set(graph)

        def record(key: str, status: str, attempts: int, output: Any = None) -> None:
            statuses[key] = status
            if status == 'succeeded':
                outputs[key] = output
            previous = audit[-1].digest if audit else _GENESIS
            seq = len(audit)
            audit.append(AuditRecord(seq, key, status, attempts, previous, _audit_digest(seq, key, status, attempts, previous)))
            remaining.remove(key)

        while remaining:
            ready = sorted(key for key in remaining if all(dep in statuses for dep in graph[key].dependencies))
            batch = ready[:self._policy.max_parallel]
            runnable: list[str] = []
            completed: dict[str, tuple[str, int, Any]] = {}
            for key in batch:
                if any(statuses[dep] != 'succeeded' for dep in graph[key].dependencies):
                    completed[key] = ('skipped', 0, None)
                elif asyncio.get_running_loop().time() >= deadline:
                    completed[key] = ('timed_out', 0, None)
                else:
                    runnable.append(key)
            results = await asyncio.gather(*(self._execute(graph[key], {dep: outputs[dep] for dep in graph[key].dependencies}, deadline) for key in runnable))
            completed.update(zip(runnable, results))
            for key in batch:
                record(key, *completed[key])
        return WorkflowResult(outputs, statuses, tuple(audit))
