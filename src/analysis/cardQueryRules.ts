import {
  AnalysisCardRejectedError,
  type AnalysisCardQueryState,
  type AnalysisPivotQuery,
  type AnalysisSnapshot,
  type CardPivotSlots,
  type CardRuleRejection,
  type ChartRecommendation,
  type OverviewAnalysisSelection,
  type OverviewCardGenerationResult,
  type PivotChartType,
  type PivotDimensionKey,
  type PivotFieldKey,
  type PivotMeasureKey,
  type PivotSlotName,
  type ResolvedPivotQuery,
  type SnapshotStage,
} from './analysisDataContracts'
import { resolveAndValidateQuery } from './queryEngine'

const DIMENSIONS = new Set<PivotFieldKey>([
  'generator', 'origin_set', 'stage', 'metric', 'cohort', 'admission_status', 'rejection_reason', 'ood_status',
])
const MEASURES = new Set<PivotFieldKey>([
  'record_count', 'unique_sequence_count', 'metric_count', 'missing_count', 'out_of_domain_count',
  'metric_mean', 'metric_median', 'metric_min', 'metric_max', 'metric_q1', 'metric_q3',
  'share', 'yield_rate', 'metric_value',
])
const SLOT_CAPACITY: Record<PivotSlotName, number> = { row: 2, column: 2, value: 4, category: 1 }

function emptySlots(): CardPivotSlots {
  return { row: [], column: [], value: [], category: [] }
}

function metricValues(snapshot: AnalysisSnapshot, metric: string): number[] {
  return snapshot.candidates
    .map((candidate) => candidate.metrics[metric]?.value)
    .filter((value): value is number => value != null && Number.isFinite(value))
}

function dimensionCardinality(snapshot: AnalysisSnapshot, field: PivotDimensionKey): number {
  if (field === 'generator') return new Set(snapshot.candidates.flatMap((candidate) => candidate.originSet)).size
  if (field === 'origin_set') return new Set(snapshot.candidates.map((candidate) => candidate.originSet.slice().sort().join(' + '))).size
  if (field === 'stage') return 6
  if (field === 'metric') return Object.keys(snapshot.metricMethods).length
  if (field === 'cohort') return snapshot.cohorts.length
  if (field === 'admission_status') return new Set(snapshot.candidates.map((candidate) => candidate.admission.status)).size
  if (field === 'rejection_reason') return new Set(snapshot.candidates.flatMap((candidate) => candidate.admission.reasons)).size
  return 2
}

export function autoAssignPivotSlots(query: ResolvedPivotQuery): CardPivotSlots {
  const slots = emptySlots()
  const dimensions = [...query.dimensions]
  if (dimensions.includes('stage')) {
    slots.row.push('stage')
    dimensions.splice(dimensions.indexOf('stage'), 1)
  }
  if (dimensions.length) slots.column.push(dimensions.shift()!)
  if (dimensions.length) slots.category.push(dimensions.shift()!)
  while (dimensions.length && slots.row.length < SLOT_CAPACITY.row) slots.row.push(dimensions.shift()!)
  if (query.grain === 'metric_evidence' && query.metrics.length) slots.value.push('metric_value')
  else slots.value.push(...query.measures.filter((measure) => measure !== 'share' && measure !== 'yield_rate').slice(0, SLOT_CAPACITY.value))
  if (!slots.value.length && query.measures.length) slots.value.push(query.measures[0])
  return slots
}

function flattenedSlots(slots: CardPivotSlots): PivotFieldKey[] {
  return [...slots.row, ...slots.column, ...slots.value, ...slots.category]
}

