import type {
  AnalysisQueryKey,
  PivotChartType,
  PivotDimensionKey,
  PivotGrain,
  PivotMeasureKey,
  ResolvedPivotQuery,
} from './analysisDataContracts'

export interface AnalysisQueryDefinition {
  key: AnalysisQueryKey
  label: string
  purpose: string
  defaultQuery: Omit<ResolvedPivotQuery, 'queryKey' | 'schemaVersion' | 'filters' | 'metrics'> & {
    filters?: ResolvedPivotQuery['filters']
    metrics?: string[]
  }
  allowedGrains: PivotGrain[]
  allowedDimensions: PivotDimensionKey[]
  allowedMeasures: PivotMeasureKey[]
  allowedCharts: PivotChartType[]
  minimumMetrics: number
  maximumMetrics: number
}

const define = (definition: AnalysisQueryDefinition) => definition

export const analysisQueryRegistry: Record<AnalysisQueryKey, AnalysisQueryDefinition> = {
  run_quality: define({
    key: 'run_quality',
    label: 'Run quality',
    purpose: 'Coverage, missingness, OOD, and population size.',
    defaultQuery: {
      grain: 'unique_candidate', dimensions: [],
      measures: ['record_count', 'unique_sequence_count'], chart: 'kpi', attribution: 'exclusive', includeValues: false,
    },
    allowedGrains: ['proposal_occurrence', 'unique_candidate', 'metric_evidence'],
    allowedDimensions: ['generator', 'origin_set', 'stage', 'metric', 'admission_status', 'ood_status'],
    allowedMeasures: ['record_count', 'unique_sequence_count', 'metric_count', 'missing_count', 'out_of_domain_count', 'share'],
    allowedCharts: ['kpi', 'bar', 'stacked_bar', 'heatmap', 'table'], minimumMetrics: 0, maximumMetrics: 11,
  }),
  generator_funnel: define({
    key: 'generator_funnel', label: 'Generator funnel', purpose: 'Loss from raw proposals to admitted candidates.',
    defaultQuery: {
      grain: 'unique_candidate', dimensions: ['generator', 'stage'],
      measures: ['unique_sequence_count', 'yield_rate'], chart: 'funnel', attribution: 'fractional', includeValues: false,
    },
    allowedGrains: ['unique_candidate'], allowedDimensions: ['generator', 'origin_set', 'stage', 'cohort'],
    allowedMeasures: ['record_count', 'unique_sequence_count', 'share', 'yield_rate'],
    allowedCharts: ['funnel', 'bar', 'stacked_bar', 'sankey', 'table'], minimumMetrics: 0, maximumMetrics: 0,
  }),
  metric_distribution_by_generator: define({
    key: 'metric_distribution_by_generator', label: 'Metric by generator', purpose: 'Compare scorer distributions across generators.',
    defaultQuery: {
      grain: 'metric_evidence', dimensions: ['generator', 'metric'],
      measures: ['metric_count', 'metric_median', 'metric_q1', 'metric_q3'], chart: 'boxplot', attribution: 'fractional', includeValues: true,
    },
    allowedGrains: ['metric_evidence'], allowedDimensions: ['generator', 'origin_set', 'stage', 'metric', 'cohort', 'admission_status', 'ood_status'],
    allowedMeasures: ['record_count', 'metric_count', 'missing_count', 'out_of_domain_count', 'metric_mean', 'metric_median', 'metric_min', 'metric_max', 'metric_q1', 'metric_q3'],
    allowedCharts: ['boxplot', 'violin', 'histogram', 'ecdf', 'bar', 'heatmap', 'table'], minimumMetrics: 1, maximumMetrics: 4,
  }),
  metric_distribution_by_stage: define({
    key: 'metric_distribution_by_stage', label: 'Metric by stage', purpose: 'Compare score drift through selection stages.',
    defaultQuery: {
      grain: 'metric_evidence', dimensions: ['stage', 'metric'],
      measures: ['metric_count', 'metric_median', 'metric_q1', 'metric_q3'], chart: 'boxplot', attribution: 'exclusive', includeValues: true,
    },
    allowedGrains: ['metric_evidence'], allowedDimensions: ['generator', 'origin_set', 'stage', 'metric', 'cohort', 'admission_status', 'ood_status'],
    allowedMeasures: ['record_count', 'metric_count', 'missing_count', 'out_of_domain_count', 'metric_mean', 'metric_median', 'metric_min', 'metric_max', 'metric_q1', 'metric_q3'],
    allowedCharts: ['boxplot', 'violin', 'histogram', 'ecdf', 'bar', 'heatmap', 'table'], minimumMetrics: 1, maximumMetrics: 4,
  }),
  origin_composition: define({
    key: 'origin_composition', label: 'Origin composition', purpose: 'Exclusive and shared generator-origin sets.',
    defaultQuery: {
      grain: 'unique_candidate', dimensions: ['origin_set', 'stage'],
      measures: ['unique_sequence_count', 'share'], chart: 'stacked_bar', attribution: 'exclusive', includeValues: false,
    },
    allowedGrains: ['unique_candidate'], allowedDimensions: ['origin_set', 'stage', 'cohort', 'admission_status'],
    allowedMeasures: ['record_count', 'unique_sequence_count', 'share', 'yield_rate'],
    allowedCharts: ['bar', 'stacked_bar', 'sankey', 'table'], minimumMetrics: 0, maximumMetrics: 0,
  }),
  admission_outcomes_by_generator: define({
    key: 'admission_outcomes_by_generator', label: 'Admission outcome', purpose: 'Admission result by source generator.',
    defaultQuery: {
      grain: 'unique_candidate', dimensions: ['generator', 'admission_status'],
      measures: ['unique_sequence_count', 'share'], chart: 'stacked_bar', attribution: 'fractional', includeValues: false,
    },
    allowedGrains: ['unique_candidate'], allowedDimensions: ['generator', 'origin_set', 'admission_status', 'cohort'],
    allowedMeasures: ['record_count', 'unique_sequence_count', 'share'],
    allowedCharts: ['bar', 'stacked_bar', 'heatmap', 'table'], minimumMetrics: 0, maximumMetrics: 0,
  }),
  rejection_reasons_by_generator: define({
    key: 'rejection_reasons_by_generator', label: 'Rejection reason', purpose: 'Gate and stability losses by source.',
    defaultQuery: {
      grain: 'unique_candidate', dimensions: ['generator', 'rejection_reason'],
      measures: ['unique_sequence_count', 'share'], chart: 'heatmap', attribution: 'fractional', includeValues: false,
    },
    allowedGrains: ['unique_candidate'], allowedDimensions: ['generator', 'origin_set', 'rejection_reason', 'admission_status'],
    allowedMeasures: ['record_count', 'unique_sequence_count', 'share'],
    allowedCharts: ['bar', 'stacked_bar', 'heatmap', 'table'], minimumMetrics: 0, maximumMetrics: 0,
  }),
  coverage_by_metric: define({
    key: 'coverage_by_metric', label: 'Metric coverage', purpose: 'Observed, missing, and OOD evidence by metric.',
    defaultQuery: {
      grain: 'metric_evidence', dimensions: ['metric', 'ood_status'],
      measures: ['metric_count', 'missing_count', 'out_of_domain_count'], chart: 'stacked_bar', attribution: 'exclusive', includeValues: false,
    },
    allowedGrains: ['metric_evidence'], allowedDimensions: ['metric', 'generator', 'stage', 'ood_status', 'admission_status'],
    allowedMeasures: ['metric_count', 'missing_count', 'out_of_domain_count', 'share'],
    allowedCharts: ['bar', 'stacked_bar', 'heatmap', 'table'], minimumMetrics: 0, maximumMetrics: 11,
  }),
  pareto_conflicts: define({
    key: 'pareto_conflicts', label: 'Pareto conflicts', purpose: 'Inspect candidates where objectives cannot improve together.',
    defaultQuery: {
      grain: 'metric_evidence', dimensions: ['generator'],
      measures: ['metric_count'], chart: 'scatter', attribution: 'fractional', includeValues: true,
    },
    allowedGrains: ['metric_evidence'], allowedDimensions: ['generator', 'origin_set', 'stage', 'admission_status', 'cohort'],
    allowedMeasures: ['metric_count'], allowedCharts: ['scatter', 'parallel', 'table'], minimumMetrics: 2, maximumMetrics: 7,
  }),
  candidate_table: define({
    key: 'candidate_table', label: 'Candidate table', purpose: 'Auditable candidate records with selected metrics.',
    defaultQuery: {
      grain: 'unique_candidate', dimensions: [], measures: [], chart: 'table', attribution: 'exclusive', includeValues: true,
    },
    allowedGrains: ['unique_candidate'], allowedDimensions: ['generator', 'origin_set', 'stage', 'cohort', 'admission_status', 'rejection_reason'],
    allowedMeasures: ['record_count'], allowedCharts: ['table'], minimumMetrics: 0, maximumMetrics: 11,
  }),
}

export const analysisQueryKeys = Object.freeze(Object.keys(analysisQueryRegistry) as AnalysisQueryKey[])
