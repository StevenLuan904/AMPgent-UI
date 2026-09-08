import type { AnalysisSnapshot } from './analysisDataContracts'

export function createTestSnapshot(): AnalysisSnapshot {
  return {
    schemaVersion: 'ampgent-analysis-snapshot.1',
    snapshotId: 'test:snapshot:1',
    snapshotSha256: 'a'.repeat(64),
    generatedAt: '2026-08-27T00:00:00Z',
    source: 'frozen_release_snapshot',
    run: {
      id: 'run-test', status: 'cancelled', specSha256: 'b'.repeat(64), name: 'Test run',
      startedAt: null, finishedAt: '2026-08-27T01:00:00Z',
    },
    cohorts: ['cohort-1'],
    generatorCells: [
      { generator: 'gen-a', toolCallId: 'tool-gen-a' },
      { generator: 'gen-b', toolCallId: 'tool-gen-b' },
    ],
    occurrences: [
      { id: 'o1', candidateId: 'c1', sequenceSha256: 's1', generator: 'gen-a', generatorCell: 'a1', rank: 1, kind: 'de_novo', disposition: 'promoted_for_scoring', displayEligible: true, exclusionReason: null },
      { id: 'o2', candidateId: 'c2', sequenceSha256: 's2', generator: 'gen-a', generatorCell: 'a1', rank: 2, kind: 'de_novo', disposition: 'promoted_for_scoring', displayEligible: true, exclusionReason: null },
      { id: 'o3', candidateId: 'c2', sequenceSha256: 's2', generator: 'gen-b', generatorCell: 'b1', rank: 1, kind: 'de_novo', disposition: 'promoted_for_scoring', displayEligible: true, exclusionReason: null },
      { id: 'o4', candidateId: null, sequenceSha256: 'bad', generator: 'gen-b', generatorCell: 'b1', rank: 2, kind: 'de_novo', disposition: 'invalid', displayEligible: true, exclusionReason: null },
    ],
    candidates: [
      {
        id: 'c1', sequence: 'AAAA', sequenceSha256: 's1', generation: 0, parentId: null,
        status: 'generated', proposalRank: 1, originSet: ['gen-a'], cohortSha256: 'cohort-1',
        displayEligible: true, exclusionReason: null,
        admission: { status: 'mature_core', reasons: ['selected_by_pareto'], paretoFront: 1, structureEligible: true },
        metrics: {
          activity: { value: 0.8, text: null, unit: null, status: 'succeeded', outOfDomain: false, limitations: [], toolCallId: 'tool-activity' },
          safety: { value: 0.1, text: null, unit: null, status: 'succeeded', outOfDomain: false, limitations: [], toolCallId: 'tool-safety' },
        },
      },
      {
        id: 'c2', sequence: 'BBBB', sequenceSha256: 's2', generation: 0, parentId: null,
        status: 'generated', proposalRank: 2, originSet: ['gen-a', 'gen-b'], cohortSha256: 'cohort-1',
        displayEligible: true, exclusionReason: null,
        admission: {
          status: 'rejected', reasons: ['label_gate_failed:safety', 'rank_instability'],
          paretoFront: null, structureEligible: false,
        },
        metrics: {
          activity: { value: 0.4, text: null, unit: null, status: 'succeeded', outOfDomain: true, limitations: ['OOD'], toolCallId: 'tool-activity' },
          safety: { value: null, text: null, unit: null, status: 'succeeded', outOfDomain: false, limitations: [], toolCallId: 'tool-safety' },
        },
      },
    ],
    candidateExclusions: [],
    displayPopulation: {
      candidateCount: 2, candidateRecordCount: 2, excludedCandidateCount: 0,
      occurrenceCount: 4, occurrenceRecordCount: 4, excludedOccurrenceCount: 0,
      exclusionReason: 'historical_exact_replay',
    },
    metricMethods: {
      activity: [{ toolCallId: 'tool-activity' }],
      safety: [{ toolCallId: 'tool-safety' }],
    },
    admissionPolicy: null,
    decisionMethods: [],
    stageCheckpoints: [],
    summary: {
      rawOccurrences: 4, uniqueCandidates: 2, promotedOccurrences: 3, invalidOccurrences: 1,
      observedEvaluations: 4, expectedEvaluations: 4, outOfDomainEvaluations: 1,
      admissionCounts: { mature_core: 1, rejected: 1 }, structureEligible: 1,
    },
    coverage: { observed: 4, expected: 4, missing: 0, outOfDomain: 1 },
    warnings: ['Test warning.'],
  }
}
