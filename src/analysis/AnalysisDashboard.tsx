import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import ReactECharts from 'echarts-for-react'
import GridLayout, { WidthProvider, type Layout } from 'react-grid-layout'
import {
  Activity,
  ArrowDownRight,
  Boxes,
  ChartNoAxesCombined,
  CircleGauge,
  DatabaseZap,
  FlaskConical,
  GripVertical,
  LayoutDashboard,
  Library,
  Move,
  Network,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TableProperties,
  X,
  type LucideIcon,
} from 'lucide-react'
import type { RunDetail } from '../types'
import { cardRegistry, defaultDashboardLayout } from './cardRegistry'
import type { AnalysisQuestion, DashboardCardDefinition } from './contracts'
import { frameworkFixture } from './frameworkFixture'
import {
  chartLabels,
  createDefaultQuery,
  fieldById,
  fieldCatalog,
  moveField,
  queriesFromNodes,
  recommendChart,
  removeField,
  validateQuery,
  type CardQuerySpec,
  type ChartType,
  type PivotSlot,
} from './queryComposer'
import './analysis-dashboard.css'

const DashboardGrid = WidthProvider(GridLayout)
const layoutStorageKey = 'ampgent.analysis-dashboard.layout.v2'

const cardIcons: Record<AnalysisQuestion, LucideIcon> = {
  run_quality: CircleGauge,
  lineage_and_yield: Network,
  score_distribution: ChartNoAxesCombined,
  filtering_loss: ArrowDownRight,
  generator_contribution: Boxes,
  safety_profile: ShieldCheck,
  multi_objective_conflict: Activity,
  candidate_laboratory: TableProperties,
}

const chartPalette = ['#4f7df3', '#9b7bd3', '#55bfc3', '#f3a76f', '#87bd55']

const generatorHelp: Record<string, string> = {
  all: '同时查看全部生成来源。',
  'AMP Designer': 'AMP Designer：面向抗菌短肽的序列生成模型。',
  'AMPGAN v2': 'AMPGAN v2：基于生成对抗网络的短肽生成模型。',
  HydrAMP: 'HydrAMP：面向抗菌活性优化的短肽生成模型。',
}

function readLayout(): Layout[] {
  try {
    const value = window.localStorage.getItem(layoutStorageKey)
    return value ? JSON.parse(value) as Layout[] : defaultDashboardLayout
  } catch {
    return defaultDashboardLayout
  }
}

function Chart({ option, height = '100%' }: { option: object; height?: number | string }) {
  const host = useRef<HTMLDivElement>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    if (!host.current) return
    const checkSize = () => {
      const bounds = host.current?.getBoundingClientRect()
      if (bounds && bounds.width > 0 && bounds.height > 0) setReady(true)
    }
    const observer = new ResizeObserver(checkSize)
    observer.observe(host.current)
    const frame = window.requestAnimationFrame(checkSize)
    return () => {
      observer.disconnect()
      window.cancelAnimationFrame(frame)
    }
  }, [])

  return (
    <div ref={host} style={{ height, width: '100%', minWidth: 0 }}>
      {ready && <ReactECharts option={option} notMerge lazyUpdate style={{ height: '100%', width: '100%' }} />}
    </div>
  )
}

const slotLabels: Record<PivotSlot, string> = {
  rows: '行',
  columns: '列',
  values: '数值',
  categories: '分类',
}

const chartChoices: ChartType[] = ['number', 'bar', 'line', 'boxplot', 'scatter', 'heatmap', 'table']

function toggleQueryFilter(query: CardQuerySpec, key: string, value: string) {
  const current = query.filters[key] ?? []
  const next = current.includes(value) ? current.filter((item) => item !== value) : [...current, value]
  return { ...query, filters: { ...query.filters, [key]: next } }
}

