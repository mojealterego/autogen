"""Single-host, crash-recoverable governed workflows backed by SQLite.

The database and its lock directory must be on a trusted local filesystem.
Idempotency is a handler contract, not an exactly-once guarantee.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

from ._governed_workflow import Handler, RetryableError, WorkflowExecutor, WorkflowPolicy, WorkflowStep, _canonical


class RunBusyError(RuntimeError):
    """Another executor holds the local run lock."""


class CheckpointConflictError(RuntimeError):
    """A run identifier is already bound to another workflow specification."""


class CheckpointIntegrityError(RuntimeError):
    """The persisted journal or pinned head failed verification."""


class OutcomeUnknownError(RuntimeError):
    """Remote side effects may have occurred; do not mark failed or retry."""


@dataclass(frozen=True)
class AttemptContext:
    tenant_id: str
    run_id: str
    step_id: str
    attempt: int
    idempotency_key: str


_attempt: ContextVar[AttemptContext | None] = ContextVar('omega_attempt', default=None)


def current_attempt() -> AttemptContext | None:
    """Return the current handler's stable operation key and attempt number."""
    return _attempt.get()


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class DurableWorkflowResult:
    outputs: dict[str, Any]
    statuses: dict[str, str]
    attempts: dict[str, int]
    journal_head: str


@dataclass(frozen=True)
class RemoteReconciliation:
    """A trusted, read-only authority's observation of a durable attempt.

    `operation_id` is the durable attempt's stable key, not a new action ID.
    Readers are trusted application bindings, never tool definitions from an LLM.
    """
    tenant_id: str
    operation_id: str
    status: str
    output: Any = None


RemoteOutcomeReader = Callable[[AttemptContext], Awaitable[RemoteReconciliation | None]]


@dataclass(frozen=True)
class _Event:
    step: str
    status: str
    attempt: int
    output: Any = None


_TERMINAL = frozenset({'succeeded', 'failed', 'timed_out', 'skipped', 'uncertain'})


