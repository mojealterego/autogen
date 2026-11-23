# OMEGA 10.0 — local autonomous service supervision

This is an **opt-in, trusted local process supervisor**, not a multi-host
infrastructure control plane or a deployment on a real cluster. It is a real
operational component: services are launched as actual OS child processes.

- `SupervisorSpec`: fixed absolute executable path, bounded fixed argument
  vector and restart/time budget; no shell, no dynamic command from model.
  `env_allowlist` defaults to empty; process output and stdin are suppressed.
- `ManagedProcessSupervisor`: asynchronous process startup, optional trusted
  readiness/liveness probe, deadline for readiness, exponential capped backoff,
  finite restart budget, terminal exhaustion state, and stop handling.
- On POSIX, services start in a new session. Shutdown sends SIGTERM and then
  SIGKILL to the entire session process group, including child processes;
  stop does not return while the tracked leader remains alive. On Windows only
  the leader is controlled, not an entire process tree.
- `InfrastructureSupervisor`: bounded inventory of independently managed
  services; duplicate service IDs and unknown probe bindings are rejected.
- `SupervisorEvent` and `verify_supervisor_audit`: SHA-256 chain over state and
  attempt counts, with no argument, environment or secret contents. Pin trusted
  audit head externally; this is not a digital signature.

## Limits and security

Executable and health callbacks must be controlled by trusted local operators.
This is not a privilege boundary, syscall sandbox, cgroup controller,
container, Kubernetes reconciliation loop, consensus manager or high-availability
service. A malicious process can escape local isolation; use OS-level identity,
network/filesystem controls, cgroups and a secure container runtime for
untrusted code. Process groups on POSIX are a best-effort local cleanup scope.
No deployment, OS service manager integration or real failover has been tested.

`TDD_RED.log` proves missing API. `TESTS.txt` runs real interpreter processes
covering process health, restart exhaustion, failing probe, environment
allowlist, trusted executable enforcement, multi-service stop, chained audit,
and POSIX orphan-descendant shutdown.
