# Research experience ledger

The workflow tables remain the immutable source of truth. Cross-run learning and harness analysis
must use the versioned database views below instead of depending on one workflow's current status
or JSON layout.

## Target-explicit records

`research_experience_records_v1` exposes evaluations, tool attempts, Agent decisions, linked
artifacts, and lifecycle events through one stable schema. Every row has an explicit target
identity:

- `target_id`
- `target_name`
- `target_accession`
- `target_sequence_sha256`

It also retains the run specification hash, peptide sequence hash, generation, source SHA-256 and
the immutable source record IDs. `record_kind` distinguishes evidence types; consumers must not
merge unlike kinds or metric families merely because they share a view.

The target fields are derived through foreign-key joins rather than copied into every write table.
This avoids target drift while making target attribution mandatory at the analysis boundary.
Content-addressed `artifacts` remain globally deduplicated; their target meaning is supplied by the
`evidence_artifacts -> tool_calls -> experiment_runs -> targets` edge.

Example target-scoped metric query:

```sql
SELECT peptide_sequence, generation, record_name, numeric_value, unit, source_sha256
FROM research_experience_records_v1
WHERE target_accession = 'P0A9G6'
  AND record_kind = 'evaluation'
ORDER BY recorded_at;
```

## Provenance graph

`research_experience_edges_v1` exposes tool dependencies, Agent-decision edges, artifact edges,
candidate ancestry and generator edges. Every edge contains `target_id` and `run_id`, allowing a
future harness to reconstruct why a sequence was proposed or promoted without importing Temporal
workflow implementation details.

These views are read-only contracts. A schema change that alters their columns requires a new view
version rather than silently changing `v1`.
