"""Bounded local service process supervision with deterministic restart budgets.

Only trusted, absolute executable paths may be registered. This is not an
adversarial sandbox, cgroup controller, host orchestration or HA control plane.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import signal
import stat
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ._governed_workflow import _canonical


class SupervisorDenied(RuntimeError):
    """An untrusted command, configuration or state transition was rejected."""


@dataclass(frozen=True)
class SupervisorSpec:
    name: str
    argv: tuple[str,...]
    max_restarts: int = 3
    restart_backoff: float = .2
    restart_cap: float = 5.0
    poll_interval: float = .1
    stop_grace: float = 1.0
    readiness_timeout: float = 5.0
    probe_timeout: float = .5
    env_allowlist: frozenset[str] = frozenset()
    cwd: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.name,str) or not 0<len(self.name)<=128
                or any(ord(c)<32 for c in self.name)
                or type(self.argv) is not tuple or not 1<=len(self.argv)<=64
                or any(type(arg) is not str or len(arg)>4096 or '\x00' in arg for arg in self.argv)
                or not os.path.isabs(self.argv[0]) or not os.path.isfile(self.argv[0])
                or not os.access(self.argv[0],os.X_OK)
                or type(self.max_restarts) is not int or not 0<=self.max_restarts<=64
                or not isinstance(self.env_allowlist,frozenset)
                or any(not isinstance(k,str) or not k.isidentifier() or len(k)>128 for k in self.env_allowlist)
                or (self.cwd is not None and (not os.path.isabs(self.cwd) or not os.path.isdir(self.cwd)))):
            raise SupervisorDenied('untrusted executable or invalid service configuration')
        for x in (self.restart_backoff,self.restart_cap,self.poll_interval,
                  self.stop_grace,self.readiness_timeout,self.probe_timeout):
            if type(x) not in (int,float) or not math.isfinite(x) or not 0<x<=3600:
                raise SupervisorDenied('invalid service resource/time limits')
        if self.restart_backoff>self.restart_cap or self.probe_timeout>self.readiness_timeout:
            raise SupervisorDenied('invalid service timeout hierarchy')


@dataclass(frozen=True)
class SupervisorEvent:
    sequence: int
    state: str
    starts: int
    previous: str
    digest: str


@dataclass(frozen=True)
class SupervisorSnapshot:
    state: str
    ready: bool
    starts: int
    restarts: int
    pid: int | None
    audit_head: str


def _event_digest(seq: int, state: str, starts: int, previous: str) -> str:
    return hashlib.sha256(_canonical(['omega-supervisor-v1',seq,state,starts,previous])).hexdigest()


def verify_supervisor_audit(events: Sequence[SupervisorEvent], expected_head: str | None = None) -> bool:
    previous='0'*64
    for i,e in enumerate(events):
        if (not isinstance(e,SupervisorEvent) or e.sequence!=i or e.previous!=previous
                or e.digest!=_event_digest(i,e.state,e.starts,previous)):
            return False
        previous=e.digest
    return expected_head is None or expected_head==previous


Probe=Callable[[],Awaitable[bool]]


class ManagedProcessSupervisor:
    """Supervise a single host-owned OS process and its POSIX session group."""

    def __init__(self, spec: SupervisorSpec, *, probe: Probe | None = None) -> None:
        if not isinstance(spec,SupervisorSpec) or probe is not None and not callable(probe):
            raise SupervisorDenied('invalid service or health probe')
        self._spec=spec
        self._probe=probe
        self._stop=asyncio.Event()
        self._runner: asyncio.Task[None] | None=None
        self._process: asyncio.subprocess.Process | None=None
        self._starts=0
        self._restarts=0
        self._state='idle'
        self._ready=False
        self._events: list[SupervisorEvent]=[]
        self._head='0'*64

    def _record(self, state: str) -> None:
        seq=len(self._events)
        digest=_event_digest(seq,state,self._starts,self._head)
        self._events.append(SupervisorEvent(seq,state,self._starts,self._head,digest))
        self._head=digest
        self._state=state

    @property
    def events(self) -> tuple[SupervisorEvent,...]:
        return tuple(self._events)

    def snapshot(self) -> SupervisorSnapshot:
        pid=self._process.pid if self._process is not None and self._process.returncode is None else None
        return SupervisorSnapshot(self._state,self._ready,self._starts,self._restarts,pid,self._head)

    async def start(self) -> None:
        if self._runner is not None:
            raise SupervisorDenied('service supervisor is single-use')
        self._runner=asyncio.create_task(self._serve(),name='omega-supervisor-'+self._spec.name)

    async def wait(self) -> None:
        if self._runner is None:
            raise SupervisorDenied('service not started')
        await self._runner

    async def stop(self) -> None:
        if self._runner is None:
            raise SupervisorDenied('service not started')
        self._stop.set()
        await self.wait()

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        if os.name=='posix':
            # Spawned with start_new_session=True; terminate the entire session,
            # including children that outlive the initial process.
            try: os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try:
                await asyncio.wait_for(proc.wait(),self._spec.stop_grace)
            except asyncio.TimeoutError:
                pass
            finally:
                # Descendants can survive a parent which handled SIGTERM.
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            await proc.wait()
        else:
            if proc.returncode is None:
                proc.terminate()
                try: await asyncio.wait_for(proc.wait(),self._spec.stop_grace)
                except asyncio.TimeoutError: proc.kill()
            await proc.wait()

    async def _observe(self, proc: asyncio.subprocess.Process) -> None:
        loop=asyncio.get_running_loop()
        readiness_deadline=loop.time()+self._spec.readiness_timeout
        while not self._stop.is_set():
            if proc.returncode is not None:
                self._ready=False
                self._record('crashed')
                return
            if self._probe is None:
                healthy=True
            else:
                try:
                    healthy=(await asyncio.wait_for(self._probe(),self._spec.probe_timeout)) is True
                except asyncio.CancelledError:
                    raise
                except Exception:
                    healthy=False
            if healthy:
                if not self._ready:
                    self._ready=True
                    self._record('running')
            else:
                self._ready=False
                if loop.time()>=readiness_deadline:
                    self._record('unhealthy')
                    return
                if self._state=='running':self._record('unhealthy')
            try:
                await asyncio.wait_for(self._stop.wait(),self._spec.poll_interval)
            except asyncio.TimeoutError:
                pass
        self._ready=False

    async def _serve(self) -> None:
        try:
            while not self._stop.is_set():
                self._record('starting')
                proc=None
                try:
                    env={key:os.environ[key] for key in self._spec.env_allowlist if key in os.environ}
                    proc=await asyncio.create_subprocess_exec(
                        *self._spec.argv,cwd=self._spec.cwd,env=env,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        start_new_session=os.name=='posix',
                    )
                    self._process=proc
                    self._starts+=1
                    await self._observe(proc)
                except asyncio.CancelledError:
                    self._stop.set()
                    raise
                except OSError:
                    self._record('launch_failed')
                finally:
                    if proc is not None:
                        # Drain cleanup after cancellation; detached children
                        # must not outlive this supervisor's ownership.
                        task=asyncio.create_task(self._terminate(proc))
                        interrupted=False
                        while not task.done():
                            try: await asyncio.shield(task)
                            except asyncio.CancelledError: interrupted=True
                        task.result()
                        self._process=None
                        if interrupted:self._stop.set()
                if self._stop.is_set():break
                if self._restarts>=self._spec.max_restarts:
                    self._record('exhausted')
                    return
                self._restarts+=1
                self._record('backoff')
                delay=min(self._spec.restart_backoff*2**(self._restarts-1),self._spec.restart_cap)
                try: await asyncio.wait_for(self._stop.wait(),delay)
                except asyncio.TimeoutError:pass
        finally:
            self._ready=False
            if self._stop.is_set():self._record('stopped')


class InfrastructureSupervisor:
    """Operator-managed group of independent local service supervisors."""

    def __init__(self, specs: Sequence[SupervisorSpec], *,
                 probes: Mapping[str,Probe] | None = None, max_services: int = 64) -> None:
        if (type(max_services) is not int or not 1<=len(specs)<=max_services<=256
                or any(not isinstance(s,SupervisorSpec) for s in specs)
                or len({s.name for s in specs})!=len(specs)
                or any(k not in {s.name for s in specs} for k in (probes or {}))):
            raise SupervisorDenied('invalid managed service inventory')
        self._workers={s.name:ManagedProcessSupervisor(s,probe=(probes or {}).get(s.name)) for s in specs}
        self._started=False

    async def start(self) -> None:
        if self._started: raise SupervisorDenied('infrastructure supervisor is single-use')
        self._started=True
        try:
            for worker in self._workers.values():
                await worker.start()
        except BaseException:
            await asyncio.gather(*(w.stop() for w in self._workers.values() if w._runner is not None),
                                 return_exceptions=True)
            raise

    def snapshot(self) -> dict[str,SupervisorSnapshot]:
        return {name:worker.snapshot() for name,worker in self._workers.items()}

    async def stop(self) -> None:
        if not self._started:raise SupervisorDenied('infrastructure supervisor not started')
        await asyncio.gather(*(worker.stop() for worker in self._workers.values()))
