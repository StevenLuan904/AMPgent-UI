/**
 * Row-level release snapshot and controlled pivot-query contracts.
 *
 * These types are intentionally UI-agnostic. The dashboard may choose any
 * compatible renderer, while the query registry remains the authority for
 * valid grain/dimension/measure/chart combinations.
 */

export type SnapshotSource = 'analytics_api' | 'frozen_release_snapshot' | 'framework_fixture'

export type PivotGrain = 'proposal_occurrence' | 'unique_candidate' | 'metric_evidence'

export type PivotDimensionKey =
  | 'generator'
  | 'origin_set'
  | 'stage'
  | 'metric'
  | 'cohort'
  | 'admission_status'
  | 'rejection_reason'
  | 'ood_status'

export type PivotMeasureKey =
  | 'record_count'
  | 'unique_sequence_count'
  | 'metric_count'
  | 'missing_count'
  | 'out_of_domain_count'
  | 'metric_mean'
  | 'metric_median'
  | 'metric_min'
  | 'metric_max'
  | 'metric_q1'
  | 'metric_q3'
  | 'share'
  | 'yield_rate'

export type PivotChartType =
  | 'kpi'
  | 'funnel'
  | 'bar'
  | 'stacked_bar'
  | 'boxplot'
  | 'violin'
  | 'histogram'
  | 'ecdf'
  | 'scatter'
  | 'parallel'
  | 'heatmap'
  | 'sankey'
  | 'table'

export type SnapshotStage =
  | 'raw_proposal'
  | 'deduplicated'
  | 'metric_complete'
  | 'safety_pass'
  | 'candidate_pool'
  | 'admitted'

export type AttributionMode = 'full' | 'fractional' | 'exclusive'

export interface SnapshotMetricEvidence {
  value: number | null
  text: string | null
  unit: string | null
  status: string
  outOfDomain: boolean
  limitations: string[]
  toolCallId: string
}

export interface SnapshotCandidate {
  id: string
  sequence: string
  sequenceSha256: string
  generation: number
  parentId: string | null
  status: string
  proposalRank: number | null
  originSet: string[]
  cohortSha256: string | null
  displayEligible: true
  exclusionReason: null
  admission: {
    status: string
    reasons: string[]
    paretoFront: number | null
    structureEligible: boolean
  }
  metrics: Record<string, SnapshotMetricEvidence>
}

export interface SnapshotOccurrence {
  id: string
  candidateId: string | null
  sequenceSha256: string
  generator: string
  generatorCell: string
  rank: number
  kind: string
  disposition: string
  displayEligible: true
  exclusionReason: null
}

export interface SnapshotCandidateExclusion {
  id: string
  sequenceSha256: string
  generation: number
  displayEligible: false
  exclusionReason: 'historical_exact_replay'
}

export interface SnapshotDisplayPopulation {
  candidateCount: number
  candidateRecordCount: number
  excludedCandidateCount: number
  occurrenceCount: number
  occurrenceRecordCount: number
  excludedOccurrenceCount: number
  exclusionReason: 'historical_exact_replay'
}

export interface AnalysisSnapshot {
  schemaVersion: 'ampgent-analysis-snapshot.1'
  snapshotId: string
  snapshotSha256: string
  generatedAt: string
  source: SnapshotSource
  run: {
    id: string
    status: string
    specSha256: string
    name: string
    startedAt: string | null
    finishedAt: string | null
  }
  cohorts: string[]
  generatorCells: Array<Record<string, unknown> & { generator: string; toolCallId: string }>
  occurrences: SnapshotOccurrence[]
  candidates: SnapshotCandidate[]
  candidateExclusions: SnapshotCandidateExclusion[]
  displayPopulation: SnapshotDisplayPopulation
  metricMethods: Record<string, Array<Record<string, unknown> & { toolCallId: string }>>
  admissionPolicy: Record<string, unknown> | null
  decisionMethods: Array<Record<string, unknown>>
  stageCheckpoints: Array<Record<string, unknown>>
  summary: {
    rawOccurrences: number
    uniqueCandidates: number
    promotedOccurrences: number
    invalidOccurrences: number
    observedEvaluations: number
    expectedEvaluations: number
    outOfDomainEvaluations: number
    admissionCounts: Record<string, number>
    structureEligible: number
  }
  coverage: { observed: number; expected: number; missing: number; outOfDomain: number }
  warnings: string[]
}

export interface MetricRangeFilter {
  metric: string
  min?: number
  max?: number
  includeMissing?: boolean
}

export interface PivotFilters {
  generators?: string[]
  originSets?: string[]
  stages?: SnapshotStage[]
  metrics?: string[]
  cohorts?: string[]
  admissionStatuses?: string[]
  rejectionReasons?: string[]
  oodStatuses?: Array<'in_domain' | 'out_of_domain'>
  candidateIds?: string[]
  metricRanges?: MetricRangeFilter[]
}

export interface AnalysisPivotQuery {
  schemaVersion: 'analysis-pivot-query.1'
  queryKey: AnalysisQueryKey
  grain?: PivotGrain
  dimensions?: PivotDimensionKey[]
  measures?: PivotMeasureKey[]
  filters?: PivotFilters
  metrics?: string[]
  chart?: PivotChartType
  attribution?: AttributionMode
  includeValues?: boolean
}

