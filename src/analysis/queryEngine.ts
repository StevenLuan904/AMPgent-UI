import {
  AnalysisQueryRejectedError,
  type AnalysisPivotQuery,
  type AnalysisPivotResult,
  type AnalysisSnapshot,
  type DistributionSummary,
  type PivotDimensionKey,
  type PivotValidationIssue,
  type ResolvedPivotQuery,
  type SnapshotCandidate,
  type SnapshotStage,
} from './analysisDataContracts'
import { analysisQueryRegistry } from './queryRegistry'

const STAGE_ORDER: SnapshotStage[] = [
  'raw_proposal', 'deduplicated', 'metric_complete', 'safety_pass', 'candidate_pool', 'admitted',
]

const FILTER_KEYS = new Set([
  'generators', 'originSets', 'stages', 'metrics', 'cohorts', 'admissionStatuses',
  'rejectionReasons', 'oodStatuses', 'candidateIds', 'metricRanges',
])

const METRIC_MEASURES = new Set([
  'metric_count', 'missing_count', 'out_of_domain_count', 'metric_mean', 'metric_median',
  'metric_min', 'metric_max', 'metric_q1', 'metric_q3',
])

const DISTRIBUTION_CHARTS = new Set(['boxplot', 'violin', 'histogram', 'ecdf'])

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`)
      .join(',')}}`
  }
  return JSON.stringify(value)
}

