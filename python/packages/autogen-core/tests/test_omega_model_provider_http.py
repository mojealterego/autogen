import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from autogen_core import (OpenAIChatProvider, RouteRequest, AdaptiveModelRouter,
    ModelProfile, TenantRoutePolicy)


def test_ssrf_and_insecure_destination_rejected():
    for endpoint in ('http://example.com/v1/chat/completions',
                     'http://192.168.0.1/v1/chat/completions',
                     'http://127.0.0.1:9999/redirect',
                     'file:///tmp/private',
                     'https://user:pass@example.com/v1/chat/completions'):
        with pytest.raises(ValueError): OpenAIChatProvider(endpoint,'trusted-model')


def test_real_loopback_http_integration_and_usage(tmp_path):
    seen=[]
    class Server(BaseHTTPRequestHandler):
        def do_POST(self):
            n=int(self.headers['Content-Length'])
            payload=json.loads(self.rfile.read(n))
            seen.append((self.path,payload,self.headers.get('Authorization')))
            data=json.dumps({'choices':[{'message':{'content':'accepted'}}],
                             'usage':{'prompt_tokens':4,'completion_tokens':2}}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self,*args):pass
    with ThreadingHTTPServer(('127.0.0.1',0),Server) as server:
        thread=threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            provider=OpenAIChatProvider(f'http://127.0.0.1:{server.server_port}/v1/chat/completions','trusted-model',api_key='private')
            r=RouteRequest('t','text',10,5,{'prompt':'hello'})
            result=asyncio.run(provider(r))
            assert result.content=='accepted'
            assert result.prompt_tokens==4 and result.completion_tokens==2
            assert seen[0][0]=='/v1/chat/completions'
            assert seen[0][1]['model']=='trusted-model'
            assert seen[0][1]['messages']==[{'role':'user','content':'hello'}]
            assert seen[0][2]=='Bearer private'
            profile=ModelProfile('trusted-model',frozenset({'text'}),8192,900,100,1000000,1000000)
            router=AdaptiveModelRouter({'trusted-model':(profile,provider)},
                                       {'t':TenantRoutePolicy(frozenset({'trusted-model'}),budget_micro_usd=50)})
            routed=asyncio.run(router.route(r))
            assert routed.model=='trusted-model' and routed.charged_micro_usd==6
            assert len(seen)==2
        finally:
            server.shutdown();thread.join(3)


def test_request_validation_no_dynamic_tools():
    provider=OpenAIChatProvider('http://127.0.0.1:8082/v1/chat/completions','trusted-model')
    with pytest.raises(ValueError):asyncio.run(provider(RouteRequest('t','text',10,5,{'messages':[{'role':'tool','content':'run code'}]})))
