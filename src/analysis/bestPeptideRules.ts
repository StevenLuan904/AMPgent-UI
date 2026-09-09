import type { SnapshotCandidate } from './analysisDataContracts'

export const bestPeptideMetricKeys = {
  activity: 'macrel_amp_probability',
  hemolysis: 'macrel_hemolysis_probability',
  toxicity: 'toxinpred3_hybrid_score',
  netCharge: 'net_charge_ph7_4',
} as const

const maturityLabels: Record<string, string> = {
  mature_core: '成熟核心',
  promising_uncertain: '潜力待确认',
  rejected: '未入选',
}

export function maturityRank(status: string) {
  if (status === 'mature_core') return 0
  if (status === 'promising_uncertain') return 1
  if (status === 'rejected') return 2
  return 3
}

export function maturityLabel(status: string) {
  return maturityLabels[status] ?? '未评估'
}

function numericMetric(candidate: SnapshotCandidate, key: string) {
  const metric = candidate.metrics[key]
  return metric?.status === 'succeeded' && metric.value !== null && metric.value !== undefined
    ? metric
    : null
}

export function displayMetric(candidate: SnapshotCandidate, key: string, digits = 3) {
  const metric = numericMetric(candidate, key)
  if (!metric) return '未评估'
  return `${Number(metric.value).toFixed(digits)}${metric.unit ? ` ${metric.unit}` : ''}`
}

function nullableAscending(left: number | null, right: number | null) {
  if (left === null && right === null) return 0
  if (left === null) return 1
  if (right === null) return -1
  return left - right
}

/**
 * Ranking is deliberately limited to homogeneous, backend-admitted facts.
 * Activity, hemolysis, and toxicity are display fields only: their units and
 * directions are not interchangeable across metric families.
 */
export function compareBestPeptides(left: SnapshotCandidate, right: SnapshotCandidate) {
  return maturityRank(left.admission.status) - maturityRank(right.admission.status)
    || nullableAscending(left.admission.paretoFront, right.admission.paretoFront)
    || Number(right.admission.structureEligible) - Number(left.admission.structureEligible)
    || nullableAscending(left.proposalRank, right.proposalRank)
    || left.id.localeCompare(right.id)
}
