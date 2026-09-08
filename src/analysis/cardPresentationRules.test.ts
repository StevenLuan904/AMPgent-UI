import { describe, expect, it } from 'vitest'
import { AnalysisCardRejectedError, type AnalysisCardQueryState } from './analysisDataContracts'
import { generateCardsFromOverview } from './cardQueryRules'
import { planCardPresentation, recommendedGridMinimum } from './cardPresentationRules'
import { createTestSnapshot } from './testSnapshot'

const snapshot = createTestSnapshot()
const funnel = generateCardsFromOverview(snapshot, { nodeIds: ['generation'], metrics: [] }).cards[0]

describe('responsive card presentation rules', () => {
  it('uses compact mode and only primary visual elements for small cards', () => {
    const plan = planCardPresentation(funnel, { width: 360, height: 210 })
    expect(plan.mode).toBe('compact')
    expect(plan.visible).toEqual(expect.arrayContaining(['title', 'primary_chart', 'primary_value']))
    expect(plan.visible).not.toContain('provenance')
    expect(plan.showLabels).toBe('key_only')
  })

  it('uses standard mode for ordinary dashboard cards', () => {
    const plan = planCardPresentation(funnel, { width: 620, height: 340 })
    expect(plan.mode).toBe('standard')
    expect(plan.visible).toContain('axes')
    expect(plan.visible).toContain('filters')
    expect(plan.visible).not.toContain('details')
  })

  it('uses expanded mode and exposes provenance for large cards', () => {
    const plan = planCardPresentation(funnel, { width: 900, height: 520 })
    expect(plan.mode).toBe('expanded')
    expect(plan.visible).toEqual(expect.arrayContaining(['details', 'provenance', 'legend', 'actions']))
    expect(plan.showLabels).toBe('all')
  })

  it.each([
    { width: 179, height: 200 },
    { width: 300, height: 119 },
    { width: Number.NaN, height: 300 },
    { width: 300, height: Number.POSITIVE_INFINITY },
  ])('rejects unreadable or non-finite size %#', (size) => {
    expect(() => planCardPresentation(funnel, size)).toThrow(AnalysisCardRejectedError)
  })

  it('downgrades a compact violin to a boxplot', () => {
    const card: AnalysisCardQueryState = { ...funnel, chart: 'violin', query: { ...funnel.query, chart: 'violin' } }
    expect(planCardPresentation(card, { width: 360, height: 210 }).effectiveChart).toBe('boxplot')
  })

  it('downgrades a compact Sankey to a funnel', () => {
    const card: AnalysisCardQueryState = { ...funnel, chart: 'sankey', query: { ...funnel.query, chart: 'sankey' } }
    expect(planCardPresentation(card, { width: 360, height: 210 }).effectiveChart).toBe('funnel')
  })

  it('reduces compact tables and parallel coordinates to a key KPI', () => {
    for (const chart of ['table', 'parallel'] as const) {
      const card: AnalysisCardQueryState = { ...funnel, chart, query: { ...funnel.query, chart } }
      expect(planCardPresentation(card, { width: 360, height: 210 }).effectiveChart).toBe('kpi')
    }
  })

  it('does not mutate card query or chart while planning a resize', () => {
    const before = JSON.stringify(funnel)
    planCardPresentation(funnel, { width: 360, height: 210 })
    expect(JSON.stringify(funnel)).toBe(before)
  })

  it('returns renderer-specific minimum dimensions', () => {
    expect(recommendedGridMinimum('kpi')).toEqual({ width: 220, height: 140 })
    expect(recommendedGridMinimum('parallel')).toEqual({ width: 560, height: 320 })
    expect(recommendedGridMinimum('table')).toEqual({ width: 520, height: 260 })
  })
})