class _Journal:
    def __init__(self, db: sqlite3.Connection, key: str, fingerprint: str, duration: float, graph: Mapping[str, WorkflowStep], max_result_bytes: int, expected_head: str | None) -> None:
        self.db, self.key, self.graph = db, key, graph
        self.max_result_bytes = max_result_bytes
        row = db.execute('SELECT fingerprint, expires, seq, head FROM omega_runs WHERE run_key=?', (key,)).fetchone()
        if row is None:
            if expected_head is not None:
                raise CheckpointIntegrityError('pinned journal missing')
            self.expires = time.time() + duration
            stored_seq, stored_head = 0, _hash(['omega-journal-v1', key, fingerprint, self.expires])
            with db:
                db.execute('INSERT INTO omega_runs VALUES (?,?,?,?,?)', (key, fingerprint, self.expires, stored_seq, stored_head))
        else:
            if row[0] != fingerprint:
                raise CheckpointConflictError('run specification or revision changed')
            self.expires, stored_seq, stored_head = row[1:]
        if type(self.expires) not in (int, float) or not math.isfinite(self.expires):
            raise CheckpointIntegrityError('invalid persisted deadline')
        self.head = _hash(['omega-journal-v1', key, fingerprint, self.expires])
        self.sequence = 0
        self.statuses: dict[str, str] = {}
        self.attempts: dict[str, int] = {}
        self.outputs: dict[str, Any] = {}
        for seq, step, status, attempt, payload, previous, digest in db.execute(
            'SELECT seq,step,status,attempt,output_json,previous,digest FROM omega_events WHERE run_key=? ORDER BY seq', (key,)
        ):
            try:
                value = json.loads(payload) if payload is not None else None
                event = _Event(step, status, attempt, value)
                self._validate(event)
                if payload != self._payload(event) or seq != self.sequence or previous != self.head or digest != self._digest(event):
                    raise CheckpointIntegrityError('invalid journal linkage or payload')
                self._apply(event, digest)
            except (TypeError, ValueError, OverflowError, KeyError) as exc:
                raise CheckpointIntegrityError('malformed journal event') from exc
        if stored_seq != self.sequence or stored_head != self.head:
            raise CheckpointIntegrityError('journal does not match its committed cursor')
        if expected_head is not None and expected_head != self.head:
            raise CheckpointIntegrityError('journal head differs from pinned head')

    def _payload(self, event: _Event) -> str | None:
        return _canonical(event.output).decode('utf-8') if event.status == 'succeeded' else None

    def _digest(self, event: _Event) -> str:
        return _hash([self.key, self.sequence, event.step, event.status, event.attempt, self._payload(event), self.head])

    def _validate(self, event: _Event) -> None:
        step = self.graph.get(event.step)
        before = self.statuses.get(event.step, 'pending')
        attempts = self.attempts.get(event.step, 0)
        if step is None or type(event.attempt) is not int or not 0 <= event.attempt <= step.attempts:
            raise CheckpointIntegrityError('invalid step state or attempt')
        dependencies = [self.statuses.get(d, 'pending') for d in step.dependencies]
        ready = all(s == 'succeeded' for s in dependencies)
        if event.status == 'running':
            valid = before in ('pending', 'retry_pending') and event.attempt == attempts + 1 and ready
        elif event.status == 'retry_pending':
            valid = before == 'running' and event.attempt == attempts and step.idempotent and attempts < step.attempts
        elif event.status in ('succeeded', 'failed', 'uncertain'):
            valid = (before == 'running' and event.attempt == attempts or
                     before == 'uncertain' and event.status in ('succeeded', 'failed') and event.attempt == attempts)
        elif event.status == 'timed_out':
            valid = before in ('pending', 'retry_pending', 'running') and event.attempt == attempts
        elif event.status == 'skipped':
            valid = before == 'pending' and event.attempt == 0 and all(s in _TERMINAL for s in dependencies) and not ready
        elif event.status == 'reopened':
            # A skipped descendant never ran. A newly confirmed remote outcome
            # may unblock it, but failed/uncertain dependencies must still veto.
            valid = (before == 'skipped' and event.attempt == 0 and bool(dependencies)
                     and all(s in ('pending', 'succeeded') for s in dependencies))
        else:
            valid = False
        if not valid:
            raise CheckpointIntegrityError('illegal workflow transition')
        if event.status == 'succeeded' and len(_canonical(event.output)) > self.max_result_bytes:
            raise CheckpointIntegrityError('oversized stored result')
        if event.status != 'succeeded' and event.output is not None:
            raise CheckpointIntegrityError('unexpected output')

    def _apply(self, event: _Event, digest: str) -> None:
        if event.status == 'reopened':
            self.statuses.pop(event.step, None)
            self.attempts.pop(event.step, None)
        else:
            self.statuses[event.step] = event.status
            self.attempts[event.step] = event.attempt
        if event.status == 'succeeded':
            self.outputs[event.step] = event.output
        self.sequence += 1
        self.head = digest

    def append(self, event: _Event) -> None:
        self._validate(event)
        digest = self._digest(event)
        with self.db:
            updated = self.db.execute('UPDATE omega_runs SET seq=?,head=? WHERE run_key=? AND seq=? AND head=?', (self.sequence + 1, digest, self.key, self.sequence, self.head))
            if updated.rowcount != 1:
                raise CheckpointIntegrityError('checkpoint cursor changed during execution')
            self.db.execute('INSERT INTO omega_events VALUES (?,?,?,?,?,?,?,?)', (self.key, self.sequence, event.step, event.status, event.attempt, self._payload(event), self.head, digest))
        self._apply(event, digest)


