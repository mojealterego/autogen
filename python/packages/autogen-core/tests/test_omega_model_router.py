import asyncio

import pytest

from autogen_core import (
    AdaptiveModelRouter, ModelProfile, ModelResponse, RouteRequest, TenantRoutePolicy,
    ModelAdmissionDenied, ModelOutcomeUnknown, RetryableModelError,
    NoModelAvailable, DurableWorkflowExecutor, WorkflowStep, WorkflowPolicy,
)


def profile(name, *, quality=800, latency=200, price=1000000, capabilities=frozenset({'text'})):
    return ModelProfile(name=name, capabilities=capabilities, context_window=8192,
        quality=quality, baseline_latency_ms=latency,
        input_micro_usd_per_million=price, output_micro_usd_per_million=price)


def request(tenant='t', **kw):
    return RouteRequest(tenant=tenant, capability='text', input_tokens=100,
                        max_output_tokens=200, payload={'prompt':'hello'}, **kw)


def policy(models=frozenset({'a','b'}), *, budget=10_000, parallel=2):
    return TenantRoutePolicy(models, budget_micro_usd=budget, max_inflight=parallel)


@pytest.mark.asyncio
async def test_routing_uses_quality_latency_and_context_caps():
    async def run(req):return ModelResponse({'ok':True},req.input_tokens,10)
    router=AdaptiveModelRouter({'a':(profile('a', quality=900),run),
                                'b':(profile('b',quality=700),run)}, {'t':policy()})
    result=await router.route(request())
    assert result.model=='a'
    assert result.charged_micro_usd==110
    assert router.snapshot().by_tenant['t'].spent_micro_usd==110
    with pytest.raises(ModelAdmissionDenied): await router.route(request(tenant='unknown'))
    with pytest.raises(ModelAdmissionDenied): await router.route(RouteRequest('t','vision',100,200,{}))
    with pytest.raises(ModelAdmissionDenied): await router.route(RouteRequest('t','text',9000,200,{}))


@pytest.mark.asyncio
async def test_adapts_to_observed_latency_without_fabricated_benchmarks():
    time=[1.0]
    async def slow(req):
        time[0]+=5
        return ModelResponse({'model':'a'},req.input_tokens,10)
    async def fast(req):
        time[0]+=.01
        return ModelResponse({'model':'b'},req.input_tokens,10)
    router=AdaptiveModelRouter({'a':(profile('a',quality=900,latency=100),slow),
                                'b':(profile('b',quality=895,latency=100),fast)}, {'t':policy()},clock=lambda:time[0])
    assert (await router.route(request())).model=='a'
    assert (await router.route(request())).model=='b'
    assert router.snapshot().by_model['a'].latency_ms==5000


@pytest.mark.asyncio
async def test_safe_failover_only_for_explicit_retryable_error():
    seen=[]
    async def a(req):seen.append('a');raise RetryableModelError('safe pre-dispatch failure')
    async def b(req):seen.append('b');return ModelResponse('ok',req.input_tokens,1)
    router=AdaptiveModelRouter({'a':(profile('a',quality=900),a),'b':(profile('b',quality=700),b)},
                                {'t':policy()},circuit_threshold=1,circuit_cooldown=10)
    result=await router.route(request(allow_safe_fallback=True))
    assert result.model=='b' and result.attempted_models==('a','b')
    assert seen==['a','b']
    assert router.snapshot().by_model['a'].circuit_open
    assert (await router.route(request())).model=='b'


@pytest.mark.asyncio
async def test_unknown_exception_does_not_auto_fallback_and_reserves_charge():
    called=[]
    async def a(req):called.append('a');raise ConnectionError('possible billed completion')
    async def b(req):called.append('b');return ModelResponse('no',req.input_tokens,1)
    router=AdaptiveModelRouter({'a':(profile('a',quality=900),a),'b':(profile('b',quality=700),b)},
                                {'t':policy()})
    with pytest.raises(ModelOutcomeUnknown): await router.route(request(allow_safe_fallback=True))
    assert called==['a']
    assert router.snapshot().by_tenant['t'].spent_micro_usd==300
    assert router.snapshot().by_tenant['t'].inflight==0