function PivotEditor({ query, onChange, onClose }: { query: CardQuerySpec; onChange: (query: CardQuerySpec) => void; onClose: () => void }) {
  const used = new Set([...query.rows, ...query.columns, ...query.values, ...query.categories])
  const recommendation = recommendChart(query)
  const errors = validateQuery(query)
  const handleDrop = (event: DragEvent, slot: PivotSlot) => {
    event.preventDefault()
    const fieldId = event.dataTransfer.getData('text/plain')
    onChange(moveField(query, fieldId, slot))
  }
  return (
    <div className="pivot-editor" onClick={(event) => event.stopPropagation()}>
      <div className="pivot-editor-head">
        <div><strong>卡片分析条件</strong><span>拖动字段改变数据透视结构</span></div>
        <div className="pivot-head-actions"><span className="query-contract">独立查询</span><button onClick={onClose} title="关闭分析条件"><X /></button></div>
      </div>
      <div className="pivot-field-bank">
        <span>可用字段</span>
        <div>{fieldCatalog.filter((field) => !used.has(field.id)).map((field) => (
          <button draggable key={field.id} onDragStart={(event) => event.dataTransfer.setData('text/plain', field.id)} className={field.kind}>
            {field.label}
          </button>
        ))}</div>
      </div>
      <div className="pivot-slots">
        {(Object.keys(slotLabels) as PivotSlot[]).map((slot) => (
          <div className={`pivot-slot slot-${slot}`} key={slot} onDragOver={(event) => event.preventDefault()} onDrop={(event) => handleDrop(event, slot)}>
            <span>{slotLabels[slot]}</span>
            <div>{query[slot].map((fieldId) => <button draggable key={fieldId} onDragStart={(event) => event.dataTransfer.setData('text/plain', fieldId)} onClick={() => onChange(removeField(query, fieldId))}>{fieldById(fieldId)?.label}<i>×</i></button>)}</div>
            {!query[slot].length && <small>拖入字段</small>}
          </div>
        ))}
      </div>
      <div className="query-filter-groups">
        <div><span>生成来源</span>{frameworkFixture.generators.map((item) => <button className={(query.filters.generator ?? []).includes(item.id) ? 'active' : ''} key={item.id} title={generatorHelp[item.label]} onClick={() => onChange(toggleQueryFilter(query, 'generator', item.id))}>{item.label}</button>)}</div>
        <div><span>评分指标</span>{[['mic', '抑菌浓度'], ['hemolysis', '溶血风险'], ['toxicity', '毒性风险'], ['developability', '成药性']].map(([id, label]) => <button className={(query.filters.metric ?? []).includes(id) ? 'active' : ''} key={id} onClick={() => onChange(toggleQueryFilter(query, 'metric', id))}>{label}</button>)}</div>
      </div>
      <div className="chart-recommendation">
        <div><Sparkles /><span><b>推荐 {chartLabels[recommendation.chart]}</b>{recommendation.reason}</span></div>
        <div className="chart-options">{chartChoices.map((chart) => <button className={query.chart === chart ? 'active' : ''} key={chart} onClick={() => onChange({ ...query, chart })}>{chartLabels[chart]}</button>)}</div>
      </div>
      {!!errors.length && <div className="query-errors"><b>当前组合不可执行</b>{errors.map((error) => <span key={error}>{error}</span>)}</div>}
      {!!query.sourceNodeIds.length && <div className="query-source"><b>由概览自动编排</b><span>{query.sourceNodeIds.length} 个流程节点</span></div>}
    </div>
  )
}

