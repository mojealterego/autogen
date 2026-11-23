"""Bounded, read-only parser for the trace tokens specified in Meta CWM report.

This parses text, never evaluates Python or treats predicted state as truth.
Exact matching is only a metric against separately acquired trusted traces.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any


class CWMTraceRejected(ValueError):
    """Untrusted CWM trace violates framing, schema, or resource budget."""


@dataclass(frozen=True)
class CWMFrame:
    kind: str
    variables: dict[str, Any] | None
    action: str
    result: Any = None


@dataclass(frozen=True)
class CWMTrace:
    frames: tuple[CWMFrame, ...]
    digest: str


@dataclass(frozen=True)
class CWMTraceMatch:
    exact: bool
    matching_frames: int
    expected_frames: int
    predicted_frames: int
    reference_digest: str
    prediction_digest: str


_SEP='<|frame_sep|>'
_BEGIN='<|trace_context_start|>'
_ACTION='<|action_sep|>'
_ARG='<|arg_sep|>'
_EVENTS={'<|call_sep|>':'call','<|line_sep|>':'line',
         '<|return_sep|>':'return','<|exception_sep|>':'exception'}


def _unique(pairs:list[tuple[str,Any]])->dict[str,Any]:
    result={}
    for k,v in pairs:
        if k in result:raise ValueError('duplicate object key')
        result[k]=v
    return result


class CWMTraceParser:
    def __init__(self, *, max_frames:int=256, max_bytes:int=65536):
        if (type(max_frames) is not int or not 1<=max_frames<=2048 or
                type(max_bytes) is not int or not 256<=max_bytes<=1048576):
            raise ValueError('invalid trace budgets')
        self.max_frames=max_frames
        self.max_bytes=max_bytes

    def parse(self, source:str)->CWMTrace:
        if type(source) is not str or not source.endswith(_SEP):
            raise CWMTraceRejected('unbounded or incomplete CWM trace')
        try:
            encoded=source.encode('utf-8')
        except UnicodeError as exc:
            raise CWMTraceRejected('invalid trace Unicode') from exc
        if len(encoded)>self.max_bytes:
            raise CWMTraceRejected('unbounded CWM trace')
        if source.startswith(_BEGIN):source=source[len(_BEGIN):]
        if not source.startswith(_SEP):raise CWMTraceRejected('missing first frame delimiter')
        chunks=source.split(_SEP)
        if chunks[0] or chunks[-1] or not 1<=len(chunks)-2<=self.max_frames:
            raise CWMTraceRejected('invalid frame count or trailing content')
        frames=[]
        observed_call=False
        for token in chunks[1:-1]:
            event=next(((tag,name) for tag,name in _EVENTS.items() if token.startswith(tag)),None)
            if event is None:raise CWMTraceRejected('unknown trace event')
            tag,kind=event
            rest=token[len(tag):]
            if rest.count(_ACTION)!=1 or any(sep in rest for sep in (*_EVENTS,_BEGIN,_SEP)):
                raise CWMTraceRejected('invalid CWM action framing')
            left,action=rest.split(_ACTION,1)
            if not 0<len(action)<=4096 or '\x00' in action:
                raise CWMTraceRejected('invalid source-code line')
            result=None;variables=None
            if kind in ('call','line'):
                if _ARG in action:raise CWMTraceRejected('argument token in nonterminal frame')
                try:variables=json.loads(left,object_pairs_hook=_unique,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError,RecursionError,TypeError) as exc:raise CWMTraceRejected('invalid CWM local state JSON') from exc
                if (type(variables) is not dict or len(variables)>128 or
                        any(type(k) is not str or len(k)>128 or type(v) not in (str,int,float,bool,type(None)) or
                                (type(v) is str and len(v)>512) or
                                (type(v) is float and not math.isfinite(v)) for k,v in variables.items())):
                    raise CWMTraceRejected('unbounded local variable representation')
                if kind=='call':observed_call=True
            else:
                if not observed_call or left or action.count(_ARG)!=1:
                    raise CWMTraceRejected('terminal event without preceding call or result')
                action,payload=action.split(_ARG,1)
                try:result=json.loads(payload,object_pairs_hook=_unique,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError,RecursionError,TypeError) as exc:raise CWMTraceRejected('invalid terminal result') from exc
                if (type(result) not in (str,int,float,bool,type(None)) or
                        (type(result) is str and len(result)>512) or
                        (type(result) is float and not math.isfinite(result))):
                    raise CWMTraceRejected('unbounded or unsupported result value')
            frames.append(CWMFrame(kind,variables,action,result))
        digest=hashlib.sha256(source.encode('utf-8')).hexdigest()
        return CWMTrace(tuple(frames),digest)


def compare_cwm_trace(reference:str,predicted:str,*,parser:CWMTraceParser|None=None)->CWMTraceMatch:
    model=parser or CWMTraceParser()
    actual=model.parse(reference)
    guess=model.parse(predicted)
    matching=sum(a==b for a,b in zip(actual.frames,guess.frames))
    return CWMTraceMatch(actual.frames==guess.frames,matching,len(actual.frames),len(guess.frames),actual.digest,guess.digest)
