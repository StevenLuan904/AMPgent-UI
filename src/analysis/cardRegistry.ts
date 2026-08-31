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
    defaultLayout: { x: 0, y: 0, w: 12, h: 3, minW: 7, minH: 2 },
    compatibleGrains: ['proposal_occurrence', 'unique_sequence'],
    extensionPoint: 'cards/run-quality',
  },
  {
    id: 'lineage_and_yield',
    title: '来源与产率',
    description: '生成、去重、评估与入池的逐级损失',
    defaultLayout: { x: 0, y: 3, w: 5, h: 4, minW: 3, minH: 3 },
    compatibleGrains: ['proposal_occurrence', 'unique_sequence'],
    extensionPoint: 'cards/lineage-yield',
  },
  {
    id: 'score_distribution',
    title: '评分分布',
    description: '按生成器和阶段比较评分器分布',
    defaultLayout: { x: 5, y: 3, w: 7, h: 4, minW: 4, minH: 3 },
    compatibleGrains: ['proposal_occurrence', 'unique_sequence', 'candidate_metric'],
    extensionPoint: 'cards/score-distribution',
  },
  {
    id: 'generator_contribution',
    title: '短肽序列家族',
    description: '序列相似性家族、生化表型与残基组成',
    defaultLayout: { x: 0, y: 13, w: 7, h: 6, minW: 5, minH: 5 },
    compatibleGrains: ['unique_sequence'],
    extensionPoint: 'cards/origin-composition',
  },
  {
    id: 'safety_profile',
    title: '活性—安全约束交集',
    description: '活性、抑菌浓度、溶血、毒性与净电荷的独占交集',
    defaultLayout: { x: 7, y: 13, w: 5, h: 6, minW: 4, minH: 5 },
    compatibleGrains: ['unique_sequence', 'candidate_metric'],
    extensionPoint: 'cards/safety-profile',
  },
  {
    id: 'multi_objective_conflict',
    title: '多目标前沿',
    description: '非支配等级、约束与不可同时改善区间',
    defaultLayout: { x: 0, y: 7, w: 7, h: 6, minW: 5, minH: 5 },
    compatibleGrains: ['unique_sequence', 'candidate_metric'],
    extensionPoint: 'cards/multi-objective',
  },
  {
    id: 'structure_energy',
    title: 'Rosetta 界面能',
    description: '按靶点比较精修构象能量分布与稳定阈值',
    defaultLayout: { x: 7, y: 7, w: 5, h: 6, minW: 4, minH: 5 },
    compatibleGrains: ['candidate_target_structure'],
    extensionPoint: 'cards/structure-energy',
  },
  {
    id: 'sequence_alluvial',
    title: '候选命运流向',
    description: '生成来源、序列家族与候选结局的逐条关联',
    defaultLayout: { x: 0, y: 19, w: 12, h: 6, minW: 8, minH: 5 },
    compatibleGrains: ['unique_sequence'],
    extensionPoint: 'cards/sequence-alluvial',
  },
  {
    id: 'composition_landscape',
    title: '序列组成空间',
    description: '正电、疏水与其它残基比例的三元分布',
    defaultLayout: { x: 0, y: 25, w: 6, h: 6, minW: 5, minH: 5 },
    compatibleGrains: ['unique_sequence'],
    extensionPoint: 'cards/composition-landscape',
  },
  {
    id: 'metric_correlation',
    title: '评分器相关结构',
    description: '八项连续指标的斯皮尔曼秩相关',
    defaultLayout: { x: 6, y: 25, w: 6, h: 6, minW: 5, minH: 5 },
    compatibleGrains: ['candidate_metric'],
    extensionPoint: 'cards/metric-correlation',
  },
  {
    id: 'residue_enrichment',
    title: '残基富集效应',
    description: '成熟核心相对未入选候选的残基优势比与区间',
    defaultLayout: { x: 0, y: 31, w: 12, h: 7, minW: 8, minH: 6 },
    compatibleGrains: ['unique_sequence'],
    extensionPoint: 'cards/residue-enrichment',
  },
  {
    id: 'candidate_laboratory',
    title: '候选审查台',
    description: '可筛选候选表、证据与后续操作',
    defaultLayout: { x: 0, y: 38, w: 12, h: 7, minW: 7, minH: 6 },
    compatibleGrains: ['unique_sequence', 'candidate_metric', 'candidate_target_structure'],
    extensionPoint: 'cards/candidate-laboratory',
  },
]

export const defaultDashboardLayout = cardRegistry.map(({ id, defaultLayout }) => ({
  i: id,
  ...defaultLayout,
}))
