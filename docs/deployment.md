# Deployment And Operations

This document describes the current BMD Compute operating model. It is documentation of the present deployment policy, not a complete production runbook.

## Current Model

```text
authorized student
  -> TAU VPN / university network
  -> BMD Compute on TAU-hosted VM
  -> Paramiko as constrained shared bmdguest identity
  -> PowerSLURM leeburton-pool partition
  -> VASP
```

BMD Compute is currently intended to run as a lab-internal service reachable only from the TAU VPN or university network.

Do not expose the Uvicorn port directly to the public Internet.

## Network And Identity

The web app does not currently provide app-level login, SSO, CSRF protection, or private per-user ownership. The access boundary is the TAU network/VPN plus the constrained shared remote identity.

Resume by Job ID is not a private authorization boundary. Jobs visible through this path should be treated as shared service/group calculations.

If the app is later exposed outside the current VPN-bound model, authentication, authorization, CSRF posture, auditing, and per-user job ownership need to be redesigned before deployment.

## Uvicorn Process

The app currently assumes a single BMD Compute Python/Uvicorn process unless an operator deliberately changes that deployment.

Important process-local state:

- remote-operation semaphore
- completed-result cache
- in-memory UI/runtime state

Running multiple workers multiplies process-local limits and caches. Do not treat the current semaphore or result cache as cross-process controls.

## Remote Operation Limits

Current defaults:

```text
BMD_MAX_CONCURRENT_REMOTE_OPERATIONS = 4
BMD_REMOTE_OPERATION_SLOT_TIMEOUT_S = 5
```

These settings limit simultaneous SSH-backed operations within one Python process. If all slots are busy for the timeout, the operation should fail fast with a user-facing busy response rather than creating unbounded SSH connections.

## Result Cache

Completed results are cached in process to avoid repeated remote parsing of the same completed job. The cache is bounded and non-durable.

Implications:

- restarting Uvicorn clears the cache
- clearing the cache does not affect cluster jobs
- Resume and Refresh Queue Status can still recover job state from SLURM/job records
- Load Results may parse remotely again after restart

## Submission Idempotency

Submission idempotency is enforced with remote-side state under the BMD logs area, including submission attempt records and lock state.

This is designed to prevent duplicate SLURM jobs from repeated submit requests with the same attempt id. It is not a substitute for a durable application database or per-user job ledger.

## Resource Policy

Current default resources:

```text
partition = leeburton-pool
nodes = 1
ntasks = 24
memory = 128G
walltime = 72:00:00
account = power-leeburton-users_v2
```

CPU counts, memory values, and queue are allow-listed in the backend. The account is fixed backend policy and is not user-editable.

## Future Production Work

Before broader production exposure, decide and document:

- systemd or equivalent process supervision
- reverse proxy and HTTPS termination
- authentication and authorization
- CSRF policy
- persistent job database or job history
- cross-process remote-operation limits if multiple workers are used
- log retention and incident response
