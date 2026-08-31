import type { GenerationQualityGate, GenerationQualityRule } from './types'

const metricLabels: Record<GenerationQualityRule['metric_key'], string> = {
  guruprasad_instability_index: '序列不稳定指数',
  maximum_hydrophobic_run: '最长连续疏水段',
  hydrophobic_fraction: '疏水残基比例',
  net_charge_ph7_4: '酸碱度7.4下净电荷',
}

const comparisonLabels: Record<GenerationQualityRule['comparison'], string> = {
  '<': '＜',
  '<=': '≤',
  '>=': '≥',
}

export function qualityGateStatusLabel(gate: GenerationQualityGate) {
  return gate.status === 'applied' ? '本轮已应用' : '本轮未应用'
}

export function formatQualityGateRule(rule: GenerationQualityRule) {
  const threshold = rule.unit === 'fraction'
    ? `${(rule.threshold * 100).toLocaleString()}%`
    : rule.unit === 'elementary_charge'
      ? `+${rule.threshold.toLocaleString()}`
      : rule.unit === 'residues'
        ? `${rule.threshold.toLocaleString()} 个残基`
        : rule.threshold.toLocaleString()
  return {
    label: metricLabels[rule.metric_key],
    value: `${comparisonLabels[rule.comparison]} ${threshold}`,
  }
}

export function qualityGateCountSteps(gate: GenerationQualityGate) {
  return [
    { label: '规则提案', value: gate.proposal_count },
    { label: '预筛通过', value: gate.prefilter_pass_count },
    { label: '谱系入库', value: gate.materialized_descendant_count },
    { label: '完成评估', value: gate.evaluated_descendant_count },
  ]
}

export function qualityGateNodeSummary(gate: GenerationQualityGate) {
  if (gate.status === 'not_applied') return '第二版质量门 · 本轮未应用'
  return `第二版质量门 · ${gate.prefilter_pass_count.toLocaleString()} 通过 · ${gate.materialized_descendant_count.toLocaleString()} 入库`
}
