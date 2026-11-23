"""OMEGA 15: verify Meta Fastgen route and bounded read-only trace grammar."""
from __future__ import annotations
import json
import os
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pytest
from autogen_core._cwm_trace import CWMTraceParser, CWMTraceRejected,compare_cwm_trace
from autogen_core._research_cwm import CWMClient,CWMRejected

class _Server(BaseHTTPRequestHandler):
    paths=[]
    requests=[]
    answer={'choices':[{'message':{'content':'{"actions":[{"operation":"increment","target":"counter","value":2}]}'}}], 'model':'cwm'}
    def do_POST(self):
        type(self).paths.append(self.path)
        n=int(self.headers['Content-Length'])
        type(self).requests.append(json.loads(self.rfile.read(n)))
        b=json.dumps(type(self).answer).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(b)))
        self.end_headers();self.wfile.write(b)
    def log_message(self,*a):pass


@pytest.fixture
def meta_server():
    class Handler(_Server): pass
    Handler.paths=[];Handler.requests=[]
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    task=threading.Thread(target=server.serve_forever,daemon=True);task.start()
    try:yield server,Handler
    finally:server.shutdown();server.server_close();task.join(timeout=3)


@pytest.mark.asyncio
async def test_actual_meta_fastgen_wire_route_and_body(meta_server):
    server,handler=meta_server
    client=CWMClient(f'http://127.0.0.1:{server.server_port}/chat/completions',model='cwm',allow_loopback_http=True)
    proposal=await client.propose({'counter':40},'increment counter two')
    assert proposal.result.final_state=={'counter':42}
    assert handler.paths==['/chat/completions']
    assert handler.requests[0]['model']=='cwm'
    assert handler.requests[0]['reasoning']=={'enabled':False}
    assert 'response_format' not in handler.requests[0]


def test_meta_route_restrictions():
    for endpoint in ('http://127.0.0.1:123/json/list','http://127.0.0.1:123/completions',
                     'http://192.168.1.1:5432/chat/completions','https://example.com/chat/completions?evil=1'):
        with pytest.raises(ValueError):CWMClient(endpoint,allow_loopback_http=True)


def test_original_meta_trace_format_readonly_and_verification():
    trace=('<|trace_context_start|><|frame_sep|><|call_sep|>{"a":"1","b":"3"}'
           '<|action_sep|>def f(a,b):<|frame_sep|><|line_sep|>{"a":".."}'
           '<|action_sep|> return a+b<|frame_sep|><|return_sep|><|action_sep|>return 4'
           '<|arg_sep|>"4"<|frame_sep|>')
    obj=CWMTraceParser(max_frames=8).parse(trace)
    assert len(obj.frames)==3
    assert obj.frames[0].variables=={'a':'1','b':'3'}
    assert obj.frames[2].result=='4'
    assert compare_cwm_trace(trace,trace).exact
    modified=trace.replace('"4"<|frame_sep|>','"5"<|frame_sep|>')
    assert not compare_cwm_trace(trace,modified).exact
    assert compare_cwm_trace(trace,modified).matching_frames==2


def test_trace_parser_denies_invalid_json_extra_code_and_resource_bombs():
    p=CWMTraceParser(max_frames=3,max_bytes=1024)
    with pytest.raises(CWMTraceRejected):p.parse('<|frame_sep|><|line_sep|>{"x":1,"x":2}<|action_sep|> x<|frame_sep|>')
    with pytest.raises(CWMTraceRejected):p.parse('<|frame_sep|><|line_sep|>{}<|action_sep|>x<|frame_sep|>JUNK')
    with pytest.raises(CWMTraceRejected):p.parse('<|frame_sep|><|line_sep|>{}<|action_sep|>x<|frame_sep|>'*100)
    with pytest.raises(CWMTraceRejected):p.parse('<|frame_sep|><|return_sep|><|action_sep|>return 1<|arg_sep|>1<|frame_sep|>')


@pytest.mark.asyncio
async def test_meta_fastgen_request_matches_reference_source_contract(meta_server):
    """Static contract test; not a real 32B inference or OpenAI SDK install."""
    source=os.environ.get('MAGNETIC_CWM_REFERENCE_SOURCE')
    if not source:
        pytest.skip('official Meta CWM source archive not mounted')
    from ast import parse, ClassDef, AnnAssign, Name
    root=Path(source)
    schema=root/'serve'/'openai_api.py'
    server_source=root/'serve'/'fgserve.py'
    tree=parse(schema.read_text())
    definitions={x.name:x for x in tree.body if isinstance(x,ClassDef)}
    assert 'ChatCompletionRequest' in definitions and 'ReasoningConfig' in definitions
    fields={x.target.id for x in definitions['ChatCompletionRequest'].body
            if isinstance(x,AnnAssign) and isinstance(x.target,Name)}
    assert {'messages','model','reasoning','max_tokens','temperature'} <= fields
    assert '@app.route("/chat/completions", methods=["POST"])' in server_source.read_text()
    server,handler=meta_server
    client=CWMClient(f'http://127.0.0.1:{server.server_port}/chat/completions',
                     model='cwm',allow_loopback_http=True)
    await client.propose({'counter':1},'increment by 2')
    sent=handler.requests[-1]
    assert sent['model']=='cwm' and sent['reasoning']=={'enabled':False}
    assert all(key in fields for key in ('messages','model','max_tokens','reasoning'))


def test_cwm_trace_rejects_nonfinite_numeric_overflow():
    parser=CWMTraceParser()
    raw='<|frame_sep|><|call_sep|>{"counter":1e999}<|action_sep|>def f():<|frame_sep|>'
    with pytest.raises(CWMTraceRejected):parser.parse(raw)
    terminal=('<|frame_sep|><|call_sep|>{}<|action_sep|>def f():'
              '<|frame_sep|><|return_sep|><|action_sep|>return x<|arg_sep|>1e999<|frame_sep|>')
    with pytest.raises(CWMTraceRejected):parser.parse(terminal)
