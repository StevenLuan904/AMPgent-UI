import type { DashboardCardDefinition } from './contracts'

/**
 * One registry drives layout defaults, the card library, compatibility checks, and future routing.
 * A follow-up implementation should add a card here before wiring rendering and an API handler.
 */
export const cardRegistry: DashboardCardDefinition[] = [
  {
    id: 'run_quality',
    title: 'Run quality',
    description: '规模、去重、覆盖率、OOD 与最终产率',
    defaultLayout: { x: 0, y: 0, w: 12, h: 2, minW: 8, minH: 2 },
    compatibleGrains: ['proposal_occurrence', 'unique_sequence'],
    extensionPoint: 'cards/run-quality',
  },
  {
    id: 'lineage_and_yield',
    title: 'Lineage & yield',
    description: '生成、去重、评估与入池的逐级损失',
    defaultLayout: { x: 0, y: 2, w: 5, h: 5, minW: 4, minH: 4 },
    compatibleGrains: ['proposal_occurrence', 'unique_sequence'],
    extensionPoint: 'cards/lineage-yield',
  },
  {
    id: 'score_distribution',
    title: 'Score distribution',
    description: '按生成器和阶段比较评分器分布',
    defaultLayout: { x: 5, y: 2, w: 7, h: 5, minW: 5, minH: 4 },
    compatibleGrains: ['proposal_occurrence', 'unique_sequence', 'candidate_metric'],
    extensionPoint: 'cards/score-distribution',
  },
  {
    id: 'generator_contribution',
    title: 'Origin composition',
    description: '独占来源、共享来源与最终贡献',
    defaultLayout: { x: 0, y: 7, w: 4, h: 4, minW: 3, minH: 4 },
    compatibleGrains: ['unique_sequence'],
    extensionPoint: 'cards/origin-composition',
  },
  {
    id: 'safety_profile',
    title: 'Safety profile',
    description: '溶血、毒性、OOD 与缺失风险',
    defaultLayout: { x: 4, y: 7, w: 4, h: 4, minW: 3, minH: 4 },
    compatibleGrains: ['unique_sequence', 'candidate_metric'],
    extensionPoint: 'cards/safety-profile',
  },
  {
    id: 'multi_objective_conflict',
    title: 'Multi-objective frontier',
    description: 'Pareto 等级、约束与不可同时改善区间',
    defaultLayout: { x: 8, y: 7, w: 4, h: 4, minW: 4, minH: 4 },
    compatibleGrains: ['unique_sequence', 'candidate_metric'],
    extensionPoint: 'cards/multi-objective',
  },
  {
    id: 'candidate_laboratory',
    title: 'Candidate laboratory',
    description: '可筛选候选表、证据与后续操作',
    defaultLayout: { x: 0, y: 11, w: 12, h: 5, minW: 8, minH: 4 },
    compatibleGrains: ['unique_sequence', 'candidate_metric', 'candidate_target_structure'],
    extensionPoint: 'cards/candidate-laboratory',
  },
]

export const defaultDashboardLayout = cardRegistry.map(({ id, defaultLayout }) => ({
  i: id,
  ...defaultLayout,
}))
