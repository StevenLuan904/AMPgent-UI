import type { SnapshotCandidate } from './analysisDataContracts'

const aminoAcids = [...'ACDEFGHIKLMNPQRSTVWY']
const hydrophobic = new Set([...'AILMFWVY'])
const aromatic = new Set([...'FWY'])
const positive = new Set([...'KR'])
const flexible = new Set([...'GP'])

export const familyPropertyLabels = ['正电残基', '疏水残基', '芳香残基', '柔性残基', '半胱氨酸', '两亲性'] as const

export interface PeptideFamilySummary {
  id: string
  label: string
  motif: string | null
  phenotype: string
  count: number
  share: number
  representative: string
  structureEligible: number
  medianActivity: number | null
  medianMic: number | null
  lowHemolysisRate: number | null
  lowToxicityRate: number | null
  phenotypes: Array<{ name: string; value: number }>
  properties: number[]
}

export interface PeptideFamilyAnalysis {
  scopeLabel: string
  candidateCount: number
  familyCount: number
  singletonRate: number
  dominantShare: number
  families: PeptideFamilySummary[]
  displayedFamilies: PeptideFamilySummary[]
  remainderCount: number
  assignments: Array<{ sequence: string; familyId: string; phenotype: string }>
}

export interface ConstraintSetSummary {
  id: string
  label: string
  detail: string
  total: number
}

export interface ConstraintIntersection {
  key: string
  count: number
  share: number
  active: boolean[]
  labels: string[]
}

export interface ConstraintIntersectionAnalysis {
  candidateCount: number
  sets: ConstraintSetSummary[]
  intersections: ConstraintIntersection[]
}

interface PreparedCandidate {
  candidate: SnapshotCandidate
  sequence: string
  dimers: Set<string>
  composition: number[]
  phenotype: string
  properties: number[]
}

function numericMetric(candidate: SnapshotCandidate, key: string) {
  const value = candidate.metrics[key]?.value
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function median(values: Array<number | null>) {
  const sorted = values.filter((value): value is number => value != null && Number.isFinite(value)).sort((left, right) => left - right)
  if (!sorted.length) return null
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}

function rate(values: Array<boolean | null>) {
  const observed = values.filter((value): value is boolean => value != null)
  return observed.length ? observed.filter(Boolean).length / observed.length : null
}

function residueFraction(sequence: string, residues: Set<string>) {
  return sequence.length ? [...sequence].filter((residue) => residues.has(residue)).length / sequence.length : 0
}

function sequenceComposition(sequence: string) {
  return aminoAcids.map((residue) => [...sequence].filter((item) => item === residue).length / Math.max(1, sequence.length))
}

function kmerSet(sequence: string, size = 2) {
  const values = new Set<string>()
  for (let index = 0; index <= sequence.length - size; index += 1) values.add(sequence.slice(index, index + size))
  return values
}

function cosine(left: number[], right: number[]) {
  const dot = left.reduce((sum, value, index) => sum + value * right[index], 0)
  const leftNorm = Math.sqrt(left.reduce((sum, value) => sum + value * value, 0))
  const rightNorm = Math.sqrt(right.reduce((sum, value) => sum + value * value, 0))
  return leftNorm && rightNorm ? dot / (leftNorm * rightNorm) : 0
}

function jaccard(left: Set<string>, right: Set<string>) {
  const intersection = [...left].filter((value) => right.has(value)).length
  const union = new Set([...left, ...right]).size
  return union ? intersection / union : 0
}

export function peptideSequenceSimilarity(left: string, right: string) {
  const a = left.toUpperCase().replace(/[^ACDEFGHIKLMNPQRSTVWY]/g, '')
  const b = right.toUpperCase().replace(/[^ACDEFGHIKLMNPQRSTVWY]/g, '')
  if (!a.length || !b.length) return 0
  const lengthRatio = Math.min(a.length, b.length) / Math.max(a.length, b.length)
  if (lengthRatio < .55) return 0
  return .55 * jaccard(kmerSet(a), kmerSet(b)) + .3 * cosine(sequenceComposition(a), sequenceComposition(b)) + .15 * lengthRatio
}

function biochemicalPhenotype(candidate: SnapshotCandidate, sequence: string) {
  const charge = numericMetric(candidate, 'net_charge_ph7_4') ?? 0
  const moment = numericMetric(candidate, 'hydrophobic_moment_eisenberg') ?? 0
  const hydrophobicRatio = numericMetric(candidate, 'hydrophobic_ratio_modlamp') ?? residueFraction(sequence, hydrophobic)
  const cysteineCount = [...sequence].filter((residue) => residue === 'C').length
  if (cysteineCount >= 2 && cysteineCount / sequence.length >= .1) return '富半胱氨酸'
  if (charge >= 4 && moment >= .4) return '强阳离子两亲型'
  if (charge >= 2.5 && moment >= .3) return '阳离子两亲型'
  if (hydrophobicRatio >= .5) return '疏水富集型'
  if (residueFraction(sequence, aromatic) >= .18) return '芳香富集型'
  return '均衡型'
}

function prepareCandidate(candidate: SnapshotCandidate): PreparedCandidate {
  const sequence = candidate.sequence.toUpperCase().replace(/[^ACDEFGHIKLMNPQRSTVWY]/g, '')
  return {
    candidate,
    sequence,
    dimers: kmerSet(sequence),
    composition: sequenceComposition(sequence),
    phenotype: biochemicalPhenotype(candidate, sequence),
    properties: [
      residueFraction(sequence, positive),
      residueFraction(sequence, hydrophobic),
      residueFraction(sequence, aromatic),
      residueFraction(sequence, flexible),
      residueFraction(sequence, new Set(['C'])),
      Math.max(0, Math.min(1, numericMetric(candidate, 'hydrophobic_moment_eisenberg') ?? 0)),
    ],
  }
}

function preparedSimilarity(left: PreparedCandidate, right: PreparedCandidate) {
  const lengthRatio = Math.min(left.sequence.length, right.sequence.length) / Math.max(left.sequence.length, right.sequence.length)
  if (lengthRatio < .55) return 0
  return .55 * jaccard(left.dimers, right.dimers) + .3 * cosine(left.composition, right.composition) + .15 * lengthRatio
}

function sharedMotif(members: PreparedCandidate[]) {
  const counts = new Map<string, number>()
  for (const member of members) {
    const observed = kmerSet(member.sequence, 3)
    for (const motif of observed) counts.set(motif, (counts.get(motif) ?? 0) + 1)
  }
  const best = [...counts.entries()].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))[0]
  return best && best[1] >= Math.max(2, Math.ceil(members.length * .25)) ? best[0] : null
}

