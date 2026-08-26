import type { AnalysisQuestion } from './contracts'

export type PivotSlot = 'rows' | 'columns' | 'values' | 'categories'
export type ChartType = 'number' | 'bar' | 'line' | 'boxplot' | 'scatter' | 'heatmap' | 'table'

export interface AnalysisField {
  id: string
  label: string
  kind: 'dimension' | 'measure'
  semantic: 'identity' | 'stage' | 'source' | 'metric' | 'target' | 'status' | 'value'
}

export interface CardQuerySpec {
  cardId: AnalysisQuestion
  rows: string[]
  columns: string[]
  values: string[]
  categories: string[]
  filters: Record<string, string[]>
  chart: ChartType
  sourceNodeIds: string[]
}

export const fieldCatalog: AnalysisField[] = [
  { id: 'candidate', label: '候选序列', kind: 'dimension', semantic: 'identity' },
  { id: 'stage', label: '分析阶段', kind: 'dimension', semantic: 'stage' },
  { id: 'generator', label: '生成来源', kind: 'dimension', semantic: 'source' },
  { id: 'origin_set', label: '来源组合', kind: 'dimension', semantic: 'source' },
  { id: 'metric', label: '评分指标', kind: 'dimension', semantic: 'metric' },
  { id: 'target', label: '靶点', kind: 'dimension', semantic: 'target' },
  { id: 'cohort', label: '候选分组', kind: 'dimension', semantic: 'status' },
  { id: 'evidence_status', label: '证据状态', kind: 'dimension', semantic: 'status' },
  { id: 'candidate_count', label: '候选数量', kind: 'measure', semantic: 'value' },
  { id: 'metric_value', label: '评分数值', kind: 'measure', semantic: 'value' },
  { id: 'activity', label: '抗菌活性', kind: 'measure', semantic: 'value' },
  { id: 'hemolysis', label: '溶血风险', kind: 'measure', semantic: 'value' },
  { id: 'toxicity', label: '毒性风险', kind: 'measure', semantic: 'value' },
  { id: 'charge', label: '净电荷', kind: 'measure', semantic: 'value' },
  { id: 'structure_count', label: '结构证据数', kind: 'measure', semantic: 'value' },
]

const defaults: Record<AnalysisQuestion, Omit<CardQuerySpec, 'cardId' | 'sourceNodeIds'>> = {
  run_quality: { rows: [], columns: [], values: ['candidate_count'], categories: [], filters: {}, chart: 'number' },
  lineage_and_yield: { rows: ['stage'], columns: [], values: ['candidate_count'], categories: ['generator'], filters: {}, chart: 'line' },
  score_distribution: { rows: ['stage'], columns: [], values: ['metric_value'], categories: ['generator'], filters: {}, chart: 'boxplot' },
  filtering_loss: { rows: ['stage'], columns: ['evidence_status'], values: ['candidate_count'], categories: [], filters: {}, chart: 'heatmap' },
  generator_contribution: { rows: ['origin_set'], columns: [], values: ['candidate_count'], categories: ['generator'], filters: {}, chart: 'bar' },
  safety_profile: { rows: ['generator'], columns: [], values: ['hemolysis', 'toxicity'], categories: ['evidence_status'], filters: {}, chart: 'bar' },
  multi_objective_conflict: { rows: ['candidate'], columns: [], values: ['activity', 'hemolysis'], categories: ['generator'], filters: {}, chart: 'scatter' },
  candidate_laboratory: { rows: ['candidate'], columns: ['metric'], values: ['metric_value'], categories: ['evidence_status'], filters: {}, chart: 'table' },
}

export function createDefaultQuery(cardId: AnalysisQuestion): CardQuerySpec {
  return { cardId, sourceNodeIds: [], ...structuredClone(defaults[cardId]) }
}

export function fieldById(id: string) {
  return fieldCatalog.find((field) => field.id === id)
}

export function moveField(query: CardQuerySpec, fieldId: string, target: PivotSlot): CardQuerySpec {
  const field = fieldById(fieldId)
  if (!field) return query
  const next: CardQuerySpec = {
    ...query,
    rows: query.rows.filter((id) => id !== fieldId),
    columns: query.columns.filter((id) => id !== fieldId),
    values: query.values.filter((id) => id !== fieldId),
    categories: query.categories.filter((id) => id !== fieldId),
  }
  if (target === 'values' && field.kind !== 'measure') return query
  if (target !== 'values' && field.kind !== 'dimension') return query
  next[target] = [...next[target], fieldId]
  return { ...next, chart: recommendChart(next).chart }
}

