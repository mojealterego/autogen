# OMEGA Distributed Fabric 5.1 — authenticated transport milestone

Additive, opt-in TCP/TLS transport for the release 5.0 coordinator. AutoGen
retains its upstream 0.7.5 package version. No new runtime dependencies.
Only the authority host opens the SQLite WAL database; remote workers never
share a WAL file over a network filesystem. This is a single-authority service,
not consensus, replica failover, or exactly-once external effects.

## Transport and authority contract

- `FabricServer(address, coordinator, tls, principals, timeout=10,
  max_frame=1048576, max_connections=32)` uses a fixed DER SHA-256 client
  certificate allowlist in addition to CA verification. Pins authorize exactly
  one `FabricPrincipal('producer', tenant_id)` or `FabricPrincipal('worker',
  worker_id)`. A trusted producer can submit/read only its own tenant. A worker
  can register only as itself, and use only its own session; the coordinator
  enforces its configured capabilities, quotas and fences. Unknown certificates
  receive no application response. A valid certificate is not sufficient without
  a pin. The server does not expose arbitrary method dispatch or SQL.
- `FabricClient(address, tls, server_hostname, lease_ms=30000, timeout=10,
  max_frame=1048576)` implements the synchronous coordinator worker backend and
  producer operations. Set `lease_ms` to the coordinator's actual lease length;
  this trusted operational configuration is **not negotiated**. `ClusterWorker`
  accepts the typed `ClusterWorkerBackend` protocol, wraps I/O in worker threads
  and preserves its existing cancellation/heartbeat semantics.
- One connection per operation; protocol v1 is a 4-byte big-endian length followed
  by UTF-8 JSON. Responses are equally framed. Duplicate keys, oversized frames,
  invalid top-level shapes and malformed input are rejected; input is not code.
  The bounded worker-thread admission takes place before TLS handshakes. The
  monotonic I/O deadline bounds frame reads/writes; database calls use the
  coordinator's own busy timeout and may commit after the caller's deadline.
  DNS resolution/TCP multi-address dialing are OS-controlled and may exceed a
  socket timeout; use configured numeric IPv4 endpoint addresses for bounded
  deployment. The TLS hostname is independently verified.
- `JobFence(tenant, job_id, fence)` references a lease without retransmitting
  its payload. It grants no authority by itself; session and actual live fence
  are verified transactionally. No sensitive errors or session tokens are
  included in error messages. Unexpected server errors use `InternalError`.
- `FabricTransportError` means communication failed or an internal failure
  occurred; it **does not** mean the mutation rolled back. No automatic retries.
  Producers reconcile via `get` or identical idempotent submission; unknown
  worker registrations may require session expiration before re-registering.
  Worker claim/finish loss is reconciled via the existing lease-expiration state
  machine. External side effects still need their own stable idempotency keys
  and fencing enforcement.

## Deployment example

Obtain separate server/producer/worker certificates signed by an approved CA.
Use server SAN matching the DNS hostname and appropriate extended key usages.
Private keys belong in restricted files or a secret manager; **never commit them**.
The test suite generates ephemeral certificates under pytest's temporary directory.

```python
import hashlib
import ssl
from pathlib import Path
from autogen_core import FabricPrincipal, FabricServer, FabricClient, ClusterWorker

# On the authority host; coordinator is the configured ClusterCoordinator.
server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
server_tls.minimum_version = ssl.TLSVersion.TLSv1_2
server_tls.verify_mode = ssl.CERT_REQUIRED
server_tls.load_verify_locations(cafile='/etc/omega/ca.pem')
server_tls.load_cert_chain('/etc/omega/server.pem', '/etc/omega/server.key')
def pin(path):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(Path(path).read_text())).hexdigest()
principals = {
    pin('/etc/omega/producer-a.pem'): FabricPrincipal('producer', 'tenant-a'),
    pin('/etc/omega/worker-a.pem'): FabricPrincipal('worker', 'worker-a'),
}
# In a dedicated service process:
# with FabricServer(('10.0.0.5', 7443), coordinator, server_tls, principals) as server:
#     server.serve_forever()

# On worker host, with the real hostname and lease settings:
worker_tls = ssl.create_default_context(cafile='/etc/omega/ca.pem')
worker_tls.minimum_version = ssl.TLSVersion.TLSv1_2
worker_tls.load_cert_chain('/etc/omega/worker-a.pem', '/etc/omega/worker-a.key')
client = FabricClient(('10.0.0.5', 7443), worker_tls, 'omega.internal', lease_ms=30000)
# session = client.register('worker-a')
# worker = ClusterWorker(client, session, {'compute': trusted_handler})
# await worker.run_once()
```

Use dedicated client/server SSLContext instances, immutable after startup.
Certificate/pin rotation requires coordinated reconfiguration/restart; no online
revocation service, CRL refresh, per-principal connection-rate limiting, IPv6,
service discovery or request pooling is implemented. Protect the service with
network ACLs and external resource/rate controls. A single bounded thread pool
can still be monopolized by authenticated or slow clients until their deadlines.
No adversarial isolation guarantee exists for worker code. Real multi-host
partition/failover, replicated authority, and durable DAG submission adapter are
still open. All TCP tests run on IPv4 loopback on a single machine, including an
independent worker process, not multiple physical hosts.

## Evidence

Run from repository root with the local package and test-utils available:

```sh
python -m pytest -q python/packages/autogen-core/tests/test_omega_fabric.py
python -m pytest -q python/packages/autogen-core/omega_tests python/packages/autogen-core/tests/test_omega*.py
python -m pytest -q python/packages/autogen-core/tests --continue-on-collection-errors
```

OpenSSL CLI is required by the new integration tests. They exercise certificate
validation, pin allowlist, hostname mismatch, producer/worker ACLs, cross-tenant
reads, stale fences, worker token misuse, lease expiry, malformed/oversized and
duplicate-key frames, connection saturation, lost-response reconciliation,
minimal control messages, and an independent worker process. Logs are retained
alongside this document. The original three absent upstream test dependencies
are not replaced with fake packages.

### Observed results (local environment)

- Final focused fabric suite: 11 passed (FABRIC_FINAL.txt).
- Initial combined OMEGA suite: 69 passed / 4 failed; failures were legacy process/timing paths (FOCUSED_TESTS.txt).
- Initial full-core attempt: 178 passed / 1 failed / 10 collection errors (FULL_CORE_ATTEMPT.txt). The independent process test hit its outer fixture startup timeout.
- Final full-core attempt: 178 passed / 1 failed / 10 collection errors (FULL_CORE_FINAL.txt). A legacy process worker returned uncertain rather than succeeded. The same legacy test subsequently passed in isolation on both baseline 5.0 and candidate 5.1 (BASELINE_PROCESS_RECHECK.txt / CANDIDATE_PROCESS_RECHECK.txt). This does not establish that all timing failures are environment-only.
- The network fence-expiry test now uses the coordinator's injected clock to avoid nondeterministic wall-clock scheduling while preserving real TLS and expiry assertions. Independent worker fixture startup has its own bounded 60-second allowance; individual network RPC limits remain unchanged.
- Missing upstream development dependencies: jsonref, asyncio_atexit, pytest_mock. The full release gate remains open.
- Python syntax compilation: see COMPILE.txt. No production or independent physical multi-host deployment was performed.
