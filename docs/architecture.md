# PepAgent MVP architecture

## System boundary

Codex proposes and supervises scientific work, but chat history is never canonical state. The
control plane accepts a versioned experiment specification and Temporal owns execution. PostgreSQL
owns identities, state, metrics and the append-only lifecycle; MinIO owns immutable byte artifacts;
MLflow owns model-release aliases and calibration experiments.

```mermaid
flowchart LR
  Codex["Codex scientific orchestrator"] --> API["FastAPI control plane"]
  API --> PG[(PostgreSQL)]
  API --> T["Temporal durable workflow"]
  T --> G1["PepMLM GPU worker"]
  T --> G2["Boltz-2 GPU worker"]
  G1 --> S3[(MinIO CAS)]
  G2 --> S3
  T --> PG
  MR["Model admission service"] --> PG
  MR --> S3
  MR --> ML["MLflow registry"]
```

## Active decision loop

1. Validate and hash the target-conditioned experiment specification.
2. PepMLM generates seeded candidates and computes conditional NLL/PPL.
3. A control activity persists the model call, environment, raw output, candidates and evaluations.
4. The lowest-PPL fraction is promoted to `structure_queued`.
5. Boltz-2 predicts a protein-chain/peptide-chain complex without invoking an affinity head.
6. A control activity persists the checkpoint manifest, complex files and confidence metrics.
7. Temporal finalizes the run only after all required evidence is committed.

No absolute-affinity metric is admitted in MVP v1. PepPAP is frozen. PPI-Affinity remains outside
the workflow until versioned code and weights are replayable. Rosetta FlexPepDock is a later P1
relative structural rescorer.

The Boltz-2 structure lane and affinity lane are separate capabilities. A peptide is represented as
a second protein chain for complex co-structure prediction; the official protein-small-molecule
affinity head remains hard-disabled. Structure confidence is never relabelled as peptide affinity.

## Non-negotiable evidence invariants

- A candidate sequence is unique within a run by SHA-256.
- A tool call is idempotent on run, tool/version, environment, weights, input, parameters and seed.
- Every successful model call has normalized input JSON, input/output hashes, executed weight hash,
  molecular-resource hash, environment hash, exact platform-release SHA-256, attempt number and
  raw-output artifact.
- Artifact keys are content hashes; database rows reference immutable `s3://` URIs.
- Metrics never stand alone: every evaluation references both a candidate and a tool call.
- Workflow retries may re-execute computation but cannot duplicate canonical evidence.
- A replay run keeps `parent_run_id` and the exact original `spec_json`/`spec_sha256`.
- Model releases must pass an admission gate before an MLflow `admitted` alias is assigned.

## Experiment-attempt graph contract

The product graph UI is deferred, but its canonical graph must be created by execution rather than
reconstructed from logs. Generation, screening, each structure sample, coordinate audit, snapshot
render, Codex snapshot review, Rosetta decoy aggregation and promotion decision are separate attempt
nodes. An attempt stores immutable input/output hashes, exact tool/model/prompt/render versions,
parameters, seed, status, timestamps, retries and artifact references. A many-to-many dependency
edge records relations such as `generated_from`, `evaluates`, `renders`, `reviews`, `refines` and
`selected_by`.

PostgreSQL owns nodes, edges and lifecycle state; MinIO owns the content-addressed bytes; Temporal
owns execution state and monitoring. A future graph is only a projection of those records.

## Snapshot-critic admission

Coordinate-derived interface checks remain the structural promotion gate. A deterministic
multi-view render bundle may be reviewed by a pinned multimodal Codex model as a shadow-mode critic
to detect and explain gross or combined failure modes. The review is evidence, not a fitness metric:
it cannot report affinity or silently change a score. It becomes decision-bearing only after a
versioned protein-peptide validation set shows stable incremental value over coordinate checks
alone. Every review stores model snapshot, prompt schema/hash, render recipe and camera parameters,
image hashes, structured output and raw response.

## Candidate lifecycle

```mermaid
stateDiagram-v2
  [*] --> generated
  generated --> ppl_scored: PepMLM evidence committed
  ppl_scored --> structure_queued: selection policy
  structure_queued --> structure_scored: Boltz evidence committed
  structure_scored --> selected: later promotion policy
  generated --> failed
  structure_queued --> failed
```

## Security and deployment boundaries

GPU workers connect outward only through two loopback-bound reverse SSH forwards: Temporal and the
MinIO S3 API. SSH authentication uses the installed DPAPI/ASKPASS helper. Remote worker concurrency
is one activity per process and each role is pinned to an explicitly selected GPU. PostgreSQL is not
exposed to the GPU server; control activities perform canonical database writes locally.
