"""OMEGA14 acceptance: real gradient updates, learned compression, scoped CWM, hard LTN, gated RSI."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import torch

from autogen_core._research_neural import TitansMAL, TenantTitansBank, VirtualTokenCodec, HierarchicalR3Memory, LTNAxiomLearner, HardLTNGate, FiniteInvariantProver
from autogen_core._research_cwm import CWMClient, CWMRejected
from autogen_core._research_rsi import RestrictedRSI, RSIDenied, canonical_policy_source
from autogen_core import SymbolicGate, SymbolicRule, EvolutionCase, EvolutionObservation, EvolutionPolicy


def test_mal_real_gradient_reduces_error_and_retains_attention_path():
    m=TitansMAL(3, learning_rate=.1, momentum=0, weight_decay=0)
    key=torch.tensor([1.,0.,0.]);target=torch.tensor([0.,.8,0.])
    before=torch.mean((m.recall(key)-target)**2).item()
    surprise=m.learn(key,target)
    for _ in range(40): m.learn(key,target)
    after=torch.mean((m.recall(key)-target)**2).item()
    assert surprise>0 and after<before*.02
    assert torch.allclose(m(key,torch.ones(3)),torch.ones(3)+m.recall(key))


def test_mal_bounds_and_gradient_reject_bad_data():
    m=TitansMAL(2)
    with pytest.raises(ValueError):m.learn(torch.tensor([1.,float('nan')]),torch.ones(2))
    with pytest.raises(ValueError):m.learn(torch.ones(3),torch.ones(3))
    with pytest.raises(ValueError):TitansMAL(8192)


def test_mal_tenant_checkpoint_integrity(tmp_path):
    bank=TenantTitansBank(tmp_path,2)
    a=bank.get('a');b=bank.get('b')
    a.learn(torch.tensor([1.,0.]),torch.tensor([1.,0.]))
    assert not torch.equal(a.recall(torch.tensor([1.,0.])),b.recall(torch.tensor([1.,0.])))
    digest=bank.save('a')
    restored=TenantTitansBank(tmp_path,2).load('a',expected_sha256=digest)
    assert torch.allclose(restored.recall(torch.tensor([1.,0.])),a.recall(torch.tensor([1.,0.])))
    with pytest.raises(ValueError):bank.get('../path')
    with pytest.raises(Exception):TenantTitansBank(tmp_path,2).load('a',expected_sha256='0'*64)


def test_r3_virtual_token_training_real_gradients_and_quality():
    torch.manual_seed(10)
    model=VirtualTokenCodec(vocabulary=10,length=4,slots=2,dimension=12)
    examples=torch.tensor([[1,2,3,4],[2,3,4,5],[5,4,3,2],[6,7,8,9]])
    initial=model.loss(examples).item()
    losses=model.train_examples(examples,steps=110,lr=.02,seed=7)
    final=model.loss(examples).item()
    assert losses and final<initial*.15
    packed=model.encode(examples[0]);assert packed.shape==(2,12)
    assert model.decode(packed).shape==(4,)
    assert 0<=model.reconstruction_accuracy(examples)<=1


def test_r3_data_bounds_and_not_promised_lossless():
    model=VirtualTokenCodec(vocabulary=16,length=3,slots=1,dimension=8)
    with pytest.raises(ValueError):model.encode(torch.tensor([1,2,999]))
    with pytest.raises(ValueError):model.train_examples(torch.ones((2,3),dtype=torch.long),steps=20001)
    assert model.latent_byte_count==32


def test_ltn_trainable_predicate_improves_axiom_satisfaction():
    learner=LTNAxiomLearner(2,seed=13)
    x=torch.tensor([[0.,0.],[0.,1.],[1.,0.],[1.,1.]])
    y=torch.tensor([0.,0.,0.,1.])
    before=learner.satisfaction(x,y)
    learner.fit(x,y,steps=180,lr=.15)
    after=learner.satisfaction(x,y)
    assert after>before+.15 and after>.8


def test_ltn_hard_symbolic_veto_overrides_neural_confidence():
    learner=LTNAxiomLearner(1,seed=1)
    with torch.no_grad():learner.predicate.weight.fill_(0);learner.predicate.bias.fill_(12)
    gate=HardLTNGate(learner,SymbolicGate({'authorized':'bool'},[SymbolicRule('access','authorized == True')]),minimum=.5)
    with pytest.raises(Exception):gate.require(torch.tensor([1.]),{'authorized':False})
    assert gate.require(torch.tensor([1.]),{'authorized':True}).accepted


class LocalCWM(BaseHTTPRequestHandler):
    content={'choices':[{'message':{'content':'{"actions":[{"operation":"increment","target":"counter","value":2}]}'}}]}
    paths=[]
    def do_POST(self):
        type(self).paths.append(self.path)
        n=int(self.headers['Content-Length']);body=json.loads(self.rfile.read(n))
        assert body['model']=='facebook/cwm'
        encoded=json.dumps(type(self).content).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    def log_message(self,*_):pass


@pytest.fixture
def cwm_server():
    class Handler(LocalCWM):pass
    Handler.paths=[]
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server,Handler
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


@pytest.mark.asyncio
async def test_cwm_actual_openai_wire_and_deterministic_simulation(cwm_server):
    server,handler=cwm_server
    c=CWMClient(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',model='facebook/cwm',allow_loopback_http=True)
    proposal=await c.propose({'counter':2},'increase counter')
    assert proposal.result.final_state=={'counter':4}
    assert len(proposal.actions)==1 and handler.paths==['/v1/chat/completions']
    assert proposal.model=='facebook/cwm'


@pytest.mark.asyncio
async def test_cwm_rejects_adversarial_json_and_forbidden_state(cwm_server):
    server,handler=cwm_server
    c=CWMClient(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',model='facebook/cwm',allow_loopback_http=True)
    handler.content={'choices':[{'message':{'content':'{"actions":[{"operation":"exec","target":"counter","value":0}]}'}}]}
    with pytest.raises(CWMRejected):await c.propose({'counter':1},'test')
    handler.content={'choices':[{'message':{'content':'{"actions":[{"operation":"set","target":"protected","value":0}]}'}}]}
    with pytest.raises(CWMRejected):await c.propose({'protected':1},'test',frozen_keys=frozenset({'protected'}))


def test_cwm_ssrf_and_unsafe_endpoint_rejected():
    for u in ['file:///etc/passwd','http://169.254.169.254/latest','http://evil.example/v1/chat/completions','https://user:pass@example.org/v1','http://127.0.0.1:9222/json/list']:
        with pytest.raises(ValueError):CWMClient(u,model='facebook/cwm',allow_loopback_http=True)


def _source(parallel=1,timeout=1000):
    return canonical_policy_source({'max_parallel':parallel,'retry_base_ms':100,'step_timeout_ms':timeout})


async def _evaluate(candidate, fixture):
    correct = bool(candidate.parameters['max_parallel']>=2)
    return EvolutionObservation(correct,100//candidate.parameters['max_parallel'],5)


@pytest.mark.asyncio
async def test_rsi_real_restricted_source_evolution_hmac_cas_and_rollback(tmp_path):
    key=b'admin-secret-only-outside-model-32-bytes'
    cases=tuple(EvolutionCase(f'case{i}',{'x':i}) for i in range(8))
    r=RestrictedRSI(tmp_path/'rsi.sqlite','tenant',_source(),cases,_evaluate,EvolutionPolicy(required_gain=0),operator_keys={'operator':key})
    proposal=await r.propose(_source(2))
    assert proposal.decision.passed
    assert r.active_source()==_source()  # evaluation never auto-activates
    with pytest.raises(RSIDenied):r.promote(proposal,operator='operator',signature='0'*64)
    signature=hmac.new(key,r.approval_payload(proposal,'promote'),hashlib.sha256).hexdigest()
    revision=r.promote(proposal,operator='operator',signature=signature)
    assert revision==1 and r.active_source()==_source(2)
    reopened=RestrictedRSI(tmp_path/'rsi.sqlite','tenant',_source(2),cases,_evaluate,EvolutionPolicy(required_gain=0),operator_keys={'operator':key})
    assert reopened.active_source()==_source(2)
    with pytest.raises(RSIDenied):r.promote(proposal,operator='operator',signature=signature)
    rollback_sig=hmac.new(key,r.rollback_payload(),hashlib.sha256).hexdigest()
    assert r.rollback(operator='operator',signature=rollback_sig)==2
    assert r.active_source()==_source()


@pytest.mark.asyncio
async def test_rsi_never_executes_generated_source_or_accepts_imports(tmp_path):
    key=b'operator-strong-secret-32-bytes-long'
    cases=tuple(EvolutionCase(f'c{i}',{}) for i in range(8))
    r=RestrictedRSI(tmp_path/'safe.sqlite','t',_source(),cases,_evaluate,EvolutionPolicy(required_gain=0),operator_keys={'op':key})
    for attack in ["import os\nPOLICY = {}", "POLICY = __import__('os').system('id')", "POLICY = {'max_parallel': 1}; open('/tmp/unsafe','w')", "POLICY = {'max_parallel': 9999}"]:
        with pytest.raises((RSIDenied,ValueError)):await r.propose(attack)
    assert not (tmp_path/'unsafe').exists()


@pytest.mark.asyncio
async def test_rsi_baseline_does_not_mutate_on_failed_evidence(tmp_path):
    key=b'operator-strong-secret-32-bytes-long'; cases=tuple(EvolutionCase(f'c{i}',{}) for i in range(8))
    r=RestrictedRSI(tmp_path/'safe.sqlite','t',_source(2),cases,_evaluate,EvolutionPolicy(required_gain=.01),operator_keys={'op':key})
    candidate=await r.propose(_source())
    assert not candidate.decision.passed
    sig=hmac.new(key,r.approval_payload(candidate,'promote'),hashlib.sha256).hexdigest()
    with pytest.raises(RSIDenied):r.promote(candidate,operator='op',signature=sig)
    assert r.active_source()==_source(2)


def test_r3_hierarchy_document_paragraph_entity_and_measurable_fidelity():
    torch.manual_seed(17)
    codec=VirtualTokenCodec(vocabulary=257,length=12,slots=2,dimension=8)
    packer=HierarchicalR3Memory(codec)
    stages={'document':['Ala ma kota'], 'paragraph':['Ala ma'], 'entity':['Ala']}
    packet=packer.compress(stages)
    assert set(packet.latents)=={'document','paragraph','entity'}
    assert len(packet.source_digest)==64
    reconstructed=packer.reconstruct(packet)
    assert all(len(reconstructed[level])==len(stages[level]) for level in stages)
    assert 0<=packer.fidelity(stages,reconstructed)<=1
    with pytest.raises(ValueError):packer.compress({'document':['X'*2048]})


def test_neural_codec_sha_pinned_checkpoint_roundtrip(tmp_path):
    torch.manual_seed(3)
    codec=VirtualTokenCodec(vocabulary=8,length=3,slots=1,dimension=4)
    tokens=torch.tensor([1,2,3])
    base=codec.encode(tokens).detach().clone()
    sha=codec.save(tmp_path/'codec.pt')
    loaded=VirtualTokenCodec.load(tmp_path/'codec.pt',expected_sha256=sha)
    assert torch.allclose(loaded.encode(tokens),base)
    with pytest.raises(ValueError):VirtualTokenCodec.load(tmp_path/'codec.pt',expected_sha256='0'*64)


@pytest.mark.asyncio
async def test_cwm_rejects_duplicate_keys_in_model_output(cwm_server):
    server,handler=cwm_server
    c=CWMClient(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',model='facebook/cwm',allow_loopback_http=True)
    handler.content={'choices':[{'message':{'content':'{"actions":[],"actions":[]}'}}]}
    with pytest.raises(CWMRejected):await c.propose({'counter':1},'test')


def test_rsi_source_is_actual_python_and_cannot_be_imported_via_devious_ast(tmp_path):
    source=_source(2)
    tree=__import__('ast').parse(source)
    assert len(tree.body)==1 and tree.body[0].targets[0].id=='POLICY'
    with pytest.raises(ValueError):canonical_policy_source({'max_parallel':0,'retry_base_ms':10,'step_timeout_ms':100})


@pytest.mark.asyncio
async def test_rsi_stale_multi_instance_cannot_commit(tmp_path):
    key=b'very-secure-operator-key-32bytes---'
    cases=tuple(EvolutionCase(f'c{i}',{}) for i in range(8))
    kwargs=dict(cases=cases,evaluator=_evaluate,policy=EvolutionPolicy(required_gain=0),operator_keys={'op':key})
    one=RestrictedRSI(tmp_path/'shared.db','t',_source(),**kwargs)
    two=RestrictedRSI(tmp_path/'shared.db','t',_source(),**kwargs)
    a=await one.propose(_source(2))
    b=await two.propose(_source(3))
    sig=hmac.new(key,one.approval_payload(a,'promote'),hashlib.sha256).hexdigest()
    assert one.promote(a,operator='op',signature=sig)==1
    stale=hmac.new(key,two.approval_payload(b,'promote'),hashlib.sha256).hexdigest()
    with pytest.raises(RSIDenied):two.promote(b,operator='op',signature=stale)
    assert one.active_source()==_source(2)


def test_finite_hard_invariant_prover_exhaustive_and_counterexample():
    g=SymbolicGate({'resource':'int'},[SymbolicRule('bounded','resource <= 3')])
    okay=FiniteInvariantProver(g,{'resource':(0,1,2,3)}).prove()
    assert okay.proved and okay.cases_checked==4 and okay.counterexample is None
    bad=FiniteInvariantProver(g,{'resource':(2,4)}).prove()
    assert not bad.proved and bad.counterexample=={'resource':4}
    assert okay.evidence_head!=bad.evidence_head
    with pytest.raises(ValueError):FiniteInvariantProver(g,{'resource':tuple(range(15))},max_cases=10)

@pytest.mark.asyncio
async def test_rsi_history_signature_detects_database_tampering(tmp_path):
    import sqlite3
    key=b'certified-external-operator-secret-32'
    cases=tuple(EvolutionCase(f'c{i}',{}) for i in range(8))
    kwargs=dict(cases=cases,evaluator=_evaluate,policy=EvolutionPolicy(required_gain=0),operator_keys={'op':key})
    r=RestrictedRSI(tmp_path/'rsi.db','tenant',_source(),**kwargs)
    p=await r.propose(_source(2))
    sig=hmac.new(key,r.approval_payload(p,'promote'),hashlib.sha256).hexdigest()
    r.promote(p,operator='op',signature=sig)
    assert r.verify_history()
    assert (tmp_path/'omega_rsi_tenant.py').read_text()==_source(2)
    with sqlite3.connect(tmp_path/'rsi.db') as db:
        db.execute("UPDATE omega_rsi_history SET approval_signature=? WHERE revision=1",('0'*64,))
    with pytest.raises(RSIDenied):r.verify_history()
    with pytest.raises(RSIDenied):RestrictedRSI(tmp_path/'rsi.db','tenant',_source(2),**kwargs)


def test_cwm_requires_pinned_production_hostname():
    with pytest.raises(ValueError):CWMClient('https://example.com/v1/chat/completions',model='facebook/cwm')
    assert CWMClient('https://models.example.com/v1/chat/completions',model='facebook/cwm',allowed_hosts=frozenset({'models.example.com'})).secure


def test_real_research_demo_executes_without_external_models():
    import os
    import subprocess
    import sys
    from pathlib import Path
    demo=Path(__file__).resolve().parents[4]/'docs/omega/research-cognition/demo.py'
    result=subprocess.run([sys.executable,str(demo)],capture_output=True,text=True,
                          check=True,timeout=30,env=os.environ.copy())
    report=json.loads(result.stdout)
    assert report['mal_error_after']<report['mal_error_before']
    assert report['ltn_satisfaction_after']>report['ltn_satisfaction_before']
    assert report['rsi_signed_audit_verified'] is True
    assert report['meta_cwm_32b_weights_executed'] is False
    assert report['privileged_operations_executed'] is False

@pytest.mark.asyncio
async def test_rsi_generates_bounded_candidate_search_but_cannot_self_approve(tmp_path):
    key=b'strong-external-signing-key-32-bytes'
    cases=tuple(EvolutionCase(f'c{i}',{}) for i in range(8))
    r=RestrictedRSI(tmp_path/'auto.db','tenant',_source(),cases,_evaluate,
                    EvolutionPolicy(required_gain=0),operator_keys={'op':key})
    best=await r.discover({'max_parallel':(2,3)})
    assert best.decision.passed and best.source==_source(3)
    assert r.active_source()==_source()
    with pytest.raises(RSIDenied):r.promote(best,operator='op',signature='0'*64)
    signature=hmac.new(key,r.approval_payload(best,'promote'),hashlib.sha256).hexdigest()
    assert r.promote(best,operator='op',signature=signature)==1
    with pytest.raises((RSIDenied,ValueError)):await r.discover({'max_parallel':tuple(range(100))})


def test_r3_exact_reconstruction_requires_proven_digest_match():
    torch.manual_seed(12)
    codec=VirtualTokenCodec(vocabulary=257,length=4,slots=1,dimension=8)
    hierarchy=HierarchicalR3Memory(codec)
    data={'document':['abc'],'paragraph':['abc'],'entity':['abc']}
    packet=hierarchy.compress(data)
    with pytest.raises(ValueError):hierarchy.reconstruct_verified(packet)
    hierarchy.train_texts(data,steps=100,lr=.03)
    trained=hierarchy.compress(data)
    assert hierarchy.reconstruct_verified(trained)==data
    # Tampered latent vectors must never be accepted as lossless reconstruction.
    corrupted=trained.latents['document'][0]
    corrupted.add_(1000)
    with pytest.raises(ValueError):hierarchy.reconstruct_verified(trained)


@pytest.mark.asyncio
async def test_cwm_rejects_empty_or_malformed_initial_state_without_dispatch(cwm_server):
    server,handler=cwm_server
    c=CWMClient(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',allow_loopback_http=True)
    with pytest.raises(CWMRejected):await c.propose({},'test')
    with pytest.raises(CWMRejected):await c.propose({'x':-1},'test')
    assert handler.paths==[]


@pytest.mark.asyncio
async def test_cwm_wrong_provider_model_claim_is_rejected(cwm_server):
    server,handler=cwm_server
    c=CWMClient(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',allow_loopback_http=True)
    handler.content={'model':'not-facebook-cwm','choices':[{'message':{'content':'{"actions":[{"operation":"read","target":"x"}]}'}}]}
    with pytest.raises(CWMRejected):await c.propose({'x':1},'test')
