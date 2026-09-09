import { describe, expect, it } from 'vitest'
import { distributionForStage } from './ResultDistribution'
import type { RunDetail } from './types'

const detailWithPreviewMetrics = (metrics: Array<{ name: string; value: number | null }>) => ({
  run: { id: 'run-1' },
  candidates: [{
    id: 'candidate-1', sequence: 'KKLL', length: 4, proposal_rank: 1, cohort: 'exploration', pareto_front: null,
    reasons: [], metrics: metrics.map((item) => ({ ...item, text: null, unit: null, status: 'succeeded', out_of_domain: false })),
  }],
} as unknown as RunDetail)

describe('runtime result distributions', () => {
  it('uses returned preview values without turning aggregate metrics into samples', () => {
    const result = distributionForStage(null, detailWithPreviewMetrics([{ name: 'llamp_log10_mic_um', value: -1 }]), 'mic')
    expect(result).toMatchObject({ label: '预测最小抑菌浓度', source: '1 条候选预览', values: [0.1] })
  })

  it('leaves a distribution absent when the detail has no raw metric values', () => {
    const result = distributionForStage(null, detailWithPreviewMetrics([{ name: 'llamp_log10_mic_um', value: null }]), 'mic')
    expect(result).toBeNull()
  })
})
