"""Real local cluster late reconciliation. No external LLM, malware or browser."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from autogen_core import (
    ClusterCoordinator, ClusterWorker, DurableWorkflowExecutor, Resources,
    TenantSpec, WorkerSpec, WorkflowPolicy, WorkflowStep,
    distributed_step_handler, distributed_step_reconciler,
)


async def demo() -> dict[str, object]:
    with TemporaryDirectory(prefix='omega13-') as directory:
        directory_path = Path(directory)
        quota = Resources(1, 128, 100)
        cluster = ClusterCoordinator(
            directory_path / 'cluster.db', quota,
            {'demo': TenantSpec(quota, frozenset({'compute'}))},
            {'worker': WorkerSpec(quota, frozenset({'demo'}), frozenset({'compute'}))},
        )
        calls = []

        async def dependents(inputs):
            calls.append('dependent')
            return {'value': inputs['remote']['value'] + 2}

        remote_handler = distributed_step_handler(
            cluster, 'demo', 'compute', quota, poll_interval=.002, poll_timeout=.02,
        )
        engine = DurableWorkflowExecutor(
            directory_path / 'workflow.db',
            {'remote': remote_handler, 'dependent': dependents},
            WorkflowPolicy(frozenset({'remote', 'dependent'}), deadline=10),
        )
        steps = (WorkflowStep('remote', 'remote'), WorkflowStep('dependent', 'dependent', ('remote',)))
        arguments = dict(tenant_id='demo', run_id='example', workflow_revision='v1')
        initial = await engine.run(steps, **arguments)
        assert initial.statuses == {'remote': 'uncertain', 'dependent': 'skipped'}

        async def compute(_):
            return {'value': 40}

        worker = ClusterWorker(cluster, cluster.register('worker'), {'compute': compute})
        assert (await worker.run_once()).state == 'succeeded'
        reconciled = await engine.reconcile(
            steps, **arguments, readers={'remote': distributed_step_reconciler(cluster, 'demo', 'compute')},
            expected_head=initial.journal_head,
        )
        finished = await engine.run(steps, **arguments, expected_head=reconciled.journal_head)
        assert finished.statuses == {'remote': 'succeeded', 'dependent': 'succeeded'}
        assert finished.outputs['dependent'] == {'value': 42}
        assert calls == ['dependent']
        return {
            'initial': initial.statuses, 'reconciled': reconciled.statuses,
            'final': finished.statuses, 'dependent_value': finished.outputs['dependent']['value'],
            'remote_resubmissions': 0, 'journal_head': finished.journal_head,
        }


if __name__ == '__main__':
    print(json.dumps(asyncio.run(demo()), sort_keys=True))