export function validateCardSlots(
  snapshot: AnalysisSnapshot,
  slots: CardPivotSlots,
  chart?: PivotChartType,
): CardRuleRejection[] {
  const issues: CardRuleRejection[] = []
  for (const slot of Object.keys(slots) as PivotSlotName[]) {
    if (slots[slot].length > SLOT_CAPACITY[slot]) {
      issues.push({ code: 'slot_capacity', path: `slots.${slot}`, message: `${slot} accepts at most ${SLOT_CAPACITY[slot]} field(s).` })
    }
    for (const field of slots[slot]) {
      const expectsDimension = slot !== 'value'
      if ((expectsDimension && !DIMENSIONS.has(field)) || (!expectsDimension && !MEASURES.has(field))) {
        issues.push({ code: 'incompatible_slot', path: `slots.${slot}`, message: `${field} cannot be placed in ${slot}.` })
      }
    }
  }
  const allFields = flattenedSlots(slots)
  if (new Set(allFields).size !== allFields.length) {
    issues.push({ code: 'duplicate_field', path: 'slots', message: 'A field may occupy only one pivot slot.' })
  }
  const categoricalFields = [...slots.row, ...slots.column, ...slots.category].filter((field): field is PivotDimensionKey => DIMENSIONS.has(field))
  const cardinalityProduct = categoricalFields.reduce((product, field) => product * Math.max(1, dimensionCardinality(snapshot, field)), 1)
  if (cardinalityProduct > 500) {
    issues.push({ code: 'unsafe_cardinality', path: 'slots', message: `Pivot cardinality ${cardinalityProduct} exceeds the safe limit of 500 cells.` })
  }

  if (chart) {
    const hasStage = categoricalFields.includes('stage')
    const hasMetricValue = slots.value.includes('metric_value')
    const numericValues = slots.value.length
    const categoricalCount = categoricalFields.length
    const incompatible =
      (chart === 'kpi' && categoricalCount > 0) ||
      ((chart === 'funnel' || chart === 'sankey') && !hasStage) ||
      (['boxplot', 'violin', 'histogram', 'ecdf'].includes(chart) && !hasMetricValue) ||
      (chart === 'heatmap' && (categoricalCount !== 2 || numericValues === 0)) ||
      (chart === 'bar' && (categoricalCount < 1 || numericValues === 0)) ||
      (chart === 'stacked_bar' && (categoricalCount < 2 || numericValues === 0)) ||
      (chart === 'scatter' && !hasMetricValue) ||
      (chart === 'parallel' && !hasMetricValue)
    if (incompatible) issues.push({ code: 'chart_incompatible', path: 'chart', message: `${chart} is incompatible with the current pivot slots.` })
  }
  return issues
}

export function recommendCharts(
  snapshot: AnalysisSnapshot,
  query: ResolvedPivotQuery,
  slots: CardPivotSlots,
): ChartRecommendation[] {
  const dimensions = [...slots.row, ...slots.column, ...slots.category]
    .filter((field): field is PivotDimensionKey => DIMENSIONS.has(field))
  const recommendations: ChartRecommendation[] = []
  const add = (chart: PivotChartType, score: number, reason: string) => {
    if (!validateCardSlots(snapshot, slots, chart).length) recommendations.push({ chart, score, reason })
  }

  if (query.queryKey === 'pareto_conflicts') {
    add(query.metrics.length === 2 ? 'scatter' : 'parallel', 100, `${query.metrics.length} continuous objectives selected.`)
    add('table', 55, 'Table preserves exact candidate evidence.')
  } else if (dimensions.includes('stage')) {
    add('funnel', 98, 'Ordered stage semantics make loss and yield directly comparable.')
    if (dimensions.includes('generator')) add('sankey', 86, 'Generator-to-stage flow is explicit.')
    add(dimensions.length > 1 ? 'stacked_bar' : 'bar', 78, 'Bars compare stage counts without implying continuity.')
    add('table', 40, 'Table is the lossless fallback.')
  } else if (slots.value.includes('metric_value')) {
    const selectedValues = query.metrics.flatMap((metric) => metricValues(snapshot, metric))
    const categoryCount = dimensions.reduce((product, field) => product * Math.max(1, dimensionCardinality(snapshot, field)), 1)
    if (categoryCount <= 16 && selectedValues.length >= 5) add('boxplot', 96, 'Continuous scores with manageable categorical groups.')
    if (categoryCount <= 8 && selectedValues.length >= 100) add('violin', 88, 'Sample size supports density-shape comparison.')
    if (dimensions.length === 0 || categoryCount <= 4) add('histogram', 82, 'Distribution shape remains readable at low category count.')
    if (categoryCount <= 8) add('ecdf', 76, 'ECDF compares full distributions without bin choices.')
    add('table', 38, 'Table preserves exact values and missingness.')
  } else if (dimensions.length === 0) {
    add('kpi', 95, 'No categorical axes; scalar values are most legible as KPIs.')
    add('table', 35, 'Table is the lossless fallback.')
  } else if (dimensions.length === 1) {
    add('bar', 92, 'One categorical axis and numeric values.')
    add('table', 45, 'Table is the lossless fallback.')
  } else {
    const product = dimensions.reduce((value, field) => value * Math.max(1, dimensionCardinality(snapshot, field)), 1)
    if (product <= 100) add('heatmap', 92, `Two-dimensional cardinality (${product}) is heatmap-safe.`)
    add('stacked_bar', 80, 'Two categorical fields can be compared as grouped composition.')
    add('table', 50, 'Table remains safe for sparse category combinations.')
  }
  return recommendations.sort((left, right) => right.score - left.score)
}

