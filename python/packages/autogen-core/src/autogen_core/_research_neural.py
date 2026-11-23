"""OMEGA 14 research substrate: learned MAL-style memory, learned token bottleneck, differentiable axioms.

Actual PyTorch gradients and bounded model checkpoints. This is NOT a verified
reproduction of full Google Titans, R3Mem, or the upstream LTNtorch package.
Keep opt-in: importing autogen_core itself never imports PyTorch.
"""
from __future__ import annotations

import hashlib
import io
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from ._neurosymbolic_gate import SymbolicGate, SymbolicVerdict, SymbolicDenied

_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_MAX_CHECKPOINT = 16 * 1024 * 1024


def _tensor(value: Tensor, shape: tuple[int, ...] | None = None) -> Tensor:
    if not isinstance(value, Tensor) or (shape is not None and tuple(value.shape) != shape):
        raise ValueError('invalid tensor shape')
    if value.device.type != 'cpu' or not torch.is_floating_point(value) and value.dtype != torch.long:
        raise ValueError('only CPU float or long tensors supported')
    if not bool(torch.isfinite(value).all()):
        raise ValueError('non-finite tensor input')
    return value


def _atomic_save(path: Path, payload: dict[str, Any]) -> str:
    if path.exists() and path.is_symlink():
        raise ValueError('symlink checkpoint denied')
    storage = io.BytesIO()
    torch.save(payload, storage)
    raw = storage.getvalue()
    if len(raw) > _MAX_CHECKPOINT:
        raise ValueError('checkpoint exceeds size budget')
    fd, temp = tempfile.mkstemp(prefix='.omega14-', dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    return hashlib.sha256(raw).hexdigest()


def _verified_load(path: Path, digest: str) -> dict[str, Any]:
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or path.is_symlink() or path.stat().st_size > _MAX_CHECKPOINT:
        raise ValueError('untrusted checkpoint identity')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:
        raise ValueError('checkpoint digest mismatch')
    data=torch.load(io.BytesIO(raw), map_location='cpu',weights_only=True)
    if not isinstance(data,dict):
        raise ValueError('invalid checkpoint shape')
    return data


class TitansMAL(nn.Module):
    """A **linear, trainable** memory layer in a MAL-style residual path.

    Updates the memory matrix at test time with true MSE-loss gradients,
    per-step surprise gradient norm, momentum and weight decay. Does not
    implement the full paper's deep memory/attention/learned optimizer.
    """
    def __init__(self, dimension: int, *, learning_rate:float=.1, momentum:float=.5, weight_decay:float=.001) -> None:
        super().__init__()
        if (type(dimension) is not int or not 1<=dimension<=256 or
            any(type(x) not in (int,float) or not math.isfinite(x) for x in (learning_rate,momentum,weight_decay)) or
            not 0 < learning_rate <= 1 or not 0 <= momentum < 1 or not 0 <= weight_decay <= .25):
            raise ValueError('invalid bounded neural memory parameters')
        self.dimension=dimension
        self.learning_rate=float(learning_rate)
        self.momentum=float(momentum)
        self.weight_decay=float(weight_decay)
        self.register_buffer('memory',torch.zeros((dimension,dimension),dtype=torch.float32))
        self.register_buffer('velocity',torch.zeros((dimension,dimension),dtype=torch.float32))
        self.updates=0

    def recall(self, key: Tensor) -> Tensor:
        _tensor(key,(self.dimension,))
        return self.memory @ key.to(dtype=torch.float32)

    def forward(self, key: Tensor, attention_output: Tensor) -> Tensor:
        _tensor(attention_output,(self.dimension,))
        return attention_output + self.recall(key)

    def learn(self, key: Tensor, value: Tensor) -> float:
        _tensor(key,(self.dimension,));_tensor(value,(self.dimension,))
        query=key.to(torch.float32).detach()
        goal=value.to(torch.float32).detach()
        matrix=self.memory.detach().clone().requires_grad_(True)
        loss=F.mse_loss(matrix@query,goal)
        grad=torch.autograd.grad(loss,matrix)[0]
        surprise=float(grad.norm().item())
        if not math.isfinite(surprise):raise ValueError('nonfinite surprise gradient')
        clipped=grad*min(1.,100./max(surprise,1e-12))
        with torch.no_grad():
            new_velocity=self.momentum*self.velocity+clipped
            updated=(1-self.weight_decay)*self.memory-self.learning_rate*new_velocity
            if not bool(torch.isfinite(updated).all()):
                raise ValueError('nonfinite neural state denied')
            self.memory.copy_(updated)
            self.velocity.copy_(new_velocity)
            self.updates+=1
        return surprise

    def export_state(self) -> dict[str,Any]:
        return {'format':'omega-mal-v1','dimension':self.dimension,'rate':self.learning_rate,
                'momentum':self.momentum,'decay':self.weight_decay,'updates':self.updates,
                'memory':self.memory.detach().clone(),'velocity':self.velocity.detach().clone()}

    @classmethod
    def from_state(cls,state:dict[str,Any]) -> 'TitansMAL':
        if not isinstance(state,dict) or state.get('format')!='omega-mal-v1':
            raise ValueError('unknown neural checkpoint schema')
        obj=cls(state['dimension'],learning_rate=state['rate'],momentum=state['momentum'],weight_decay=state['decay'])
        _tensor(state['memory'],(obj.dimension,obj.dimension))
        _tensor(state['velocity'],(obj.dimension,obj.dimension))
        if type(state['updates']) is not int or not 0<=state['updates']<=10**9:
            raise ValueError('invalid update count')
        with torch.no_grad():
            obj.memory.copy_(state['memory']);obj.velocity.copy_(state['velocity'])
        obj.updates=state['updates']
        return obj


class TenantTitansBank:
    """Local trusted tenancy with SHA-pinned checkpoint files, no global shared weights."""
    def __init__(self, directory:str|Path,dimension:int):
        if type(dimension) is not int or not 1<=dimension<=256:
            raise ValueError('invalid dimension')
        self.directory=Path(directory).resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.dimension=dimension
        self._models:dict[str,TitansMAL]={}

    def _path(self,tenant:str)->Path:
        if type(tenant) is not str or _ID.fullmatch(tenant) is None:
            raise ValueError('invalid tenant key')
        return self.directory / f'titans-{tenant}.pt'

    def get(self,tenant:str)->TitansMAL:
        self._path(tenant)
        if tenant not in self._models:self._models[tenant]=TitansMAL(self.dimension)
        return self._models[tenant]

    def save(self,tenant:str)->str:
        return _atomic_save(self._path(tenant),self.get(tenant).export_state())

    def load(self,tenant:str,*,expected_sha256:str)->TitansMAL:
        state=_verified_load(self._path(tenant),expected_sha256)
        model=TitansMAL.from_state(state)
        if model.dimension!=self.dimension:raise ValueError('memory dimension mismatch')
        self._models[tenant]=model
        return model


class VirtualTokenCodec(nn.Module):
    """Small *learned* sequence-to-K-virtual-tokens encoder and decoder.

    R3Mem-inspired supervised memory bottleneck: decoder reconstruction is
    explicitly lossy unless empirically perfect on measured samples. Stored
    episodic originals remain authoritative for lossless recovery.
    """
    def __init__(self,*,vocabulary:int,length:int,slots:int,dimension:int):
        super().__init__()
        vals=(vocabulary,length,slots,dimension)
        if (any(type(x) is not int for x in vals) or not 2<=vocabulary<=4096
            or not 1<=length<=128 or not 1<=slots<=length or not 2<=dimension<=128
            or length*vocabulary*slots*dimension>4_000_000):
            raise ValueError('unbounded virtual token model')
        self.vocabulary,self.length,self.slots,self.dimension=vals
        self.embedding=nn.Embedding(vocabulary,dimension)
        self.encoder=nn.Linear(length*dimension,slots*dimension)
        self.decoder=nn.Linear(slots*dimension,length*vocabulary)

    @property
    def latent_byte_count(self)->int:
        return self.slots*self.dimension*4

    def _tokens(self,tokens:Tensor,batch:bool)->Tensor:
        shape=(None,self.length) if batch else (self.length,)
        if not isinstance(tokens,Tensor) or tokens.dtype!=torch.long or tokens.device.type!='cpu' or tokens.ndim!=len(shape):
            raise ValueError('invalid token tensor')
        if tuple(tokens.shape)[-1]!=self.length or (batch and not 1<=tokens.shape[0]<=512):
            raise ValueError('unbounded input sequence')
        if not bool(((tokens>=0)&(tokens<self.vocabulary)).all()):
            raise ValueError('token outside fixed vocabulary')
        return tokens

    def encode(self,tokens:Tensor)->Tensor:
        self._tokens(tokens,False)
        return self.encoder(self.embedding(tokens).reshape(-1)).reshape(self.slots,self.dimension)

    def decode(self,virtual_tokens:Tensor)->Tensor:
        _tensor(virtual_tokens,(self.slots,self.dimension))
        return self.decoder(virtual_tokens.reshape(-1)).reshape(self.length,self.vocabulary).argmax(dim=-1)

    def loss(self,tokens:Tensor)->Tensor:
        x=self._tokens(tokens,True)
        z=self.encoder(self.embedding(x).reshape(len(x),-1))
        logits=self.decoder(z).reshape(len(x),self.length,self.vocabulary)
        return F.cross_entropy(logits.reshape(-1,self.vocabulary),x.reshape(-1))

    def train_examples(self,tokens:Tensor,*,steps:int,lr:float=.01,seed:int=0)->tuple[float,...]:
        self._tokens(tokens,True)
        if (type(steps) is not int or not 1<=steps<=2048 or type(seed) is not int or not 0<=seed<2**32
            or type(lr) not in (float,int) or not math.isfinite(lr) or not 0<lr<=.2):
            raise ValueError('invalid training budget')
        optimizer=torch.optim.AdamW(self.parameters(),lr=lr,weight_decay=0)
        losses=[]
        for _ in range(steps):
            optimizer.zero_grad(set_to_none=True)
            loss=self.loss(tokens)
            if not torch.isfinite(loss):raise ValueError('nonfinite training loss')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.parameters(),max_norm=10)
            optimizer.step()
            losses.append(float(loss.item()))
        return tuple(losses)

    def reconstruction_accuracy(self,tokens:Tensor)->float:
        self._tokens(tokens,True)
        with torch.no_grad():
            predictions=torch.stack([self.decode(self.encode(row)) for row in tokens])
            return float((predictions==tokens).float().mean().item())

    def save(self,path:str|Path)->str:
        p=Path(path)
        return _atomic_save(p,{'format':'omega-virtual-memory-v1','settings':(self.vocabulary,self.length,self.slots,self.dimension),
                                'weights':{k:v.detach().clone() for k,v in self.state_dict().items()}})

    @classmethod
    def load(cls,path:str|Path,*,expected_sha256:str)->'VirtualTokenCodec':
        state=_verified_load(Path(path),expected_sha256)
        if state.get('format')!='omega-virtual-memory-v1':raise ValueError('wrong codec checkpoint')
        settings=state['settings']
        if not isinstance(settings,(list,tuple)) or len(settings)!=4:raise ValueError('corrupt codec settings')
        obj=cls(vocabulary=settings[0],length=settings[1],slots=settings[2],dimension=settings[3])
        weights=state['weights']
        if not isinstance(weights,dict) or set(weights)!=set(obj.state_dict()):raise ValueError('invalid codec weights')
        for k,expected in obj.state_dict().items():
            _tensor(weights[k],tuple(expected.shape))
        obj.load_state_dict(weights,strict=True)
        return obj


