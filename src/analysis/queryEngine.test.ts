import { describe, expect, it } from 'vitest'
import { AnalysisQueryRejectedError, type AnalysisPivotQuery } from './analysisDataContracts'
import { executeAnalysisQuery, resolveAndValidateQuery } from './queryEngine'
import { analysisQueryKeys, analysisQueryRegistry } from './queryRegistry'
import { createTestSnapshot } from './testSnapshot'

const snapshot = createTestSnapshot()
const query = (overrides: Partial<AnalysisPivotQuery> = {}): AnalysisPivotQuery => ({
  schemaVersion: 'analysis-pivot-query.1',
  queryKey: 'metric_distribution_by_generator',
  metrics: ['activity'],
  ...overrides,
})

function rejection(overrides: Partial<AnalysisPivotQuery>) {
  try {
    resolveAndValidateQuery(snapshot, query(overrides))
    throw new Error('expected query to be rejected')
  } catch (error) {
    expect(error).toBeInstanceOf(AnalysisQueryRejectedError)
    return error as AnalysisQueryRejectedError
  }
}

describe('controlled query registry', () => {
  it('registers all public query keys', () => {
    expect(analysisQueryKeys).toHaveLength(10)
    expect(Object.keys(analysisQueryRegistry).sort()).toEqual([...analysisQueryKeys].sort())
  })

  it.each([
    ['run_quality', []],
    ['generator_funnel', []],
    ['metric_distribution_by_generator', ['activity']],
    ['metric_distribution_by_stage', ['activity']],
    ['origin_composition', []],
    ['admission_outcomes_by_generator', []],
    ['rejection_reasons_by_generator', []],
    ['coverage_by_metric', []],
    ['pareto_conflicts', ['activity', 'safety']],
    ['candidate_table', []],
  ] as const)('resolves registered preset %s', (queryKey, metrics) => {
    expect(resolveAndValidateQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey, metrics: [...metrics],
    }).queryKey).toBe(queryKey)
  })

  it('rejects an unknown query key at runtime', () => {
    expect(rejection({ queryKey: 'not-real' as never }).issues[0].code).toBe('unknown_query_key')
  })

  it('rejects an unsupported grain', () => {
    expect(rejection({ grain: 'proposal_occurrence' }).issues.some((issue) => issue.code === 'unsupported_grain')).toBe(true)
  })

  it('rejects unsupported dimensions', () => {
    expect(rejection({ dimensions: ['rejection_reason'] }).issues[0].code).toBe('unsupported_dimension')
  })

  it('rejects duplicate dimensions', () => {
    expect(rejection({ dimensions: ['generator', 'generator'] }).issues.some((issue) => issue.message.includes('Duplicate'))).toBe(true)
  })

  it('rejects more than four pivot dimensions', () => {
    expect(rejection({ dimensions: ['generator', 'origin_set', 'stage', 'metric', 'cohort'] }).issues.some((issue) => issue.code === 'unsafe_cardinality')).toBe(true)
  })

  it('rejects unsupported measures', () => {
    expect(rejection({ measures: ['yield_rate'] }).issues.some((issue) => issue.code === 'unsupported_measure')).toBe(true)
  })

  it('rejects unsupported chart types', () => {
    expect(rejection({ chart: 'sankey' }).issues.some((issue) => issue.code === 'unsupported_chart')).toBe(true)
  })

  it('rejects a missing distribution metric', () => {
    expect(rejection({ metrics: [] }).issues.some((issue) => issue.code === 'missing_metric')).toBe(true)
  })

  it('rejects excessive metric cardinality', () => {
    expect(rejection({ metrics: ['activity', 'safety', 'activity', 'safety', 'activity'] }).issues.some((issue) => issue.code === 'unsafe_cardinality')).toBe(true)
  })

  it('rejects unknown metrics', () => {
    expect(rejection({ metrics: ['fictional'] }).issues.some((issue) => issue.code === 'unknown_metric')).toBe(true)
  })

  it('rejects unknown filter keys from untyped callers', () => {
    expect(rejection({ filters: { malicious: ['x'] } as never }).issues.some((issue) => issue.path === 'filters.malicious')).toBe(true)
  })

  it.each([
    { metric: 'activity', min: 2, max: 1 },
    { metric: 'activity', min: Number.NaN },
    { metric: 'activity', max: Number.POSITIVE_INFINITY },
  ])('rejects invalid metric ranges %#', (range) => {
    expect(rejection({ filters: { metricRanges: [range] } }).issues.some((issue) => issue.code === 'invalid_metric_range')).toBe(true)
  })

  it('rejects a range for an unknown metric', () => {
    expect(rejection({ filters: { metricRanges: [{ metric: 'fictional', min: 0 }] } }).issues.some((issue) => issue.code === 'unknown_metric')).toBe(true)
  })

  it('rejects metric measures at candidate grain', () => {
    const error = rejection({
      queryKey: 'run_quality', grain: 'unique_candidate', measures: ['metric_count'], metrics: [],
    })
    expect(error.issues.some((issue) => issue.message.includes('metric_evidence'))).toBe(true)
  })

  it('rejects yield rate without stage', () => {
    const error = rejection({
      queryKey: 'generator_funnel', dimensions: ['generator'], measures: ['yield_rate'], metrics: [],
    })
    expect(error.issues.some((issue) => issue.message.includes('stage'))).toBe(true)
  })

  it('rejects funnel without stage', () => {
    const error = rejection({
      queryKey: 'generator_funnel', dimensions: ['generator'], measures: ['unique_sequence_count'], metrics: [],
    })
    expect(error.issues.some((issue) => issue.path === 'chart')).toBe(true)
  })

  it('requires exactly two scatter metrics', () => {
    expect(rejection({ queryKey: 'pareto_conflicts', chart: 'scatter', metrics: ['activity'] }).issues.some((issue) => issue.message.includes('exactly two'))).toBe(true)
  })

  it('requires at least three parallel metrics', () => {
    expect(rejection({ queryKey: 'pareto_conflicts', chart: 'parallel', metrics: ['activity', 'safety'] }).issues.some((issue) => issue.message.includes('at least three'))).toBe(true)
  })
})

