import { describe, expect, it } from 'vitest'
import {
  AnalysisCardRejectedError,
  type AnalysisCardQueryState,
  type AnalysisSnapshot,
  type CardPivotSlots,
} from './analysisDataContracts'
import {
  autoAssignPivotSlots,
  generateCardsFromOverview,
  moveCardPivotField,
  recommendCharts,
  setCardChart,
  validateCardSlots,
} from './cardQueryRules'
import { resolveAndValidateQuery } from './queryEngine'
import { createTestSnapshot } from './testSnapshot'

const snapshot = createTestSnapshot()

function scoringCard(): AnalysisCardQueryState {
  const result = generateCardsFromOverview(snapshot, { nodeIds: ['scoring'], metrics: ['activity'] })
  return result.cards.find((card) => card.cardId === 'overview-score-activity')!
}

function funnelCard(): AnalysisCardQueryState {
  return generateCardsFromOverview(snapshot, { nodeIds: ['generation'], metrics: [] }).cards[0]
}

describe('overview selection to independent card queries', () => {
  it('rejects an empty selection explicitly', () => {
    const result = generateCardsFromOverview(snapshot, { nodeIds: [], metrics: [] })
    expect(result.cards).toEqual([])
    expect(result.rejections[0].code).toBe('empty_selection')
  })

  it('generates a stage funnel from a generation node', () => {
    const card = funnelCard()
    expect(card.query.queryKey).toBe('generator_funnel')
    expect(card.query.filters.stages).toEqual(['raw_proposal', 'deduplicated'])
    expect(card.slots).toMatchObject({ row: ['stage'], column: ['generator'] })
  })

  it('generates one independent distribution card per selected metric plus coverage', () => {
    const result = generateCardsFromOverview(snapshot, { nodeIds: ['scoring'], metrics: ['activity', 'safety'] })
    expect(result.cards.map((card) => card.cardId)).toEqual(expect.arrayContaining([
      'overview-score-activity', 'overview-score-safety', 'overview-metric-coverage',
    ]))
    expect(result.cards.find((card) => card.cardId === 'overview-score-activity')?.query.metrics).toEqual(['activity'])
    expect(result.cards.find((card) => card.cardId === 'overview-score-safety')?.query.metrics).toEqual(['safety'])
  })

  it('adds a two-objective conflict card with a scatter recommendation', () => {
    const result = generateCardsFromOverview(snapshot, { nodeIds: ['scoring'], metrics: ['activity', 'safety'] })
    const conflict = result.cards.find((card) => card.query.queryKey === 'pareto_conflicts')
    expect(conflict?.chart).toBe('scatter')
    expect(conflict?.query.metrics).toEqual(['activity', 'safety'])
  })

  it('deduplicates repeated node and metric selections', () => {
    const result = generateCardsFromOverview(snapshot, {
      nodeIds: ['scoring', 'scoring'], metrics: ['activity', 'activity'],
    })
    expect(result.cards.filter((card) => card.cardId === 'overview-score-activity')).toHaveLength(1)
  })

  it('preserves generator and cohort filters on every generated card', () => {
    const result = generateCardsFromOverview(snapshot, {
      nodeIds: ['scoring'], metrics: ['activity'], generators: ['gen-a'], cohortIds: ['cohort-1'],
    })
    expect(result.cards.every((card) => card.query.filters.generators?.[0] === 'gen-a')).toBe(true)
    expect(result.cards.every((card) => card.query.filters.cohorts?.[0] === 'cohort-1')).toBe(true)
  })

  it('reports unknown metrics while still generating valid metric cards', () => {
    const result = generateCardsFromOverview(snapshot, {
      nodeIds: ['scoring'], metrics: ['activity', 'unknown'],
    })
    expect(result.rejections.some((issue) => issue.code === 'unknown_metric')).toBe(true)
    expect(result.cards.some((card) => card.cardId === 'overview-score-activity')).toBe(true)
  })

  it('rejects scoring without a metric', () => {
    const result = generateCardsFromOverview(snapshot, { nodeIds: ['scoring'], metrics: [] })
    expect(result.rejections.some((issue) => issue.code === 'metric_required')).toBe(true)
  })

  it.each(['structure', 'portfolio'] as const)('rejects incomplete %s nodes from the frozen run', (node) => {
    const result = generateCardsFromOverview(snapshot, { nodeIds: [node], metrics: [] })
    expect(result.cards).toEqual([])
    expect(result.rejections[0]).toMatchObject({ code: 'node_unavailable', path: `nodeIds.${node}` })
  })

  it('can generate different query types on the same dashboard selection', () => {
    const result = generateCardsFromOverview(snapshot, {
      nodeIds: ['generation', 'scoring', 'safety', 'admission'], metrics: ['activity', 'safety'],
    })
    expect(new Set(result.cards.map((card) => card.query.queryKey))).toEqual(new Set([
      'generator_funnel', 'metric_distribution_by_generator', 'coverage_by_metric',
      'rejection_reasons_by_generator', 'pareto_conflicts',
    ]))
  })
})