class LTNAxiomLearner(nn.Module):
    """Trainable sigmoid predicate and fuzzy universal quantifier, not a proof system."""
    def __init__(self,features:int,*,seed:int=0):
        super().__init__()
        if type(features) is not int or not 1<=features<=256 or type(seed) is not int or not 0<=seed<2**32:
            raise ValueError('invalid predicate size or seed')
        self.features=features
        self.predicate=nn.Linear(features,1)
        generator=torch.Generator().manual_seed(seed)
        with torch.no_grad():
            self.predicate.weight.uniform_(-.2,.2,generator=generator)
            self.predicate.bias.zero_()

    def _examples(self,x:Tensor,y:Tensor)->tuple[Tensor,Tensor]:
        _tensor(x);_tensor(y)
        if (x.ndim!=2 or x.shape[1]!=self.features or not 1<=x.shape[0]<=2048
                or y.ndim!=1 or y.shape[0]!=x.shape[0] or not bool(((y==0)|(y==1)).all())):
            raise ValueError('invalid predicate evidence')
        return x.float(),y.float()

    def forward(self,x:Tensor)->Tensor:
        if not isinstance(x,Tensor) or x.ndim not in (1,2) or x.shape[-1]!=self.features:
            raise ValueError('invalid predicate input')
        _tensor(x)
        return torch.sigmoid(self.predicate(x.float())).squeeze(-1)

    def satisfaction(self,x:Tensor,y:Tensor,p:int=2)->float:
        x,y=self._examples(x,y)
        if type(p) is not int or not 1<=p<=8:raise ValueError('invalid fuzzy quantifier')
        with torch.no_grad():
            prob=self(x)
            truth=prob*y+(1-prob)*(1-y)
            # LTN pMeanError universal quantifier.
            return float((1-torch.mean((1-truth).pow(p)).pow(1/p)).item())

    def fit(self,x:Tensor,y:Tensor,*,steps:int=100,lr:float=.1)->tuple[float,...]:
        x,y=self._examples(x,y)
        if type(steps) is not int or not 1<=steps<=2048 or type(lr) not in (int,float) or not math.isfinite(lr) or not 0<lr<=.5:
            raise ValueError('invalid LTN training budget')
        opt=torch.optim.Adam(self.parameters(),lr=lr)
        series=[]
        for _ in range(steps):
            opt.zero_grad(set_to_none=True)
            loss=F.binary_cross_entropy(self(x),y)
            if not torch.isfinite(loss):raise ValueError('nonfinite LTN loss')
            loss.backward();opt.step()
            series.append(float(loss.item()))
        return tuple(series)


