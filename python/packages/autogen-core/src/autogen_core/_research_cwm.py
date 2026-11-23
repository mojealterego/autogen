"""OMEGA 14: strict Meta CWM compatible inference transport, not bundled model weights.

The *configured*, trusted OpenAI-compatible service must actually serve
facebook/cwm. This client neither executes untrusted code nor infers model
identity from an HTTP response. Model text becomes bounded, typed WorldAction.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import ssl
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from ._cognitive_ooda import WorldAction, WorldModel, SimulationOutcome, WorldPolicyDenied
from ._governed_workflow import _canonical


class CWMRejected(RuntimeError):
    """Provider response, declarative plan or simulated invariants rejected."""


@dataclass(frozen=True)
class CWMProposal:
    model:str
    actions:tuple[WorldAction,...]
    result:SimulationOutcome
    response_digest:str


def _unique(pairs:list[tuple[str,Any]])->dict[str,Any]:
    out={}
    for key,value in pairs:
        if key in out:raise ValueError('duplicate JSON key')
        out[key]=value
    return out


class CWMClient:
    """Trusted fixed endpoint; bounded real HTTP exchange and declarative-only result.

    HTTP loopback exception is for local integration tests; production HTTPS
    hostname must be pre-authorized by operator. No arbitrary URL or tool calls
    may be chosen by model. External sandbox required for executable programs.
    """
    def __init__(self,endpoint:str,*,model:str='facebook/cwm',allowed_hosts:frozenset[str]|None=None,
                 allow_loopback_http:bool=False,auth_env:str|None=None,timeout:float=8,max_response_bytes:int=65536):
        if type(endpoint) is not str or len(endpoint)>2048 or type(model) is not str or not 1<=len(model)<=128:
            raise ValueError('invalid fixed model transport')
        u=urlsplit(endpoint)
        if (u.username is not None or u.password is not None or u.fragment or u.query or u.path!='/v1/chat/completions'
                or u.scheme not in ('https','http') or not u.hostname or
                type(allow_loopback_http) is not bool):
            raise ValueError('unsupported model endpoint')
        host=u.hostname.lower()
        loopback=host=='127.0.0.1'
        try:
            address=ipaddress.ip_address(host)
            if not loopback or address.version!=4:raise ValueError('non-loopback IP disallowed')
        except ValueError:
            if re.fullmatch(r'[0-9.]+',host) or not re.fullmatch(r'[a-z0-9.-]+',host):
                raise ValueError('untrusted hostname')
            if not loopback and (host.endswith('.local') or host.endswith('.internal') or '.' not in host):
                raise ValueError('untrusted private hostname')
        if (u.scheme=='http' and (not loopback or not allow_loopback_http)) or (u.scheme=='https' and loopback):
            raise ValueError('TLS required outside isolated loopback test')
        if not loopback and (not isinstance(allowed_hosts,frozenset) or host not in allowed_hosts):
            raise ValueError('model endpoint not in trusted production hostname allowlist')
        if allowed_hosts is not None and (not isinstance(allowed_hosts,frozenset) or host not in allowed_hosts):
            raise ValueError('model endpoint not in trusted allowlist')
        try:port=u.port
        except ValueError as exc:raise ValueError('invalid endpoint port') from exc
        if (port is not None and not 1<=port<=65535 or
            type(timeout) not in (int,float) or not math.isfinite(timeout) or not .1<=timeout<=30
            or type(max_response_bytes) is not int or not 256<=max_response_bytes<=2**20
            or auth_env is not None and (not isinstance(auth_env,str) or not re.fullmatch('[A-Z][A-Z0-9_]{1,63}',auth_env))):
            raise ValueError('invalid model connection budgets')
        self.host,self.port,self.secure,self.model,self.auth_env=host,port or (443 if u.scheme=='https' else 80),u.scheme=='https',model,auth_env
        self.timeout=float(timeout);self.max_bytes=max_response_bytes

    def _invoke(self,body:bytes)->bytes:
        conn=(http.client.HTTPSConnection(self.host,self.port,timeout=self.timeout,context=ssl.create_default_context())
              if self.secure else http.client.HTTPConnection(self.host,self.port,timeout=self.timeout))
        headers={'Content-Type':'application/json','Accept':'application/json'}
        if self.auth_env:
            secret=os.getenv(self.auth_env)
            if not secret or len(secret)>8192:raise CWMRejected('provider credential unavailable')
            headers['Authorization']='Bearer '+secret
        try:
            conn.request('POST','/v1/chat/completions',body,headers)
            response=conn.getresponse()
            if response.status!=200:raise CWMRejected('model service rejected request')
            length=response.getheader('Content-Length')
            if length and (not length.isascii() or not length.isdigit() or int(length)>self.max_bytes):
                raise CWMRejected('untrusted model response length')
            output=response.read(self.max_bytes+1)
            if len(output)>self.max_bytes:raise CWMRejected('model response too large')
            return output
        except (OSError,http.client.HTTPException,ssl.SSLError) as exc:
            raise CWMRejected('model service unavailable or untrusted') from exc
        finally:conn.close()

    async def propose(self,initial:Mapping[str,int],goal:str,*,frozen_keys:frozenset[str]=frozenset())->CWMProposal:
        if (type(goal) is not str or not 1<=len(goal)<=1024 or '\x00' in goal or
                not isinstance(initial,Mapping) or not initial or len(initial)>128 or not isinstance(frozen_keys,frozenset)):
            raise CWMRejected('invalid bounded model prompt')
        try:
            # This validates preconditions independently from the model provider.
            check=WorldModel(frozen_keys=frozen_keys)
            check.simulate(initial,(WorldAction('read',next(iter(initial))),))
            body=_canonical({'model':self.model,'temperature':0,'max_tokens':1024,
                'response_format':{'type':'json_object'},'messages':[
                    {'role':'system','content':'Return ONLY JSON {"actions":[{"operation":"read|set|increment","target":"existing_key","value":integer_or_null}]}. Never return code, tools or untrusted permissions. Maximum 32 actions.'},
                    {'role':'user','content':json.dumps({'initial':dict(initial),'goal':goal},sort_keys=True)}]})
            if len(body)>8192:raise CWMRejected('request budget exceeded')
            response=await asyncio.wait_for(asyncio.to_thread(self._invoke,body),timeout=self.timeout+1)
            outer=json.loads(response,object_pairs_hook=_unique,
                             parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite')))
            if type(outer) is not dict or ('model' in outer and outer['model']!=self.model):
                raise CWMRejected('provider claims unexpected model identity')
            text=outer['choices'][0]['message']['content']
            if type(text) is not str or len(text)>32768:raise CWMRejected('invalid model answer')
            plan=json.loads(text,object_pairs_hook=_unique,
                            parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite')))
            if type(plan) is not dict or set(plan)!={'actions'} or type(plan['actions']) is not list or not 1<=len(plan['actions'])<=32:
                raise CWMRejected('model returned non-declarative plan')
            actions=[]
            for raw in plan['actions']:
                if type(raw) is not dict or set(raw) not in ({'operation','target'},{'operation','target','value'}):
                    raise CWMRejected('untrusted action schema')
                actions.append(WorldAction(raw['operation'],raw['target'],raw.get('value')))
            result=check.simulate(initial,tuple(actions))
            return CWMProposal(self.model,tuple(actions),result,hashlib.sha256(response).hexdigest())
        except CWMRejected:raise
        except (ValueError,TypeError,KeyError,IndexError,WorldPolicyDenied,OverflowError,RecursionError,UnicodeError,asyncio.TimeoutError) as exc:
            raise CWMRejected('invalid or unsafe model response or simulation') from exc
