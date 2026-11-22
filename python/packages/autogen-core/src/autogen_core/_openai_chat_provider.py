"""Bounded OpenAI-compatible chat client using the Python standard library.

URLs, models and credentials must be provided by trusted configuration, never
by prompts or agents. Plaintext HTTP is allowed only for numeric loopback IPs.
No redirects, proxy discovery, network retries or arbitrary tool calls.
"""
from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import math
from urllib.parse import urlsplit

from ._adaptive_model_router import ModelResponse, RouteRequest


class OpenAIChatProvider:
    """A concrete provider for llama.cpp/vLLM/OpenAI-compatible chat servers.

    Caller estimates token usage in RouteRequest. Actual provider usage is
    validated by AdaptiveModelRouter before budget settlement. A timeout or
    disconnection may occur after billable work; never retry automatically.
    """

    def __init__(self, endpoint: str, model: str, *, api_key: str | None = None,
                 timeout: float = 20.0, max_response_bytes: int = 65536) -> None:
        if not isinstance(endpoint,str) or not isinstance(model,str) or not 0 < len(model) <= 128:
            raise ValueError('invalid endpoint or model')
        if any(ord(c)<32 for c in model):
            raise ValueError('invalid model name')
        parts=urlsplit(endpoint)
        if (parts.scheme not in ('http','https') or parts.username is not None or
                parts.password is not None or parts.fragment or parts.query or
                parts.path != '/v1/chat/completions' or not parts.hostname):
            raise ValueError('unapproved chat endpoint')
        try:
            port=parts.port or (443 if parts.scheme=='https' else 80)
        except ValueError as exc:
            raise ValueError('invalid endpoint port') from exc
        if not 1 <= port <= 65535:
            raise ValueError('invalid endpoint port')
        if parts.scheme=='http':
            try:
                address=ipaddress.ip_address(parts.hostname)
            except ValueError as exc:
                raise ValueError('plaintext requires numeric loopback IP') from exc
            if not address.is_loopback:
                raise ValueError('plaintext requires loopback IP')
        if (type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0 < timeout <= 300
                or type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1024**2):
            raise ValueError('invalid HTTP limits')
        if api_key is not None and (not isinstance(api_key,str) or any(ord(c)<32 for c in api_key)):
            raise ValueError('invalid bearer token')
        self._hostname=parts.hostname
        self._port=port
        self._tls=parts.scheme=='https'
        self._model=model
        self._key=api_key
        self._timeout=float(timeout)
        self._max=max_response_bytes

    @staticmethod
    def _messages(req: RouteRequest) -> list[dict[str,str]]:
        fields=set(req.payload)
        if fields=={'prompt'} and isinstance(req.payload.get('prompt'),str):
            messages=[{'role':'user','content':req.payload['prompt']}]
        elif fields=={'messages'} and isinstance(req.payload.get('messages'),list):
            messages=req.payload['messages']
        else:
            raise ValueError('only fixed prompt/messages payloads are supported')
        if not 1 <= len(messages) <= 32:
            raise ValueError('invalid message count')
        result=[]
        for msg in messages:
            if (not isinstance(msg,dict) or set(msg)!={'role','content'} or
                    msg['role'] not in ('system','user','assistant') or
                    not isinstance(msg['content'],str)):
                raise ValueError('unsupported role or message shape')
            result.append({'role':msg['role'],'content':msg['content']})
        return result

    def _invoke(self, req: RouteRequest) -> ModelResponse:
        messages=self._messages(req)
        data=json.dumps({'model':self._model,'messages':messages,'max_tokens':req.max_output_tokens,
                         'stream':False},ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('utf8')
        if len(data)>65536:
            raise ValueError('HTTP request too large')
        headers={'Content-Type':'application/json','Accept':'application/json'}
        if self._key:
            headers['Authorization']='Bearer '+self._key
        connection=(http.client.HTTPSConnection if self._tls else http.client.HTTPConnection)(
            self._hostname,self._port,timeout=self._timeout)
        try:
            connection.request('POST','/v1/chat/completions',body=data,headers=headers)
            response=connection.getresponse()
            if response.status!=200 or not response.getheader('Content-Type','').lower().startswith('application/json'):
                raise RuntimeError('chat provider returned unsuccessful or invalid response')
            content=response.read(self._max+1)
            if len(content)>self._max:
                raise RuntimeError('chat response exceeds configured byte limit')
            parsed=json.loads(content)
            if (not isinstance(parsed,dict) or not isinstance(parsed.get('choices'),list)
                    or not parsed['choices'] or not isinstance(parsed['choices'][0],dict)
                    or not isinstance(parsed['choices'][0].get('message'),dict)):
                raise ValueError('invalid chat completion')
            msg=parsed['choices'][0]['message']
            usage=parsed.get('usage')
            if (not isinstance(msg.get('content'),str) or not isinstance(usage,dict)
                    or type(usage.get('prompt_tokens')) is not int or usage['prompt_tokens']<0
                    or type(usage.get('completion_tokens')) is not int or usage['completion_tokens']<0):
                raise ValueError('chat response missing text or usage')
            return ModelResponse(msg['content'],usage['prompt_tokens'],usage['completion_tokens'])
        finally:
            connection.close()

    async def __call__(self, request: RouteRequest) -> ModelResponse:
        if not isinstance(request,RouteRequest):
            raise ValueError('route request required')
        self._messages(request)  # fail before dispatch on malformed messages
        return await asyncio.to_thread(self._invoke,request)
