# Changelog

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

