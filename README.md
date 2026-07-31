# AMPgent

This is a durable, evidence-first peptide-design platform. Codex is the scientific control plane;
it does not keep canonical experiment state in chat history or ad-hoc output folders.

## MVP decision loop

1. PepMLM generates target-conditioned peptide candidates and reports conditional pseudo-PPL.
2. PostgreSQL assigns immutable candidate identities and records lifecycle events.
3. The lowest-PPL candidates enter Boltz-2 peptide–protein complex prediction.
4. Boltz-2 reports peptide-protein complex confidence; its affinity head is not used for peptides.
5. No absolute-affinity evaluator is admitted. PepPAP is frozen and PPI-Affinity remains outside the
   workflow. MVP-v2 admits Rosetta FlexPepDock/InterfaceAnalyzer only as a high-cost relative
   structural rescorer for already plausible pocket-localized poses; its REU values are never
   presented as experimental affinity or Kd.
6. Every admitted model call stores inputs, environment, weight hashes, raw outputs and parsed metrics in
   content-addressed storage, referenced by PostgreSQL. Temporal owns retries and recovery.

## Services

- PostgreSQL: canonical experiments, candidates, evaluations and append-only lifecycle history.
- MinIO: immutable content-addressed artifacts (structures, raw JSON, logs, environments).
- Temporal: durable multi-stage workflows, retry policy and resumption after process failure.
- MLflow: model releases and calibration experiments; it is not the candidate database.
- FastAPI: run submission and evidence queries.
- Model workers: isolated PepMLM and Boltz-2 GPU queues plus a dedicated Rosetta CPU queue. There is
  no absolute-affinity worker lane.

## Local control-plane bootstrap

```powershell
docker compose up -d --build
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\alembic upgrade head
```

Run the API and control worker in separate terminals:

```powershell
.\.venv\Scripts\pepagent-api
$env:PEPAGENT_WORKER_ROLE='control'
.\.venv\Scripts\pepagent-worker
```

GPU/model workers use the same package and Temporal namespace with roles `pepmlm` and `boltz2`.
Production secrets belong in an external secret store or local untracked `.env`, never
in experiment specifications or artifacts.

Scientific metric semantics and non-negotiable limitations are defined in
[`docs/metric-contract.md`](docs/metric-contract.md). Exact upstream revisions are pinned in
[`config/models.yaml`](config/models.yaml).

See [`docs/architecture.md`](docs/architecture.md) for component boundaries and evidence invariants,
and [`docs/runbook.md`](docs/runbook.md) for deployment, replay and recovery operations.

The validated MVP result and deliberately small v2 scope are summarized in
[`docs/MVP-v2-plan.zh-CN.md`](docs/MVP-v2-plan.zh-CN.md). Codex delivery and release rules are defined
in [`AGENTS.md`](AGENTS.md), and release history is in [`CHANGELOG.md`](CHANGELOG.md).