export function buildPeptideFamilyAnalysis(
  candidates: SnapshotCandidate[],
  scopeLabel = '本轮候选库',
  displayLimit = 7,
): PeptideFamilyAnalysis {
  const prepared = candidates.map(prepareCandidate).sort((left, right) => left.sequence.localeCompare(right.sequence))
  const clusters: Array<{ representative: PreparedCandidate; members: PreparedCandidate[] }> = []
  for (const candidate of prepared) {
    let bestIndex = -1
    let bestScore = 0
    for (let index = 0; index < clusters.length; index += 1) {
      const score = preparedSimilarity(candidate, clusters[index].representative)
      if (score > bestScore) {
        bestScore = score
        bestIndex = index
      }
    }
    if (bestIndex >= 0 && bestScore >= .46) clusters[bestIndex].members.push(candidate)
    else clusters.push({ representative: candidate, members: [candidate] })
  }

  const ordered = clusters.sort((left, right) => right.members.length - left.members.length || left.representative.sequence.localeCompare(right.representative.sequence))
  const families = ordered.map((cluster, index): PeptideFamilySummary => {
    const phenotypeCounts = new Map<string, number>()
    for (const member of cluster.members) phenotypeCounts.set(member.phenotype, (phenotypeCounts.get(member.phenotype) ?? 0) + 1)
    const phenotypes = [...phenotypeCounts.entries()].map(([name, value]) => ({ name, value })).sort((left, right) => right.value - left.value || left.name.localeCompare(right.name))
    const motif = sharedMotif(cluster.members)
    const id = `F${String(index + 1).padStart(2, '0')}`
    const count = cluster.members.length
    const propertyMeans = familyPropertyLabels.map((_, propertyIndex) => cluster.members.reduce((sum, member) => sum + member.properties[propertyIndex], 0) / count)
    return {
      id,
      label: `${id} · ${motif ? `${motif}基序` : phenotypes[0]?.name ?? '序列簇'}`,
      motif,
      phenotype: phenotypes[0]?.name ?? '均衡型',
      count,
      share: candidates.length ? count / candidates.length : 0,
      representative: cluster.representative.sequence,
      structureEligible: cluster.members.filter((member) => member.candidate.admission.structureEligible).length,
      medianActivity: median(cluster.members.map((member) => numericMetric(member.candidate, 'macrel_amp_probability'))),
      medianMic: median(cluster.members.map((member) => {
        const value = numericMetric(member.candidate, 'llamp_log10_mic_um')
        return value == null ? null : 10 ** value
      })),
      lowHemolysisRate: rate(cluster.members.map((member) => {
        const value = numericMetric(member.candidate, 'macrel_hemolysis_probability')
        return value == null ? null : value < .5
      })),
      lowToxicityRate: rate(cluster.members.map((member) => {
        const value = numericMetric(member.candidate, 'toxinpred3_hybrid_score')
        return value == null ? null : value < .5
      })),
      phenotypes,
      properties: propertyMeans,
    }
  })
  const displayedFamilies = families.slice(0, displayLimit)
  const remainderCount = families.slice(displayLimit).reduce((sum, family) => sum + family.count, 0)
  const assignments = ordered.flatMap((cluster, index) => cluster.members.map((member) => ({
    sequence: member.candidate.sequence,
    familyId: families[index].id,
    phenotype: member.phenotype,
  })))
  return {
    scopeLabel,
    candidateCount: candidates.length,
    familyCount: families.length,
    singletonRate: families.length ? families.filter((family) => family.count === 1).length / families.length : 0,
    dominantShare: families[0]?.share ?? 0,
    families,
    displayedFamilies,
    remainderCount,
    assignments,
  }
}

