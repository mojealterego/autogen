import asyncio
import hashlib
import socket
import ssl
import struct
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from autogen_core import (
    ClusterCoordinator, ClusterDenied, ClusterWorker, LeaseLost, Resources,
    TenantSpec, WorkerSpec, FabricClient, FabricServer, FabricPrincipal, FabricTransportError,
)


@pytest.fixture(scope='module')
def pki(tmp_path_factory):
    root = tmp_path_factory.mktemp('fabric-pki')
    def openssl(*args):
        subprocess.run(['openssl', *args], cwd=root, check=True, capture_output=True)
    openssl('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
            '-keyout', 'ca.key', '-out', 'ca.pem', '-subj', '/CN=OMEGA test CA', '-days', '1',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
    for name in ('server', 'a', 'b', 'worker', 'unlisted'):
        openssl('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                '-keyout', name+'.key', '-out', name+'.csr', '-subj', '/CN='+name)
        ext = 'extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost\n' if name == 'server' else 'extendedKeyUsage=clientAuth\n'
        (root/'ext').write_text(ext)
        openssl('x509', '-req', '-in', name+'.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key',
                '-CAcreateserial', '-out', name+'.pem', '-days', '1', '-extfile', 'ext')
    return root


def fingerprint(root, name):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert((root/(name+'.pem')).read_text())).hexdigest()


def client_context(root, name):
    context = ssl.create_default_context(cafile=str(root/'ca.pem'))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if name:
        context.load_cert_chain(root/(name+'.pem'), root/(name+'.key'))
    return context


def server_context(root):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=str(root/'ca.pem'))
    context.load_cert_chain(root/'server.pem', root/'server.key')
    return context


@contextmanager
def serving(root, db, *, max_connections=32, **options):
    quota = Resources(1, 100, 100)
    c = ClusterCoordinator(db, quota, {x: TenantSpec(quota, frozenset({'f'})) for x in ('a','b')},
                           {'w': WorkerSpec(quota, frozenset({'a'}), frozenset({'f'}))}, **options)
    identities = {fingerprint(root, 'a'): FabricPrincipal('producer', 'a'),
                  fingerprint(root, 'b'): FabricPrincipal('producer', 'b'),
                  fingerprint(root, 'worker'): FabricPrincipal('worker', 'w')}
    with FabricServer(('127.0.0.1', 0), c, server_context(root), identities, timeout=2, max_connections=max_connections) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        try:
            def client(name, hostname='localhost'):
                return FabricClient(server.server_address, client_context(root, name), hostname, timeout=2)
            yield c, client, server.server_address
        finally:
            server.shutdown()
            thread.join(3)
            assert not thread.is_alive()


def test_remote_job_roundtrip_and_role_separation(tmp_path, pki):
    with serving(pki, tmp_path/'db') as (c, connect, _):
        a,b,w = connect('a'),connect('b'),connect('worker')
        demand=Resources(1,100,100)
        assert a.submit('a','j','f',{'n':2},demand).state == 'queued'
        with pytest.raises(ClusterDenied): b.get('a','j')
        with pytest.raises(ClusterDenied): a.register('w')
        with pytest.raises(ClusterDenied): w.submit('a','j','f',{},demand)
        with pytest.raises(ClusterDenied): w.register('forged')
        with pytest.raises(ClusterDenied): b.submit('b','j','f',{},demand)
        session=w.register('w'); lease=w.claim(session)
        assert lease is not None and lease.payload == {'n':2}
        assert w.renew(session,lease) >= lease.expires_ms
        assert w.complete(session,lease,{'ok':True}).state=='succeeded'
        assert a.get('a','j').result == {'ok':True}
        with pytest.raises(LeaseLost): w.complete(session,lease,'stale')
        with pytest.raises(ClusterDenied): w.get('a','j')


def test_tls_and_pin_fail_closed(tmp_path,pki):
    with serving(pki,tmp_path/'db') as (_,connect,_):
        for name,host in [(None,'localhost'),('unlisted','localhost'),('a','wrong-host')]:
            with pytest.raises(FabricTransportError): connect(name,host).get('a','j')


def test_insecure_contexts_rejected(tmp_path,pki):
    ctx=client_context(pki,'a'); ctx.check_hostname=False
    with pytest.raises(ValueError): FabricClient(('127.0.0.1',1),ctx,'localhost')
    with serving(pki,tmp_path/'db') as (c,_,__):
        context=server_context(pki);context.verify_mode=ssl.CERT_OPTIONAL
        with pytest.raises(ValueError): FabricServer(('127.0.0.1',0),c,context,{})


def test_remote_worker_integrates_real_coordinator(tmp_path,pki):
    with serving(pki,tmp_path/'db') as (_,connect,__):
        a,w=connect('a'),connect('worker')
        a.submit('a','j','f',{'n':3},Resources(1,100,100))
        async def handle(p): return p['n']*7
        runner=ClusterWorker(w,w.register('w'),{'f':handle},heartbeat_interval=.1)
        assert asyncio.run(runner.run_once()).result==21
        assert a.get('a','j').result==21


def test_frame_limit_duplicate_keys_and_recovery(tmp_path,pki):
    with serving(pki,tmp_path/'db') as (_,connect,address):
        ctx=client_context(pki,'a')
        duplicate = b'{"op":"get","op":"get"}'
        for frame in (struct.pack('!I', 2**30), struct.pack('!I',len(duplicate))+duplicate):
            with socket.create_connection(address,timeout=2) as raw:
                with ctx.wrap_socket(raw,server_hostname='localhost') as s:
                    s.sendall(frame)
                    try: assert s.recv(4)==b''
                    except (ConnectionResetError,ssl.SSLError): pass
        assert connect('a').submit('a','new','f',{},Resources(1,100,100)).state=='queued'