function hashString(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

function unique<T>(items: T[]): T[] {
  return [...new Set(items)]
}

export function resolveAndValidateQuery(
  snapshot: AnalysisSnapshot,
  input: AnalysisPivotQuery,
): ResolvedPivotQuery {
  const definition = analysisQueryRegistry[input.queryKey]
  if (!definition) {
    throw new AnalysisQueryRejectedError([{
      code: 'unknown_query_key', path: 'queryKey', message: `Unknown query key: ${String(input.queryKey)}`,
    }])
  }

  const resolved: ResolvedPivotQuery = {
    schemaVersion: 'analysis-pivot-query.1',
    queryKey: input.queryKey,
    grain: input.grain ?? definition.defaultQuery.grain,
    dimensions: input.dimensions ?? definition.defaultQuery.dimensions,
    measures: input.measures ?? definition.defaultQuery.measures,
    filters: input.filters ?? definition.defaultQuery.filters ?? {},
    metrics: input.metrics ?? definition.defaultQuery.metrics ?? [],
    chart: input.chart ?? definition.defaultQuery.chart,
    attribution: input.attribution ?? definition.defaultQuery.attribution,
    includeValues: input.includeValues ?? definition.defaultQuery.includeValues,
  }
  const issues: PivotValidationIssue[] = []

  if (!definition.allowedGrains.includes(resolved.grain)) {
    issues.push({ code: 'unsupported_grain', path: 'grain', message: `${input.queryKey} does not support grain ${resolved.grain}.` })
  }
  for (const dimension of resolved.dimensions) {
    if (!definition.allowedDimensions.includes(dimension)) {
      issues.push({ code: 'unsupported_dimension', path: 'dimensions', message: `${input.queryKey} does not support dimension ${dimension}.` })
    }
  }
  if (unique(resolved.dimensions).length !== resolved.dimensions.length) {
    issues.push({ code: 'incompatible_combination', path: 'dimensions', message: 'Duplicate dimensions are not allowed.' })
  }
  if (resolved.dimensions.length > 4) {
    issues.push({ code: 'unsafe_cardinality', path: 'dimensions', message: 'At most four dimensions may be pivoted at once.' })
  }
  for (const measure of resolved.measures) {
    if (!definition.allowedMeasures.includes(measure)) {
      issues.push({ code: 'unsupported_measure', path: 'measures', message: `${input.queryKey} does not support measure ${measure}.` })
    }
  }
  if (!definition.allowedCharts.includes(resolved.chart)) {
    issues.push({ code: 'unsupported_chart', path: 'chart', message: `${input.queryKey} cannot be rendered as ${resolved.chart}.` })
  }
  if (resolved.metrics.length < definition.minimumMetrics) {
    issues.push({ code: 'missing_metric', path: 'metrics', message: `${input.queryKey} requires at least ${definition.minimumMetrics} metric(s).` })
  }
  if (resolved.metrics.length > definition.maximumMetrics) {
    issues.push({ code: 'unsafe_cardinality', path: 'metrics', message: `${input.queryKey} accepts at most ${definition.maximumMetrics} metric(s).` })
  }
  const knownMetrics = new Set(Object.keys(snapshot.metricMethods))
  for (const metric of resolved.metrics) {
    if (!knownMetrics.has(metric)) {
      issues.push({ code: 'unknown_metric', path: 'metrics', message: `Unknown metric: ${metric}.` })
    }
  }
  for (const key of Object.keys(resolved.filters)) {
    if (!FILTER_KEYS.has(key)) {
      issues.push({ code: 'incompatible_combination', path: `filters.${key}`, message: `Unknown filter key: ${key}.` })
    }
  }
  for (const range of resolved.filters.metricRanges ?? []) {
    if (!knownMetrics.has(range.metric)) {
      issues.push({ code: 'unknown_metric', path: 'filters.metricRanges', message: `Unknown range metric: ${range.metric}.` })
    }
    if ((range.min != null && !Number.isFinite(range.min)) || (range.max != null && !Number.isFinite(range.max)) ||
        (range.min != null && range.max != null && range.min > range.max)) {
      issues.push({ code: 'invalid_metric_range', path: 'filters.metricRanges', message: `Invalid range for ${range.metric}.` })
    }
  }
  if (resolved.grain !== 'metric_evidence' && resolved.measures.some((measure) => METRIC_MEASURES.has(measure))) {
    issues.push({ code: 'incompatible_combination', path: 'measures', message: 'Metric measures require metric_evidence grain.' })
  }
  if (resolved.measures.includes('yield_rate') && !resolved.dimensions.includes('stage')) {
    issues.push({ code: 'incompatible_combination', path: 'measures', message: 'yield_rate requires the stage dimension.' })
  }
  if ((resolved.chart === 'funnel' || resolved.chart === 'sankey') && !resolved.dimensions.includes('stage')) {
    issues.push({ code: 'incompatible_combination', path: 'chart', message: `${resolved.chart} requires the stage dimension.` })
  }
  if (DISTRIBUTION_CHARTS.has(resolved.chart) && resolved.metrics.length === 0) {
    issues.push({ code: 'missing_metric', path: 'metrics', message: `${resolved.chart} requires a numeric metric.` })
  }
  if (resolved.chart === 'scatter' && resolved.metrics.length !== 2) {
    issues.push({ code: 'incompatible_combination', path: 'metrics', message: 'scatter requires exactly two metrics.' })
  }
  if (resolved.chart === 'parallel' && resolved.metrics.length < 3) {
    issues.push({ code: 'incompatible_combination', path: 'metrics', message: 'parallel requires at least three metrics.' })
  }

  if (issues.length) throw new AnalysisQueryRejectedError(issues)
  return resolved
}

function candidateStages(candidate: SnapshotCandidate, requiredMetrics: string[]): SnapshotStage[] {
  const stages: SnapshotStage[] = ['deduplicated', 'candidate_pool']
  const metricComplete = requiredMetrics.every((metric) => candidate.metrics[metric]?.status === 'succeeded')
  if (metricComplete) stages.push('metric_complete')
  const safetyPass = candidate.admission.status !== 'not_evaluated' &&
    !candidate.admission.reasons.some((reason) => reason.startsWith('label_gate_failed:'))
  if (safetyPass) stages.push('safety_pass')
  if (candidate.admission.structureEligible) stages.push('admitted')
  return STAGE_ORDER.filter((stage) => stages.includes(stage))
}

function includesAny(actual: string[], expected?: string[]): boolean {
  return !expected?.length || expected.some((item) => actual.includes(item))
}

function candidateMatches(candidate: SnapshotCandidate, query: ResolvedPivotQuery): boolean {
  const filters = query.filters
  if (!includesAny(candidate.originSet, filters.generators)) return false
  const originLabel = candidate.originSet.slice().sort().join(' + ')
  if (filters.originSets?.length && !filters.originSets.includes(originLabel)) return false
  if (filters.cohorts?.length && (!candidate.cohortSha256 || !filters.cohorts.includes(candidate.cohortSha256))) return false
  if (filters.admissionStatuses?.length && !filters.admissionStatuses.includes(candidate.admission.status)) return false
  if (filters.rejectionReasons?.length && !includesAny(candidate.admission.reasons, filters.rejectionReasons)) return false
  const stages = candidateStages(candidate, Object.keys(candidate.metrics))
  if (filters.stages?.length && !filters.stages.some((stage) => stages.includes(stage))) return false
  for (const range of filters.metricRanges ?? []) {
    const value = candidate.metrics[range.metric]?.value
    if (value == null) {
      if (!range.includeMissing) return false
      continue
    }
    if (range.min != null && value < range.min) return false
    if (range.max != null && value > range.max) return false
  }
  return true
}

interface FactRecord {
  candidateId: string | null
  sequenceSha256: string
  generator: string
  origin_set: string
  stage: SnapshotStage
  metric: string
  cohort: string
  admission_status: string
  rejection_reason: string
  ood_status: 'in_domain' | 'out_of_domain'
  value: number | null
  weight: number
}

function attributionRows(candidate: SnapshotCandidate, query: ResolvedPivotQuery): Array<{ generator: string; weight: number }> {
  if (!query.dimensions.includes('generator')) return [{ generator: '', weight: 1 }]
  if (query.attribution === 'exclusive') {
    return [{
      generator: candidate.originSet.length === 1 ? candidate.originSet[0] : `shared:${candidate.originSet.slice().sort().join('+')}`,
      weight: 1,
    }]
  }
  const weight = query.attribution === 'fractional' ? 1 / candidate.originSet.length : 1
  return candidate.originSet.map((generator) => ({ generator, weight }))
}

function dimensionValues(candidate: SnapshotCandidate, query: ResolvedPivotQuery): {
  stages: SnapshotStage[]
  reasons: string[]
} {
  const requiredMetrics = Object.keys(candidate.metrics)
  return {
    stages: query.dimensions.includes('stage') ? candidateStages(candidate, requiredMetrics) : ['candidate_pool'],
    reasons: query.dimensions.includes('rejection_reason')
      ? (candidate.admission.reasons.length ? candidate.admission.reasons : ['none'])
      : [''],
  }
}

function buildFacts(snapshot: AnalysisSnapshot, query: ResolvedPivotQuery): FactRecord[] {
  if (query.grain === 'proposal_occurrence') {
    return snapshot.occurrences
      .filter((item) => !query.filters.generators?.length || query.filters.generators.includes(item.generator))
      .map((item) => ({
        candidateId: item.candidateId, sequenceSha256: item.sequenceSha256, generator: item.generator,
        origin_set: item.generator, stage: 'raw_proposal', metric: '', cohort: '', admission_status: '',
        rejection_reason: '', ood_status: 'in_domain', value: null, weight: 1,
      }))
  }

  const facts: FactRecord[] = []
  for (const candidate of snapshot.candidates) {
    if (!candidateMatches(candidate, query)) continue
    const originSet = candidate.originSet.slice().sort().join(' + ')
    const { stages, reasons } = dimensionValues(candidate, query)
    const origins = attributionRows(candidate, query)
    const metrics = query.grain === 'metric_evidence'
      ? (query.metrics.length ? query.metrics : Object.keys(snapshot.metricMethods))
      : ['']
    for (const metric of metrics) {
      const evidence = metric ? candidate.metrics[metric] : undefined
      if (query.filters.metrics?.length && !query.filters.metrics.includes(metric)) continue
      const oodStatus = evidence?.outOfDomain ? 'out_of_domain' : 'in_domain'
      if (query.filters.oodStatuses?.length && !query.filters.oodStatuses.includes(oodStatus)) continue
      for (const origin of origins) {
        for (const stage of stages) {
          for (const reason of reasons) {
            facts.push({
              candidateId: candidate.id, sequenceSha256: candidate.sequenceSha256,
              generator: origin.generator, origin_set: originSet, stage, metric,
              cohort: candidate.cohortSha256 ?? 'unknown', admission_status: candidate.admission.status,
              rejection_reason: reason, ood_status: oodStatus, value: evidence?.value ?? null,
              weight: origin.weight,
            })
          }
        }
      }
    }
  }
  return facts
}

function quantile(sorted: number[], position: number): number | null {
  if (!sorted.length) return null
  const index = (sorted.length - 1) * position
  const lower = Math.floor(index)
  const fraction = index - lower
  return sorted[lower + 1] === undefined
    ? sorted[lower]
    : sorted[lower] + fraction * (sorted[lower + 1] - sorted[lower])
}

function distribution(values: Array<number | null>, includeValues: boolean): DistributionSummary {
  const numeric = values.filter((value): value is number => value != null && Number.isFinite(value)).sort((a, b) => a - b)
  const summary: DistributionSummary = {
    count: numeric.length,
    missing: values.length - numeric.length,
    min: numeric[0] ?? null,
    q1: quantile(numeric, 0.25),
    median: quantile(numeric, 0.5),
    q3: quantile(numeric, 0.75),
    max: numeric.at(-1) ?? null,
    mean: numeric.length ? numeric.reduce((sum, value) => sum + value, 0) / numeric.length : null,
  }
  if (includeValues) summary.values = numeric
  return summary
}

function keyFor(record: FactRecord, dimensions: PivotDimensionKey[]): Record<string, string> {
  return Object.fromEntries(dimensions.map((dimension) => [dimension, record[dimension]]))
}

function aggregateFacts(facts: FactRecord[], query: ResolvedPivotQuery): Pick<AnalysisPivotResult, 'rows' | 'distributions'> {
  const groups = new Map<string, { key: Record<string, string>; records: FactRecord[] }>()
  for (const fact of facts) {
    const key = keyFor(fact, query.dimensions)
    const serialised = stableStringify(key)
    const group = groups.get(serialised) ?? { key, records: [] }
    group.records.push(fact)
    groups.set(serialised, group)
  }
  if (!groups.size && query.dimensions.length === 0) groups.set('{}', { key: {}, records: [] })

  const rows = [...groups.values()].map(({ key, records }) => {
    const values = records.map((record) => record.value)
    const stats = distribution(values, false)
    const uniqueSequences = new Map<string, number>()
    records.forEach((record) => uniqueSequences.set(record.sequenceSha256, Math.max(uniqueSequences.get(record.sequenceSha256) ?? 0, record.weight)))
    const row: Record<string, string | number | boolean | null> = { ...key }
    for (const measure of query.measures) {
      if (measure === 'record_count') row[measure] = records.reduce((sum, record) => sum + record.weight, 0)
      else if (measure === 'unique_sequence_count') row[measure] = [...uniqueSequences.values()].reduce((sum, weight) => sum + weight, 0)
      else if (measure === 'metric_count') row[measure] = stats.count
      else if (measure === 'missing_count') row[measure] = stats.missing
      else if (measure === 'out_of_domain_count') row[measure] = records.filter((record) => record.ood_status === 'out_of_domain').length
      else if (measure === 'metric_mean') row[measure] = stats.mean
      else if (measure === 'metric_median') row[measure] = stats.median
      else if (measure === 'metric_min') row[measure] = stats.min
      else if (measure === 'metric_max') row[measure] = stats.max
      else if (measure === 'metric_q1') row[measure] = stats.q1
      else if (measure === 'metric_q3') row[measure] = stats.q3
    }
    return row
  })

  for (const measure of query.measures) {
    if (measure !== 'share' && measure !== 'yield_rate') continue
    const baseMeasure = query.grain === 'metric_evidence' ? 'metric_count' :
      query.measures.includes('unique_sequence_count') ? 'unique_sequence_count' : 'record_count'
    const total = rows.reduce((sum, row) => sum + Number(row[baseMeasure] ?? 0), 0)
    rows.forEach((row) => { row[measure] = total ? Number(row[baseMeasure] ?? 0) / total : 0 })
  }

  rows.sort((left, right) => stableStringify(left).localeCompare(stableStringify(right)))
  const distributions = query.grain === 'metric_evidence'
    ? [...groups.values()].map(({ key, records }) => ({ key, summary: distribution(records.map((record) => record.value), query.includeValues) }))
    : undefined
  return { rows, distributions }
}

function generatorFunnel(snapshot: AnalysisSnapshot, query: ResolvedPivotQuery): AnalysisPivotResult['rows'] {
  const generators = unique(snapshot.generatorCells.map((cell) => cell.generator)).sort()
  const rows: AnalysisPivotResult['rows'] = []
  for (const generator of generators) {
    if (query.filters.generators?.length && !query.filters.generators.includes(generator)) continue
    const raw = snapshot.occurrences.filter((item) => item.generator === generator).length
    const candidates = snapshot.candidates.filter((candidate) => candidate.originSet.includes(generator) && candidateMatches(candidate, query))
    const counts = new Map<SnapshotStage, number>([['raw_proposal', raw]])
    for (const stage of STAGE_ORDER.slice(1)) {
      counts.set(stage, candidates.filter((candidate) => candidateStages(candidate, Object.keys(snapshot.metricMethods)).includes(stage)).length)
    }
    for (const stage of STAGE_ORDER) {
      if (query.filters.stages?.length && !query.filters.stages.includes(stage)) continue
      const count = counts.get(stage) ?? 0
      rows.push({ generator, stage, unique_sequence_count: count, yield_rate: raw ? count / raw : 0 })
    }
  }
  return rows
}

function resultProvenance(snapshot: AnalysisSnapshot): AnalysisPivotResult['provenance'] {
  return {
    snapshotId: snapshot.snapshotId,
    snapshotSha256: snapshot.snapshotSha256,
    source: snapshot.source,
    runId: snapshot.run.id,
    runStatus: snapshot.run.status,
    computedAt: new Date().toISOString(),
    coverage: snapshot.coverage,
    warnings: [...snapshot.warnings],
  }
}

export function executeAnalysisQuery(snapshot: AnalysisSnapshot, input: AnalysisPivotQuery): AnalysisPivotResult {
  const query = resolveAndValidateQuery(snapshot, input)
  const queryId = `pivot:${hashString(stableStringify(query))}`
  const provenance = resultProvenance(snapshot)

  if (query.queryKey === 'generator_funnel') {
    return { query, queryId, rows: generatorFunnel(snapshot, query), provenance }
  }

  const candidates = snapshot.candidates.filter((candidate) => candidateMatches(candidate, query))
  if (query.queryKey === 'candidate_table') {
    return {
      query, queryId, rows: [], provenance,
      records: candidates.map((candidate) => ({
        id: candidate.id, sequence: candidate.sequence, sequenceSha256: candidate.sequenceSha256,
        originSet: candidate.originSet, cohortSha256: candidate.cohortSha256,
        admission: candidate.admission,
        metrics: Object.fromEntries((query.metrics.length ? query.metrics : Object.keys(candidate.metrics))
          .map((metric) => [metric, candidate.metrics[metric] ?? null])),
      })),
    }
  }

  if (query.queryKey === 'pareto_conflicts') {
    return {
      query, queryId, rows: [], provenance,
      records: candidates
        .filter((candidate) => query.metrics.every((metric) => candidate.metrics[metric]?.value != null))
        .map((candidate) => ({
          id: candidate.id, sequence: candidate.sequence, originSet: candidate.originSet,
          admissionStatus: candidate.admission.status, paretoFront: candidate.admission.paretoFront,
          structureEligible: candidate.admission.structureEligible,
          metrics: Object.fromEntries(query.metrics.map((metric) => [metric, candidate.metrics[metric].value])),
        })),
    }
  }

  const facts = buildFacts(snapshot, query)
  const aggregate = aggregateFacts(facts, query)
  return { query, queryId, ...aggregate, provenance }
}