@dataclass(frozen=True)
class HardLTNVerdict:
    accepted:bool
    fuzzy_score:float
    symbolic:SymbolicVerdict


class HardLTNGate:
    """Fuzzy neural assessment NEVER overrides deterministic SymbolicGate veto."""
    def __init__(self,neural:LTNAxiomLearner,hard:SymbolicGate,*,minimum:float=.8):
        if (not isinstance(neural,LTNAxiomLearner) or not isinstance(hard,SymbolicGate) or
            type(minimum) not in (int,float) or not math.isfinite(minimum) or not 0<minimum<=1):
            raise ValueError('invalid joint gate')
        self.neural,self.hard,self.minimum=neural,hard,float(minimum)

    def require(self,features:Tensor,context:dict[str,Any])->HardLTNVerdict:
        verified=self.hard.require(context)
        with torch.no_grad():
            score=float(self.neural(features).item())
        if not math.isfinite(score) or score<self.minimum:
            raise SymbolicDenied('learned predicate below threshold; not sufficient for authorization')
        return HardLTNVerdict(True,score,verified)

@dataclass(frozen=True)
class HierarchicalMemoryPacket:
    """Neural latents only; not sufficient to guarantee exact text recovery."""
    latents:dict[str,tuple[Tensor,...]]
    source_digest:str
    latent_digest:str


