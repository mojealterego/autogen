"""Opt-in research components. Requires PyTorch; never loaded by autogen_core root.

All five abstractions are research/protocol subsets, NOT a claim of complete
Titans, R3Mem, Meta CWM inference weights, LTNtorch or unrestricted RSI.
"""
from .._research_neural import (
    TitansMAL, TenantTitansBank, VirtualTokenCodec,
    HierarchicalR3Memory, HierarchicalMemoryPacket,
    LTNAxiomLearner, HardLTNGate, HardLTNVerdict, FiniteInvariantProver, FiniteProof,
)
from .._research_cwm import CWMClient, CWMRejected, CWMProposal
from .._research_rsi import RestrictedRSI, RSIDenied, RSIProposal, canonical_policy_source

__all__ = [
    'TitansMAL','TenantTitansBank','VirtualTokenCodec',
    'HierarchicalR3Memory','HierarchicalMemoryPacket',
    'LTNAxiomLearner','HardLTNGate','HardLTNVerdict','FiniteInvariantProver','FiniteProof',
    'CWMClient','CWMRejected','CWMProposal',
    'RestrictedRSI','RSIDenied','RSIProposal','canonical_policy_source',
]
