import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import type { AnalysisSnapshot } from './analysisDataContracts'
import { executeAnalysisQuery } from './queryEngine'
import { validateAnalysisSnapshot, verifyAnalysisSnapshotDigest } from './snapshotAdapter'

const path = new URL('../../public/data/launch-analysis.snapshot.json', import.meta.url)
const rawSnapshot = readFileSync(path, 'utf8')
const snapshot = JSON.parse(rawSnapshot) as AnalysisSnapshot

function groupBy<T>(rows: T[], key: (row: T) => string): Map<string, T[]> {
  const groups = new Map<string, T[]>()
  for (const row of rows) {
    const value = key(row)
    groups.set(value, [...(groups.get(value) ?? []), row])
  }
  return groups
}

describe('real release snapshot invariants', () => {
  it('matches the runtime contract and cryptographic digest', async () => {
    expect(validateAnalysisSnapshot(snapshot)).toEqual([])
    expect(await verifyAnalysisSnapshotDigest(snapshot, rawSnapshot)).toBe(true)
  })

  it('preserves the audited run identity and cancelled status', () => {
    expect(snapshot.run).toMatchObject({
      id: '57afecc7-22e9-4efb-9051-acb11234013d',
      status: 'cancelled',
      specSha256: 'c0352933a7614aa98d75dfcc0ff3a83649d382e6f8865c1c38c523007e042b2a',
    })
    expect(snapshot.warnings.join(' ')).toContain('must not be inferred')
  })

  it('contains the exact audited row-level closure', () => {
    expect(snapshot.summary).toMatchObject({
      rawOccurrences: 900,
      uniqueCandidates: 773,
      promotedOccurrences: 773,
      invalidOccurrences: 127,
      observedEvaluations: 8503,
      expectedEvaluations: 8503,
      outOfDomainEvaluations: 0,
      structureEligible: 35,
      admissionCounts: { mature_core: 26, promising_uncertain: 124, rejected: 623 },
    })
    expect(Object.keys(snapshot.metricMethods)).toHaveLength(11)
  })

  it('contains three generators, three seeded cells each, and 300 proposals each', () => {
    const generatorCells = groupBy(snapshot.generatorCells, (cell) => cell.generator)
    const occurrences = groupBy(snapshot.occurrences, (item) => item.generator)
    expect([...generatorCells.keys()].sort()).toEqual(['amp_designer', 'ampgan_v2', 'hydramp'])
    for (const generator of generatorCells.keys()) {
      expect(generatorCells.get(generator)).toHaveLength(3)
      expect(occurrences.get(generator)).toHaveLength(300)
    }
  })

  it('executes a real per-generator funnel without count inflation', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'generator_funnel', metrics: [],
    })
    for (const generator of ['amp_designer', 'ampgan_v2', 'hydramp']) {
      const raw = result.rows.find((row) => row.generator === generator && row.stage === 'raw_proposal')
      const pool = result.rows.find((row) => row.generator === generator && row.stage === 'candidate_pool')
      const admitted = result.rows.find((row) => row.generator === generator && row.stage === 'admitted')
      expect(raw?.unique_sequence_count).toBe(300)
      expect(Number(pool?.unique_sequence_count)).toBeLessThanOrEqual(300)
      expect(Number(admitted?.unique_sequence_count)).toBeLessThanOrEqual(Number(pool?.unique_sequence_count))
    }
  })

  it('pivots every numeric scorer distribution with finite five-number summaries', () => {
    const numericMetrics = Object.keys(snapshot.metricMethods).filter((metric) =>
      snapshot.candidates.some((candidate) => candidate.metrics[metric]?.value != null),
    )
    expect(numericMetrics).toHaveLength(9)
    for (const metric of numericMetrics) {
      const result = executeAnalysisQuery(snapshot, {
        schemaVersion: 'analysis-pivot-query.1', queryKey: 'metric_distribution_by_generator',
        metrics: [metric], includeValues: false,
      })
      expect(result.distributions).toHaveLength(3)
      for (const item of result.distributions ?? []) {
        expect(item.summary.count).toBeGreaterThan(0)
        expect(item.summary.min).not.toBeNull()
        expect(item.summary.max).not.toBeNull()
        expect(Number(item.summary.min)).toBeLessThanOrEqual(Number(item.summary.max))
      }
    }
  })

  it('returns exactly 35 structure-eligible candidate records', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'candidate_table', metrics: ['macrel_amp_probability'],
      filters: { stages: ['admitted'] },
    })
    expect(result.records).toHaveLength(35)
  })

  it('returns complete real Pareto points for two audited objectives', () => {
    const result = executeAnalysisQuery(snapshot, {
      schemaVersion: 'analysis-pivot-query.1', queryKey: 'pareto_conflicts',
      metrics: ['llamp_log10_mic_um', 'macrel_hemolysis_probability'],
    })
    expect(result.records).toHaveLength(773)
    expect(result.records?.filter((record) => record.structureEligible)).toHaveLength(35)
  })
})
