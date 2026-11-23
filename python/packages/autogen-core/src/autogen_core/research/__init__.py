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

# OMEGA 15: real, optionally installed LTNtorch 1.0.2 API. Importing this
# package does not install, download, or silently vendor external sources.
from .._research_upstream import LTNtorchBridge, LTNtorchRejected, LTNtorchReport, LTNtorchDecision
from .._cwm_trace import CWMTraceParser, CWMTraceRejected, CWMFrame, CWMTrace, CWMTraceMatch, compare_cwm_trace
__all__.extend([
    'LTNtorchBridge', 'LTNtorchRejected', 'LTNtorchReport', 'LTNtorchDecision',
    'CWMTraceParser', 'CWMTraceRejected', 'CWMFrame', 'CWMTrace', 'CWMTraceMatch',
    'compare_cwm_trace',
])
