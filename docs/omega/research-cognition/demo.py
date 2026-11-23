"""OMEGA 14 local research walkthrough: real torch learning, no live model or privileged tools."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import torch

from autogen_core.research import (
    TitansMAL, TenantTitansBank, VirtualTokenCodec, HierarchicalR3Memory,
    LTNAxiomLearner, HardLTNGate, FiniteInvariantProver,
    RestrictedRSI, canonical_policy_source,
)
from autogen_core import SymbolicGate, SymbolicRule, EvolutionCase, EvolutionObservation, EvolutionPolicy


async def demo() -> dict[str,object]:
    torch.set_num_threads(1)
    torch.manual_seed(7)
    with TemporaryDirectory(prefix='omega14-') as directory:
        root=Path(directory)
        memory=TenantTitansBank(root/'titans',3)
        mal=memory.get('t1')
        key=torch.tensor([1.,0.,0.]);target=torch.tensor([0.,.8,0.])
        before=float(torch.mean((mal.recall(key)-target)**2))
        for _ in range(40):surprise=mal.learn(key,target)
        after=float(torch.mean((mal.recall(key)-target)**2))
        checkpoint=memory.save('t1')
        memory.load('t1',expected_sha256=checkpoint)
        codec=VirtualTokenCodec(vocabulary=257,length=12,slots=2,dimension=8)
        levels={'document':['Ala ma kota'],'paragraph':['Ala ma'],'entity':['Ala']}
        hierarchy=HierarchicalR3Memory(codec)
        hierarchy.train_texts(levels,steps=70,lr=.03)
        reconstructed=hierarchy.reconstruct(hierarchy.compress(levels))
        fidelity=hierarchy.fidelity(levels,reconstructed)

        ltn=LTNAxiomLearner(1,seed=8)
        features=torch.tensor([[0.],[1.]])
        labels=torch.tensor([0.,1.])
        fuzzy_before=ltn.satisfaction(features,labels)
        ltn.fit(features,labels,steps=80,lr=.15)
        fuzzy_after=ltn.satisfaction(features,labels)
        hard=SymbolicGate({'authorized':'bool'},[SymbolicRule('mandatory','authorized == True')])
        formal=FiniteInvariantProver(hard,{'authorized':(True,False)}).prove()
        accepted=HardLTNGate(ltn,hard,minimum=.5).require(torch.tensor([1.]),{'authorized':True})

        async def evaluation(model,case):
            passes=model.parameters['max_parallel']>=2
            return EvolutionObservation(passes,100//model.parameters['max_parallel'],1)
        cases=tuple(EvolutionCase(f'case{i}',{'i':i}) for i in range(8))
        original={'max_parallel':1,'retry_base_ms':100,'step_timeout_ms':1000}
        keybytes=b'external-operator-only-test-key-very-long'
        manager=RestrictedRSI(root/'rsi.sqlite','t1',canonical_policy_source(original),cases,
                              evaluation,EvolutionPolicy(required_gain=0),operator_keys={'admin':keybytes})
        proposal=await manager.discover({'max_parallel':(2,3)})
        signature=hmac.new(keybytes,manager.approval_payload(proposal,'promote'),hashlib.sha256).hexdigest()
        revision=manager.promote(proposal,operator='admin',signature=signature)
        return {
            'mal_true_gradient_surprise':round(surprise,7),
            'mal_error_before':round(before,7),'mal_error_after':round(after,7),
            'mal_pinned_checkpoint':len(checkpoint)==64,
            'r3_decoder_measured_exact_fraction':fidelity,
            'r3_raw_episodic_backing_required_for_lossless':True,
            'ltn_satisfaction_before':round(fuzzy_before,5),
            'ltn_satisfaction_after':round(fuzzy_after,5),
            'ltngate_authorized_accepted':accepted.accepted,
            'bounded_proof_detects_counterexample':not formal.proved,
            'rsi_source_revision':revision,
            'rsi_selected_parallelism':proposal.decision.candidate.parameters['max_parallel'],
            'rsi_signed_audit_verified':manager.verify_history(),
            'meta_cwm_32b_weights_executed':False,
            'privileged_operations_executed':False,
        }


if __name__=='__main__':
    print(json.dumps(asyncio.run(demo()),sort_keys=True))