class HierarchicalR3Memory:
    """Bounded UTF-8 document/paragraph/entity layers over the learned codec.

    No original text is stored in virtual-token packets. Use the existing
    EpisodicStore as the trusted **lossless backing store** where exact recall
    is required. Report learned decoder fidelity instead of claiming reversibility.
    """
    levels=('document','paragraph','entity')

    def __init__(self,codec:VirtualTokenCodec):
        if not isinstance(codec,VirtualTokenCodec) or codec.vocabulary!=257:
            raise ValueError('hierarchical UTF-8 codec requires 256 bytes and one PAD token')
        self.codec=codec

    def _tokens(self,text:str)->Tensor:
        if type(text) is not str or '\x00' in text:
            raise ValueError('invalid UTF-8 text')
        try:raw=text.encode('utf-8',errors='strict')
        except UnicodeError as exc:raise ValueError('invalid UTF-8 source') from exc
        if not 1<=len(raw)<=self.codec.length:
            raise ValueError('text exceeds bounded learned decoder length')
        return torch.tensor(list(raw)+[256]*(self.codec.length-len(raw)),dtype=torch.long)

    def _validate(self,content:dict[str,list[str]])->None:
        if (not isinstance(content,dict) or set(content)!=set(self.levels) or
            any(type(texts) not in (tuple,list) or not 1<=len(texts)<=32 for texts in content.values()) or
            sum(map(len,content.values()))>64):
            raise ValueError('invalid bounded document hierarchy')
        for level in self.levels:
            for text in content[level]:self._tokens(text)

    def train_texts(self,content:dict[str,list[str]],*,steps:int,lr:float=.01,seed:int=0)->tuple[float,...]:
        self._validate(content)
        samples=torch.stack([self._tokens(text) for level in self.levels for text in content[level]])
        return self.codec.train_examples(samples,steps=steps,lr=lr,seed=seed)

    def _latents_digest(self,latents:dict[str,tuple[Tensor,...]])->str:
        digest=hashlib.sha256(b'omega14-hierarchical-latents-v1')
        for level in self.levels:
            for item in latents[level]:
                _tensor(item,(self.codec.slots,self.codec.dimension))
                digest.update(level.encode('ascii')+b'\x00')
                digest.update(item.detach().to(torch.float32).contiguous().numpy().tobytes())
        return digest.hexdigest()

    def compress(self,content:dict[str,list[str]])->HierarchicalMemoryPacket:
        self._validate(content)
        raw={level:list(content[level]) for level in self.levels}
        with torch.no_grad():
            latents={level:tuple(self.codec.encode(self._tokens(t)).detach().clone()
                                 for t in content[level]) for level in self.levels}
        from ._governed_workflow import _canonical
        return HierarchicalMemoryPacket(latents,hashlib.sha256(_canonical(raw)).hexdigest(),self._latents_digest(latents))

    def reconstruct(self,packet:HierarchicalMemoryPacket)->dict[str,list[str]]:
        if (not isinstance(packet,HierarchicalMemoryPacket) or set(packet.latents)!=set(self.levels)
                or not re.fullmatch(r'[a-f0-9]{64}',packet.source_digest)):
            raise ValueError('invalid virtual memory packet')
        if not re.fullmatch(r'[a-f0-9]{64}',packet.latent_digest) or self._latents_digest(packet.latents)!=packet.latent_digest:
            raise ValueError('virtual memory latent integrity mismatch')
        result={}
        with torch.no_grad():
            for level in self.levels:
                if not 1<=len(packet.latents[level])<=32:raise ValueError('invalid packet length')
                output=[]
                for latent in packet.latents[level]:
                    token_ids=self.codec.decode(latent).tolist()
                    while token_ids and token_ids[-1]==256:token_ids.pop()
                    output.append(bytes(v for v in token_ids if v<256).decode('utf-8',errors='replace'))
                result[level]=output
        return result

    def reconstruct_verified(self,packet:HierarchicalMemoryPacket)->dict[str,list[str]]:
        """Permit exact retrieval only when learned decode matches trusted hash.

        Without a matching digest, a caller MUST fetch the original from
        EpisodicStore rather than treating virtual tokens as lossless.
        """
        from ._governed_workflow import _canonical
        result=self.reconstruct(packet)
        if hashlib.sha256(_canonical(result)).hexdigest()!=packet.source_digest:
            raise ValueError('learned R3 reconstruction not exact; use lossless backing store')
        return result

    def fidelity(self,source:dict[str,list[str]],decoded:dict[str,list[str]])->float:
        self._validate(source)
        if not isinstance(decoded,dict) or set(decoded)!=set(self.levels) or any(
                type(decoded[x]) is not list or len(decoded[x])!=len(source[x])
                for x in self.levels):
            raise ValueError('invalid reconstructed hierarchy')
        matches=sum(a==b for level in self.levels for a,b in zip(source[level],decoded[level]))
        return matches/sum(map(len,source.values()))