function CardShell({ definition, editing, children, meta, query, onQueryChange }: {
  definition: DashboardCardDefinition
  editing: boolean
  children: ReactNode
  meta?: ReactNode
  query: CardQuerySpec
  onQueryChange: (query: CardQuerySpec) => void
}) {
  const Icon = cardIcons[definition.id]
  const [queryOpen, setQueryOpen] = useState(false)
  const errors = validateQuery(query)
  return (
    <article className={`analysis-card card-${definition.id} ${editing ? 'is-editing' : ''} ${queryOpen ? 'query-open' : ''}`}>
      <header className="analysis-card-header">
        <span className="card-icon"><Icon /></span>
        <div>
          <h2>{definition.title}</h2>
          <p>{definition.description}</p>
        </div>
        {meta && <div className="card-meta">{meta}</div>}
        <button className={`card-query-button ${queryOpen ? 'active' : ''} ${errors.length ? 'invalid' : ''}`} onClick={() => setQueryOpen((value) => !value)} title="配置本卡片的数据透视条件">
          <SlidersHorizontal /><span>{chartLabels[query.chart]}</span>
        </button>
        <button className="card-drag-handle" aria-label={`拖动 ${definition.title}`} title="拖动卡片">
          {editing ? <Move /> : <GripVertical />}
        </button>
      </header>
      {queryOpen && createPortal(
        <div className="pivot-editor-layer" onClick={() => setQueryOpen(false)}>
          <PivotEditor query={query} onChange={onQueryChange} onClose={() => setQueryOpen(false)} />
        </div>,
        document.body,
      )}
      <div className="analysis-card-body">
        {errors.length ? (
          <div className="query-blocked"><SlidersHorizontal /><b>分析条件需要调整</b><span>{errors.join(' ')}</span></div>
        ) : children}
      </div>
    </article>
  )
}

function RunQualityCard() {
  const generators = frameworkFixture.generators
  const raw = generators.reduce((sum, item) => sum + item.raw, 0)
  const unique = generators.reduce((sum, item) => sum + item.unique, 0)
  const complete = generators.reduce((sum, item) => sum + item.metricComplete, 0)
  const admitted = generators.reduce((sum, item) => sum + item.admitted, 0)
  const stats = [
    { label: '原始生成', value: raw.toLocaleString(), detail: '三个生成器各 300 条', tone: 'blue' },
    { label: '唯一序列', value: unique.toLocaleString(), detail: `保留 ${((unique / raw) * 100).toFixed(1)}%`, tone: 'violet' },
    { label: '评分覆盖率', value: `${((complete / unique) * 100).toFixed(1)}%`, detail: '缺失 33 条 · 分布外 67 条', tone: 'teal' },
    { label: '候选池', value: '35', detail: '占唯一序列 4.5%', tone: 'orange' },
    { label: '最终入选', value: admitted.toLocaleString(), detail: '进入最终组合', tone: 'green' },
  ]
  return (
    <div className="quality-grid">
      {stats.map((item) => (
        <div className={`quality-stat tone-${item.tone}`} key={item.label}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
          <small>{item.detail}</small>
        </div>
      ))}
      <div className="quality-callout">
        <Sparkles />
        <div><b>数据质量门槛</b><span>先核对覆盖、缺失与分布外数据，再比较生成器。</span></div>
      </div>
    </div>
  )
}

