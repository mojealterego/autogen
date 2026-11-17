import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / 'src/autogen_core/_process_worker.py'
spec = importlib.util.spec_from_file_location('omega_process_worker', MODULE)
assert spec and spec.loader
worker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = worker
spec.loader.exec_module(worker)


def script(tmp_path, body):
    path = tmp_path / 'worker.py'
    path.write_text(body)
    return path


def test_roundtrip_and_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGA_SECRET", "private")
    p = script(tmp_path, 'import json, os, sys\nprint(json.dumps([json.load(sys.stdin), os.getenv("OMEGA_SECRET")]))')
    result = asyncio.run(worker.ProcessWorker(p)({'foo': 3}))
    assert result == [{'foo': 3}, None]


def test_bounded_output(tmp_path):
    p = script(tmp_path, 'import sys\nwhile True: sys.stdout.write("x" * 4096); sys.stdout.flush()')
    with pytest.raises(worker.WorkerError, match='output limit'):
        asyncio.run(worker.ProcessWorker(p, worker.WorkerLimits(max_output=1024))({}))


def test_bounded_stderr(tmp_path):
    p = script(tmp_path, 'import sys\nwhile True: sys.stderr.write("x" * 4096); sys.stderr.flush()')
    with pytest.raises(worker.WorkerError, match='output limit'):
        asyncio.run(worker.ProcessWorker(p, worker.WorkerLimits(max_output=1024))({}))


def test_timeout(tmp_path):
    p = script(tmp_path, 'import time\nwhile True: time.sleep(1)')
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(worker.ProcessWorker(p, worker.WorkerLimits(timeout=.2))({}))


def test_bad_json_and_exit(tmp_path):
    p = script(tmp_path, 'print("not json")')
    with pytest.raises(worker.WorkerError, match='invalid JSON'):
        asyncio.run(worker.ProcessWorker(p)({}))
    p.write_text('raise SystemExit(9)')
    with pytest.raises(worker.WorkerError, match='exit status'):
        asyncio.run(worker.ProcessWorker(p)({}))


def test_input_bound(tmp_path):
    p = script(tmp_path, 'raise AssertionError("must not run")')
    with pytest.raises(worker.WorkerError, match='input limit'):
        asyncio.run(worker.ProcessWorker(p, worker.WorkerLimits(max_input=8))({'s': '123456789'}))


def test_resource_limits(tmp_path):
    p = script(tmp_path, 'import json, resource\nprint(json.dumps([resource.getrlimit(k)[0] for k in (resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_NOFILE)]))')
    result = asyncio.run(worker.ProcessWorker(p, worker.WorkerLimits(memory_bytes=128*1024**2, cpu_seconds=2, open_files=32))({}))
    assert result == [128*1024**2, 2, 32]


def test_cancellation_kills_process(tmp_path):
    import os
    marker = tmp_path / 'pid'
    p = script(tmp_path, f'import os,time\nopen({str(marker)!r},"w").write(str(os.getpid()))\nwhile True: time.sleep(1)')
    async def run():
        task = asyncio.create_task(worker.ProcessWorker(p)({}))
        for _ in range(200):
            if marker.exists() and marker.read_text(): break
            await asyncio.sleep(.01)
        pid = int(marker.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        with pytest.raises(ProcessLookupError): os.kill(pid, 0)
    asyncio.run(run())


def test_nonfinite_result(tmp_path):
    p = script(tmp_path, 'print("1e999")')
    with pytest.raises(worker.WorkerError, match='invalid JSON'):
        asyncio.run(worker.ProcessWorker(p)({}))


def test_durable_pipeline_replays_worker_output(tmp_path):
    import types
    package = types.ModuleType('omega_integration')
    package.__path__ = [str(MODULE.parent)]
    sys.modules[package.__name__] = package
    from omega_integration._durable_workflow import DurableWorkflowExecutor
    from omega_integration._governed_workflow import WorkflowPolicy, WorkflowStep
    p = script(tmp_path, 'import json,sys\nprint(json.dumps(json.load(sys.stdin).get("a", 0) + 1))')
    handler = worker.ProcessWorker(p)
    async def run():
        engine = DurableWorkflowExecutor(tmp_path / 'db', {'worker': handler}, WorkflowPolicy(frozenset({'worker'})))
        steps = [WorkflowStep('a', 'worker'), WorkflowStep('b', 'worker', ('a',))]
        args = dict(tenant_id='t', run_id='r', workflow_revision='isolated-v1')
        first = await engine.run(steps, **args)
        assert first.outputs == {'a': 1, 'b': 2}
        p.write_text('raise AssertionError("must not execute on replay")')
        second = await engine.run(steps, **args, expected_head=first.journal_head)
        assert first == second
    asyncio.run(run())