def test_remote_fencing_and_cross_worker_session(tmp_path,pki):
    from dataclasses import replace
    # Expiry is deterministic; network remains real TLS. Avoid coupling lease
    # validity to interpreter scheduling on contended CI hosts.
    tick = [1000]
    with serving(pki,tmp_path/'db',lease_ms=500,clock_ms=lambda:tick[0]) as (_,connect,__):
        a,w=connect('a'),connect('worker')
        a.submit('a','j','f',{},Resources(1,100,100),idempotent=True,max_attempts=2)
        session=w.register('w'); lease=w.claim(session)
        assert lease is not None
        with pytest.raises(LeaseLost): w.renew(replace(session,token='z'*43),lease)
        with pytest.raises(ClusterDenied): w.heartbeat(replace(session,worker_id='other'))
        with pytest.raises(LeaseLost): w.complete(session,replace(lease,fence=lease.fence+1), 'future')
        tick[0]=1500
        session2=w.register('w'); lease2=w.claim(session2)
        assert lease2 and lease2.fence>lease.fence and lease2.idempotency_key==lease.idempotency_key
        with pytest.raises(LeaseLost): w.complete(session2,lease,'stale')
        assert w.complete(session2,lease2,'valid').result=='valid'


def test_disconnect_after_committed_submit_reconciles_without_duplication(tmp_path,pki):
    import json,time
    with serving(pki,tmp_path/'db') as (c,connect,address):
        args={'tenant':'a','job_id':'j','handler':'f','payload':{},'demand':{'slots':1,'memory_bytes':100,'cpu_millis':100},'idempotent':False,'max_attempts':1}
        raw_data=json.dumps({'v':1,'op':'submit','args':args}).encode()
        with socket.create_connection(address,timeout=2) as raw:
            with client_context(pki,'a').wrap_socket(raw,server_hostname='localhost') as s:
                s.sendall(struct.pack('!I',len(raw_data))+raw_data)
                # Observe committed write from independent local authority, then close without consuming reply.
                for _ in range(100):
                    try:
                        assert c.get('a','j').state=='queued'; break
                    except ClusterDenied: time.sleep(.01)
                else: pytest.fail('write not committed')
        a=connect('a');w=connect('worker')
        assert a.submit('a','j','f',{},Resources(1,100,100))==a.get('a','j')
        session=w.register('w');lease=w.claim(session)
        assert lease and w.complete(session,lease,1).state=='succeeded'
        assert w.claim(session) is None


def test_lease_control_messages_do_not_echo_payload(tmp_path,pki):
    with serving(pki,tmp_path/'db') as (_,connect,__):
        a,w=connect('a'),connect('worker')
        a.submit('a','j','f',{'sensitive':'example'},Resources(1,100,100))
        session=w.register('w');lease=w.claim(session)
        assert lease
        # Observe actual client serialization, not the server handler implementation.
        seen=[]; original=w._call
        def capture(op,**args):
            seen.append(args['lease']); return original(op,**args)
        w._call=capture
        w.renew(session,lease);w.complete(session,lease,1)
        assert seen==[{'tenant':'a','job_id':'j','fence':lease.fence}]*2


def test_worker_in_independent_process(tmp_path,pki):
    import subprocess,sys
    with serving(pki,tmp_path/'db') as (_,connect,address):
        a=connect('a');a.submit('a','j','f',{'n':5},Resources(1,100,100))
        child='''import asyncio,ssl,sys
from pathlib import Path
from autogen_core import FabricClient, ClusterWorker
root=Path(sys.argv[1]);ctx=ssl.create_default_context(cafile=str(root/'ca.pem'))
ctx.minimum_version=ssl.TLSVersion.TLSv1_2
ctx.load_cert_chain(root/'worker.pem',root/'worker.key')
c=FabricClient(('127.0.0.1',int(sys.argv[2])),ctx,'localhost',timeout=2)
async def f(data): return data['n']+9
assert asyncio.run(ClusterWorker(c,c.register('w'),{'f':f}).run_once()).result==14
'''
        # Allow interpreter/import startup on shared CI hosts independently of
        # the worker's strict two-second per-RPC deadline.
        result=subprocess.run([sys.executable,'-c',child,str(pki),str(address[1])],capture_output=True,text=True,timeout=60)
        assert result.returncode==0,result.stderr
        assert a.get('a','j').result==14


def test_saturation_rejects_extra_connection_without_losing_service(tmp_path,pki):
    import json,time
    with serving(pki,tmp_path/'db',max_connections=1) as (_,connect,address):
        ctx=client_context(pki,'a')
        with socket.create_connection(address,timeout=2) as raw:
            with ctx.wrap_socket(raw,server_hostname='localhost') as s:
                with pytest.raises(FabricTransportError): connect('a').get('a','j')
                args={'tenant':'a','job_id':'j','handler':'f','payload':{},'demand':{'slots':1,'memory_bytes':100,'cpu_millis':100},'idempotent':False,'max_attempts':1}
                data=json.dumps({'v':1,'op':'submit','args':args}).encode()
                s.sendall(struct.pack('!I',len(data))+data)
                assert s.recv(4)
        for _ in range(100):
            try:
                assert connect('a').get('a','j').state=='queued';break
            except FabricTransportError: time.sleep(.01)
        else: pytest.fail('service did not recover')


def test_fence_rejects_boolean_and_invalid_value():
    from autogen_core import JobFence
    for value in (True,0,-1,2**63):
        with pytest.raises(ValueError): JobFence('a','j',value)