describe('pivot execution corner cases', () => {
  it('produces a stable query id for equivalent queries', () => {
    const first = executeAnalysisQuery(snapshot, query())
    const second = executeAnalysisQuery(snapshot, query())
    expect(first.queryId).toBe(second.queryId)
  })

  it('preserves cancelled run provenance and warnings', () => {
    const result = executeAnalysisQuery(snapshot, query())
    expect(result.provenance.runStatus).toBe('cancelled')
    expect(result.provenance.warnings).toContain('Test warning.')
  })

  it('computes generator funnel raw, pool, safety, and admitted counts', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'generator_funnel', metrics: [],
    })
    const row = (generator: string, stage: string) => result.rows.find((item) => item.generator === generator && item.stage === stage)
    expect(row('gen-a', 'raw_proposal')?.unique_sequence_count).toBe(2)
    expect(row('gen-b', 'raw_proposal')?.unique_sequence_count).toBe(2)
    expect(row('gen-a', 'candidate_pool')?.unique_sequence_count).toBe(2)
    expect(row('gen-b', 'candidate_pool')?.unique_sequence_count).toBe(1)
    expect(row('gen-a', 'safety_pass')?.unique_sequence_count).toBe(1)
    expect(row('gen-a', 'admitted')?.unique_sequence_count).toBe(1)
    expect(row('gen-b', 'admitted')?.unique_sequence_count).toBe(0)
  })

  it('applies generator and stage filters to the funnel', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'generator_funnel', metrics: [],
      filters: { generators: ['gen-b'], stages: ['admitted'] },
    })
    expect(result.rows).toEqual([{ generator: 'gen-b', stage: 'admitted', unique_sequence_count: 0, yield_rate: 0 }])
  })

  it('uses fractional attribution for shared-origin candidates', () => {
    const result = executeAnalysisQuery(snapshot, query({
      dimensions: ['generator', 'metric'], measures: ['record_count'], attribution: 'fractional',
    }))
    const genB = result.rows.find((row) => row.generator === 'gen-b')
    expect(genB?.record_count).toBe(0.5)
  })

  it('uses full attribution when requested', () => {
    const result = executeAnalysisQuery(snapshot, query({
      dimensions: ['generator', 'metric'], measures: ['record_count'], attribution: 'full',
    }))
    expect(result.rows.find((row) => row.generator === 'gen-b')?.record_count).toBe(1)
  })

  it('labels multi-origin candidates as shared under exclusive attribution', () => {
    const result = executeAnalysisQuery(snapshot, query({
      dimensions: ['generator'], measures: ['record_count'], attribution: 'exclusive',
    }))
    expect(result.rows.some((row) => row.generator === 'shared:gen-a+gen-b')).toBe(true)
  })

  it('filters exact origin sets without losing source identity', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'candidate_table',
      filters: { originSets: ['gen-a + gen-b'] }, metrics: ['activity'],
    })
    expect(result.records?.map((record) => record.id)).toEqual(['c2'])
  })

  it('filters by admission status', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'candidate_table',
      filters: { admissionStatuses: ['mature_core'] }, metrics: [],
    })
    expect(result.records?.map((record) => record.id)).toEqual(['c1'])
  })

  it('filters by rejection reason', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'candidate_table',
      filters: { rejectionReasons: ['rank_instability'] }, metrics: [],
    })
    expect(result.records?.map((record) => record.id)).toEqual(['c2'])
  })

  it('expands multiple rejection reasons into independent pivot rows', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'rejection_reasons_by_generator', metrics: [],
      attribution: 'exclusive', dimensions: ['origin_set', 'rejection_reason'],
    })
    expect(result.rows.filter((row) => row.origin_set === 'gen-a + gen-b')).toHaveLength(2)
  })

  it('applies inclusive metric ranges', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'candidate_table', metrics: ['activity'],
      filters: { metricRanges: [{ metric: 'activity', min: 0.8, max: 0.8 }] },
    })
    expect(result.records?.map((record) => record.id)).toEqual(['c1'])
  })

  it('excludes missing range values by default', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'candidate_table', metrics: ['safety'],
      filters: { metricRanges: [{ metric: 'safety', max: 1 }] },
    })
    expect(result.records?.map((record) => record.id)).toEqual(['c1'])
  })

  it('can explicitly retain missing range values', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'candidate_table', metrics: ['safety'],
      filters: { metricRanges: [{ metric: 'safety', max: 1, includeMissing: true }] },
    })
    expect(result.records).toHaveLength(2)
  })

  it('filters OOD evidence', () => {
    const result = executeAnalysisQuery(snapshot, query({ filters: { oodStatuses: ['out_of_domain'] } }))
    expect(result.distributions?.[0].summary.values).toEqual([0.4])
  })

  it('reports missing numeric values separately from evidence count', () => {
    const result = executeAnalysisQuery(snapshot, query({ metrics: ['safety'] }))
    const summary = result.distributions?.find((item) => item.key.generator === 'gen-a')?.summary
    expect(summary?.count).toBe(1)
    expect(summary?.missing).toBe(1)
  })

  it('computes interpolated quartiles and mean', () => {
    const result = executeAnalysisQuery(snapshot, query({ dimensions: ['metric'], attribution: 'exclusive' }))
    const summary = result.distributions?.[0].summary
    expect(summary?.min).toBe(0.4)
    expect(summary?.q1).toBeCloseTo(0.5)
    expect(summary?.median).toBeCloseTo(0.6)
    expect(summary?.q3).toBeCloseTo(0.7)
    expect(summary?.max).toBe(0.8)
    expect(summary?.mean).toBeCloseTo(0.6)
  })

  it('returns an empty result rather than inventing data', () => {
    const result = executeAnalysisQuery(snapshot, query({ filters: { generators: ['does-not-exist'] } }))
    expect(result.rows).toEqual([])
    expect(result.distributions).toEqual([])
  })

  it('returns selected candidate metrics only', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'candidate_table', metrics: ['activity'],
    })
    expect(Object.keys((result.records?.[0].metrics ?? {}) as object)).toEqual(['activity'])
  })

  it('returns only complete points for Pareto conflict plots', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'pareto_conflicts', metrics: ['activity', 'safety'],
    })
    expect(result.records?.map((record) => record.id)).toEqual(['c1'])
  })

  it('does not mutate snapshot warning arrays', () => {
    const before = [...snapshot.warnings]
    executeAnalysisQuery(snapshot, query())
    expect(snapshot.warnings).toEqual(before)
  })
})
