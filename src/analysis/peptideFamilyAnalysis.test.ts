import { describe, expect, it } from 'vitest'
import type { SnapshotCandidate } from './analysisDataContracts'
import { buildConstraintIntersectionAnalysis, buildPeptideFamilyAnalysis, peptideSequenceSimilarity } from './peptideFamilyAnalysis'

function candidate(sequence: string, overrides: Partial<SnapshotCandidate> = {}): SnapshotCandidate {
  const metric = (value: number) => ({ value, text: null, unit: null, status: 'succeeded', outOfDomain: false, limitations: [], toolCallId: 'tool' })
  return {
    id: sequence,
    sequence,
    sequenceSha256: sequence,
    generation: 0,
    parentId: null,
    status: 'generated',
    proposalRank: 1,
    originSet: ['amp_designer'],
    cohortSha256: null,
    admission: { status: 'mature_core', reasons: [], paretoFront: 1, structureEligible: false },
    metrics: {
      macrel_amp_probability: metric(.8),
      llamp_log10_mic_um: metric(1),
      macrel_hemolysis_probability: metric(.2),
      toxinpred3_hybrid_score: metric(.1),
      net_charge_ph7_4: metric(4),
      hydrophobic_moment_eisenberg: metric(.55),
      hydrophobic_ratio_modlamp: metric(.45),
    },
    ...overrides,
  }
}

describe('peptide family analysis', () => {
  it('clusters close sequence variants and preserves every member', () => {
    const input = [candidate('KLLKLLKLL'), candidate('KLLKILKLL'), candidate('CCGACCGAC'), candidate('CCGACCGAA')]
    const result = buildPeptideFamilyAnalysis(input, '测试靶点')
    expect(result.scopeLabel).toBe('测试靶点')
    expect(result.families.reduce((sum, family) => sum + family.count, 0)).toBe(input.length)
    expect(result.familyCount).toBe(2)
    expect(result.families[0].motif).not.toBeNull()
  })

  it('is deterministic regardless of candidate input order', () => {
    const input = [candidate('KLLKLLKLL'), candidate('KLLKILKLL'), candidate('CCGACCGAC')]
    const forward = buildPeptideFamilyAnalysis(input)
    const reverse = buildPeptideFamilyAnalysis([...input].reverse())
    expect(reverse.families.map((family) => [family.label, family.count])).toEqual(forward.families.map((family) => [family.label, family.count]))
  })

  it('scores identical sequences above unrelated sequences', () => {
    expect(peptideSequenceSimilarity('KLLKLLKLL', 'KLLKLLKLL')).toBeCloseTo(1)
    expect(peptideSequenceSimilarity('KLLKLLKLL', 'DEDEDEDED')).toBeLessThan(.2)
  })
})

describe('constraint intersection analysis', () => {
  it('builds exclusive intersections and set totals from real metric thresholds', () => {
    const passing = candidate('KLLKLLKLL')
    const failing = candidate('DEDEDEDED', {
      metrics: {
        ...candidate('X').metrics,
        macrel_amp_probability: { ...candidate('X').metrics.macrel_amp_probability, value: .2 },
        macrel_hemolysis_probability: { ...candidate('X').metrics.macrel_hemolysis_probability, value: .9 },
        toxinpred3_hybrid_score: { ...candidate('X').metrics.toxinpred3_hybrid_score, value: .8 },
        net_charge_ph7_4: { ...candidate('X').metrics.net_charge_ph7_4, value: -2 },
      },
    })
    const result = buildConstraintIntersectionAnalysis([passing, failing])
    expect(result.sets.map((set) => set.total)).toEqual([1, 2, 1, 1, 1])
    expect(result.intersections.reduce((sum, item) => sum + item.count, 0)).toBe(2)
    expect(result.intersections).toHaveLength(2)
  })
})