export type AnalysisQueryKey =
  | 'run_quality'
  | 'generator_funnel'
  | 'metric_distribution_by_generator'
  | 'metric_distribution_by_stage'
  | 'origin_composition'
  | 'admission_outcomes_by_generator'
  | 'rejection_reasons_by_generator'
  | 'coverage_by_metric'
  | 'pareto_conflicts'
  | 'candidate_table'

export interface ResolvedPivotQuery {
  schemaVersion: 'analysis-pivot-query.1'
  queryKey: AnalysisQueryKey
  grain: PivotGrain
  dimensions: PivotDimensionKey[]
  measures: PivotMeasureKey[]
  filters: PivotFilters
  metrics: string[]
  chart: PivotChartType
  attribution: AttributionMode
  includeValues: boolean
}

export interface PivotValidationIssue {
  code:
    | 'unknown_query_key'
    | 'unsupported_grain'
    | 'unsupported_dimension'
    | 'unsupported_measure'
    | 'unsupported_chart'
    | 'missing_metric'
    | 'unknown_metric'
    | 'invalid_metric_range'
    | 'incompatible_combination'
    | 'unsafe_cardinality'
  path: string
  message: string
}

export class AnalysisQueryRejectedError extends Error {
  readonly issues: PivotValidationIssue[]

  constructor(issues: PivotValidationIssue[]) {
    super(issues.map((issue) => issue.message).join('; '))
    this.name = 'AnalysisQueryRejectedError'
    this.issues = issues
  }
}

export interface DistributionSummary {
  count: number
  missing: number
  min: number | null
  q1: number | null
  median: number | null
  q3: number | null
  max: number | null
  mean: number | null
  values?: number[]
}

export interface AnalysisPivotResult {
  query: ResolvedPivotQuery
  queryId: string
  rows: Array<Record<string, string | number | boolean | null>>
  distributions?: Array<{
    key: Record<string, string>
    summary: DistributionSummary
  }>
  records?: Array<Record<string, unknown>>
  provenance: {
    snapshotId: string
    snapshotSha256: string
    source: SnapshotSource
    runId: string
    runStatus: string
    computedAt: string
    coverage: AnalysisSnapshot['coverage']
    warnings: string[]
  }
}

export type PivotSlotName = 'row' | 'column' | 'value' | 'category'
export type PivotFieldKey = PivotDimensionKey | PivotMeasureKey | 'metric_value'

export interface CardPivotSlots {
  row: PivotFieldKey[]
  column: PivotFieldKey[]
  value: PivotFieldKey[]
  category: PivotFieldKey[]
}

export interface AnalysisCardQueryState {
  cardId: string
  title: string
  revision: number
  query: ResolvedPivotQuery
  slots: CardPivotSlots
  chart: PivotChartType
  recommendedCharts: ChartRecommendation[]
  createdFrom: 'overview_rule' | 'card_library' | 'user'
}

export interface ChartRecommendation {
  chart: PivotChartType
  score: number
  reason: string
}

export type OverviewFlowNode =
  | 'generation'
  | 'deduplication'
  | 'scoring'
  | 'safety'
  | 'admission'
  | 'structure'
  | 'portfolio'

export interface OverviewAnalysisSelection {
  nodeIds: OverviewFlowNode[]
  metrics: string[]
  generators?: string[]
  cohortIds?: string[]
}

export interface CardRuleRejection {
  code:
    | 'empty_selection'
    | 'unknown_metric'
    | 'node_unavailable'
    | 'metric_required'
    | 'incompatible_slot'
    | 'duplicate_field'
    | 'slot_capacity'
    | 'chart_incompatible'
    | 'card_too_small'
    | 'unsafe_cardinality'
  path: string
  message: string
}

export class AnalysisCardRejectedError extends Error {
  readonly issues: CardRuleRejection[]

  constructor(issues: CardRuleRejection[]) {
    super(issues.map((issue) => issue.message).join('; '))
    this.name = 'AnalysisCardRejectedError'
    this.issues = issues
  }
}

export interface OverviewCardGenerationResult {
  cards: AnalysisCardQueryState[]
  rejections: CardRuleRejection[]
}

export type CardPresentationMode = 'compact' | 'standard' | 'expanded'
export type CardVisualElement =
  | 'title'
  | 'primary_value'
  | 'primary_chart'
  | 'axes'
  | 'legend'
  | 'filters'
  | 'summary'
  | 'details'
  | 'warnings'
  | 'provenance'
  | 'actions'

export interface CardGridSize {
  width: number
  height: number
}

export interface CardPresentationPlan {
  mode: CardPresentationMode
  effectiveChart: PivotChartType
  visible: CardVisualElement[]
  hidden: Array<{ element: CardVisualElement; reason: string }>
  minimumSize: CardGridSize
  density: 'tight' | 'comfortable' | 'spacious'
  showLabels: 'key_only' | 'selected' | 'all'
}
