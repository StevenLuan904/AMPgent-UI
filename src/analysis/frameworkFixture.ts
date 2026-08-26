import type { AnalysisDataset } from './contracts'

/**
 * Layout fixture only. Values are intentionally labeled in the UI and must never be interpreted
 * as persisted scientific evidence. Replace this adapter with GET/POST /v1/analytics/* results.
 */
export const frameworkFixture: AnalysisDataset = {
  runLabel: '短肽设计 · 框架示例数据',
  generators: [
    { id: 'amp_designer', label: 'AMP Designer', color: '#4f7df3', raw: 300, unique: 258, metricComplete: 241, safetyPass: 93, candidatePool: 18, admitted: 15 },
    { id: 'ampgan_v2', label: 'AMPGAN v2', color: '#9b7bd3', raw: 300, unique: 244, metricComplete: 221, safetyPass: 81, candidatePool: 15, admitted: 12 },
    { id: 'hydramp', label: 'HydrAMP', color: '#55bfc3', raw: 300, unique: 271, metricComplete: 236, safetyPass: 72, candidatePool: 12, admitted: 8 },
  ],
  sourcePatterns: [
    { label: '仅 AMP Designer', count: 11 },
    { label: '仅 AMPGAN', count: 7 },
    { label: '仅 HydrAMP', count: 5 },
    { label: 'AMP Designer 与 AMPGAN', count: 6 },
    { label: 'AMP Designer 与 HydrAMP', count: 3 },
    { label: 'AMPGAN 与 HydrAMP', count: 2 },
    { label: '三个生成器共有', count: 1 },
  ],
  distributions: [
    { generator: 'AMP Designer', stage: 'raw_proposal', count: 300, fiveNumberSummary: [0.12, 0.39, 0.58, 0.74, 0.96], missing: 9, outOfDomain: 18 },
    { generator: 'AMP Designer', stage: 'candidate_pool', count: 18, fiveNumberSummary: [0.43, 0.62, 0.77, 0.86, 0.98], missing: 0, outOfDomain: 1 },
    { generator: 'AMPGAN v2', stage: 'raw_proposal', count: 300, fiveNumberSummary: [0.09, 0.31, 0.52, 0.69, 0.93], missing: 14, outOfDomain: 22 },
    { generator: 'AMPGAN v2', stage: 'candidate_pool', count: 15, fiveNumberSummary: [0.37, 0.57, 0.72, 0.82, 0.95], missing: 0, outOfDomain: 2 },
    { generator: 'HydrAMP', stage: 'raw_proposal', count: 300, fiveNumberSummary: [0.16, 0.36, 0.55, 0.71, 0.91], missing: 10, outOfDomain: 27 },
    { generator: 'HydrAMP', stage: 'candidate_pool', count: 12, fiveNumberSummary: [0.41, 0.59, 0.69, 0.79, 0.92], missing: 0, outOfDomain: 1 },
  ],
  pareto: [
    { id: 'P-018', sequence: 'KLLKRLVKKLL', generator: 'AMP Designer', activity: 0.89, hemolysis: 0.18, charge: 5.2, paretoRank: 1 },
    { id: 'P-031', sequence: 'KWLKKIGAVLK', generator: 'AMP Designer', activity: 0.82, hemolysis: 0.11, charge: 4.1, paretoRank: 1 },
    { id: 'P-044', sequence: 'FLKLLKKLAFK', generator: 'AMPGAN v2', activity: 0.93, hemolysis: 0.27, charge: 4.8, paretoRank: 1 },
    { id: 'P-067', sequence: 'KRLVQRLKELG', generator: 'HydrAMP', activity: 0.74, hemolysis: 0.06, charge: 3.7, paretoRank: 1 },
    { id: 'P-074', sequence: 'RLLRAVLKRL', generator: 'AMPGAN v2', activity: 0.78, hemolysis: 0.15, charge: 4.5, paretoRank: 2 },
    { id: 'P-086', sequence: 'KALKWLAKRL', generator: 'HydrAMP', activity: 0.70, hemolysis: 0.12, charge: 3.9, paretoRank: 2 },
    { id: 'P-092', sequence: 'LKKLGLKLLK', generator: 'AMP Designer', activity: 0.86, hemolysis: 0.31, charge: 4.9, paretoRank: 2 },
    { id: 'P-103', sequence: 'GLFDIVKKVVG', generator: 'HydrAMP', activity: 0.62, hemolysis: 0.08, charge: 2.8, paretoRank: 3 },
    { id: 'P-114', sequence: 'KWWRWLKKL', generator: 'AMPGAN v2', activity: 0.91, hemolysis: 0.43, charge: 4.4, paretoRank: 3 },
    { id: 'P-127', sequence: 'AKLKKIGQLLK', generator: 'AMP Designer', activity: 0.67, hemolysis: 0.22, charge: 3.6, paretoRank: 4 },
  ],
  candidates: [
    { id: 'P-018', sequence: 'KLLKRLVKKLL', originSet: ['AMP Designer'], stage: 'candidate_pool', activity: 0.89, hemolysis: 0.18, toxicity: 0.09, charge: 5.2, paretoRank: 1, flags: [] },
    { id: 'P-031', sequence: 'KWLKKIGAVLK', originSet: ['AMP Designer', 'AMPGAN v2'], stage: 'candidate_pool', activity: 0.82, hemolysis: 0.11, toxicity: 0.08, charge: 4.1, paretoRank: 1, flags: ['shared-origin'] },
    { id: 'P-044', sequence: 'FLKLLKKLAFK', originSet: ['AMPGAN v2'], stage: 'candidate_pool', activity: 0.93, hemolysis: 0.27, toxicity: 0.13, charge: 4.8, paretoRank: 1, flags: ['safety-boundary'] },
    { id: 'P-067', sequence: 'KRLVQRLKELG', originSet: ['HydrAMP'], stage: 'candidate_pool', activity: 0.74, hemolysis: 0.06, toxicity: 0.05, charge: 3.7, paretoRank: 1, flags: [] },
    { id: 'P-074', sequence: 'RLLRAVLKRL', originSet: ['AMPGAN v2', 'HydrAMP'], stage: 'candidate_pool', activity: 0.78, hemolysis: 0.15, toxicity: 0.10, charge: 4.5, paretoRank: 2, flags: ['shared-origin'] },
  ],
  provenance: {
    resultId: 'fixture-result-001',
    queryId: 'fixture-query-001',
    snapshotId: 'framework-preview',
    computedAt: '2026-08-26T00:00:00Z',
    source: 'framework_fixture',
    method: 'UI framework fixture; no scientific computation performed',
    coverage: { observed: 698, expected: 900, missing: 33, outOfDomain: 67 },
    warnings: ['示例数据仅用于验证信息架构、布局与交互，不代表任何真实运行结果。'],
  },
}
