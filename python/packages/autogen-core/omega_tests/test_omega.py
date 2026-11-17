"""Dependency-free tests for the exact shipped module sources."""
import asyncio
import importlib.util
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'src' / 'autogen_core'

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

ct = load('_cancellation_token')

class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_reentrant_callback_and_failure_isolation(self):
        token = ct.CancellationToken()
        called = []
        def bad():
            raise ValueError('callback failed')
        token.add_callback(bad)
        token.add_callback(lambda: called.append(token.is_cancelled()))
        # A daemon thread allows a regression to fail without hanging the test suite.
        thread = threading.Thread(target=token.cancel, daemon=True)
        thread.start()
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(called, [True])

    async def test_cross_thread_future_cancellation(self):
        token = ct.CancellationToken()
        future = asyncio.get_running_loop().create_future()
        token.link_future(future)
        await asyncio.to_thread(token.cancel)
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(future, 0.5)

    async def test_completed_future_is_released(self):
        token = ct.CancellationToken()
        future = asyncio.get_running_loop().create_future()
        token.link_future(future)
        future.set_result(1)
        await asyncio.sleep(0)
        self.assertEqual(len(token._callbacks), 0)

class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = load('_governed_workflow')

    async def test_dependencies_and_audit(self):
        w = self.w
        async def inc(inputs): return inputs.get('a', 1) + 1
        engine = w.WorkflowExecutor({'inc': inc}, w.WorkflowPolicy(frozenset({'inc'})))
        result = await engine.run([w.WorkflowStep('b','inc',('a',)), w.WorkflowStep('a','inc')])
        self.assertEqual(result.outputs, {'a': 2, 'b': 3})
        self.assertTrue(w.verify_audit(result.audit))
        from dataclasses import replace
        self.assertFalse(w.verify_audit((replace(result.audit[0], status='fake'), *result.audit[1:])))

    async def test_cycle_denied_and_retry_requires_idempotency(self):
        w = self.w
        async def f(inputs): return 1
        engine = w.WorkflowExecutor({'f': f}, w.WorkflowPolicy(frozenset({'f'})))
        for steps in ([w.WorkflowStep('a','f',('a',))], [w.WorkflowStep('a','no')], [w.WorkflowStep('a','f',attempts=2)]):
            with self.assertRaises(ValueError): await engine.run(steps)

    async def test_retry_failure_and_skip(self):
        w = self.w
        calls = 0
        async def flaky(inputs):
            nonlocal calls
            calls += 1
            if calls == 1: raise w.RetryableError('transient')
            return 7
        async def bad(inputs): raise RuntimeError('secret')
        engine = w.WorkflowExecutor({'f': flaky,'bad':bad}, w.WorkflowPolicy(frozenset({'f','bad'}), retry_base=0))
        result = await engine.run([w.WorkflowStep('a','f',attempts=2,idempotent=True), w.WorkflowStep('b','bad'),w.WorkflowStep('c','f',('b',))])
        self.assertEqual(calls,2)
        self.assertEqual(result.outputs,{'a':7})
        self.assertEqual(result.statuses,{'a':'succeeded','b':'failed','c':'skipped'})
        self.assertNotIn('secret', repr(result.audit))

    async def test_limits_and_timeout(self):
        w = self.w
        async def slow(inputs): await asyncio.sleep(5)
        async def large(inputs): return 'x'*200
        policy = w.WorkflowPolicy(frozenset({'slow','large'}), max_result_bytes=32)
        engine = w.WorkflowExecutor({'slow':slow,'large':large},policy)
        result = await engine.run([w.WorkflowStep('a','slow',timeout=.01),w.WorkflowStep('b','large')])
        self.assertEqual(result.statuses,{'a':'timed_out','b':'failed'})

    async def test_parallelism_and_preflight_attempt_budget(self):
        w = self.w
        active = peak = 0
        async def f(inputs):
            nonlocal active, peak
            active += 1; peak = max(peak,active)
            await asyncio.sleep(.01)
            active -= 1
            return 1
        engine = w.WorkflowExecutor({'f':f}, w.WorkflowPolicy(frozenset({'f'}),max_parallel=2,max_attempts=5))
        with self.assertRaises(ValueError): await engine.run([w.WorkflowStep(str(i),'f') for i in range(6)])
        await engine.run([w.WorkflowStep(str(i),'f') for i in range(5)])
        self.assertEqual(peak,2)

if __name__ == '__main__': unittest.main()
