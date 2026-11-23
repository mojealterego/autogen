import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from autogen_core import (
    SupervisorSpec, ManagedProcessSupervisor, InfrastructureSupervisor,
    SupervisorDenied, verify_supervisor_audit,
)


def spec(*args, **kwargs):
    return SupervisorSpec('test',(sys.executable,'-c',*args),poll_interval=.03,
                          max_restarts=kwargs.get('max_restarts',0),
                          restart_backoff=.02, restart_cap=.1, stop_grace=.1,
                          readiness_timeout=kwargs.get('readiness_timeout',.3),
                          probe_timeout=.05,
                          env_allowlist=kwargs.get('env_allowlist',frozenset()))


async def wait_for(supervisor, state, timeout=5):
    async with asyncio.timeout(timeout):
        while supervisor.snapshot().state != state:
            await asyncio.sleep(.02)
    return supervisor.snapshot()


@pytest.mark.asyncio
async def test_real_process_start_health_and_graceful_shutdown():
    mgr=ManagedProcessSupervisor(spec('import time;time.sleep(120)'))
    await mgr.start()
    snap=await wait_for(mgr,'running')
    assert snap.pid and snap.starts==1 and snap.ready
    await mgr.stop()
    assert mgr.snapshot().state=='stopped' and mgr.snapshot().pid is None
    assert verify_supervisor_audit(mgr.events,mgr.snapshot().audit_head)


@pytest.mark.asyncio
async def test_crash_restart_budget_exhaustion():
    mgr=ManagedProcessSupervisor(spec('import sys;sys.exit(9)',max_restarts=2))
    await mgr.start(); await asyncio.wait_for(mgr.wait(),5)
    state=mgr.snapshot()
    assert state.starts==3 and state.restarts==2 and state.state=='exhausted' and not state.ready
    assert verify_supervisor_audit(mgr.events,state.audit_head)


@pytest.mark.asyncio
async def test_probe_failure_restarts_and_fails_closed():
    called=0
    async def bad_probe():
        nonlocal called
        called+=1
        return False
    mgr=ManagedProcessSupervisor(spec('import time;time.sleep(120)',max_restarts=1,
                                       readiness_timeout=.15),probe=bad_probe)
    await mgr.start();await asyncio.wait_for(mgr.wait(),5)
    assert mgr.snapshot().state=='exhausted' and mgr.snapshot().starts==2
    assert called>=2


@pytest.mark.asyncio
async def test_secret_environment_does_not_leak(tmp_path,monkeypatch):
    monkeypatch.setenv('OMEGA_PRODUCTION_SECRET','never-in-child')
    out=tmp_path/'env.txt'
    code=f'import os,pathlib;pathlib.Path({str(out)!r}).write_text(str(os.getenv("OMEGA_PRODUCTION_SECRET")))'
    mgr=ManagedProcessSupervisor(spec(code))
    await mgr.start();await asyncio.wait_for(mgr.wait(),5)
    assert out.read_text()=='None'


@pytest.mark.asyncio
async def test_explicit_env_allowlist_preserves_intended_configuration(tmp_path,monkeypatch):
    monkeypatch.setenv('OMEGA_EXPLICIT','yes')
    out=tmp_path/'env.txt'
    code=f'import os,pathlib;pathlib.Path({str(out)!r}).write_text(str(os.getenv("OMEGA_EXPLICIT")))'
    mgr=ManagedProcessSupervisor(spec(code,env_allowlist=frozenset({'OMEGA_EXPLICIT'})))
    await mgr.start();await asyncio.wait_for(mgr.wait(),5)
    assert out.read_text()=='yes'


@pytest.mark.asyncio
async def test_duplicate_and_untrusted_executable_rejected():
    with pytest.raises(SupervisorDenied):
        SupervisorSpec('bad',('python','-c','pass'))
    with pytest.raises(SupervisorDenied):
        InfrastructureSupervisor([spec('pass'),spec('pass')])


@pytest.mark.asyncio
async def test_multi_service_supervisor_stops_both_processes():
    specs=[replace(spec('import time;time.sleep(120)'),name=x) for x in ('a','b')]
    infra=InfrastructureSupervisor(specs)
    await infra.start()
    async with asyncio.timeout(5):
        while not all(v.state=='running' for v in infra.snapshot().values()):
            await asyncio.sleep(.02)
    assert len({v.pid for v in infra.snapshot().values()})==2
    await infra.stop()
    assert all(v.state=='stopped' for v in infra.snapshot().values())


def test_hash_chain_tampering_detected():
    event=()
    assert verify_supervisor_audit(event,'0'*64)
    from autogen_core import SupervisorEvent
    fake=SupervisorEvent(0,'running',1,'0'*64,'0'*64)
    assert not verify_supervisor_audit((fake,),'0'*64)


@pytest.mark.skipif(os.name!='posix', reason='POSIX process groups')
@pytest.mark.asyncio
async def test_terminate_process_group_including_descendant(tmp_path):
    # Parent exits on SIGTERM; child remains unless the whole process group dies.
    parent=tmp_path/'parent.py';pidfile=tmp_path/'pid.txt'
    parent.write_text('import subprocess,sys,time,pathlib\n'
        'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(120)"])\n'
        f'pathlib.Path({str(pidfile)!r}).write_text(str(p.pid))\n'
        'time.sleep(120)\n')
    mgr=ManagedProcessSupervisor(replace(spec('import time;time.sleep(120)'),argv=(sys.executable,str(parent))))
    await mgr.start();await wait_for(mgr,'running')
    async with asyncio.timeout(5):
        while not pidfile.exists(): await asyncio.sleep(.02)
    childpid=int(pidfile.read_text())
    await mgr.stop()
    # A killed descendant may remain a zombie until its parent/OS reaps it.
    async with asyncio.timeout(5):
        while True:
            try: status=Path(f'/proc/{childpid}/stat').read_text().split()[2]
            except FileNotFoundError: break
            if status=='Z':break
            await asyncio.sleep(.03)
    assert mgr.snapshot().state=='stopped'
