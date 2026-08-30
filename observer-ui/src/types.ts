export type RunStatus = 'created' | 'submitted' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type StageStatus = 'pending' | 'running' | 'completed' | 'stopped'

export interface RunListItem {
  id: string
  name: string
  kind: string
  schema_version: string | null
  status: RunStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  candidate_count: number
  candidate_record_count: number
  excluded_candidate_count: number
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
  proposal_rank: number | null
  cohort: string
  pareto_front: number | null
  reasons: string[]
  display_eligible: true
  exclusion_reason: null
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
  display_population: DisplayPopulation
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
  run: RunListItem & { spec_sha256: string; workflow_id: string | null }
  display_population: DisplayPopulation
  counts: Record<string, number>
  branches: Branch[]
  admission: Record<string, boolean | number | null>
  tool_summary: Record<string, Record<string, number>>
  structure_counts: Record<string, Record<string, number>>
  checkpoints: Array<Record<string, unknown>>
  graph: { nodes: GraphStage[]; edges: GraphEdgeDetail[] }
  candidates: CandidatePreview[]
  candidate_exclusions: CandidateExclusion[]
  viewer: ViewerArtifact | null
  viewers: Record<string, ViewerArtifact | null>
  events: TimelineEvent[]
}