function createCard(
  snapshot: AnalysisSnapshot,
  cardId: string,
  title: string,
  input: AnalysisPivotQuery,
  createdFrom: AnalysisCardQueryState['createdFrom'] = 'overview_rule',
): AnalysisCardQueryState {
  const query = resolveAndValidateQuery(snapshot, input)
  const slots = autoAssignPivotSlots(query)
  const recommendations = recommendCharts(snapshot, query, slots)
  const chart = recommendations[0]?.chart ?? query.chart
  const issues = validateCardSlots(snapshot, slots, chart)
  if (issues.length) throw new AnalysisCardRejectedError(issues)
  return { cardId, title, revision: 1, query: { ...query, chart }, slots, chart, recommendedCharts: recommendations, createdFrom }
}

function availableNode(snapshot: AnalysisSnapshot, node: OverviewAnalysisSelection['nodeIds'][number]): boolean {
  if (node !== 'structure' && node !== 'portfolio') return true
  const warningText = snapshot.warnings.join(' ').toLowerCase()
  return !warningText.includes(`${node} and final portfolio stages are incomplete`) &&
    snapshot.stageCheckpoints.some((checkpoint) => checkpoint.stage_name === node && checkpoint.stage_status === 'completed')
}

export function generateCardsFromOverview(
  snapshot: AnalysisSnapshot,
  selection: OverviewAnalysisSelection,
): OverviewCardGenerationResult {
  const cards: AnalysisCardQueryState[] = []
  const rejections: CardRuleRejection[] = []
  const nodes = [...new Set(selection.nodeIds)]
  const metrics = [...new Set(selection.metrics)]
  if (!nodes.length && !metrics.length) {
    return { cards, rejections: [{ code: 'empty_selection', path: 'selection', message: 'Select at least one process node or metric.' }] }
  }
  const knownMetrics = new Set(Object.keys(snapshot.metricMethods))
  for (const metric of metrics) {
    if (!knownMetrics.has(metric)) rejections.push({ code: 'unknown_metric', path: 'metrics', message: `Unknown metric: ${metric}.` })
  }
  const validMetrics = metrics.filter((metric) => knownMetrics.has(metric))
  const filters = {
    ...(selection.generators?.length ? { generators: selection.generators } : {}),
    ...(selection.cohortIds?.length ? { cohorts: selection.cohortIds } : {}),
  }

  for (const node of nodes) {
    if (!availableNode(snapshot, node)) {
      rejections.push({ code: 'node_unavailable', path: `nodeIds.${node}`, message: `${node} is not complete in snapshot ${snapshot.snapshotId}.` })
      continue
    }
    if (node === 'generation' || node === 'deduplication' || node === 'admission') {
      const stageMap: Record<typeof node, SnapshotStage[]> = {
        generation: ['raw_proposal', 'deduplicated'],
        deduplication: ['raw_proposal', 'deduplicated', 'candidate_pool'],
        admission: ['candidate_pool', 'safety_pass', 'admitted'],
      }
      cards.push(createCard(snapshot, `overview-${node}-funnel`, `${node} funnel`, {
        schemaVersion: 'analysis-pivot-query.1', queryKey: 'generator_funnel',
        filters: { ...filters, stages: stageMap[node] }, metrics: [],
      }))
    }
    if (node === 'scoring') {
      if (!validMetrics.length) {
        rejections.push({ code: 'metric_required', path: `nodeIds.${node}`, message: 'scoring requires at least one known metric.' })
      } else {
        for (const metric of validMetrics) {
          cards.push(createCard(snapshot, `overview-score-${metric}`, `${metric} distribution`, {
            schemaVersion: 'analysis-pivot-query.1', queryKey: 'metric_distribution_by_generator',
            metrics: [metric], filters,
          }))
        }
        cards.push(createCard(snapshot, 'overview-metric-coverage', 'Metric coverage', {
          schemaVersion: 'analysis-pivot-query.1', queryKey: 'coverage_by_metric', metrics: validMetrics, filters,
        }))
      }
    }
    if (node === 'safety') {
      cards.push(createCard(snapshot, 'overview-safety-outcomes', 'Safety gate outcomes', {
        schemaVersion: 'analysis-pivot-query.1', queryKey: 'rejection_reasons_by_generator', metrics: [], filters,
      }))
    }
  }

  if (validMetrics.length >= 2) {
    cards.push(createCard(snapshot, `overview-conflict-${validMetrics.join('-')}`, 'Multi-objective conflicts', {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'pareto_conflicts',
      metrics: validMetrics.slice(0, 7), chart: validMetrics.length === 2 ? 'scatter' : 'parallel', filters,
    }))
  }
  return { cards, rejections }
}

