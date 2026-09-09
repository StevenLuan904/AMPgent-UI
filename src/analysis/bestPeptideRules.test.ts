import { describe, expect, it } from 'vitest'
import { compareBestPeptides, displayMetric, maturityLabel, maturityRank } from './bestPeptideRules'
import type { SnapshotCandidate } from './analysisDataContracts'

function candidate(id: string, status: string, overrides: Partial<SnapshotCandidate> = {}): SnapshotCandidate {
  return {
    id, sequence: id, sequenceSha256: id, generation: 0, parentId: null, status: 'generated', proposalRank: 1,
    originSet: ['test'], cohortSha256: null, displayEligible: true, exclusionReason: null,
    admission: { status, reasons: [], paretoFront: 1, structureEligible: false }, metrics: {}, ...overrides,
  }
}

describe('best peptide rules', () => {
  it('orders only by admitted cohort facts, never by heterogeneous metric values', () => {
    const first = candidate('first', 'mature_core', { metrics: { macrel_amp_probability: { value: 0.01, text: null, unit: null, status: 'succeeded', outOfDomain: false, limitations: [], toolCallId: 'a' } } })
    const second = candidate('second', 'mature_core', { metrics: { macrel_amp_probability: { value: 0.99, text: null, unit: null, status: 'succeeded', outOfDomain: false, limitations: [], toolCallId: 'b' } } })
    expect(compareBestPeptides(first, second)).toBeLessThan(0)
    expect(compareBestPeptides(candidate('uncertain', 'promising_uncertain'), candidate('rejected', 'rejected'))).toBeLessThan(0)
    expect(maturityRank('candidate_pool')).toBe(3)
  })

  it('uses fixed metric keys and renders missing evidence as 未评估', () => {
    const item = candidate('fixed', 'mature_core', { metrics: {
      macrel_amp_probability: { value: 0.8, text: null, unit: 'prob', status: 'succeeded', outOfDomain: false, limitations: [], toolCallId: 'a' },
      amp_read_log10_mic_um: { value: 4, text: null, unit: 'µM', status: 'succeeded', outOfDomain: false, limitations: [], toolCallId: 'b' },
      toxinpred3_hybrid_score: { value: null, text: null, unit: null, status: 'succeeded', outOfDomain: false, limitations: [], toolCallId: 'c' },
    } })
    expect(displayMetric(item, 'macrel_amp_probability')).toBe('0.800 prob')
    expect(displayMetric(item, 'macrel_hemolysis_probability')).toBe('未评估')
    expect(displayMetric(item, 'toxinpred3_hybrid_score')).toBe('未评估')
    expect(maturityLabel('mature_core')).toBe('成熟核心')
    expect(maturityLabel('promising_uncertain')).toBe('潜力待确认')
    expect(maturityLabel('rejected')).toBe('未入选')
  })
})
