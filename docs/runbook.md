# Operator runbook

## Local control plane

```powershell
docker compose up -d --build
.\.venv-local\Scripts\python -m alembic upgrade head
.\.venv-local\Scripts\python -m uvicorn pepagent.api.main:app --host 127.0.0.1 --port 8080
$env:PEPAGENT_WORKER_ROLE='control'
.\.venv-local\Scripts\python -m pepagent.workers.temporal_worker
```

Import the versioned pocket catalog after migrations. The command is idempotent: changed source
records create new immutable evidence rows, while an unchanged catalog does not duplicate them.

```powershell
.\.venv-local\Scripts\python -m pepagent.cli import-pockets config\pockets\mvp_v2_pocket_catalog.yaml
Invoke-RestMethod http://127.0.0.1:8080/v1/targets/by-accession/P0A9G6/pockets
```

Only records with `conditioning_enabled=true` may supply Boltz pocket constraints. Evidence grade
and conditioning priority are separate fields; a high-grade composite or payload interface can
still be excluded.

Run the Temporal and object-store reverse tunnels in separate supervised terminals:

```powershell
.\deploy\tunnels\start_synth_temporal_tunnel.ps1
.\deploy\tunnels\start_synth_object_store_tunnel.ps1
```

## Remote workers

Deploying a source release is a two-step operation: activate the content-addressed archive, then
reinstall that exact release into the managed worker environment.

```powershell
gpu-synth -RemoteCommand 'bash /sdd_data/pepagent/bootstrap/activate_platform_release_synth.sh <archive-sha256>'
gpu-synth -RemoteCommand 'env UV_CACHE_DIR=/sdd_data/pepagent/uv-cache /sdd_data/pepagent/runtime/uv-0.11.12/bin/uv pip install --python /sdd_data/pepagent/envs/gpu-worker-py311-v1/bin/python --reinstall --no-deps /sdd_data/pepagent/platform/current'
```

The worker launcher resolves the active archive SHA-256 from the content-addressed release path and
adds it to every runtime environment manifest. Do not infer the environment path from an interactive
shell; workers always use the managed `gpu-worker-py311-v1` environment.

Probe GPU ownership and capacity before launch. Never stop an unverified PID.

```powershell
gpu-synth -RemoteCommand 'nvidia-smi --query-gpu=index,memory.free,memory.total,utilization.gpu --format=csv,noheader'
gpu-synth -RemoteCommand 'bash /sdd_data/pepagent/platform/current/deploy/remote/start_worker_synth.sh pepmlm 7'
gpu-synth -RemoteCommand 'bash /sdd_data/pepagent/platform/current/deploy/remote/start_worker_synth.sh boltz2 6'
```

Bootstrap and launch the Rosetta CPU worker only from the pinned quarterly wheel. The bootstrap
script rejects a wheel whose SHA-256 differs from the committed release identity.

```powershell
gpu-synth -RemoteCommand 'bash /sdd_data/pepagent/platform/current/deploy/remote/bootstrap_pyrosetta_synth.sh'
gpu-synth -RemoteCommand 'bash /sdd_data/pepagent/platform/current/deploy/remote/start_worker_synth.sh rosetta cpu'
```

`PEPAGENT_ROSETTA_CONCURRENCY` may be set explicitly after checking CPU load and memory. It controls
concurrent Temporal activities, not the number of decoys. Keep the default of one on shared or
uninspected hosts.

## Submit and inspect

```powershell
.\.venv-local\Scripts\python -m pepagent.cli submit config\experiments\acea_smoke_v1.yaml
Invoke-RestMethod http://127.0.0.1:8080/v1/runs/<run-id>
Invoke-RestMethod http://127.0.0.1:8080/v1/runs/<run-id>/candidates
Invoke-RestMethod http://127.0.0.1:8080/v1/runs/<run-id>/evidence
```

`POST /v1/runs/<run-id>/replay` creates a child run with the original raw specification and hash.
An old run containing a now-frozen evaluator is rejected rather than silently migrated.

## Model admission

```powershell
.\.venv-local\Scripts\python -m pepagent.cli register-pepmlm model-cache\PepMLM-650M
.\.venv-local\Scripts\python -m pepagent.cli register-boltz2 model-cache\boltz2
Invoke-RestMethod http://127.0.0.1:8080/v1/model-releases
```

Registration verifies the expected checkpoint SHA-256, copies all release files to content-addressed
MinIO objects, writes the canonical PostgreSQL release, creates an MLflow model version and assigns
the `admitted` alias. Failed or frozen evaluators must not receive this alias.

## Recovery expectations

- If a GPU worker is absent, the Temporal activity remains scheduled without losing the run.
- A worker restart resumes polling the same queue; no manual resubmission is required.
- Model subprocesses heartbeat every 30 seconds; retries use exponential backoff.
- After retry exhaustion, `mark_run_failed` records a terminal lifecycle event and error summary.
- Local work directories are caches only. A run is complete only when PostgreSQL and MinIO evidence
  queries succeed.