function LineageCard() {
  const stages = ['原始生成', '唯一序列', '完成评分', '安全通过', '候选池']
  const stageKey = ['raw', 'unique', 'metricComplete', 'safetyPass', 'candidatePool'] as const
  return (
    <div className="chart-with-summary">
      <Chart option={{
        animationDuration: 500,
        color: frameworkFixture.generators.map((item) => item.color),
        grid: { left: 42, right: 18, top: 24, bottom: 42 },
        tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${value} 条` },
        legend: { bottom: 0, icon: 'circle', itemWidth: 8, textStyle: { color: '#6f7888', fontSize: 10 } },
        xAxis: { type: 'category', data: stages, axisLine: { lineStyle: { color: '#dfe5ed' } }, axisTick: { show: false }, axisLabel: { color: '#7f8898', fontSize: 9 } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef1f5' } }, axisLabel: { color: '#8b94a3', fontSize: 9 } },
        series: frameworkFixture.generators.map((generator) => ({
          name: generator.label,
          type: 'line',
          smooth: 0.28,
          symbolSize: 7,
          lineStyle: { width: 2 },
          areaStyle: { opacity: 0.035 },
          data: stageKey.map((key) => generator[key]),
        })),
      }} />
      <div className="card-insight"><ArrowDownRight /><span><b>主要损失段</b> 唯一序列 → 安全通过 · −68.2%</span></div>
    </div>
  )
}

function DistributionCard({ generator }: { generator: string }) {
  const rows = frameworkFixture.distributions.filter((item) => generator === 'all' || item.generator === generator)
  const labels = rows.map((item) => `${item.generator}\n${item.stage === 'raw_proposal' ? '原始' : '候选池'}`)
  return (
    <div className="distribution-layout">
      <Chart option={{
        color: chartPalette,
        grid: { left: 44, right: 18, top: 20, bottom: 50 },
        tooltip: { trigger: 'item' },
        xAxis: { type: 'category', data: labels, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe5ed' } }, axisLabel: { color: '#747e90', fontSize: 9, lineHeight: 14 } },
        yAxis: { type: 'value', min: 0, max: 1, name: '抗菌概率', nameTextStyle: { color: '#9aa2af', fontSize: 9 }, splitLine: { lineStyle: { color: '#eef1f5' } }, axisLabel: { color: '#8b94a3', fontSize: 9 } },
        series: [{
          name: '五数概括',
          type: 'boxplot',
          data: rows.map((item, index) => ({
            value: item.fiveNumberSummary,
            itemStyle: { color: `${chartPalette[Math.floor(index / 2)]}24`, borderColor: chartPalette[Math.floor(index / 2)], borderWidth: 1.5 },
          })),
          boxWidth: [12, 30],
        }],
      }} />
      <aside className="distribution-summary">
        <span className="summary-eyebrow">当前比较</span>
        <strong>原始生成 → 候选池</strong>
        <div><b>+0.19</b><span>中位数变化</span></div>
        <div><b>35 条</b><span>候选池覆盖</span></div>
        <div><b>4 条</b><span>分布外数据</span></div>
        <small>待补充：累积分布、效应量与自助法置信区间</small>
      </aside>
    </div>
  )
}

function OriginCard() {
  const patterns = frameworkFixture.sourcePatterns
  return <Chart option={{
    color: chartPalette,
    grid: { left: 118, right: 20, top: 8, bottom: 18 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', max: 12, splitLine: { lineStyle: { color: '#eff2f6' } }, axisLabel: { color: '#8b94a3', fontSize: 9 } },
    yAxis: { type: 'category', inverse: true, data: patterns.map((item) => item.label), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: '#626d7e', fontSize: 9 } },
    series: [{ type: 'bar', barWidth: 10, data: patterns.map((item, index) => ({ value: item.count, itemStyle: { color: chartPalette[index % chartPalette.length], borderRadius: [0, 4, 4, 0] } })), label: { show: true, position: 'right', color: '#4e5969', fontSize: 9 } }],
  }} />
}

function SafetyCard({ generator }: { generator: string }) {
  const labels = generator === 'all' ? ['AMP Designer', 'AMPGAN', 'HydrAMP'] : [generator]
  const index = generator === 'AMP Designer' ? 0 : generator === 'AMPGAN v2' ? 1 : 2
  const values = generator === 'all'
    ? { hemolysis: [31, 38, 22], toxicity: [12, 17, 9], ood: [6, 7, 10] }
    : { hemolysis: [[31, 38, 22][index]], toxicity: [[12, 17, 9][index]], ood: [[6, 7, 10][index]] }
  return <Chart option={{
    color: ['#ef8c7c', '#f1bc66', '#8d9aac'],
    grid: { left: 38, right: 12, top: 30, bottom: 28 },
    legend: { top: 0, icon: 'circle', itemWidth: 7, textStyle: { fontSize: 9, color: '#737d8d' } },
    tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${value}%` },
    xAxis: { type: 'category', data: labels, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe5ed' } }, axisLabel: { fontSize: 9, color: '#737d8d' } },
    yAxis: { type: 'value', max: 50, axisLabel: { formatter: '{value}%', fontSize: 9, color: '#8b94a3' }, splitLine: { lineStyle: { color: '#eef1f5' } } },
    series: [
      { name: '溶血风险', type: 'bar', barWidth: 11, data: values.hemolysis, itemStyle: { borderRadius: [4, 4, 0, 0] } },
      { name: '毒性风险', type: 'bar', barWidth: 11, data: values.toxicity, itemStyle: { borderRadius: [4, 4, 0, 0] } },
      { name: '分布外', type: 'bar', barWidth: 11, data: values.ood, itemStyle: { borderRadius: [4, 4, 0, 0] } },
    ],
  }} />
}

function ParetoCard({ generator }: { generator: string }) {
  const points = frameworkFixture.pareto.filter((item) => generator === 'all' || item.generator === generator)
  const groups = frameworkFixture.generators.map((item) => ({
    ...item,
    points: points.filter((point) => point.generator === item.label),
  })).filter((item) => item.points.length)
  return (
    <div className="pareto-layout">
      <Chart option={{
        color: groups.map((item) => item.color),
        grid: { left: 48, right: 18, top: 28, bottom: 38 },
        tooltip: { formatter: (params: { data: { value: number[]; sequence: string; rank: number } }) => `${params.data.sequence}<br/>抗菌活性 ${params.data.value[0]} · 溶血风险 ${params.data.value[1]}<br/>前沿层级 ${params.data.rank}` },
        legend: { top: 0, icon: 'circle', itemWidth: 7, textStyle: { fontSize: 9, color: '#737d8d' } },
        xAxis: { type: 'value', min: 0.55, max: 1, name: '抗菌活性 ↑', nameLocation: 'middle', nameGap: 25, nameTextStyle: { fontSize: 9, color: '#7b8494' }, axisLabel: { fontSize: 9, color: '#8b94a3' }, splitLine: { lineStyle: { color: '#eef1f5' } } },
        yAxis: { type: 'value', min: 0, max: 0.5, inverse: true, name: '溶血风险 ↓', nameTextStyle: { fontSize: 9, color: '#7b8494' }, axisLabel: { fontSize: 9, color: '#8b94a3' }, splitLine: { lineStyle: { color: '#eef1f5' } } },
        series: groups.map((group) => ({
          name: group.label,
          type: 'scatter',
          symbolSize: (value: number[], params: { data: { rank: number } }) => params.data.rank === 1 ? 13 : 8,
          data: group.points.map((point) => ({ value: [point.activity, point.hemolysis, point.charge], sequence: point.sequence, rank: point.paretoRank, itemStyle: { opacity: point.paretoRank === 1 ? 1 : 0.46, borderColor: '#fff', borderWidth: 1.5 } })),
          markArea: group === groups[0] ? { silent: true, itemStyle: { color: 'rgba(91,191,157,.07)' }, data: [[{ xAxis: 0.75, yAxis: 0 }, { xAxis: 1, yAxis: 0.2 }]] } : undefined,
        })),
      }} />
      <aside className="conflict-note">
        <span>主要约束</span>
        <strong>溶血风险 · 42%</strong>
        <p>前沿高活性区间同时抬升溶血风险。</p>
        <small>需由统计冲突、选择冲突与前沿冲突三层证据确认。</small>
      </aside>
    </div>
  )
}

function CandidateTable({ generator }: { generator: string }) {
  const rows = frameworkFixture.candidates.filter((item) => generator === 'all' || item.originSet.includes(generator))
  return (
    <div className="candidate-table-wrap">
      <table className="candidate-table">
        <thead><tr><th>候选序列</th><th>生成来源</th><th>抗菌活性 ↑</th><th>溶血风险 ↓</th><th>毒性风险 ↓</th><th>净电荷</th><th>前沿层级</th><th>证据状态</th></tr></thead>
        <tbody>{rows.map((item) => (
          <tr key={item.id}>
            <td><b>{item.id}</b><code>{item.sequence}</code></td>
            <td><div className="origin-pills">{item.originSet.map((origin) => <span key={origin}>{origin.replace('AMP Designer', 'Designer')}</span>)}</div></td>
            <td><strong>{item.activity.toFixed(2)}</strong></td>
            <td>{item.hemolysis.toFixed(2)}</td>
            <td>{item.toxicity.toFixed(2)}</td>
            <td>+{item.charge.toFixed(1)}</td>
            <td><span className={`pareto-rank rank-${item.paretoRank}`}>P{item.paretoRank}</span></td>
            <td>{item.flags.length ? item.flags.map((flag) => <span className="evidence-flag" key={flag}>{flag === 'shared-origin' ? '多来源' : flag === 'safety-boundary' ? '安全边界' : flag}</span>) : <span className="evidence-ok">证据完整</span>}</td>
          </tr>
        ))}</tbody>
      </table>
      <footer className="table-footer"><span>显示 35 条中的 {rows.length} 条</span><span>保留序列身份 · 多来源不强制归属</span></footer>
    </div>
  )
}

const generatorIdsToLabels: Record<string, string> = {
  amp_designer: 'AMP Designer',
  ampgan: 'AMPGAN v2',
  hydramp: 'HydrAMP',
}

function generatorFromQuery(query: CardQuerySpec) {
  const selected = query.filters.generator ?? []
  return selected.length === 1 ? generatorIdsToLabels[selected[0]] ?? 'all' : 'all'
}

function CardContent({ id, query }: { id: AnalysisQuestion; query: CardQuerySpec }) {
  const generator = generatorFromQuery(query)
  if (id === 'run_quality') return <RunQualityCard />
  if (id === 'lineage_and_yield') return <LineageCard />
  if (id === 'score_distribution') return <DistributionCard generator={generator} />
  if (id === 'generator_contribution') return <OriginCard />
  if (id === 'safety_profile') return <SafetyCard generator={generator} />
  if (id === 'multi_objective_conflict') return <ParetoCard generator={generator} />
  if (id === 'candidate_laboratory') return <CandidateTable generator={generator} />
  return <div className="card-placeholder">扩展接口已就绪</div>
}

export function AnalysisDashboard({ detail, seedNodeIds = [] }: { detail?: RunDetail | null; seedNodeIds?: string[] }) {
  const [editing, setEditing] = useState(false)
  const [layout, setLayout] = useState<Layout[]>(readLayout)
  const [hiddenCards, setHiddenCards] = useState<Set<AnalysisQuestion>>(new Set())
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [queries, setQueries] = useState<Record<AnalysisQuestion, CardQuerySpec>>(() => Object.fromEntries(cardRegistry.map((card) => [card.id, createDefaultQuery(card.id)])) as Record<AnalysisQuestion, CardQuerySpec>)
  const seedKey = seedNodeIds.join('|')

  useEffect(() => {
    window.localStorage.setItem(layoutStorageKey, JSON.stringify(layout))
  }, [layout])

  useEffect(() => {
    if (!seedNodeIds.length) return
    const seeded = queriesFromNodes(seedNodeIds)
    if (!seeded.length) return
    setQueries((current) => ({ ...current, ...Object.fromEntries(seeded.map((query) => [query.cardId, query])) }))
    const seededIds = new Set(seeded.map((query) => query.cardId))
    setHiddenCards(new Set(cardRegistry.filter((card) => !seededIds.has(card.id)).map((card) => card.id)))
  }, [seedKey])

  const visibleCards = useMemo(() => cardRegistry.filter((card) => !hiddenCards.has(card.id)), [hiddenCards])
  const runLabel = detail
    ? `框架示例 · 当前轮次含 ${detail.counts.candidates.toLocaleString()} 条候选，分析数值尚未接入该轮次`
    : frameworkFixture.runLabel

  const toggleCard = (id: AnalysisQuestion) => {
    setHiddenCards((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const resetLayout = () => {
    setLayout(defaultDashboardLayout)
    setHiddenCards(new Set())
  }

  return (
    <section className="analysis-page">
      <header className="analysis-page-header">
        <div className="analysis-heading">
          <div className="analysis-eyebrow"><DatabaseZap /> 确定性科学分析 <span>无智能体</span></div>
          <h1>短肽分析</h1>
          <p>{runLabel}</p>
        </div>
        <div className="analysis-header-actions">
          <span className="fixture-badge" title="当前数值仅用于验证页面结构，不代表所选运行。"><FlaskConical /> 框架示例数据</span>
          <button className={editing ? 'active' : ''} onClick={() => setEditing((value) => !value)}><LayoutDashboard />{editing ? '完成布局' : '编辑布局'}</button>
          <div className="card-library-wrap">
            <button onClick={() => setLibraryOpen((value) => !value)}><Library />卡片库</button>
            {libraryOpen && (
              <div className="card-library-popover">
                <div><strong>分析卡片</strong><span>按科学问题组织</span></div>
                {cardRegistry.map((card) => {
                  const Icon = cardIcons[card.id]
                  return <label key={card.id}><input type="checkbox" checked={!hiddenCards.has(card.id)} onChange={() => toggleCard(card.id)} /><span><Icon /></span><b>{card.title}</b><small>{card.description}</small></label>
                })}
              </div>
            )}
          </div>
          <button className="icon-only" onClick={resetLayout} title="重置布局"><RotateCcw /></button>
        </div>
      </header>

      <div className="analysis-orchestration">
        <div className="orchestration-label"><SlidersHorizontal /><span><b>卡片独立分析</b><small>每张卡片拥有自己的字段、筛选和图表</small></span></div>
        <div className="orchestration-flow">
          <span className="flow-step active">概览多选</span><i />
          <span className="flow-step">自动编排字段</span><i />
          <span className="flow-step">拖动微调</span><i />
          <span className="flow-step">图表推荐</span>
        </div>
        <div className={`orchestration-result ${seedNodeIds.length ? 'has-selection' : ''}`}>
          <Sparkles />
          <span>{seedNodeIds.length ? `已根据 ${seedNodeIds.length} 个流程节点生成 ${queriesFromNodes(seedNodeIds).length} 张分析卡片` : '在概览中选择多个流程节点，即可自动生成分析卡片'}</span>
        </div>
      </div>

      <div className={`layout-note ${editing ? 'visible' : ''}`}><Move /> 拖动卡片标题调整位置，拖动右下角调整尺寸；布局自动保存在本机。</div>

      <div className="analysis-grid-shell">
        <DashboardGrid
          className="analysis-grid"
          layout={layout.filter((item) => !hiddenCards.has(item.i as AnalysisQuestion))}
          cols={12}
          rowHeight={62}
          margin={[14, 14]}
          containerPadding={[0, 0]}
          isDraggable={editing}
          isResizable={editing}
          draggableHandle=".card-drag-handle"
          onLayoutChange={(visibleLayout) => setLayout((current) => [
            ...visibleLayout,
            ...current.filter((item) => hiddenCards.has(item.i as AnalysisQuestion)),
          ])}
        >
          {visibleCards.map((definition) => (
            <div key={definition.id}>
              <CardShell
                definition={definition}
                editing={editing}
                query={queries[definition.id]}
                onQueryChange={(query) => setQueries((current) => ({ ...current, [definition.id]: query }))}
                meta={<b>{queries[definition.id].sourceNodeIds.length ? `${queries[definition.id].sourceNodeIds.length} 个节点` : '独立条件'}</b>}
              >
                <CardContent id={definition.id} query={queries[definition.id]} />
              </CardShell>
            </div>
          ))}
        </DashboardGrid>
      </div>

      <footer className="analysis-provenance-bar">
        <div><DatabaseZap /><span><b>来源</b> 框架示例</span><span><b>快照</b> 页面结构预览</span><span><b>查询契约</b> 第一版</span></div>
        <p>{frameworkFixture.provenance.warnings[0]}</p>
      </footer>
    </section>
  )
}