class DurableWorkflowExecutor:
    """Execute trusted async handlers with an intent/outcome journal and recovery.

    `workflow_revision` must change with handler code or semantics. Caller must
    authenticate tenant_id. Distinct run IDs represent distinct operations.
    """

    def __init__(self, database: str | Path, handlers: Mapping[str, Handler], policy: WorkflowPolicy) -> None:
        self._path = Path(database).resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self._locks = self._path.with_name(self._path.name + '.locks')
        self._locks.mkdir(mode=0o700, exist_ok=True)
        self._handlers = dict(handlers)
        self._policy = policy
        self._validator = WorkflowExecutor(handlers, policy)
        with closing(self._connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS omega_runs (run_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, expires REAL NOT NULL, seq INTEGER NOT NULL, head TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS omega_events (run_key TEXT NOT NULL REFERENCES omega_runs(run_key), seq INTEGER NOT NULL, step TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL, output_json TEXT, previous TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(run_key,seq))')

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=5)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA foreign_keys=ON')
        except BaseException:
            db.close()
            raise
        return db

    @contextmanager
    def _lock(self, key: str) -> Iterator[None]:
        # Separate SQLite lock databases avoid holding the checkpoint write lock
        # during handler execution. Never delete lock files while workers exist.
        with closing(sqlite3.connect(self._locks / (key + '.sqlite'), timeout=0)) as lock:
            try:
                lock.execute('BEGIN EXCLUSIVE')
            except sqlite3.OperationalError as exc:
                if getattr(exc, 'sqlite_errorcode', None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or 'locked' in str(exc).lower():
                    raise RunBusyError('run is already active') from exc
                raise
            try:
                yield
            finally:
                lock.rollback()

    async def _step(self, journal: _Journal, step: WorkflowStep, tenant: str, run: str, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        while True:
            count = journal.attempts.get(step.id, 0)
            remaining = deadline - loop.time()
            if remaining <= 0:
                journal.append(_Event(step.id, 'timed_out', count))
                return
            if journal.statuses.get(step.id) == 'retry_pending':
                try:
                    delay = min(math.ldexp(self._policy.retry_base, min(count - 1, 4096)), self._policy.retry_cap)
                except OverflowError:
                    delay = self._policy.retry_cap
                if delay >= remaining:
                    journal.append(_Event(step.id, 'timed_out', count))
                    return
                await asyncio.sleep(delay)
                remaining = deadline - loop.time()
                if remaining <= 0:
                    journal.append(_Event(step.id, 'timed_out', count))
                    return
            attempt = count + 1
            inputs = json.loads(_canonical({d: journal.outputs[d] for d in step.dependencies}))
            journal.append(_Event(step.id, 'running', attempt))
            token = _attempt.set(AttemptContext(tenant, run, step.id, attempt, _hash(['omega-operation-v1', journal.key, step.id])))
            try:
                limit = min(step.timeout, deadline - loop.time())
                if limit <= 0:
                    event = _Event(step.id, 'timed_out', attempt)
                else:
                    until = loop.time() + limit
                    value = await asyncio.wait_for(self._handlers[step.handler](inputs), limit)
                    if loop.time() > until:
                        event = _Event(step.id, 'timed_out', attempt)
                    else:
                        encoded = _canonical(value)
                        event = _Event(step.id, 'succeeded', attempt, json.loads(encoded)) if len(encoded) <= self._policy.max_result_bytes else _Event(step.id, 'failed', attempt)
            except OutcomeUnknownError:
                event = _Event(step.id, 'uncertain', attempt)
            except RetryableError:
                event = _Event(step.id, 'retry_pending' if step.idempotent and attempt < step.attempts else 'failed', attempt)
            except asyncio.TimeoutError:
                event = _Event(step.id, 'timed_out', attempt)
            except Exception:
                event = _Event(step.id, 'failed', attempt)
            finally:
                _attempt.reset(token)
            journal.append(event)
            if event.status != 'retry_pending':
                return

    async def reconcile(
        self, steps: Sequence[WorkflowStep], *, tenant_id: str, run_id: str,
        workflow_revision: str, readers: Mapping[str, RemoteOutcomeReader],
        expected_head: str, read_timeout: float = 10.0,
    ) -> DurableWorkflowResult:
        """Read late remote outcomes and advance the journal without dispatching work.

        `expected_head` MUST be independently pinned by the trusted caller.
        A configured reader is bound to a registered handler and must validate
        the remote tenant and operation identity. Reads are non-mutating. Only
        independently confirmed terminal states can change an uncertain step.
        Skipped descendants are reopened *without running them* so a subsequent
        explicit `run(..., expected_head=new_head)` can resume the DAG.
        Original run deadline is never extended; a late result can be recorded
        after expiry but new dependent work may then time out.
        """
        for name in (tenant_id, run_id, workflow_revision):
            if type(name) is not str or not 0 < len(name) <= 256:
                raise ValueError('identifiers must be nonempty strings of up to 256 characters')
        if (type(expected_head) is not str or len(expected_head) != 64 or
                any(c not in '0123456789abcdef' for c in expected_head)):
            raise ValueError('reconciliation requires externally pinned SHA-256 journal head')
        if (type(read_timeout) not in (int, float) or not math.isfinite(read_timeout)
                or not .001 <= read_timeout <= 60):
            raise ValueError('invalid reconciliation read timeout')
        if not isinstance(readers, Mapping) or any(
            type(k) is not str or not callable(v) for k, v in readers.items()
        ):
            raise ValueError('readers must be trusted callable handler bindings')
        graph = self._validator._validate(steps)
        if any(k not in self._handlers for k in readers):
            raise ValueError('unknown remote reconciliation reader')
        policy = asdict(self._policy)
        policy['allowed_handlers'] = sorted(self._policy.allowed_handlers)
        fingerprint = _hash([workflow_revision, policy, [asdict(graph[k]) for k in sorted(graph)]])
        key = _hash([tenant_id, run_id])
        with self._lock(key), closing(self._connect()) as db:
            journal = _Journal(db, key, fingerprint, self._policy.deadline,
                               graph, self._policy.max_result_bytes, expected_head)
            # Stage reads and validate all outcomes *before* persisting any.
            pending: list[_Event] = []
            for step_id in sorted(graph):
                if journal.statuses.get(step_id) != 'uncertain':
                    continue
                reader = readers.get(graph[step_id].handler)
                if reader is None:
                    continue
                operation_id = _hash(['omega-operation-v1', key, step_id])
                ctx = AttemptContext(tenant_id, run_id, step_id,
                                     journal.attempts[step_id], operation_id)
                try:
                    result = await asyncio.wait_for(reader(ctx), read_timeout)
                except asyncio.TimeoutError:
                    # No late status is not proof of a failed remote job.
                    continue
                if result is None:
                    continue
                if (not isinstance(result, RemoteReconciliation)
                        or result.tenant_id != tenant_id
                        or result.operation_id != operation_id
                        or result.status not in ('succeeded', 'failed')):
                    raise CheckpointIntegrityError('untrusted remote outcome identity or state')
                if result.status == 'failed' and result.output is not None:
                    raise CheckpointIntegrityError('failed remote outcome cannot carry output')
                if result.status == 'succeeded':
                    try:
                        encoded = _canonical(result.output)
                        if len(encoded) > self._policy.max_result_bytes:
                            raise CheckpointIntegrityError('late result exceeds output policy')
                        output = json.loads(encoded)
                    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
                        raise CheckpointIntegrityError('invalid late result serialization') from exc
                else:
                    output = None
                pending.append(_Event(step_id, result.status,
                                      journal.attempts[step_id], output))
            for event in pending:
                journal.append(event)
            if any(event.status == 'succeeded' for event in pending):
                # Fixpoint across the DAG: descendants of an originally
                # skipped descendant may also become actionable.
                while True:
                    eligible = [
                        k for k in sorted(graph)
                        if journal.statuses.get(k) == 'skipped'
                        and graph[k].dependencies
                        and all(journal.statuses.get(d, 'pending') in ('pending', 'succeeded')
                                for d in graph[k].dependencies)
                    ]
                    if not eligible:
                        break
                    for k in eligible:
                        journal.append(_Event(k, 'reopened', 0))
            return DurableWorkflowResult(dict(journal.outputs), dict(journal.statuses),
                                         dict(journal.attempts), journal.head)

    async def run(self, steps: Sequence[WorkflowStep], *, tenant_id: str, run_id: str, workflow_revision: str, expected_head: str | None = None) -> DurableWorkflowResult:
        for name in (tenant_id, run_id, workflow_revision):
            if type(name) is not str or not 0 < len(name) <= 256:
                raise ValueError('identifiers must be nonempty strings of up to 256 characters')
        graph = self._validator._validate(steps)
        policy = asdict(self._policy)
        policy['allowed_handlers'] = sorted(self._policy.allowed_handlers)
        fingerprint = _hash([workflow_revision, policy, [asdict(graph[k]) for k in sorted(graph)]])
        key = _hash([tenant_id, run_id])
        with self._lock(key), closing(self._connect()) as db:
            journal = _Journal(db, key, fingerprint, self._policy.deadline, graph, self._policy.max_result_bytes, expected_head)
            deadline = asyncio.get_running_loop().time() + max(0, min(self._policy.deadline, journal.expires - time.time()))
            for k in sorted(graph):
                if journal.statuses.get(k) == 'running':
                    step = graph[k]
                    count = journal.attempts[k]
                    # A crash after intent may have produced an external effect.
                    journal.append(_Event(k, 'retry_pending' if step.idempotent and count < step.attempts else 'uncertain', count))
            while True:
                remaining = [k for k in sorted(graph) if journal.statuses.get(k) not in _TERMINAL]
                if not remaining:
                    break
                ready = [k for k in remaining if all(journal.statuses.get(d) in _TERMINAL for d in graph[k].dependencies)]
                if not ready:
                    raise CheckpointIntegrityError('journal cannot progress')
                tasks: list[asyncio.Task[None]] = []
                try:
                    for k in ready[:self._policy.max_parallel]:
                        if any(journal.statuses[d] != 'succeeded' for d in graph[k].dependencies):
                            journal.append(_Event(k, 'skipped', 0))
                        else:
                            tasks.append(asyncio.create_task(self._step(journal, graph[k], tenant_id, run_id, deadline)))
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    if tasks:
                        cleanup = asyncio.gather(*tasks, return_exceptions=True)
                        interrupted = False
                        # Repeated caller cancellation must not release the local lock
                        # while cooperative handler tasks are still unwinding.
                        while not cleanup.done():
                            try:
                                await asyncio.shield(cleanup)
                            except asyncio.CancelledError:
                                interrupted = True
                        if interrupted:
                            raise asyncio.CancelledError
            return DurableWorkflowResult(dict(journal.outputs), dict(journal.statuses), dict(journal.attempts), journal.head)
