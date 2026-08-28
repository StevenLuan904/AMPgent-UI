import { describe, expect, it } from 'vitest'
import type { SnapshotCandidate } from './analysisDataContracts'
import { buildMetricCorrelationAnalysis, buildResidueEnrichmentAnalysis, buildTernaryComposition } from './advancedBiologicalAnalysis'

function candidate(sequence: string, status: 'mature_core' | 'rejected', x: number, y: number): SnapshotCandidate {
  return {
    id: sequence,
    sequence,
    sequenceSha256: sequence,
    originSet: ['amp_designer'],
    generation: 0,
    parentId: null,
    proposalRank: 1,
    cohortSha256: 'cohort',
    status: 'generated',
    admission: { status, paretoFront: null, structureEligible: status === 'mature_core', reasons: [] },
    metrics: {
      macrel_amp_probability: { value: x, text: null, unit: null, status: 'succeeded', outOfDomain: false, toolCallId: 'tool-activity', limitations: [] },
      llamp_log10_mic_um: { value: y, text: null, unit: null, status: 'succeeded', outOfDomain: false, toolCallId: 'tool-mic', limitations: [] },
    },
  }
}

describe('advanced biological analysis', () => {
  it('computes rank correlation without assuming a linear scale', () => {
    const rows = [candidate('KKAA', 'mature_core', 1, 10), candidate('KAAA', 'mature_core', 2, 20), candidate('AAAA', 'rejected', 3, 30)]
    const analysis = buildMetricCorrelationAnalysis(rows, [{ key: 'macrel_amp_probability', label: '甲' }, { key: 'llamp_log10_mic_um', label: '乙' }])
    expect(analysis.cells.find((cell) => cell.x === 1 && cell.y === 0)?.value).toBeCloseTo(1)
  })

  it('maps residue composition to a closed ternary simplex', () => {
    const point = buildTernaryComposition([candidate('KKAILD', 'mature_core', 1, 1)])[0]
    expect(point.positive + point.hydrophobic + point.other).toBeCloseTo(1)
    expect(point.positive).toBeCloseTo(2 / 6)
  })

  it('detects residue enrichment in the selected cohort', () => {
    const rows = [candidate('KKKKAA', 'mature_core', 1, 1), candidate('KKKAAA', 'mature_core', 1, 1), candidate('DDDDGG', 'rejected', 1, 1), candidate('DDDGGG', 'rejected', 1, 1)]
    const analysis = buildResidueEnrichmentAnalysis(rows)
    expect(analysis.rows.find((row) => row.residue === 'K')?.log2OddsRatio).toBeGreaterThan(0)
    expect(analysis.rows.find((row) => row.residue === 'D')?.log2OddsRatio).toBeLessThan(0)
  })
})
