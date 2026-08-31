export type RunStatus = 'created' | 'submitted' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type StageStatus = 'pending' | 'running' | 'completed' | 'stopped'

export interface RunListItem {
  id: string
  name: string
  kind: string
  schema_version: string | null
  status: RunStatus
  temporal_workflow_id?: string | null
  temporal_run_id?: string | null
  workflow_id?: string | null
  generation_population?: GenerationPopulation
  generation_quality_gate?: GenerationQualityGate
  scientific_run_status?: ScientificRunStatus
  temporal_observability?: TemporalObservability
  created_at: string
  started_at: string | null
  finished_at: string | null
  candidate_count: number
  candidate_record_count?: number
  excluded_candidate_count?: number
  tool_call_count: number
  structure_record_count: number
}

export interface RunListResponse {
  source: string
  read_only: boolean
  runs: RunListItem[]
}

export interface GraphStage {
  id: string
  label: string
  kind: 'data' | 'model' | 'decision' | 'structure' | 'review'
  group: 'inputs' | 'design' | 'evaluation' | 'decision' | 'structure' | 'review'
  status: StageStatus
  current: number
  total: number
  provenance: 'database' | 'derived' | 'missing'
  generation_population?: GenerationPopulation
  generation_quality_gate?: GenerationQualityGate
  insight: {
    grade: 'good' | 'okay' | 'fair' | 'bad' | 'neutral'
    verdict: string
    reason: string
    facts: Array<{ label: string; value: string }>
    source: 'observer_summary' | 'persisted_decision'
  }
}

export interface CandidateMetric {
  name: string
  value: number | null
  text: string | null
  unit: string | null
  status: string
  out_of_domain: boolean
}

export interface CandidatePreview {
  id: string
  sequence: string
  length: number
  generation?: number
  proposal_rank: number | null
  cohort: string
  pareto_front: number | null
  reasons: string[]
  display_eligible?: true
  exclusion_reason?: null
  metrics: CandidateMetric[]
}

export interface CandidateExclusion {
  id: string
  sequence_sha256: string
  generation: number
  display_eligible: false
  exclusion_reason: 'historical_exact_replay'
}

export interface DisplayPopulation {
  candidate_count: number
  candidate_record_count: number
  excluded_candidate_count: number
  exclusion_reason: 'historical_exact_replay'
}

export interface GenerationPopulation {
  baseline_candidate_count: number
  descendant_candidate_count: number
  max_generation: number
}

export interface GenerationQualityRule {
  metric_key: 'guruprasad_instability_index' | 'maximum_hydrophobic_run' | 'hydrophobic_fraction' | 'net_charge_ph7_4'
  comparison: '<' | '<=' | '>='
  threshold: number
  unit: 'dimensionless' | 'residues' | 'fraction' | 'elementary_charge'
}

export interface GenerationQualityGate {
  status: 'applied' | 'not_applied'
  operator_name: string
  operator_version: string
  proposal_count: number
  prefilter_pass_count: number
  materialized_descendant_count: number
  evaluated_descendant_count: number
  count_scope: {
    source: 'postgresql'
    run_id: string
    operator_id: string
  }
  semantics: {
    proposal_and_prefilter_pass_are_not_materialized_descendants: true
    materialized_descendant_requires_persisted_lineage_edge: true
    evaluated_descendant_requires_persisted_evaluation: true
    offline_validation_included: false
  }
  rules: GenerationQualityRule[]
}

export interface ScientificRunStatus {
  status: RunStatus
  source: 'postgresql'
  run_id: string
}

export interface TemporalObservability {
  status: 'identity_recorded' | 'identity_missing'
  temporal_workflow_id: string | null
  temporal_run_id: string | null
  history_read_status: string
  history_read_error: string | null
  affects_scientific_run_status: false
}

export interface ViewerArtifact {
  candidate_id: string
  sequence: string
  target_id: string
  target_name: string
  lane: string
  seed: number
  artifact_sha256: string
  media_type: string
  artifact_url: string
}

export interface Branch {
  order: number
  key: string
  role: string
  status: string
  target_id: string
  target_name: string
  organism: string | null
  accession: string | null
  sequence: string
  sequence_length: number
  evidence_namespace: string
  coordinate_sha256: string
}

export interface TimelineEvent {
  sequence_no: number
  type: string
  actor: string
  payload: Record<string, unknown>
  occurred_at: string
}

export interface GraphEdgeDetail {
  source: string
  target: string
  label: string | null
  rationale: string
  provenance: 'database' | 'topology'
}

export interface ToolArtifact {
  role: string
  sha256: string
  size_bytes: number
  media_type: string
  url: string
}

export interface ToolAttempt {
  id: string
  tool_name: string
  tool_version: string
  status: string
  attempt: number
  queued_at: string
  started_at: string | null
  finished_at: string | null
  duration_seconds: number | null
  random_seed: number | null
  model_uri: string | null
  weights_sha256: string | null
  environment_sha256: string
  input_sha256: string
  output_sha256: string | null
  inputs: unknown
  parameters: unknown
  error: unknown
  structure_context?: Array<{
    candidate_sequence: string
    target: string
    lane: string
    seed: number
    kind: string
    records: number
  }>
  artifacts: ToolArtifact[]
}

export interface MetricSummary {
  count: number
  numeric_count: number
  mean: number | null
  min: number | null
  max: number | null
  unit: string | null
  out_of_domain: number
  status_counts: Record<string, number>
}

export interface NodeDetail {
  source: string
  read_only: boolean
  node_id: string
  display_population?: DisplayPopulation
  generation_population?: GenerationPopulation
  generation_quality_gate?: GenerationQualityGate
  narrative: string[]
  calls: ToolAttempt[]
  metrics: Record<string, MetricSummary>
  reasoning: {
    decisions: Array<Record<string, unknown>>
    status_counts: Record<string, number>
    reason_counts: Record<string, number>
    considered: number
    admitted: number
  }
  structure_results: Array<{
    kind: string
    lane: string
    target: string
    records: number
    seeds: number
  }>
}

export interface RunDetail {
  source: string
  read_only: boolean
  updated_at: string
  run: RunListItem & { spec_sha256: string }
  counts: Record<string, number>
  branches: Branch[]
  admission: Record<string, boolean | number | null>
  tool_summary: Record<string, Record<string, number>>
  structure_counts: Record<string, Record<string, number>>
  checkpoints: Array<Record<string, unknown>>
  display_population?: DisplayPopulation
  generation_population?: GenerationPopulation
  generation_quality_gate?: GenerationQualityGate
  graph: { nodes: GraphStage[]; edges: GraphEdgeDetail[] }
  candidates: CandidatePreview[]
  candidate_exclusions?: CandidateExclusion[]
  viewer: ViewerArtifact | null
  viewers: Record<string, ViewerArtifact | null>
  events: TimelineEvent[]
}
