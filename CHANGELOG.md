# Changelog

## Unreleased

- Added a configurable lightweight-first evaluation ladder. Open sequence AMP,
  MIC, toxicity, hemolysis, physicochemical, and PepMLM evidence can drive
  early selection before any structure work is authorized.
- Added `manual`, `late_elite`, and evidence-triggered structure escalation,
  with an independently configurable Rosetta escalation tier. Every Research
  Director decision now records the chosen tier and its reasons alongside the
  evidence hashes.
- Added durable Temporal cancellation reconciliation so cancelled runs and
  still-queued candidates cannot remain incorrectly marked as running.
- Added `acea_autoresearch_v11_lightweight.yaml`, which intentionally holds
  Boltz and Rosetta while six lightweight generations optimize multiple soft
  AMP/MIC/safety signals without treating any predictor as experimental truth.

- Replaced the incomplete stability proxy path with the exact Guruprasad instability index from
  Biopython ProtParam, cross-checked against the 919 pMHCDiff implementation. The metric is a hard,
  lower-is-better qualification in the historical AceA v5 experiment.
- Added proposal-stage AMP physicochemical descriptors (molecular weight, pI, charge at pH 7.4,
  GRAVY, K/R fraction, and Eisenberg hydrophobic moment), soft qualification ordering, and an AceA
  v10 policy that demotes uncalibrated short-peptide heuristics and excludes unsupported predictors.
- Restored ToxinPred3 as conflict-preserving soft evidence and upgraded MIC evaluation to parallel,
  separately reported LLAMP and open-weight AMP-READ inference without cross-model averaging.
- Generalized GPU worker deployment roots for data0/data1 hosts, added fail-closed GPU occupancy
  checks, and extended the public melittin replay to cover AMP-READ alongside LLAMP.
- Added a task-independent search-regime architecture: persisted distribution diagnostics,
  plateau-versus-collapse discrimination and a versioned E0--E4 escalation ladder from sampler
  broadening through independent-generator challenge and model redesign.

## v0.2.0 — MVP-v2

- Added a dedicated, durable Rosetta CPU lane using pinned PyRosetta 2026.29, FlexPepDock
  prepack/refinement, `ref2015` and InterfaceAnalyzer `dG_separated`.
- Added standard FlexPepDock `reweighted_sc` ranking, top-ten median dG aggregation, deterministic
  decoy seeds and byte-replayable content-addressed PDB/JSON evidence.
- Added explicit tool-call dependency edges, validation workflows and replayable post-run
  native-start accuracy summaries.
- Validated 2DS8, 1NVR and Rosetta's official 1ER8 benchmark input with 200 decoys each. All three
  completed with near-native refined ensembles and complete PostgreSQL/MinIO evidence.
- Kept the visual snapshot critic as a separately deployed, non-blocking shadow module. It is not a
  metric and is absent from the Auto Research decision workflow.

Scientific limitation: Rosetta energy units are admitted only for relative structural rescoring
within a fixed target and protocol. They are not kcal/mol, experimental affinity or Kd, and the
native-start validation does not establish blind pocket discovery.

## v0.1.0 — MVP

- Added a durable PepMLM → Boltz-2 design workflow backed by Temporal.
- Added PostgreSQL candidate lifecycle state, MinIO content-addressed evidence, and MLflow model
  release records.
- Added immutable model, environment, input, output, and source hashes for replay.
- Validated interruption recovery with an AceA smoke run.
- Kept peptide affinity outside the admitted workflow: PepPAP is frozen, PPI-Affinity is not
  replayable, and Rosetta is deferred to relative structural rescoring.

Known limitation: the first blind AceA candidate had weak protein–peptide interface confidence and
is not a biological hit or affinity claim.
