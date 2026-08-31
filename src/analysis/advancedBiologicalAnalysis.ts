import type { SnapshotCandidate } from './analysisDataContracts'

const aminoAcids = [...'ACDEFGHIKLMNPQRSTVWY']
const positiveResidues = new Set([...'KR'])
const hydrophobicResidues = new Set([...'AILMFWVY'])

export interface MetricDefinition {
  key: string
  label: string
}

export interface MetricCorrelationCell {
  x: number
  y: number
  value: number
  count: number
}

export interface MetricCorrelationAnalysis {
  metrics: MetricDefinition[]
  cells: MetricCorrelationCell[]
}

export interface TernaryCompositionPoint {
  sequence: string
  positive: number
  hydrophobic: number
  other: number
  cohort: string
  structureEligible: boolean
}

export interface ResidueEnrichmentRow {
  residue: string
  log2OddsRatio: number
  lower: number
  upper: number
  selectedFraction: number
  referenceFraction: number
  selectedCount: number
  referenceCount: number
}

export interface ResidueEnrichmentAnalysis {
  selectedCount: number
  referenceCount: number
  rows: ResidueEnrichmentRow[]
}

export const defaultCorrelationMetrics: MetricDefinition[] = [
  { key: 'macrel_amp_probability', label: '抗菌概率' },
  { key: 'llamp_log10_mic_um', label: '最小抑菌浓度' },
  { key: 'amp_read_log10_mic_um', label: '交叉抑菌浓度' },
  { key: 'macrel_hemolysis_probability', label: '溶血概率' },
  { key: 'toxinpred3_hybrid_score', label: '毒性评分' },
  { key: 'net_charge_ph7_4', label: '净电荷' },
  { key: 'hydrophobic_moment_eisenberg', label: '疏水矩' },
  { key: 'hydrophobic_ratio_modlamp', label: '疏水比例' },
]

function metricValue(candidate: SnapshotCandidate, key: string) {
  const value = candidate.metrics[key]?.value
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function averageRanks(values: number[]) {
  const sorted = values.map((value, index) => ({ value, index })).sort((left, right) => left.value - right.value)
  const ranks = Array(values.length).fill(0) as number[]
  for (let start = 0; start < sorted.length;) {
    let end = start + 1
    while (end < sorted.length && sorted[end].value === sorted[start].value) end += 1
    const rank = (start + end - 1) / 2 + 1
    for (let index = start; index < end; index += 1) ranks[sorted[index].index] = rank
    start = end
  }
  return ranks
}

function pearson(left: number[], right: number[]) {
  if (left.length < 3 || left.length !== right.length) return 0
  const leftMean = left.reduce((sum, value) => sum + value, 0) / left.length
  const rightMean = right.reduce((sum, value) => sum + value, 0) / right.length
  let numerator = 0
  let leftVariance = 0
  let rightVariance = 0
  for (let index = 0; index < left.length; index += 1) {
    const leftDelta = left[index] - leftMean
    const rightDelta = right[index] - rightMean
    numerator += leftDelta * rightDelta
    leftVariance += leftDelta ** 2
    rightVariance += rightDelta ** 2
  }
  const denominator = Math.sqrt(leftVariance * rightVariance)
  return denominator ? numerator / denominator : 0
}

export function buildMetricCorrelationAnalysis(
  candidates: SnapshotCandidate[],
  metrics = defaultCorrelationMetrics,
): MetricCorrelationAnalysis {
  const cells: MetricCorrelationCell[] = []
  for (let y = 0; y < metrics.length; y += 1) {
    for (let x = y; x < metrics.length; x += 1) {
      const pairs = candidates.map((candidate) => [metricValue(candidate, metrics[x].key), metricValue(candidate, metrics[y].key)] as const)
        .filter((pair): pair is readonly [number, number] => pair[0] != null && pair[1] != null)
      const left = pairs.map((pair) => pair[0])
      const right = pairs.map((pair) => pair[1])
      const value = x === y ? 1 : pearson(averageRanks(left), averageRanks(right))
      cells.push({ x, y, value, count: pairs.length })
    }
  }
  return { metrics, cells }
}

export function buildTernaryComposition(candidates: SnapshotCandidate[]): TernaryCompositionPoint[] {
  return candidates.map((candidate) => {
    const sequence = candidate.sequence.toUpperCase().replace(/[^ACDEFGHIKLMNPQRSTVWY]/g, '')
    const denominator = Math.max(1, sequence.length)
    const positive = [...sequence].filter((residue) => positiveResidues.has(residue)).length / denominator
    const hydrophobic = [...sequence].filter((residue) => hydrophobicResidues.has(residue)).length / denominator
    return {
      sequence: candidate.sequence,
      positive,
      hydrophobic,
      other: Math.max(0, 1 - positive - hydrophobic),
      cohort: candidate.admission.status,
      structureEligible: candidate.admission.structureEligible,
    }
  })
}

export function buildResidueEnrichmentAnalysis(candidates: SnapshotCandidate[]): ResidueEnrichmentAnalysis {
  const selected = candidates.filter((candidate) => candidate.admission.status === 'mature_core')
  const reference = candidates.filter((candidate) => candidate.admission.status === 'rejected')
  const selectedSequences = selected.map((candidate) => candidate.sequence.toUpperCase().replace(/[^ACDEFGHIKLMNPQRSTVWY]/g, ''))
  const referenceSequences = reference.map((candidate) => candidate.sequence.toUpperCase().replace(/[^ACDEFGHIKLMNPQRSTVWY]/g, ''))
  const selectedTotal = selectedSequences.reduce((sum, sequence) => sum + sequence.length, 0)
  const referenceTotal = referenceSequences.reduce((sum, sequence) => sum + sequence.length, 0)
  const rows = aminoAcids.map((residue): ResidueEnrichmentRow => {
    const selectedCount = selectedSequences.reduce((sum, sequence) => sum + [...sequence].filter((item) => item === residue).length, 0)
    const referenceCount = referenceSequences.reduce((sum, sequence) => sum + [...sequence].filter((item) => item === residue).length, 0)
    const a = selectedCount + .5
    const b = Math.max(0, selectedTotal - selectedCount) + .5
    const c = referenceCount + .5
    const d = Math.max(0, referenceTotal - referenceCount) + .5
    const log2OddsRatio = Math.log2((a * d) / (b * c))
    const standardError = Math.sqrt(1 / a + 1 / b + 1 / c + 1 / d) / Math.log(2)
    return {
      residue,
      log2OddsRatio,
      lower: log2OddsRatio - 1.96 * standardError,
      upper: log2OddsRatio + 1.96 * standardError,
      selectedFraction: selectedTotal ? selectedCount / selectedTotal : 0,
      referenceFraction: referenceTotal ? referenceCount / referenceTotal : 0,
      selectedCount,
      referenceCount,
    }
  }).sort((left, right) => right.log2OddsRatio - left.log2OddsRatio)
  return { selectedCount: selected.length, referenceCount: reference.length, rows }
}
