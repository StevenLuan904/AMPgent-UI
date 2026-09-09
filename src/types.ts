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
  /** Explicit root run for a generator round; never inferred from title or evidence names. */
  root_generation_run_id?: string | null
  /** Explicit member run ids for a generator round, including evidence-only members. */
  member_run_ids?: string[] | null
  /** Optional backend display label for the authoritative generator round. */
  display_round?: string | null
  /** Explicit source relation for a post-generation evidence run. */
  source_run_id?: string | null
  /** Backend-declared role; UI never derives this from tool names. */
  run_role?: 'generator' | 'evidence' | 'member' | string | null
  authoritative_candidate_id?: string | null
  /** Backend summary for the authoritative generator round; never inferred by UI. */
  round_summary?: {
    evaluation_count?: number | null
    operation_type_count?: number | null
    scientific_card_count?: number | null
    scientific_card_types?: string[] | null
  } | null
}

export interface RunListResponse {
  source: string
  read_only: boolean
  runs: RunListItem[]
  page?: {
    total_rounds?: number
    readable_rounds?: number
    offset?: number
    limit?: number
    order?: 'informative' | 'recent' | string
    next_offset?: number | null
  }
}

export interface GraphStage {
  id: string
  label: string
  kind: 'data' | 'model' | 'decision' | 'structure' | 'review' | 'tool'
  group: 'inputs' | 'design' | 'evaluation' | 'decision' | 'structure' | 'review' | 'observed'
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
  runtime?: RuntimeNodeMeta
}

export interface RuntimeNodeMeta {
  node_type: 'tool_call' | 'tool_group' | 'event_group' | 'batch_group' | 'tool_summary_group' | 'tool_summary' | 'lifecycle_event' | 'scientific_stage' | 'generation' | 'population_summary' | 'candidate_group' | 'candidate_preview' | 'structure_evidence'
  source_id: string
  observed_at: string | null
  actor?: string
  tool_name?: string
  attempt?: number
  explicit_relation_count?: number
  candidate_count?: number
  child_ids?: string[]
  event_ids?: string[]
  grouping_basis?: string
  expanded?: boolean
  status_breakdown?: string
  observed_span?: string
  raw_label?: string
  summary_tools?: RuntimeSummaryTool[]
  summary_only?: boolean
  latest_iteration?: number
  activity_retry_count?: number
  max_activity_attempt?: number
  preview_index?: number
  preview_total?: number | null
  population_scope?: 'display_population' | 'generation_population' | 'mixed'
  has_viewer?: boolean
  /** Explicit backend/source key used to retrieve structure evidence. */
  viewer_key?: string
  /** Why viewer_key was selected; shown in inspector, never inferred from UI text. */
  viewer_mapping_basis?: '后端 viewer 键' | '后端节点 viewer' | '限定工具名映射'
  /** Stable evidence source key, independent from any structure viewer key. */
  evidence_key?: string
  /** Metric/result distribution lookup key, independent from viewer_key. */
  distribution_key?: string
  parallel_group_id?: string
}

export interface RuntimeSummaryTool {
  tool_name: string
  display_name: string
  summary_count: number
  materialized_count: number
  missing_count: number
  status_counts: Record<string, number>
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
  parent_id?: string | null
  generator_call_id?: string | null
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
  status: 'healthy' | 'degraded' | 'unavailable' | 'unknown'
  source: 'postgresql_operational_evidence'
  observed_at: string | null
  history_read_status: 'succeeded' | 'failed' | 'unavailable' | 'not_queried'
  history_read_error_category: SchedulerErrorCategory | null
  scheduler_error_category: SchedulerErrorCategory | null
  stale_after_seconds: 300
  is_stale: boolean
  postgresql_run_id: string
  temporal_workflow_id: string | null
  temporal_run_id: string | null
  evidence_type: string | null
  affects_scientific_run_status: false
}

export type SchedulerErrorCategory = 'timeout' | 'connectivity' | 'unavailable' | 'permission' | 'not_found' | 'cancelled' | 'application_error' | 'unknown'

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
  provenance: 'database' | 'topology' | 'derived'
  relation_kind?: 'dependency' | 'retry' | 'fallback' | 'parallel' | 'sequence' | 'association' | 'lineage' | 'grouping'
}

export interface ToolArtifact {
  role: string
  sha256: string
  size_bytes: number
  media_type: string
  url: string
}

export interface ToolCallRelation {
  direction: 'upstream' | 'downstream'
  related_call_id: string
  relation_type: string
}

export interface ToolAttempt {
  id: string
  tool_name: string
  /** Present only when a lifecycle event supplied a semantic activity name. */
  activity_type?: string
  /** False means the lifecycle payload did not provide an attempt number. */
  attempt_observed?: boolean
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
  /** Explicit persisted ToolCallDependency records returned by Observer. */
  relations?: ToolCallRelation[]
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
  /** Optional cursor contract; older Observer responses omit it. */
  calls_window?: {
    limit?: number
    next_cursor?: string | null
    has_more?: boolean
    remaining?: number
    total?: number
  }
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
  viewer?: ViewerArtifact | null
  viewers?: Record<string, ViewerArtifact | null>
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
  /** Optional forward-compatible cursor contract; older Observer payloads omit it. */
  event_window?: {
    limit?: number
    next_cursor?: string | null
    has_more?: boolean
    /** Server-reported count of older events not included in this payload. */
    remaining?: number
  }
}
