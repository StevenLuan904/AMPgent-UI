import type { DashboardCardDefinition } from './contracts'

/**
 * One registry drives layout defaults, the card library, compatibility checks, and future routing.
 * A follow-up implementation should add a card here before wiring rendering and an API handler.
 */
export const cardRegistry: DashboardCardDefinition[] = [
  {
    id: 'run_quality',
    title: '运行质量',
    description: '规模、去重、覆盖率、分布外数据与最终产率',
    defaultLayout: { x: 0, y: 0, w: 12, h: 3, minW: 8, minH: 3 },
    compatibleGrains: ['proposal_occurrence', 'unique_sequence'],
    extensionPoint: 'cards/run-quality',
  },
  {
    id: 'lineage_and_yield',
    title: '来源与产率',
    description: '生成、去重、评估与入池的逐级损失',
    defaultLayout: { x: 0, y: 3, w: 5, h: 5, minW: 4, minH: 4 },
    compatibleGrains: ['proposal_occurrence', 'unique_sequence'],
    extensionPoint: 'cards/lineage-yield',
  },
  {
    id: 'score_distribution',
    title: '评分分布',
    description: '按生成器和阶段比较评分器分布',
    defaultLayout: { x: 5, y: 3, w: 7, h: 5, minW: 5, minH: 4 },
    compatibleGrains: ['proposal_occurrence', 'unique_sequence', 'candidate_metric'],
    extensionPoint: 'cards/score-distribution',
  },
  {
    id: 'generator_contribution',
    title: '来源构成',
    description: '独占来源、共享来源与最终贡献',
    defaultLayout: { x: 0, y: 8, w: 4, h: 4, minW: 3, minH: 4 },
    compatibleGrains: ['unique_sequence'],
    extensionPoint: 'cards/origin-composition',
  },
  {
    id: 'safety_profile',
    title: '安全性概览',
    description: '溶血、毒性、分布外数据与缺失风险',
    defaultLayout: { x: 4, y: 8, w: 4, h: 4, minW: 3, minH: 4 },
    compatibleGrains: ['unique_sequence', 'candidate_metric'],
    extensionPoint: 'cards/safety-profile',
  },
  {
    id: 'multi_objective_conflict',
    title: '多目标前沿',
    description: '非支配等级、约束与不可同时改善区间',
    defaultLayout: { x: 8, y: 8, w: 4, h: 4, minW: 4, minH: 4 },
    compatibleGrains: ['unique_sequence', 'candidate_metric'],
    extensionPoint: 'cards/multi-objective',
  },
  {
    id: 'candidate_laboratory',
    title: '候选审查台',
    description: '可筛选候选表、证据与后续操作',
    defaultLayout: { x: 0, y: 12, w: 12, h: 5, minW: 8, minH: 4 },
    compatibleGrains: ['unique_sequence', 'candidate_metric', 'candidate_target_structure'],
    extensionPoint: 'cards/candidate-laboratory',
  },
]

export const defaultDashboardLayout = cardRegistry.map(({ id, defaultLayout }) => ({
  i: id,
  ...defaultLayout,
}))
