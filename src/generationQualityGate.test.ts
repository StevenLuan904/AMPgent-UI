import { describe, expect, it } from 'vitest'
import { formatQualityGateRule, qualityGateCountSteps, qualityGateNodeSummary, qualityGateStatusLabel } from './generationQualityGate'
import type { GenerationQualityGate } from './types'

const gate: GenerationQualityGate = {
  status: 'not_applied',
  operator_name: 'autoresearch-rule-de-novo',
  operator_version: 'v2',
  proposal_count: 0,
  prefilter_pass_count: 0,
  materialized_descendant_count: 0,
  evaluated_descendant_count: 0,
  count_scope: { source: 'postgresql', run_id: 'run-1', operator_id: 'autoresearch-rule-de-novo-v2' },
  semantics: {
    proposal_and_prefilter_pass_are_not_materialized_descendants: true,
    materialized_descendant_requires_persisted_lineage_edge: true,
    evaluated_descendant_requires_persisted_evaluation: true,
    offline_validation_included: false,
  },
  rules: [
    { metric_key: 'guruprasad_instability_index', comparison: '<', threshold: 50, unit: 'dimensionless' },
    { metric_key: 'maximum_hydrophobic_run', comparison: '<=', threshold: 2, unit: 'residues' },
    { metric_key: 'hydrophobic_fraction', comparison: '<=', threshold: 0.45, unit: 'fraction' },
    { metric_key: 'net_charge_ph7_4', comparison: '>=', threshold: 3, unit: 'elementary_charge' },
  ],
}

describe('新生序列质量门', () => {
  it('旧运行明确显示未应用', () => {
    expect(qualityGateStatusLabel(gate)).toBe('本轮未应用')
    expect(qualityGateNodeSummary(gate)).toBe('第二版质量门 · 本轮未应用')
  })

  it('将提案、预筛、入库和评估拆成四层计数', () => {
    expect(qualityGateCountSteps({ ...gate, status: 'applied', proposal_count: 64, prefilter_pass_count: 64, materialized_descendant_count: 4, evaluated_descendant_count: 3 }))
      .toEqual([
        { label: '规则提案', value: 64 },
        { label: '预筛通过', value: 64 },
        { label: '谱系入库', value: 4 },
        { label: '完成评估', value: 3 },
      ])
  })

  it('将四项规则格式化为科学单位', () => {
    expect(gate.rules.map(formatQualityGateRule)).toEqual([
      { label: '序列不稳定指数', value: '＜ 50' },
      { label: '最长连续疏水段', value: '≤ 2 个残基' },
      { label: '疏水残基比例', value: '≤ 45%' },
      { label: '酸碱度7.4下净电荷', value: '≥ +3' },
    ])
  })
})