export function buildConstraintIntersectionAnalysis(candidates: SnapshotCandidate[], limit = 9): ConstraintIntersectionAnalysis {
  const definitions = [
    { id: 'activity', label: '活性预测', detail: '抗菌概率 ≥ 0.50', pass: (candidate: SnapshotCandidate) => (numericMetric(candidate, 'macrel_amp_probability') ?? -Infinity) >= .5 },
    { id: 'mic', label: '抑菌浓度', detail: '预测浓度 ≤ 32 微摩尔', pass: (candidate: SnapshotCandidate) => {
      const value = numericMetric(candidate, 'llamp_log10_mic_um')
      return value != null && 10 ** value <= 32
    } },
    { id: 'hemolysis', label: '低溶血', detail: '溶血概率 < 0.50', pass: (candidate: SnapshotCandidate) => (numericMetric(candidate, 'macrel_hemolysis_probability') ?? Infinity) < .5 },
    { id: 'toxicity', label: '低毒性', detail: '毒性评分 < 0.50', pass: (candidate: SnapshotCandidate) => (numericMetric(candidate, 'toxinpred3_hybrid_score') ?? Infinity) < .5 },
    { id: 'charge', label: '正电性', detail: '酸碱度7.4下净电荷 ≥ 2', pass: (candidate: SnapshotCandidate) => (numericMetric(candidate, 'net_charge_ph7_4') ?? -Infinity) >= 2 },
  ]
  const totals = definitions.map(() => 0)
  const groups = new Map<string, { active: boolean[]; count: number }>()
  for (const candidate of candidates) {
    const active = definitions.map((definition, index) => {
      const passed = definition.pass(candidate)
      if (passed) totals[index] += 1
      return passed
    })
    const key = active.map((value) => value ? '1' : '0').join('')
    const group = groups.get(key) ?? { active, count: 0 }
    group.count += 1
    groups.set(key, group)
  }
  const intersections = [...groups.entries()]
    .map(([key, group]) => ({
      key,
      count: group.count,
      share: candidates.length ? group.count / candidates.length : 0,
      active: group.active,
      labels: definitions.filter((_, index) => group.active[index]).map((definition) => definition.label),
    }))
    .sort((left, right) => right.count - left.count || right.active.filter(Boolean).length - left.active.filter(Boolean).length || left.key.localeCompare(right.key))
    .slice(0, limit)
  return {
    candidateCount: candidates.length,
    sets: definitions.map((definition, index) => ({ id: definition.id, label: definition.label, detail: definition.detail, total: totals[index] })),
    intersections,
  }
}
