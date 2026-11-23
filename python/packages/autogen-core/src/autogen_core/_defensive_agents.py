"""OMEGA 12: evidence-based, bounded defense specialists for a trusted AutoGen host.

No model can acquire new privileges, execute code, alter CDP tabs, forge approvals,
or autonomously attack external systems. CDP is local, inventory-only HTTP.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.client
import ipaddress
import json
import math
import re
import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ._cognitive_ooda import CognitiveEvent, CognitiveOODA, CognitiveDecision, WorldAction
from ._governed_workflow import _canonical

_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_HOST = re.compile(r'^(?=.{1,253}$)[a-z0-9.-]+$')
_DIGEST = re.compile(r'^[a-f0-9]{64}$')
_INJECTION = re.compile(
    r'\b(?:ignore|disregard)\s+(?:all\s+|the\s+)?(?:previous|prior|system)\s+instructions\b'
    r'|\b(?:override|reveal|leak)\s+(?:the\s+)?(?:system|developer)\s+(?:prompt|instructions)\b'
    r'|\[im_start\]\s*system|<\|im_start\|>\s*system', re.IGNORECASE,
)


def _valid_id(value: Any) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _origin(value: str) -> str | None:
    """Normalize an observed HTTP origin; this function NEVER opens the URL."""
    if type(value) is not str or not 1 <= len(value) <= 2048 or any(ord(c) < 33 for c in value):
        return None
    try:
        u = urlsplit(value)
        if u.scheme not in ('http', 'https') or not u.netloc or u.username is not None or u.password is not None:
            return None
        host = u.hostname
        port = u.port
        if not host or '%' in host or u.fragment:
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _HOST.fullmatch(host) or '..' in host or host.startswith('.') or host.endswith('.'):
                return None
        # Preserve nondefault ports, avoiding ambiguity between configured origins.
        standard = 443 if u.scheme == 'https' else 80
        canonical_host = f'[{host}]' if ':' in host else host
        return f'{u.scheme}://{canonical_host}' + (f':{port}' if port not in (None, standard) else '')
    except ValueError:
        return None


class ShadowDenied(ValueError):
    """Untrusted or unauthorized shadow-telemetry request."""


@dataclass(frozen=True)
class ShadowObservation:
    tenant: str
    agent: str
    endpoint: str = field(repr=False)
    untrusted_excerpt: str = field(default='', repr=False)

    def __post_init__(self) -> None:
        if (not _valid_id(self.tenant) or not _valid_id(self.agent)
                or type(self.endpoint) is not str or len(self.endpoint) > 2048
                or type(self.untrusted_excerpt) is not str or len(self.untrusted_excerpt) > 4096):
            raise ValueError('invalid bounded observation')
        try:
            self.endpoint.encode('utf-8', errors='strict')
            self.untrusted_excerpt.encode('utf-8', errors='strict')
        except UnicodeError as exc:
            raise ValueError('invalid unicode in untrusted observation') from exc


@dataclass(frozen=True)
class ShadowVerdict:
    tenant: str
    agent: str
    alert: bool
    origin: str | None
    findings: tuple[str, ...]
    digest: str


class ShadowHunter:
    """Static evidence classifier, not a malware scanner or attribution engine.

    The trusted application provides (tenant -> agent -> allowed origins).
    Unknown sources and simple instruction-injection markers are REVIEW alerts,
    never facts about compromise or permission to perform remediation.
    """

    def __init__(self, allowlist: Mapping[str, Mapping[str, frozenset[str]]]) -> None:
        if not isinstance(allowlist, Mapping) or not allowlist:
            raise ValueError('empty trusted registry')
        registry: dict[str, dict[str, frozenset[str]]] = {}
        for tenant, agents in allowlist.items():
            if not _valid_id(tenant) or not isinstance(agents, Mapping) or not agents:
                raise ValueError('invalid tenant registry')
            validated: dict[str, frozenset[str]] = {}
            for name, origins in agents.items():
                if (not _valid_id(name) or not isinstance(origins, frozenset) or not origins
                        or any(_origin(x) != x for x in origins)):
                    raise ValueError('invalid agent endpoint allowlist')
                validated[name] = origins
            registry[tenant] = validated
        self._registry = registry

    def inspect(self, observation: ShadowObservation) -> ShadowVerdict:
        if not isinstance(observation, ShadowObservation) or observation.tenant not in self._registry:
            raise ShadowDenied('unknown tenant or observation')
        found: list[str] = []
        agent = self._registry[observation.tenant].get(observation.agent)
        origin = _origin(observation.endpoint)
        if agent is None:
            found.append('unregistered_agent')
        if origin is None:
            found.append('invalid_endpoint')
        elif agent is None or origin not in agent:
            found.append('unapproved_endpoint')
        if _INJECTION.search(observation.untrusted_excerpt):
            found.append('suspected_instruction_attack')
        digest = hashlib.sha256(_canonical([
            observation.tenant, observation.agent, origin, found,
            hashlib.sha256(observation.untrusted_excerpt.encode('utf-8')).hexdigest(),
        ])).hexdigest()
        return ShadowVerdict(observation.tenant, observation.agent, bool(found), origin, tuple(found), digest)


class CDPDenied(RuntimeError):
    """Local CDP read-only discovery unavailable, malformed or untrustworthy."""


@dataclass(frozen=True)
class BrowserTarget:
    target_id: str
    kind: str
    origin: str | None
    pid: None = None  # CDP /json/list does not establish an OS process PID.


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate CDP JSON field')
        out[key] = value
    return out


class ReadOnlyWebWarden:
    """Read only GET /json/list from operator-enabled loopback Chrome CDP.

    Does not attach to pages, issue mutating protocol commands or infer PID.
    The loopback port is trusted configuration, NEVER an input from a model.
    """

    def __init__(self, port: int, *, host: str = '127.0.0.1', timeout: float = 2,
                 max_response_bytes: int = 131072, max_targets: int = 64) -> None:
        if (type(port) is not int or not 1 <= port <= 65535 or host != '127.0.0.1'
                or type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= 10 or type(max_response_bytes) is not int
                or not 256 <= max_response_bytes <= 2**20 or type(max_targets) is not int
                or not 1 <= max_targets <= 256):
            raise ValueError('unsafe or unbounded local CDP configuration')
        self._port = port
        self._timeout = timeout
        self._max_bytes = max_response_bytes
        self._max_targets = max_targets

    def _read(self) -> tuple[BrowserTarget, ...]:
        connection = http.client.HTTPConnection('127.0.0.1', self._port, timeout=self._timeout)
        try:
            connection.request('GET', '/json/list', headers={'Accept': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                raise CDPDenied('CDP discovery did not return HTTP 200')
            length = response.getheader('Content-Length')
            if length is not None and (not length.isascii() or not length.isdigit()
                                       or int(length) > self._max_bytes):
                raise CDPDenied('CDP response length untrusted')
            body = response.read(self._max_bytes + 1)
            if len(body) > self._max_bytes:
                raise CDPDenied('CDP response exceeds configured bound')
            data = json.loads(body, object_pairs_hook=_no_duplicates,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid numeric value')))
            if type(data) is not list or len(data) > self._max_targets:
                raise CDPDenied('invalid or unbounded CDP target list')
            result = []
            for item in data:
                if type(item) is not dict or not _valid_id(item.get('id')) or type(item.get('type')) is not str:
                    raise CDPDenied('invalid target metadata')
                kind = item['type']
                if len(kind) > 64 or any(ord(ch) < 33 for ch in kind):
                    raise CDPDenied('invalid CDP target kind')
                # Strip title, full URL/query, WebSocket debugger URL, extension details.
                origin = _origin(item.get('url', ''))
                result.append(BrowserTarget(item['id'], kind, origin))
            return tuple(result)
        except (OSError, UnicodeError, ValueError, RecursionError, http.client.HTTPException) as exc:
            raise CDPDenied('CDP inventory failed closed') from exc
        finally:
            connection.close()

    async def inspect(self) -> tuple[BrowserTarget, ...]:
        return await asyncio.to_thread(self._read)


class EconomicDenied(RuntimeError):
    """Tenant quota, replay, repeated discussion or unknown economic outcome."""


@dataclass(frozen=True)
class EconomicPolicy:
    max_tokens: int
    max_requests: int
    max_duplicate_messages: int
    budget_micro_usd: int
    micro_usd_per_token: int

    def __post_init__(self) -> None:
        limits = (self.max_tokens, self.max_requests, self.max_duplicate_messages,
                  self.budget_micro_usd, self.micro_usd_per_token)
        if (any(type(x) is not int or x < 0 or x > 10**12 for x in limits)
                or self.max_tokens == 0 or self.max_requests == 0
                or self.max_duplicate_messages == 0):
            raise ValueError('invalid tenant economic policy')


@dataclass(frozen=True)
class EconomicReservation:
    operation_id: str
    limit_tokens: int


@dataclass(frozen=True)
class EconomicSnapshot:
    spent_tokens: int
    reserved_tokens: int
    spent_micro_usd: int
    reserved_micro_usd: int
    request_count: int


def _connect_economy(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    db.execute('PRAGMA busy_timeout=5000')
    return db


class EconomicOptimizer:
    """SQLite WAL tenant budget, atomic across processes sharing one local DB.

    Quotes are configured token-price estimates; providers must report actual
    usage through trusted callbacks. No cross-host consensus or invoice claims.
    Unknown or cancelled in-flight outcomes spend the *maximum* reservation.
    """

    def __init__(self, path: str | Path, policies: Mapping[str, EconomicPolicy]) -> None:
        if (not isinstance(policies, Mapping) or not policies
                or any(not _valid_id(t) or not isinstance(p, EconomicPolicy) for t, p in policies.items())):
            raise ValueError('invalid economic tenant registry')
        self._path = Path(path)
        self._policies = dict(policies)
        with closing(_connect_economy(self._path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS economic_policy (
                tenant TEXT PRIMARY KEY, policy_hash TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS economic_operations (
                tenant TEXT NOT NULL, operation_id TEXT NOT NULL, content_digest TEXT NOT NULL,
                max_tokens INTEGER NOT NULL, used_tokens INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK(status IN ('reserved','charged','uncertain')),
                PRIMARY KEY(tenant, operation_id))''')
            db.execute('CREATE INDEX IF NOT EXISTS econ_duplicate ON economic_operations(tenant,content_digest)')
            db.execute('BEGIN IMMEDIATE')
            try:
                for tenant, policy in self._policies.items():
                    digest = hashlib.sha256(_canonical([
                        policy.max_tokens, policy.max_requests, policy.max_duplicate_messages,
                        policy.budget_micro_usd, policy.micro_usd_per_token,
                    ])).hexdigest()
                    row = db.execute('SELECT policy_hash FROM economic_policy WHERE tenant=?',(tenant,)).fetchone()
                    if row is not None and row[0] != digest:
                        raise ValueError('conflicting immutable tenant economic policy')
                    db.execute('INSERT OR IGNORE INTO economic_policy(tenant,policy_hash) VALUES(?,?)',(tenant,digest))
                db.commit()
            except BaseException:
                db.rollback();raise

    def _policy(self, tenant: str) -> EconomicPolicy:
        if tenant not in self._policies or not _valid_id(tenant):
            raise EconomicDenied('unknown tenant')
        return self._policies[tenant]

    def _totals(self, db: sqlite3.Connection, tenant: str) -> tuple[int, int, int]:
        return tuple(db.execute('''SELECT
            COALESCE(SUM(CASE WHEN status='reserved' THEN 0 ELSE used_tokens END),0),
            COALESCE(SUM(CASE WHEN status='reserved' THEN max_tokens ELSE 0 END),0),
            COUNT(*) FROM economic_operations WHERE tenant=?''',(tenant,)).fetchone())  # type: ignore[return-value]

    def snapshot(self, tenant: str) -> EconomicSnapshot:
        policy = self._policy(tenant)
        with closing(_connect_economy(self._path)) as db:
            spent, reserved, count = self._totals(db, tenant)
        return EconomicSnapshot(spent, reserved, spent * policy.micro_usd_per_token,
                                reserved * policy.micro_usd_per_token, count)

    def reserve(self, tenant: str, operation_id: str, max_tokens: int, content_digest: str) -> EconomicReservation:
        policy = self._policy(tenant)
        if (not _valid_id(operation_id) or type(max_tokens) is not int or not 1 <= max_tokens <= policy.max_tokens
                or type(content_digest) is not str or _DIGEST.fullmatch(content_digest) is None):
            raise EconomicDenied('invalid bounded economic reservation')
        with closing(_connect_economy(self._path)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if db.execute('SELECT 1 FROM economic_operations WHERE tenant=? AND operation_id=?',
                              (tenant,operation_id)).fetchone():
                    raise EconomicDenied('operation identity already used; no implicit replay')
                spent, reserved, count = self._totals(db,tenant)
                if (count >= policy.max_requests or spent + reserved + max_tokens > policy.max_tokens
                        or (spent + reserved + max_tokens) * policy.micro_usd_per_token > policy.budget_micro_usd):
                    raise EconomicDenied('tenant token, cost or request budget exceeded')
                duplicates=db.execute('''SELECT COUNT(*) FROM economic_operations
                    WHERE tenant=? AND content_digest=?''',(tenant,content_digest)).fetchone()[0]
                if duplicates >= policy.max_duplicate_messages:
                    raise EconomicDenied('repeated identical content exceeds discussion budget')
                db.execute('''INSERT INTO economic_operations
                    (tenant,operation_id,content_digest,max_tokens,used_tokens,status)
                    VALUES(?,?,?,?,0,'reserved')''',(tenant,operation_id,content_digest,max_tokens))
                db.commit()
                return EconomicReservation(operation_id,max_tokens)
            except BaseException:
                db.rollback();raise

    def _finish(self, tenant: str, operation_id: str, tokens: int | None,
                outcome: str) -> EconomicSnapshot:
        self._policy(tenant)
        if not _valid_id(operation_id):
            raise EconomicDenied('invalid operation identity')
        with closing(_connect_economy(self._path)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute('''SELECT max_tokens,used_tokens,status FROM economic_operations
                    WHERE tenant=? AND operation_id=?''',(tenant,operation_id)).fetchone()
                if row is None:
                    raise EconomicDenied('missing economic reservation')
                cap,used,status=row
                if outcome == 'charged':
                    if type(tokens) is not int or not 0 <= tokens <= cap:
                        raise EconomicDenied('untrusted provider token usage')
                    if status == 'charged' and used == tokens:
                        db.commit();return self.snapshot(tenant)
                    if status != 'reserved':
                        raise EconomicDenied('stale economic outcome or uncertainty')
                    used=tokens
                else:
                    if status=='uncertain':
                        db.commit();return self.snapshot(tenant)
                    if status!='reserved':
                        raise EconomicDenied('economic outcome already committed')
                    used=cap
                db.execute('''UPDATE economic_operations SET status=?,used_tokens=?
                    WHERE tenant=? AND operation_id=? AND status='reserved' ''',
                    (outcome,used,tenant,operation_id))
                db.commit()
                return self.snapshot(tenant)
            except BaseException:
                db.rollback();raise

    def settle(self, tenant: str, operation_id: str, actual_tokens: int) -> EconomicSnapshot:
        return self._finish(tenant,operation_id,actual_tokens,'charged')

    def mark_uncertain(self, tenant: str, operation_id: str) -> EconomicSnapshot:
        return self._finish(tenant,operation_id,None,'uncertain')


MeteredHandler = Callable[[Mapping[str,Any]], Awaitable[tuple[Mapping[str,Any],int]]]


def budgeted_swarm_handler(economy: EconomicOptimizer, tenant: str, run_id: str, agent: str,
                           max_tokens: int, handler: MeteredHandler) -> Callable[[Mapping[str,Any]],Awaitable[dict[str,Any]]]:
    """Bind a *trusted* metered handler to a fixed tenant, run and agent.

    Model text is only payload; result usage comes from the trusted provider.
    Avoid auto retry after an ambiguous dispatch. Budget reservation survives.
    """
    if (not isinstance(economy, EconomicOptimizer) or not _valid_id(tenant)
            or not _valid_id(run_id) or not _valid_id(agent) or type(max_tokens) is not int
            or max_tokens < 1 or not callable(handler)):
        raise ValueError('invalid fixed model handler binding')

    async def invoke(inputs: Mapping[str,Any]) -> dict[str,Any]:
        if not isinstance(inputs, Mapping):
            raise EconomicDenied('invalid handler input')
        try:
            wire = _canonical(dict(inputs))
            if len(wire) > 65536:
                raise EconomicDenied('handler input budget exceeded')
        except (OverflowError, RecursionError, ValueError, TypeError) as exc:
            raise EconomicDenied('untrusted handler input') from exc
        content_digest = hashlib.sha256(wire).hexdigest()
        operation_id = hashlib.sha256(_canonical([tenant,run_id,agent,content_digest])).hexdigest()[:48]
        await asyncio.to_thread(economy.reserve,tenant,operation_id,max_tokens,content_digest)
        try:
            output,actual = await handler(json.loads(wire))
            if not isinstance(output, Mapping) or type(actual) is not int:
                raise EconomicDenied('invalid metered provider output')
            value=_canonical(dict(output))
            if len(value) > 65536:
                raise EconomicDenied('metered output exceeds limit')
            await asyncio.to_thread(economy.settle,tenant,operation_id,actual)
            return json.loads(value)
        except BaseException as exc:
            # The model or remote service might have run. Never release the token quote.
            # Shield the DB update against task cancellation; a pending to_thread
            # keeps working even when its awaiting coroutine is cancelled.
            if isinstance(exc, asyncio.CancelledError):
                # Do the small, bounded SQLite write synchronously: this must
                # finish before the task and possibly its event loop disappear.
                # Avoid delegating this safety-critical update to an orphan
                # thread that may not finish during process shutdown.
                economy.mark_uncertain(tenant, operation_id)
                raise
            try:
                await asyncio.to_thread(economy.mark_uncertain,tenant,operation_id)
            except EconomicDenied:
                # A committed settlement already has a durable record.
                pass
            raise EconomicDenied('remote model or handler outcome uncertain') from exc

    return invoke


@dataclass(frozen=True)
class DefensiveProbe:
    case_id: str
    observation: ShadowObservation
    expected_alert: bool

    def __post_init__(self) -> None:
        if not _valid_id(self.case_id) or not isinstance(self.observation,ShadowObservation) or type(self.expected_alert) is not bool:
            raise ValueError('invalid offline red-team probe')


@dataclass(frozen=True)
class RedTeamReport:
    total: int
    correct: int
    false_accepts: int
    false_rejects: int
    evidence_head: str


class DefensiveRedTeamer:
    """Pure offline fixture evaluator; no network scans, exploits or 24/7 daemon."""

    def __init__(self, hunter: ShadowHunter, *, max_probes: int = 64) -> None:
        if not isinstance(hunter,ShadowHunter) or type(max_probes) is not int or not 1 <= max_probes <= 1024:
            raise ValueError('invalid bounded adversarial evaluator')
        self._hunter,self._max=hunter,max_probes

    def evaluate(self, probes: Sequence[DefensiveProbe]) -> RedTeamReport:
        if (not isinstance(probes,(list,tuple)) or not 1 <= len(probes) <= self._max
                or any(not isinstance(p,DefensiveProbe) for p in probes)
                or len({p.case_id for p in probes})!=len(probes)):
            raise ValueError('invalid or unbounded red-team fixture')
        correct=fp=fn=0
        evidence=[]
        for p in probes:
            predicted=self._hunter.inspect(p.observation).alert
            correct+=int(predicted==p.expected_alert)
            fp+=int(predicted and not p.expected_alert)
            fn+=int(not predicted and p.expected_alert)
            evidence.append((p.case_id,p.expected_alert,predicted))
        head=hashlib.sha256(_canonical(evidence)).hexdigest()
        # False acceptance = missed alert; false rejection = spurious alert.
        return RedTeamReport(len(probes),correct,fn,fp,head)


class SecurityOODAObserver:
    """Connect shadow observations to pre-existing OODA, with no actuator."""

    def __init__(self, hunter: ShadowHunter, ooda: CognitiveOODA) -> None:
        if not isinstance(hunter, ShadowHunter) or not isinstance(ooda,CognitiveOODA):
            raise ValueError('invalid OODA binding')
        self._hunter,self._ooda=hunter,ooda

    async def observe(self, observation: ShadowObservation, *, event_id: str) -> tuple[ShadowVerdict,CognitiveDecision]:
        if not _valid_id(event_id):
            raise ShadowDenied('invalid security event id')
        verdict=self._hunter.inspect(observation)
        event=CognitiveEvent(observation.tenant,event_id,'security',
                             {'finding_count':len(verdict.findings), 'evidence':verdict.digest})
        decision=await self._ooda.assess(event,{'alerts':len(verdict.findings)},
                                         (WorldAction('read','alerts'),))
        return verdict, decision