@pytest.mark.asyncio
async def test_tenant_budget_reserved_before_dispatch_and_partial_refund():
    calls=[]
    async def work(req):calls.append(1);return ModelResponse({'answer':1},req.input_tokens,1)
    router=AdaptiveModelRouter({'a':(profile('a'),work)}, {'t':policy(frozenset({'a'}),budget=450)})
    assert (await router.route(request())).charged_micro_usd==101
    assert router.snapshot().by_tenant['t'].spent_micro_usd==101
    assert (await router.route(request())).charged_micro_usd==101
    with pytest.raises(ModelAdmissionDenied):await router.route(request())
    assert len(calls)==2


@pytest.mark.asyncio
async def test_concurrent_budget_and_inflight_rejected_without_leak():
    start=asyncio.Event();release=asyncio.Event()
    async def f(req):start.set();await release.wait();return ModelResponse(1,req.input_tokens,1)
    router=AdaptiveModelRouter({'a':(profile('a'),f)}, {'t':policy(frozenset({'a'}),budget=400,parallel=1)},max_global_inflight=1)
    task=asyncio.create_task(router.route(request()))
    await start.wait()
    assert router.snapshot().by_tenant['t'].reserved_micro_usd==300
    with pytest.raises(ModelAdmissionDenied):await router.route(request())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    snap=router.snapshot().by_tenant['t']
    assert snap.inflight==0 and snap.reserved_micro_usd==0 and snap.spent_micro_usd==300


@pytest.mark.asyncio
async def test_invalid_output_fail_closed_and_no_budget_refund():
    async def bad(req):return ModelResponse({'x':float('nan')},req.input_tokens,5)
    router=AdaptiveModelRouter({'a':(profile('a'),bad)}, {'t':policy(frozenset({'a'}))})
    with pytest.raises(ModelOutcomeUnknown):await router.route(request())
    assert router.snapshot().by_tenant['t'].spent_micro_usd==300


@pytest.mark.asyncio
async def test_isolated_tenant_quotas_and_allowlists():
    async def f(req):return ModelResponse(req.tenant,req.input_tokens,1)
    router=AdaptiveModelRouter({'a':(profile('a'),f),'b':(profile('b'),f)},
       {'t':policy(frozenset({'a'}),budget=350),
        's':policy(frozenset({'b'}),budget=350)})
    assert (await router.route(request('t'))).model=='a'
    assert (await router.route(request('s'))).model=='b'
    assert router.snapshot().by_tenant['t'].spent_micro_usd==101
    assert router.snapshot().by_tenant['s'].spent_micro_usd==101


@pytest.mark.asyncio
async def test_real_durable_workflow_integration(tmp_path):
    calls=[]
    async def provider(req):
        calls.append(req.payload)
        return ModelResponse({'accepted':True},req.input_tokens,1)
    router=AdaptiveModelRouter({'a':(profile('a'),provider)}, {'t':policy(frozenset({'a'}))})
    async def handler(inputs):
        return (await router.route(RouteRequest('t','text',10,10,dict(inputs)))).response.content
    e=DurableWorkflowExecutor(tmp_path/'db',{'llm':handler},WorkflowPolicy(frozenset({'llm'})))
    args=dict(tenant_id='t',run_id='run',workflow_revision='router-v1')
    result=await e.run([WorkflowStep('generate','llm')],**args)
    assert result.outputs=={'generate':{'accepted':True}}
    assert await e.run([WorkflowStep('generate','llm')],**args,expected_head=result.journal_head)==result
    assert len(calls)==1


def test_route_request_isolated_from_mutated_external_payload():
    original={'messages':[{'role':'user','content':'first'}]}
    req=RouteRequest('t','text',100,100,original)
    original['messages'][0]['content']='replaced'
    assert req.payload['messages'][0]['content']=='first'


@pytest.mark.asyncio
async def test_circuit_recovers_after_cooldown_with_deterministic_clock():
    tick=[100.0]
    calls=[]
    async def first(req):
        calls.append('a')
        if len(calls)==1:
            raise RetryableModelError('before dispatch')
        return ModelResponse('a',req.input_tokens,1)
    async def other(req):
        calls.append('b')
        return ModelResponse('b',req.input_tokens,1)
    router=AdaptiveModelRouter({'a':(profile('a',quality=900),first),
                                'b':(profile('b',quality=700),other)},
                               {'t':policy()},clock=lambda:tick[0],circuit_threshold=1,circuit_cooldown=10)
    assert (await router.route(request(allow_safe_fallback=True))).model=='b'
    assert (await router.route(request())).model=='b'
    tick[0]+=10.01
    assert (await router.route(request())).model=='a'
    assert calls==['a','b','b','a']
