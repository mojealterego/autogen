"""OMEGA 14 bounded self-modifying policy SOURCE, not self-authorizing core rewriting.

Generated Python consists ONLY of one constant assignment with three approved
numeric configuration keys; it is never exec'd/imported. A trusted evaluator,
operator HMAC and SQLite compare-and-swap gate promote a revised .py source
artifact. The caller must independently protect signing keys and test fixtures.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import os
import re
import sqlite3
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._workflow_evolution import EvolutionCandidate,EvolutionCase,EvolutionDecision,EvolutionObservation,EvolutionPolicy,WorkflowEvolutionEngine
from ._governed_workflow import _canonical

_VALID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_ALLOWED = {'max_parallel':(1,64), 'retry_base_ms':(0,10000), 'step_timeout_ms':(1,600000)}


class RSIDenied(RuntimeError):
    """Invalid source mutation, stale evidence, or absent external approval."""


def canonical_policy_source(parameters:Mapping[str,int])->str:
    if (not isinstance(parameters,Mapping) or set(parameters)!=set(_ALLOWED) or
        any(type(v) is not int or not _ALLOWED[k][0]<=v<=_ALLOWED[k][1] for k,v in parameters.items())):
        raise ValueError('bounded policy schema denied')
    return 'POLICY = {'+', '.join(f'{key!r}: {parameters[key]}' for key in sorted(_ALLOWED))+'}\n'


def _parse(source:str)->dict[str,int]:
    if type(source) is not str or not 1<=len(source)<=1024:
        raise RSIDenied('invalid code artifact length')
    try:
        tree=ast.parse(source)
        if (len(tree.body)!=1 or not isinstance(tree.body[0],ast.Assign)
            or len(tree.body[0].targets)!=1 or not isinstance(tree.body[0].targets[0],ast.Name)
            or tree.body[0].targets[0].id!='POLICY'):
            raise RSIDenied('only one policy assignment is allowed')
        mapping=ast.literal_eval(tree.body[0].value)
        expected=canonical_policy_source(mapping)
        if source!=expected:
            raise RSIDenied('policy source must be canonical; no extra code allowed')
        return dict(mapping)
    except (SyntaxError,ValueError,TypeError,RecursionError,MemoryError) as exc:
        raise RSIDenied('untrusted or non-declarative source') from exc


def _sha(text:str)->str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class RSIProposal:
    source:str
    expected_revision:int
    previous_sha:str
    decision:EvolutionDecision


class RestrictedRSI:
    """Persistent local source-policy evolution via real fixture evaluation.

    This is NOT Gödel-machine proof of arbitrary code self-improvement.
    No hot-reload, arbitrary Python, privileged I/O or self-granted approvals.
    """
    def __init__(self,database:str|Path,tenant:str,initial_source:str,cases:Sequence[EvolutionCase],
                 evaluator:Callable[[EvolutionCandidate,EvolutionCase],Awaitable[EvolutionObservation]],
                 policy:EvolutionPolicy,*,operator_keys:Mapping[str,bytes]):
        if (type(tenant) is not str or _VALID.fullmatch(tenant) is None or
            not isinstance(operator_keys,Mapping) or not operator_keys or
            any(type(k) is not str or _VALID.fullmatch(k) is None or type(v) is not bytes or len(v)<16 for k,v in operator_keys.items())):
            raise ValueError('invalid trusted RSI identities or signing keys')
        _parse(initial_source)
        self.tenant=tenant
        self._path=Path(database).resolve()
        self._path.parent.mkdir(parents=True,exist_ok=True)
        self._artifact=self._path.with_name('omega_rsi_'+tenant+'.py')
        self._operator_keys=dict(operator_keys)
        self._cases=tuple(cases)
        self._evaluator=evaluator
        self._policy=policy
        self._pending:RSIProposal|None=None
        with closing(self._connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS omega_rsi_policy (
                tenant TEXT PRIMARY KEY, revision INTEGER NOT NULL, source TEXT NOT NULL,
                digest TEXT NOT NULL, prior_source TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS omega_rsi_history (
                tenant TEXT NOT NULL, revision INTEGER NOT NULL, source_digest TEXT NOT NULL,
                prior_digest TEXT NOT NULL, evidence_digest TEXT NOT NULL,
                action TEXT NOT NULL, operator TEXT NOT NULL, approval_signature TEXT NOT NULL,
                PRIMARY KEY(tenant,revision))''')
            with db:
                db.execute('INSERT OR IGNORE INTO omega_rsi_policy(tenant,revision,source,digest) VALUES(?,0,?,?)',
                           (tenant,initial_source,_sha(initial_source)))
            _,s,_,_=self._read(db)
            self._verify_history(db)
            if s!=initial_source:
                raise RSIDenied('different initial source cannot overwrite existing durable state')
        self.materialize()

    def _connect(self)->sqlite3.Connection:
        db=sqlite3.connect(self._path,timeout=5,isolation_level=None)
        db.execute('PRAGMA busy_timeout=5000')
        return db

    def _read(self,db:sqlite3.Connection)->tuple[int,str,str,str|None]:
        row=db.execute('SELECT revision,source,digest,prior_source FROM omega_rsi_policy WHERE tenant=?',(self.tenant,)).fetchone()
        if row is None or type(row[0]) is not int or row[0]<0 or _sha(row[1])!=row[2]:
            raise RSIDenied('missing or corrupt source evolution state')
        _parse(row[1])
        if row[3] is not None:_parse(row[3])
        return row

    def _verify_history(self, db:sqlite3.Connection)->None:
        revision,source,current_digest,_=self._read(db)
        history=db.execute('''SELECT revision,source_digest,prior_digest,evidence_digest,action,operator,approval_signature
                              FROM omega_rsi_history WHERE tenant=? ORDER BY revision''',(self.tenant,)).fetchall()
        if len(history)!=revision:
            raise RSIDenied('source audit history incomplete')
        previous=None
        for expected_revision,row in enumerate(history,start=1):
            rev,after,before,evidence,action,actor,signature=row
            if rev!=expected_revision or (previous is not None and before!=previous):
                raise RSIDenied('source audit revision or parent mismatch')
            if action=='promote':
                payload=_canonical(['omega-rsi-approval-v1',self.tenant,'promote',rev-1,before,after,evidence])
            elif action=='rollback':
                if evidence!=after:
                    raise RSIDenied('rollback evidence identity invalid')
                payload=_canonical(['omega-rsi-approval-v1',self.tenant,'rollback',rev-1,before,after])
            else:raise RSIDenied('unexpected source audit action')
            self._authorize(actor,signature,payload)
            previous=after
        if previous is not None and previous!=current_digest:
            raise RSIDenied('source head differs from signed audit')

    def verify_history(self)->bool:
        with closing(self._connect()) as db:self._verify_history(db)
        return True

    def active_source(self)->str:
        with closing(self._connect()) as db:return self._read(db)[1]

    def materialize(self)->str:
        """Repair derived source artifact from authoritative database after crash."""
        source=self.active_source()
        if self._artifact.is_symlink():raise RSIDenied('symlink source artifact rejected')
        fd,path=tempfile.mkstemp(prefix='.omega-rsi-',dir=self._artifact.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                stream.write(source);stream.flush();os.fsync(stream.fileno())
            os.replace(path,self._artifact)
        finally:
            if os.path.exists(path):os.unlink(path)
        return _sha(source)

    async def propose(self,source:str)->RSIProposal:
        candidate_parameters=_parse(source)
        with closing(self._connect()) as db:revision,active,digest,_=self._read(db)
        if source==active:raise RSIDenied('candidate must change actual source')
        name=lambda s:'sha-'+_sha(s)[:32]
        engine=WorkflowEvolutionEngine(EvolutionCandidate(name(active),_parse(active)),self._cases,
                                       self._evaluator,self._policy)
        decision=await engine.compare(EvolutionCandidate(name(source),candidate_parameters))
        pending=RSIProposal(source,revision,digest,decision)
        self._pending=pending
        return pending

    async def discover(self,variants:Mapping[str,tuple[int,...]])->RSIProposal:
        """Bounded autonomous policy-source candidate generator and fixture search.

        Does NOT authorize, import or activate its own choice. Only a distinct
        trusted HMAC operator approval can promote the proposed Python source.
        """
        if (not isinstance(variants,Mapping) or not 1<=len(variants)<=3 or
                any(k not in _ALLOWED or type(v) is not tuple or not 1<=len(v)<=8 or
                    any(type(n) is not int or not _ALLOWED[k][0]<=n<=_ALLOWED[k][1] for n in v)
                    for k,v in variants.items()) or sum(len(v) for v in variants.values())>16):
            raise RSIDenied('unbounded or unauthorized mutation search')
        active=_parse(self.active_source())
        best:RSIProposal|None=None
        try:
            for key in sorted(variants):
                for value in sorted(set(variants[key])):
                    if value==active[key]:continue
                    source=canonical_policy_source({**active,key:value})
                    proposal=await self.propose(source)
                    d=proposal.decision
                    if not d.passed:continue
                    if best is None or (d.candidate_accuracy,-d.candidate_p95_ms,-d.candidate_cost)>                            (best.decision.candidate_accuracy,-best.decision.candidate_p95_ms,-best.decision.candidate_cost):
                        best=proposal
        except BaseException:
            self._pending=None
            raise
        if best is None:
            self._pending=None
            raise RSIDenied('no evidence-backed improving source mutation found')
        self._pending=best
        return best

    def approval_payload(self,proposal:RSIProposal,action:str)->bytes:
        if (action!='promote' or proposal is not self._pending):
            raise RSIDenied('proposal not bound to a trusted completed evaluation')
        return _canonical(['omega-rsi-approval-v1',self.tenant,action,
                           proposal.expected_revision,proposal.previous_sha,
                           _sha(proposal.source),proposal.decision.evidence_digest])

    def rollback_payload(self)->bytes:
        with closing(self._connect()) as db:
            revision,source,sha,prior=self._read(db)
        if prior is None:raise RSIDenied('no previous source available')
        return _canonical(['omega-rsi-approval-v1',self.tenant,'rollback',revision,sha,_sha(prior)])

    def _authorize(self,operator:str,signature:str,message:bytes)->None:
        key=self._operator_keys.get(operator)
        if (key is None or type(signature) is not str or re.fullmatch('[0-9a-f]{64}',signature) is None or
            not hmac.compare_digest(signature,hmac.new(key,message,hashlib.sha256).hexdigest())):
            raise RSIDenied('external signed operator authorization required')

    def promote(self,proposal:RSIProposal,*,operator:str,signature:str)->int:
        if proposal is not self._pending or not proposal.decision.passed:
            raise RSIDenied('only locally evaluated improving candidate is promotable')
        msg=self.approval_payload(proposal,'promote')
        self._authorize(operator,signature,msg)
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                revision,source,sha,_=self._read(db)
                if revision!=proposal.expected_revision or sha!=proposal.previous_sha:
                    raise RSIDenied('concurrent source revision or stale evaluation')
                update=db.execute('''UPDATE omega_rsi_policy SET revision=?,source=?,digest=?,prior_source=?
                                  WHERE tenant=? AND revision=? AND digest=?''',
                                  (revision+1,proposal.source,_sha(proposal.source),source,
                                   self.tenant,revision,sha))
                if update.rowcount!=1:raise RSIDenied('source compare-and-swap failed')
                db.execute('INSERT INTO omega_rsi_history VALUES(?,?,?,?,?,?,?,?)',
                           (self.tenant,revision+1,_sha(proposal.source),sha,
                            proposal.decision.evidence_digest,'promote',operator,signature))
                db.commit()
            except BaseException:
                db.rollback();raise
        self._pending=None
        self.materialize()
        return revision+1

    def rollback(self,*,operator:str,signature:str)->int:
        message=self.rollback_payload()
        self._authorize(operator,signature,message)
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                revision,source,sha,prior=self._read(db)
                if prior is None or message!=_canonical(['omega-rsi-approval-v1',self.tenant,'rollback',revision,sha,_sha(prior)]):
                    raise RSIDenied('stale rollback authority')
                db.execute('''UPDATE omega_rsi_policy SET revision=?,source=?,digest=?,prior_source=NULL
                           WHERE tenant=? AND revision=? AND digest=?''',
                           (revision+1,prior,_sha(prior),self.tenant,revision,sha))
                db.execute('INSERT INTO omega_rsi_history VALUES(?,?,?,?,?,?,?,?)',
                           (self.tenant,revision+1,_sha(prior),sha,_sha(prior),'rollback',operator,signature))
                db.commit()
            except BaseException:
                db.rollback();raise
        self._pending=None
        self.materialize()
        return revision+1