describe('pivot slot assignment and user drag rules', () => {
  it('assigns stage to row, generator to column, and a count to value', () => {
    const slots = autoAssignPivotSlots(funnelCard().query)
    expect(slots).toMatchObject({ row: ['stage'], column: ['generator'], value: ['unique_sequence_count'] })
  })

  it('assigns continuous scorer evidence to metric_value', () => {
    expect(scoringCard().slots.value).toEqual(['metric_value'])
  })

  it('moves a field immutably within one card', () => {
    const card = scoringCard()
    const moved = moveCardPivotField(snapshot, card, 'generator', 'row')
    expect(moved.slots.row).toContain('generator')
    expect(moved.slots.column).not.toContain('generator')
    expect(moved.revision).toBe(card.revision + 1)
    expect(card.slots.column).toContain('generator')
  })

  it('does not alter sibling card state', () => {
    const first = scoringCard()
    const second = { ...scoringCard(), cardId: 'sibling' }
    moveCardPivotField(snapshot, first, 'generator', 'row')
    expect(second.slots.column).toContain('generator')
    expect(second.revision).toBe(1)
  })

  it('rejects a measure dragged into a categorical row slot', () => {
    expect(() => moveCardPivotField(snapshot, scoringCard(), 'metric_value', 'row')).toThrow(AnalysisCardRejectedError)
  })

  it('rejects a dimension dragged into the value slot', () => {
    expect(() => moveCardPivotField(snapshot, scoringCard(), 'generator', 'value')).toThrow(AnalysisCardRejectedError)
  })

  it('rejects duplicate fields in manually supplied slots', () => {
    const slots: CardPivotSlots = { row: ['generator'], column: ['generator'], value: ['record_count'], category: [] }
    expect(validateCardSlots(snapshot, slots).some((issue) => issue.code === 'duplicate_field')).toBe(true)
  })

  it('rejects slot capacity overflow', () => {
    const slots: CardPivotSlots = {
      row: ['generator', 'stage', 'metric'], column: [], value: ['record_count'], category: [],
    }
    expect(validateCardSlots(snapshot, slots).some((issue) => issue.code === 'slot_capacity')).toBe(true)
  })

  it('rejects preserving a chart after slots become incompatible', () => {
    const card = funnelCard()
    expect(() => moveCardPivotField(snapshot, card, 'stage', 'column', 0, 'preserve')).not.toThrow()
    expect(() => moveCardPivotField(snapshot, card, 'stage', 'value', 0, 'preserve')).toThrow(AnalysisCardRejectedError)
  })

  it('rejects an explicitly incompatible chart choice', () => {
    expect(() => setCardChart(snapshot, scoringCard(), 'funnel')).toThrow(AnalysisCardRejectedError)
  })

  it('allows a compatible alternative chart choice', () => {
    const changed = setCardChart(snapshot, scoringCard(), 'histogram')
    expect(changed.chart).toBe('histogram')
    expect(changed.revision).toBe(2)
  })

  it('rejects unsafe category products over 500 cells', () => {
    const highCardinality: AnalysisSnapshot = {
      ...snapshot,
      candidates: Array.from({ length: 501 }, (_, index) => ({
        ...snapshot.candidates[0], id: `c-${index}`, sequenceSha256: `s-${index}`, originSet: [`g-${index}`],
      })),
    }
    const slots: CardPivotSlots = { row: ['origin_set'], column: ['stage'], value: ['record_count'], category: [] }
    expect(validateCardSlots(highCardinality, slots).some((issue) => issue.code === 'unsafe_cardinality')).toBe(true)
  })
})

describe('semantic and cardinality chart recommendation', () => {
  it('recommends funnel first for ordered stages', () => {
    expect(funnelCard().recommendedCharts[0].chart).toBe('funnel')
  })

  it('does not recommend density plots for tiny samples', () => {
    expect(scoringCard().recommendedCharts.some((item) => item.chart === 'violin')).toBe(false)
  })

  it('recommends violin when sample size and category count are safe', () => {
    const large: AnalysisSnapshot = {
      ...snapshot,
      candidates: Array.from({ length: 120 }, (_, index) => ({
        ...snapshot.candidates[0], id: `c-${index}`, sequenceSha256: `s-${index}`,
        metrics: { ...snapshot.candidates[0].metrics, activity: { ...snapshot.candidates[0].metrics.activity, value: index / 120 } },
      })),
    }
    const query = resolveAndValidateQuery(large, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'metric_distribution_by_generator', metrics: ['activity'],
    })
    const slots = autoAssignPivotSlots(query)
    expect(recommendCharts(large, query, slots).some((item) => item.chart === 'violin')).toBe(true)
  })

  it('always retains a table fallback for valid semantic layouts', () => {
    expect(scoringCard().recommendedCharts.at(-1)?.chart).toBe('table')
    expect(funnelCard().recommendedCharts.at(-1)?.chart).toBe('table')
  })
})

