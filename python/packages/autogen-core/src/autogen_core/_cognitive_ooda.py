"""Opt-in, bounded OODA substrate: verified episodic data, declarative simulation, HITL.

This is NOT Titans training, R3Mem virtual tokens, CWM inference, LTN theorem
proving, a hardware sandbox or an autonomous privilege escalation mechanism.
The supplied actuator is trusted application code; model output is always data.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
import zlib
from contextlib import closing
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ._governed_workflow import _canonical
from ._neurosymbolic_gate import SymbolicGate, SymbolicDenied

_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_MAX_DOCUMENT = 65536
_MAX_VALUE = 10**9


class MemoryConflict(ValueError):
    """Event collision or declared per-tenant capacity exceeded."""


class MemoryCorrupt(RuntimeError):
    """Persisted event failed decompression or integrity verification."""


class WorldPolicyDenied(ValueError):
    """Proposed declarative transition violates a deterministic invariant."""


class ApprovalDenied(PermissionError):
    """Ticket is not authorized, expired, used, or bound to a different plan."""


class CognitiveDenied(RuntimeError):
    """Plan or actuator violates a precondition, or its outcome is uncertain."""


def _identifier(value: Any) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError('expected JSON object')
    try:
        encoded = _canonical(dict(value))
        if len(encoded) > _MAX_DOCUMENT:
            raise ValueError('JSON input too large')
        decoded = json.loads(encoded)
        if type(decoded) is not dict:
            raise ValueError('expected JSON object')
        return decoded
    except (TypeError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ValueError('invalid JSON input') from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _connection(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=3, isolation_level=None)
    db.execute('PRAGMA busy_timeout=3000')
    return db


@dataclass(frozen=True)
class CognitiveEvent:
    tenant: str
    event_id: str
    kind: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not all(_identifier(x) for x in (self.tenant, self.event_id, self.kind)):
            raise ValueError('invalid event identity')
        object.__setattr__(self, 'payload', _json_object(self.payload))


@dataclass(frozen=True)
class MemoryReceipt:
    digest: str
    novelty_proxy: float
    occurrences: int


class EpisodicStore:
    """Local SQLite WAL data store with exact canonical-JSON roundtrip.

    Surprise is intentionally only a frequency proxy. Zlib is lossless storage
    compression, NOT learned R3Mem; reads verify SHA256 and bounded expansion.
    Tenant isolation relies on the trusted application-supplied tenant ID.
    """

    def __init__(self, path: str | Path, *, max_per_tenant: int = 1024) -> None:
        if type(max_per_tenant) is not int or not 1 <= max_per_tenant <= 100000:
            raise ValueError('invalid tenant capacity')
        self._path = Path(path)
        self._limit = max_per_tenant
        with closing(_connection(self._path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS cognitive_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL,
                payload BLOB NOT NULL, digest TEXT NOT NULL,
                occurrences INTEGER NOT NULL,
                UNIQUE(tenant, event_id))''')
            db.execute('CREATE INDEX IF NOT EXISTS idx_cognitive_tenant ON cognitive_events(tenant, seq)')

    @staticmethod
    def _encoded(event: CognitiveEvent) -> bytes:
        return _canonical([event.tenant, event.event_id, event.kind, dict(event.payload)])

    @staticmethod
    def _decode(blob: bytes, digest: str) -> CognitiveEvent:
        try:
            z = zlib.decompressobj()
            decoded = z.decompress(blob, _MAX_DOCUMENT + 1024)
            if not z.eof or z.unconsumed_tail or z.unused_data or len(decoded) > _MAX_DOCUMENT + 512:
                raise MemoryCorrupt('oversized or invalid memory record')
            if hashlib.sha256(decoded).hexdigest() != digest:
                raise MemoryCorrupt('memory digest mismatch')
            values = json.loads(decoded)
            if type(values) is not list or len(values) != 4:
                raise MemoryCorrupt('invalid memory envelope')
            event = CognitiveEvent(*values)
            if EpisodicStore._encoded(event) != decoded:
                raise MemoryCorrupt('noncanonical memory data')
            return event
        except (ValueError, TypeError, zlib.error, RecursionError, UnicodeError) as exc:
            raise MemoryCorrupt('memory record not trustworthy') from exc

    def record(self, event: CognitiveEvent) -> MemoryReceipt:
        if not isinstance(event, CognitiveEvent):
            raise ValueError('expected validated CognitiveEvent')
        data = self._encoded(event)
        digest = hashlib.sha256(data).hexdigest()
        with closing(_connection(self._path)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                existing = db.execute('''SELECT payload,digest,occurrences FROM cognitive_events
                                             WHERE tenant=? AND event_id=?''', (event.tenant, event.event_id)).fetchone()
                if existing:
                    other = self._decode(existing[0], existing[1])
                    if existing[1] != digest or other != event:
                        raise MemoryConflict('event identity already bound to another payload')
                    receipt = MemoryReceipt(digest, 1.0 / existing[2], existing[2])
                else:
                    count = db.execute('SELECT COUNT(*) FROM cognitive_events WHERE tenant=?', (event.tenant,)).fetchone()[0]
                    if count >= self._limit:
                        raise MemoryConflict('tenant episode capacity exceeded')
                    prior = db.execute('SELECT COUNT(*) FROM cognitive_events WHERE tenant=? AND kind=?',
                                       (event.tenant, event.kind)).fetchone()[0]
                    occurrences = prior + 1
                    db.execute('''INSERT INTO cognitive_events(tenant,event_id,kind,payload,digest,occurrences)
                                  VALUES(?,?,?,?,?,?)''', (event.tenant, event.event_id, event.kind,
                                                          zlib.compress(data, level=6), digest, occurrences))
                    receipt = MemoryReceipt(digest, 1.0 / occurrences, occurrences)
                db.commit()
                return receipt
            except BaseException:
                db.rollback()
                raise

    def recall(self, tenant: str, event_id: str) -> CognitiveEvent | None:
        if not _identifier(tenant) or not _identifier(event_id):
            raise ValueError('invalid event key')
        with closing(_connection(self._path)) as db:
            row = db.execute('SELECT payload,digest FROM cognitive_events WHERE tenant=? AND event_id=?',
                             (tenant, event_id)).fetchone()
        if row is None:
            return None
        event = self._decode(*row)
        if event.tenant != tenant or event.event_id != event_id:
            raise MemoryCorrupt('memory identity mismatch')
        return event

    def recent(self, tenant: str, *, limit: int = 20) -> tuple[CognitiveEvent, ...]:
        if not _identifier(tenant) or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid bounded memory query')
        with closing(_connection(self._path)) as db:
            rows = db.execute('''SELECT payload,digest FROM cognitive_events WHERE tenant=?
                                ORDER BY seq DESC LIMIT ?''', (tenant, limit)).fetchall()
        out = tuple(self._decode(*r) for r in rows)
        if any(x.tenant != tenant for x in out):
            raise MemoryCorrupt('cross-tenant memory identity')
        return out


@dataclass(frozen=True)
class WorldAction:
    operation: Literal['read', 'set', 'increment']
    target: str
    value: int | None = None

    def __post_init__(self) -> None:
        if (self.operation not in ('read', 'set', 'increment') or not _identifier(self.target)
                or (self.operation == 'read' and self.value is not None)
                or (self.operation != 'read' and
                    (type(self.value) is not int or abs(self.value) > _MAX_VALUE))):
            raise ValueError('invalid or executable world action')


@dataclass(frozen=True)
class SimulationOutcome:
    final_state: Mapping[str, int]
    trace: tuple[tuple[str, str, int], ...]
    digest: str


class WorldModel:
    """Deterministic state-machine evaluator; NEVER runs supplied Python code."""

    def __init__(self, *, frozen_keys: frozenset[str] = frozenset(), max_actions: int = 32) -> None:
        if (not isinstance(frozen_keys, frozenset) or any(not _identifier(x) for x in frozen_keys)
                or type(max_actions) is not int or not 1 <= max_actions <= 128):
            raise ValueError('invalid immutable world specification')
        self._frozen = frozen_keys
        self._max_actions = max_actions

    def simulate(self, initial: Mapping[str, int], actions: Sequence[WorldAction]) -> SimulationOutcome:
        if (not isinstance(initial, Mapping) or not 1 <= len(initial) <= 128
                or any(not _identifier(k) or type(v) is not int or not 0 <= v <= _MAX_VALUE
                       for k, v in initial.items())
                or not isinstance(actions, (tuple, list)) or not 1 <= len(actions) <= self._max_actions
                or any(not isinstance(a, WorldAction) for a in actions)):
            raise WorldPolicyDenied('invalid bounded world state or action list')
        state = dict(initial)
        trace = []
        for action in actions:
            if action.target not in state or (action.operation != 'read' and action.target in self._frozen):
                raise WorldPolicyDenied('unknown or immutable world key')
            if action.operation == 'set':
                proposed = action.value
            elif action.operation == 'increment':
                proposed = state[action.target] + action.value
            else:
                proposed = state[action.target]
            if not 0 <= proposed <= _MAX_VALUE:
                raise WorldPolicyDenied('world invariant or resource limit exceeded')
            state[action.target] = proposed
            trace.append((action.operation, action.target, proposed))
        return SimulationOutcome(state, tuple(trace), _hash([dict(initial),
            [(a.operation, a.target, a.value) for a in actions], state, trace]))


class ApprovalLedger:
    """Durable local HITL transition gate with single-use plan-bound tickets."""

    def __init__(self, path: str | Path, approvers: Mapping[str, frozenset[str]], *,
                 lifetime_seconds: float = 3600, clock: Callable[[], float] = time.time) -> None:
        if (not isinstance(approvers, Mapping) or not approvers or
                any(not _identifier(k) or not isinstance(v, frozenset) or not v or
                    any(not _identifier(a) for a in v) for k,v in approvers.items()) or
                type(lifetime_seconds) not in (int,float) or not math.isfinite(lifetime_seconds) or
                not 0 < lifetime_seconds <= 86400 or not callable(clock)):
            raise ValueError('invalid trusted approver registry')
        self._path = Path(path)
        self._approvers = {k: frozenset(v) for k,v in approvers.items()}
        self._lifetime = lifetime_seconds
        self._clock = clock
        with closing(_connection(self._path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS cognitive_approvals (
                ticket TEXT PRIMARY KEY, tenant TEXT NOT NULL, plan_digest TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','approved','consumed')),
                expires REAL NOT NULL, approver TEXT,
                UNIQUE(tenant,operation_id))''')
            db.execute('CREATE TABLE IF NOT EXISTS cognitive_approval_clock (id INTEGER PRIMARY KEY CHECK(id=1), last_seen REAL NOT NULL)')

    def _now(self, db: sqlite3.Connection) -> float:
        current = self._clock()
        if type(current) not in (int,float) or not math.isfinite(current):
            raise ApprovalDenied('invalid trusted clock')
        db.execute('INSERT OR IGNORE INTO cognitive_approval_clock(id,last_seen) VALUES(1,?)', (current,))
        last = db.execute('SELECT last_seen FROM cognitive_approval_clock WHERE id=1').fetchone()[0]
        # Rollback cannot revive an expired ticket after a later timestamp was witnessed.
        observed = max(last, current)
        db.execute('UPDATE cognitive_approval_clock SET last_seen=? WHERE id=1', (observed,))
        return observed

    def stage(self, tenant: str, plan_digest: str, *, operation_id: str | None = None) -> str:
        if (tenant not in self._approvers or type(plan_digest) is not str or
                not re.fullmatch(r'[a-f0-9]{64}',plan_digest) or
                (operation_id is not None and not _identifier(operation_id))):
            raise ApprovalDenied('unauthorized ticket creation')
        ticket = secrets.token_hex(24)
        key = operation_id if operation_id is not None else ticket
        with closing(_connection(self._path)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                expires = self._now(db) + self._lifetime
                existing = db.execute('''SELECT ticket,plan_digest FROM cognitive_approvals
                                         WHERE tenant=? AND operation_id=?''',(tenant,key)).fetchone()
                if existing:
                    if existing[1] != plan_digest:
                        raise ApprovalDenied('operation already bound to a different plan')
                    db.commit()
                    return existing[0]
                db.execute('''INSERT INTO cognitive_approvals(ticket,tenant,plan_digest,operation_id,status,expires)
                              VALUES(?,?,?,?,'pending',?)''',(ticket,tenant,plan_digest,key,expires))
                db.commit()
                return ticket
            except BaseException:
                db.rollback()
                raise

    def _transition(self, ticket: str, tenant: str, from_status: str, to_status: str,
                    *, digest: str | None = None, actor: str | None = None) -> None:
        if (not _identifier(tenant) or type(ticket) is not str or not re.fullmatch(r'[a-f0-9]{48}',ticket)):
            raise ApprovalDenied('invalid approval ticket')
        with closing(_connection(self._path)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                now = self._now(db)
                row = db.execute('SELECT plan_digest,status,expires FROM cognitive_approvals WHERE ticket=? AND tenant=?',
                                 (ticket,tenant)).fetchone()
                if (row is None or row[1] != from_status or now >= row[2] or
                        (digest is not None and digest != row[0]) or
                        (actor is not None and actor not in self._approvers.get(tenant, frozenset()))):
                    db.commit()  # Persist max witnessed time even on denied transition.
                    raise ApprovalDenied('ticket absent, expired, mismatched or unauthorized')
                updated = db.execute('''UPDATE cognitive_approvals SET status=?,approver=COALESCE(?,approver)
                    WHERE ticket=? AND tenant=? AND status=?''',
                    (to_status,actor,ticket,tenant,from_status))
                if updated.rowcount != 1:
                    raise ApprovalDenied('stale approval transition')
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def approve(self, ticket: str, tenant: str, approver: str) -> None:
        if not _identifier(approver):
            raise ApprovalDenied('untrusted approver identity')
        self._transition(ticket,tenant,'pending','approved',actor=approver)

    def consume(self, ticket: str, tenant: str, plan_digest: str) -> bool:
        self._transition(ticket,tenant,'approved','consumed',digest=plan_digest)
        return True


@dataclass(frozen=True)
class CognitiveDecision:
    tenant: str
    event_digest: str
    initial_state: Mapping[str,int]
    final_state: Mapping[str,int]
    actions: tuple[WorldAction,...]
    simulation_digest: str
    digest: str
    novelty_proxy: float
    ticket: str | None
    status: Literal['simulated','awaiting_approval']


class CognitiveOODA:
    """Observe, orient, simulate, authorize, dispatch via a trusted callback.

    The callback is a trusted application capability, NOT arbitrary code from a
    model. A consumed ticket is never replayed: failure/timeout is UNKNOWN.
    """

    def __init__(self, memory: EpisodicStore, world: WorldModel,
                 approvals: ApprovalLedger, policy: SymbolicGate | None = None,
                 *, act_timeout: float = 30.0) -> None:
        if (not isinstance(memory,EpisodicStore) or not isinstance(world,WorldModel) or
                not isinstance(approvals,ApprovalLedger) or (policy is not None and not isinstance(policy,SymbolicGate)) or
                type(act_timeout) not in (int,float) or not math.isfinite(act_timeout) or
                not 0 < act_timeout <= 120):
            raise ValueError('invalid bounded OODA dependencies')
        self._memory,self._world,self._approvals,self._policy = memory,world,approvals,policy
        self._timeout=act_timeout

    @staticmethod
    def _decision_digest(decision: CognitiveDecision) -> str:
        return _hash([decision.tenant,decision.event_digest,dict(decision.initial_state),
                      [(a.operation,a.target,a.value) for a in decision.actions],
                      dict(decision.final_state),decision.simulation_digest])

    async def assess(self, event: CognitiveEvent, initial: Mapping[str,int],
                     actions: Sequence[WorldAction]) -> CognitiveDecision:
        if not isinstance(event,CognitiveEvent):
            raise CognitiveDenied('event must be prevalidated')
        # SQLite may block on another writer; move I/O off the asyncio event loop.
        receipt = await asyncio.to_thread(self._memory.record,event)
        outcome = self._world.simulate(initial,actions)
        mutations=sum(a.operation!='read' for a in actions)
        if self._policy is not None:
            try:
                self._policy.require({'tenant':event.tenant,'steps':len(actions),
                                      'mutations':mutations,'novelty_bp':round(receipt.novelty_proxy*10000)})
            except SymbolicDenied as exc:
                raise CognitiveDenied('symbolic preflight denied') from exc
        basis = CognitiveDecision(event.tenant,receipt.digest,dict(initial),dict(outcome.final_state),
                                  tuple(actions),outcome.digest,'',receipt.novelty_proxy,None,'simulated')
        digest=self._decision_digest(basis)
        ticket=await asyncio.to_thread(self._approvals.stage,event.tenant,digest,operation_id=event.event_id) if mutations else None
        return CognitiveDecision(event.tenant,receipt.digest,dict(initial),dict(outcome.final_state),
                                 tuple(actions),outcome.digest,digest,receipt.novelty_proxy,
                                 ticket,'awaiting_approval' if ticket else 'simulated')

    async def act(self, decision: CognitiveDecision,
                  handler: Callable[[tuple[WorldAction,...],Mapping[str,int]],Awaitable[Any]]) -> Any:
        if (not isinstance(decision,CognitiveDecision) or not callable(handler) or
                not decision.ticket or decision.status!='awaiting_approval' or
                self._decision_digest(decision)!=decision.digest or
                self._world.simulate(decision.initial_state,decision.actions).digest!=decision.simulation_digest or
                dict(self._world.simulate(decision.initial_state,decision.actions).final_state)!=dict(decision.final_state)):
            raise CognitiveDenied('action is unapproved or decision evidence was modified')
        # Consume before call. If a process crashes after consumption, it must
        # reconcile externally; blindly retrying could repeat real side effects.
        await asyncio.to_thread(self._approvals.consume,decision.ticket,decision.tenant,decision.digest)
        try:
            result = await asyncio.wait_for(handler(tuple(decision.actions),dict(decision.initial_state)),
                                            timeout=self._timeout)
            return _json_object(result)  # return isolated, non-executable JSON
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise CognitiveDenied('actuation outcome unknown; do not automatically retry') from exc
