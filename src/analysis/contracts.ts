/**
 * Stable boundary between the product shell and the deterministic analytics service.
 * Keep this file free of React and database implementation details.
 */

export type AnalysisGrain =
  | 'proposal_occurrence'
  | 'unique_sequence'
  | 'candidate_metric'
  | 'candidate_target_structure'

export type AnalysisStage =
  | 'raw_proposal'
  | 'deduplicated'
  | 'metric_complete'
  | 'safety_pass'
  | 'candidate_pool'
  | 'admitted'

export type AnalysisQuestion =
  | 'run_quality'
  | 'lineage_and_yield'
  | 'score_distribution'
  | 'filtering_loss'
  | 'generator_contribution'
  | 'safety_profile'
  | 'multi_objective_conflict'
  | 'structure_energy'
  | 'sequence_alluvial'
  | 'composition_landscape'
  | 'metric_correlation'
  | 'residue_enrichment'
  | 'candidate_laboratory'

export interface AnalysisQuerySpec {
  schemaVersion: 'analysis-query.1'
  runId: string
  grain: AnalysisGrain
  population: {
    generators: string[]
    stages: AnalysisStage[]
    cohortIds: string[]
    targetIds: string[]
  }
  measures: string[]
  groupBy: Array<'generator' | 'stage' | 'cohort' | 'target' | 'ood_status'>
  questions: AnalysisQuestion[]
  filters: Record<string, string | number | boolean | string[]>
}

export interface AnalysisProvenance {
  resultId: string
  queryId: string
  snapshotId: string
  computedAt: string
  source: 'analytics_api' | 'framework_fixture'
  method: string
  coverage: { observed: number; expected: number; missing: number; outOfDomain: number }
  warnings: string[]
}

export interface GeneratorYield {
  id: string
  label: string
  color: string
  raw: number
  unique: number
  metricComplete: number
  safetyPass: number
  candidatePool: number
  admitted: number
}

export interface DistributionSeries {
  generator: string
  stage: AnalysisStage
  count: number
  /** min, q1, median, q3, max */
  fiveNumberSummary: [number, number, number, number, number]
  missing: number
  outOfDomain: number
}

export interface ParetoPoint {
  id: string
  sequence: string
  generator: string
  activity: number
  hemolysis: number
  charge: number
  paretoRank: number
}

export interface CandidateRow {
  id: string
  sequence: string
  originSet: string[]
  stage: AnalysisStage
  activity: number
  hemolysis: number
  toxicity: number
  charge: number
  paretoRank: number
  flags: string[]
}

export interface AnalysisDataset {
  runLabel: string
  generators: GeneratorYield[]
  sourcePatterns: Array<{ label: string; count: number }>
  distributions: DistributionSeries[]
  pareto: ParetoPoint[]
  candidates: CandidateRow[]
  provenance: AnalysisProvenance
}

export interface DashboardCardDefinition {
  id: AnalysisQuestion
  title: string
  description: string
  defaultLayout: { x: number; y: number; w: number; h: number; minW: number; minH: number }
  compatibleGrains: AnalysisGrain[]
  extensionPoint: string
}