@dataclass(frozen=True)
class FiniteProof:
    """Exact proof ONLY for the explicitly enumerated finite state domain."""
    proved:bool
    cases_checked:int
    counterexample:dict[str,Any]|None
    evidence_head:str


class FiniteInvariantProver:
    """Exhaustively check hard invariants on bounded user-provided finite domains.

    This is actual finite-state enumeration, NOT unbounded SMT completeness.
    LTN probabilities are deliberately excluded from the proof predicate.
    """
    def __init__(self,gate:SymbolicGate,domains:dict[str,tuple[Any,...]],*,max_cases:int=4096):
        if (not isinstance(gate,SymbolicGate) or not isinstance(domains,dict)
                or set(domains)!=set(gate._variables) or
                type(max_cases) is not int or not 1<=max_cases<=100_000):
            raise ValueError('invalid finite safety domain')
        total=1
        for key,values in domains.items():
            if not isinstance(values,tuple) or not 1<=len(values)<=100:
                raise ValueError('invalid finite safety values')
            total*=len(values)
            if total>max_cases:raise ValueError('finite state space exceeds proof budget')
            for value in values:gate._context({k: (value if k==key else domains[k][0]) for k in domains})
        self.gate,self.domains,self.max_cases=gate,domains,max_cases

    def prove(self)->FiniteProof:
        import itertools
        from ._governed_workflow import _canonical
        names=tuple(sorted(self.domains))
        result=[];first=None
        for values in itertools.product(*(self.domains[name] for name in names)):
            context=dict(zip(names,values))
            verdict=self.gate.evaluate(context)
            result.append([context,verdict.accepted,verdict.evidence_head])
            if not verdict.accepted and first is None:first=context
        digest=hashlib.sha256(_canonical(['finite-proof-v1',result])).hexdigest()
        return FiniteProof(first is None,len(result),first,digest)