export function removeField(query: CardQuerySpec, fieldId: string): CardQuerySpec {
  const next = {
    ...query,
    rows: query.rows.filter((id) => id !== fieldId),
    columns: query.columns.filter((id) => id !== fieldId),
    values: query.values.filter((id) => id !== fieldId),
    categories: query.categories.filter((id) => id !== fieldId),
  }
  return { ...next, chart: recommendChart(next).chart }
}

export function validateQuery(query: CardQuerySpec): string[] {
  const errors: string[] = []
  if (!query.values.length) errors.push('至少需要一个数值字段。')
  if (query.rows.length > 2) errors.push('行字段最多保留两个，避免生成不可读的层级。')
  if (query.columns.length > 1) errors.push('列字段最多保留一个。')
  if (query.chart === 'scatter' && query.values.length < 2) errors.push('散点图需要两个数值字段。')
  if (query.chart === 'heatmap' && (!query.rows.length || !query.columns.length)) errors.push('热力图需要行字段和列字段。')
  if (query.chart === 'number' && (query.rows.length || query.columns.length || query.categories.length)) errors.push('指标卡只接受汇总数值，不接受分组字段。')
  if (query.chart === 'number' && query.values.length !== 1) errors.push('指标卡只能显示一个汇总数值。')
  if (query.chart === 'line' && !query.rows.includes('stage')) errors.push('趋势图需要“分析阶段”作为行字段。')
  if (query.chart === 'boxplot' && !query.values.includes('metric_value')) errors.push('箱线图需要“评分数值”字段。')
  if (query.chart === 'bar' && !query.rows.length && !query.categories.length) errors.push('条形图需要至少一个分组字段。')
  return errors
}

export function recommendChart(query: CardQuerySpec): { chart: ChartType; reason: string } {
  if (!query.values.length) return { chart: 'table', reason: '缺少数值字段，先以表格核对原始记录。' }
  if (query.values.length >= 2 && query.rows.includes('candidate')) return { chart: 'scatter', reason: '候选级双数值最适合比较目标冲突。' }
  if (query.rows.includes('stage') && query.categories.length) return { chart: 'line', reason: '阶段具有顺序，按分类绘制趋势最清晰。' }
  if (query.columns.length && query.rows.length) return { chart: 'heatmap', reason: '行列维度形成矩阵，推荐热力图。' }
  if (query.values.includes('metric_value') && query.rows.includes('stage')) return { chart: 'boxplot', reason: '连续评分按阶段比较，推荐箱线图。' }
  if (!query.rows.length && !query.columns.length && !query.categories.length && query.values.length === 1) return { chart: 'number', reason: '单一汇总数值适合指标卡。' }
  if (query.rows.includes('candidate')) return { chart: 'table', reason: '候选身份需要保留完整序列与证据字段。' }
  return { chart: 'bar', reason: '分类与单一数值的比较适合条形图。' }
}

const nodeCardRules: Record<string, AnalysisQuestion[]> = {
  target_data: ['run_quality'],
  knowledge: ['run_quality'],
  amp_designer: ['lineage_and_yield', 'generator_contribution'],
  ampgan: ['lineage_and_yield', 'generator_contribution'],
  hydramp: ['lineage_and_yield', 'generator_contribution'],
  candidate_pool: ['run_quality', 'candidate_laboratory'],
  mic: ['score_distribution'],
  amp_read: ['score_distribution'],
  hemolysis: ['score_distribution', 'safety_profile'],
  toxicity: ['score_distribution', 'safety_profile'],
  developability: ['score_distribution', 'safety_profile'],
  admission: ['multi_objective_conflict', 'candidate_laboratory'],
  targets: ['candidate_laboratory'],
  boltz: ['candidate_laboratory'],
  rosetta: ['candidate_laboratory'],
  portfolio: ['run_quality', 'candidate_laboratory'],
}

export function queriesFromNodes(nodeIds: string[]): CardQuerySpec[] {
  const cardIds = [...new Set(nodeIds.flatMap((id) => nodeCardRules[id] ?? []))]
  return cardIds.map((cardId) => {
    const query = createDefaultQuery(cardId)
    query.sourceNodeIds = nodeIds
    const generators = nodeIds.filter((id) => ['amp_designer', 'ampgan', 'hydramp'].includes(id))
    const metrics = nodeIds.filter((id) => ['mic', 'amp_read', 'hemolysis', 'toxicity', 'developability'].includes(id))
    if (generators.length) query.filters.generator = generators
    if (metrics.length) query.filters.metric = metrics
    if (nodeIds.includes('targets') || nodeIds.includes('boltz') || nodeIds.includes('rosetta')) query.filters.evidence = ['structure']
    return query
  })
}

export const chartLabels: Record<ChartType, string> = {
  number: '指标卡',
  bar: '条形图',
  line: '趋势图',
  boxplot: '箱线图',
  scatter: '散点图',
  heatmap: '热力图',
  table: '明细表',
}