function queryFromSlots(card: AnalysisCardQueryState, slots: CardPivotSlots): ResolvedPivotQuery {
  const dimensions = [...slots.row, ...slots.column, ...slots.category]
    .filter((field): field is PivotDimensionKey => DIMENSIONS.has(field))
  const measures = slots.value
    .filter((field): field is PivotMeasureKey => field !== 'metric_value' && MEASURES.has(field))
  return { ...card.query, dimensions, measures }
}

export function moveCardPivotField(
  snapshot: AnalysisSnapshot,
  card: AnalysisCardQueryState,
  field: PivotFieldKey,
  targetSlot: PivotSlotName,
  targetIndex = Number.POSITIVE_INFINITY,
  chartPolicy: 'recommend' | 'preserve' = 'recommend',
): AnalysisCardQueryState {
  const slots: CardPivotSlots = {
    row: card.slots.row.filter((item) => item !== field),
    column: card.slots.column.filter((item) => item !== field),
    value: card.slots.value.filter((item) => item !== field),
    category: card.slots.category.filter((item) => item !== field),
  }
  const index = Math.min(Math.max(0, targetIndex), slots[targetSlot].length)
  slots[targetSlot].splice(index, 0, field)
  const baseIssues = validateCardSlots(snapshot, slots)
  if (baseIssues.length) throw new AnalysisCardRejectedError(baseIssues)

  const query = queryFromSlots(card, slots)
  const recommendations = recommendCharts(snapshot, query, slots)
  const chart = chartPolicy === 'preserve' ? card.chart : recommendations[0]?.chart ?? card.chart
  const chartIssues = validateCardSlots(snapshot, slots, chart)
  if (chartIssues.length) throw new AnalysisCardRejectedError(chartIssues)
  resolveAndValidateQuery(snapshot, { ...query, chart })
  return {
    ...card, revision: card.revision + 1, query: { ...query, chart }, slots, chart,
    recommendedCharts: recommendations,
  }
}

export function setCardChart(
  snapshot: AnalysisSnapshot,
  card: AnalysisCardQueryState,
  chart: PivotChartType,
): AnalysisCardQueryState {
  const issues = validateCardSlots(snapshot, card.slots, chart)
  if (issues.length) throw new AnalysisCardRejectedError(issues)
  resolveAndValidateQuery(snapshot, { ...card.query, chart })
  return { ...card, revision: card.revision + 1, chart, query: { ...card.query, chart } }
}
