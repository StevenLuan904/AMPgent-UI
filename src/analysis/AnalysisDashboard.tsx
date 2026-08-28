import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import ReactECharts from 'echarts-for-react'
import GridLayout, { WidthProvider, type Layout } from 'react-grid-layout'
import {
  Activity,
  Atom,
  ArrowDownRight,
  Boxes,
  ChartNoAxesCombined,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  CircleGauge,
  DatabaseZap,
  FlaskConical,
  GripVertical,
  Library,
  Microscope,
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
import { planCardPresentation } from './cardPresentationRules'
import type { AnalysisQuestion, DashboardCardDefinition } from './contracts'
import type { AnalysisCardQueryState, PivotChartType } from './analysisDataContracts'
import { loadAnalysisSnapshot, type AnalysisSnapshot } from './dataKernel'
import { executeAnalysisQuery, type AnalysisPivotResult, type PivotDimensionKey } from './dataKernel'
import { frameworkFixture } from './frameworkFixture'
import { CandidateCaseWorkbench } from './CandidateCaseWorkbench'
import { ParetoFront3D, RosettaEnergyViolin, type EnergyGroup, type ParetoPoint3D } from './ScientificDashboardCharts'
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
const layoutStorageKey = 'ampgent.analysis-dashboard.layout.v4'
const queryStorageKey = 'ampgent.analysis-dashboard.queries.v2'
const hiddenStorageKey = 'ampgent.analysis-dashboard.hidden.v1'

const cardIcons: Record<AnalysisQuestion, LucideIcon> = {
  run_quality: CircleGauge,
  lineage_and_yield: Network,
  score_distribution: ChartNoAxesCombined,
  filtering_loss: ArrowDownRight,
  generator_contribution: Boxes,
  safety_profile: ShieldCheck,
  multi_objective_conflict: Activity,
  structure_energy: Atom,
  candidate_laboratory: TableProperties,
}

const chartPalette = ['#4f7df3', '#9b7bd3', '#55bfc3', '#f3a76f', '#87bd55']

const generatorDisplay: Record<string, string> = {
  amp_designer: 'AMP Designer',
  ampgan: 'AMPGAN v2',
  ampgan_v2: 'AMPGAN v2',
  hydramp: 'HydrAMP',
}

const metricQueryKeys: Record<string, string> = {
  mic: 'llamp_log10_mic_um',
  hemolysis: 'macrel_hemolysis_probability',
  toxicity: 'toxinpred3_hybrid_score',
  developability: 'hydrophobic_moment_eisenberg',
}

const dimensionQueryKeys: Record<string, PivotDimensionKey> = {
  stage: 'stage',
  generator: 'generator',
  origin_set: 'origin_set',
  metric: 'metric',
  cohort: 'admission_status',
  evidence_status: 'admission_status',
}

function selectedMetric(query: CardQuerySpec) {
  return metricQueryKeys[(query.filters.metric ?? [])[0]] ?? 'macrel_amp_probability'
}

function selectedGenerators(query: CardQuerySpec) {
  return (query.filters.generator ?? []).map((id) => id === 'ampgan' ? 'ampgan_v2' : id)
}

function queryDimensions(query: CardQuerySpec, fallback: PivotDimensionKey[]) {
  const mapped = [...query.rows, ...query.columns, ...query.categories].map((id) => dimensionQueryKeys[id]).filter((id): id is PivotDimensionKey => Boolean(id))
  return mapped.length ? [...new Set(mapped)] : fallback
}

function runKernel(snapshot: AnalysisSnapshot, input: Parameters<typeof executeAnalysisQuery>[1]): AnalysisPivotResult {
  return executeAnalysisQuery(snapshot, input)
}

function metricValue(candidate: AnalysisSnapshot['candidates'][number], key: string) {
  return candidate.metrics[key]?.value ?? null
}

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

function readQueries(): Record<AnalysisQuestion, CardQuerySpec> {
  const defaults = Object.fromEntries(cardRegistry.map((card) => [card.id, createDefaultQuery(card.id)])) as Record<AnalysisQuestion, CardQuerySpec>
  try {
    const saved = window.localStorage.getItem(queryStorageKey)
    if (!saved) return defaults
    const merged = { ...defaults, ...JSON.parse(saved) as Partial<Record<AnalysisQuestion, CardQuerySpec>> }
    const sourceQuery = merged.generator_contribution
    if (sourceQuery.rows.includes('origin_set') && sourceQuery.categories.includes('generator')) {
      merged.generator_contribution = { ...sourceQuery, chart: 'sunburst' }
    }
    return merged
  } catch {
    return defaults
  }
}

function readHiddenCards() {
  try {
    return new Set<AnalysisQuestion>(JSON.parse(window.localStorage.getItem(hiddenStorageKey) ?? '[]'))
  } catch {
    return new Set<AnalysisQuestion>()
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

  const source = option as Record<string, unknown>
  const configuredOption = {
    ...source,
    animation: false,
    textStyle: { fontFamily: 'Inter, "Noto Sans SC", system-ui, sans-serif', fontSize: 11, ...(source.textStyle as object ?? {}) },
    tooltip: source.tooltip ? {
      renderMode: 'html',
      appendToBody: true,
      confine: false,
      className: 'ampgent-chart-tooltip',
      borderWidth: 1,
      borderColor: '#ccd6e5',
      backgroundColor: 'rgba(255,255,255,.98)',
      textStyle: { color: '#344054', fontSize: 12, lineHeight: 19 },
      extraCssText: 'box-shadow:0 12px 32px rgba(31,48,78,.16);border-radius:8px;max-width:360px;white-space:normal;',
      ...(source.tooltip as object),
    } : undefined,
  }
  return (
    <div ref={host} className="analysis-chart-host" style={{ height, width: '100%', minWidth: 0 }}>
      {ready && <ReactECharts option={configuredOption} notMerge lazyUpdate style={{ height: '100%', width: '100%' }} />}
    </div>
  )
}

const slotLabels: Record<PivotSlot, string> = {
  rows: '行',
  columns: '列',
  values: '数值',
  categories: '分类',
}

const chartChoices: ChartType[] = ['number', 'bar', 'line', 'boxplot', 'violin', 'scatter', 'heatmap', 'table']

function toggleQueryFilter(query: CardQuerySpec, key: string, value: string) {
  const current = query.filters[key] ?? []
  const next = current.includes(value) ? current.filter((item) => item !== value) : [...current, value]
  return { ...query, filters: { ...query.filters, [key]: next } }
}

function PivotEditor({ query, onChange, onClose }: { query: CardQuerySpec; onChange: (query: CardQuerySpec) => void; onClose: () => void }) {
  const [dropError, setDropError] = useState<string | null>(null)
  const used = new Set([...query.rows, ...query.columns, ...query.values, ...query.categories])
  const recommendation = recommendChart(query)
  const errors = validateQuery(query)
  const handleDrop = (event: DragEvent, slot: PivotSlot) => {
    event.preventDefault()
    const fieldId = event.dataTransfer.getData('text/plain')
    const field = fieldById(fieldId)
    if (!field) {
      setDropError('无法识别该字段。')
      return
    }
    if (slot === 'values' && field.kind !== 'measure') {
      setDropError(`“${field.label}”是分组字段，不能放入数值。`)
      return
    }
    if (slot !== 'values' && field.kind !== 'dimension') {
      setDropError(`“${field.label}”是数值字段，只能放入数值。`)
      return
    }
    setDropError(null)
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
      {dropError && <div className="query-errors"><b>字段放置被拒绝</b><span>{dropError}</span></div>}
      {!!query.sourceNodeIds.length && <div className="query-source"><b>由概览自动编排</b><span>{query.sourceNodeIds.length} 个流程节点</span></div>}
    </div>
  )
}

function CardShell({ definition, children, meta, query, onQueryChange }: {
  definition: DashboardCardDefinition
  children: ReactNode
  meta?: ReactNode
  query: CardQuerySpec
  onQueryChange: (query: CardQuerySpec) => void
}) {
  const Icon = cardIcons[definition.id]
  const [queryOpen, setQueryOpen] = useState(false)
  const cardRef = useRef<HTMLElement>(null)
  const [presentation, setPresentation] = useState<{ mode: 'compact' | 'standard' | 'expanded'; effectiveChart: PivotChartType }>({ mode: 'standard', effectiveChart: 'bar' })
  const errors = validateQuery(query)
  useEffect(() => {
    const element = cardRef.current
    if (!element) return
    const chartMap: Record<ChartType, PivotChartType> = { number: 'kpi', bar: 'bar', line: 'funnel', boxplot: 'boxplot', violin: 'violin', scatter: 'scatter', heatmap: 'heatmap', sunburst: 'stacked_bar', table: 'table' }
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (width < 180 || height < 120) return
      const plan = planCardPresentation({ chart: chartMap[query.chart] } as AnalysisCardQueryState, { width, height })
      setPresentation((current) => current.mode === plan.mode && current.effectiveChart === plan.effectiveChart
        ? current
        : { mode: plan.mode, effectiveChart: plan.effectiveChart })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [query.chart])
  return (
    <article ref={cardRef} data-presentation={presentation.mode} data-effective-chart={presentation.effectiveChart} className={`analysis-card card-${definition.id} presentation-${presentation.mode} ${queryOpen ? 'query-open' : ''}`}>
      <header className="analysis-card-header" title="拖动标题移动卡片；拖动右下角调整大小">
        <span className="card-icon"><Icon /></span>
        <div>
          <h2 title={definition.id === 'structure_energy' ? 'Rosetta：对蛋白质—短肽界面进行构象精修与能量评估。' : undefined}>{definition.title}</h2>
          <p>{definition.description}</p>
        </div>
        {meta && <div className="card-meta">{meta}</div>}
        <button className={`card-query-button ${queryOpen ? 'active' : ''} ${errors.length ? 'invalid' : ''}`} onClick={() => setQueryOpen((value) => !value)} title="配置本卡片的数据透视条件">
          <SlidersHorizontal /><span>{chartLabels[query.chart]}</span>
        </button>
        <span className="card-drag-handle" aria-hidden="true"><GripVertical /></span>
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

function RunQualityCard({ snapshot }: { snapshot: AnalysisSnapshot | null }) {
  if (snapshot) {
    const summary = snapshot.summary
    const coverage = summary.expectedEvaluations ? (summary.observedEvaluations / summary.expectedEvaluations) * 100 : 0
    const stats = [
      { label: '原始提案', value: summary.rawOccurrences.toLocaleString(), detail: `${snapshot.generatorCells.length} 个生成批次`, tone: 'blue' },
      { label: '唯一候选', value: summary.uniqueCandidates.toLocaleString(), detail: `剔除 ${summary.invalidOccurrences} 条无效记录`, tone: 'violet' },
      { label: '评分证据', value: `${coverage.toFixed(1)}%`, detail: `${summary.observedEvaluations.toLocaleString()} / ${summary.expectedEvaluations.toLocaleString()} 项`, tone: 'teal' },
      { label: '结构资格', value: summary.structureEligible.toLocaleString(), detail: '候选决策输出', tone: 'orange' },
      { label: '成熟核心', value: (summary.admissionCounts.mature_core ?? 0).toLocaleString(), detail: '最高优先级队列', tone: 'green' },
    ]
    return (
      <div className="quality-grid">
        {stats.map((item) => <div className={`quality-stat tone-${item.tone}`} key={item.label}><span>{item.label}</span><strong>{item.value}</strong><small>{item.detail}</small></div>)}
        <div className="quality-callout"><Sparkles /><div><b>轮次已取消</b><span>序列、评分与候选决策记录完整；结构阶段待续。</span></div></div>
      </div>
    )
  }
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

function LineageCard({ snapshot, chart, query }: { snapshot: AnalysisSnapshot | null; chart: ChartType; query: CardQuerySpec }) {
  const stages = ['原始生成', '唯一序列', '完成评分', '候选池', '安全通过', '结构资格']
  const stageKey = ['raw', 'unique', 'metricComplete', 'candidatePool', 'safetyPass', 'admitted'] as const
  const kernelResult = snapshot ? runKernel(snapshot, {
    schemaVersion: 'analysis-pivot-query.1',
    queryKey: 'generator_funnel',
    dimensions: [...new Set([...queryDimensions(query, ['generator', 'stage']).filter((dimension) => ['generator', 'origin_set', 'stage', 'cohort'].includes(dimension)), 'stage' as PivotDimensionKey])],
    filters: { generators: selectedGenerators(query) },
  }) : null
  const generators = snapshot && kernelResult
    ? [...new Set(kernelResult.rows.map((item) => String(item.generator ?? item.origin_set ?? '全部候选')))].map((id, index) => {
      const value = (stage: string) => Number(kernelResult.rows.find((item) => String(item.generator ?? item.origin_set ?? '全部候选') === id && item.stage === stage)?.unique_sequence_count ?? 0)
      return {
        id,
        label: generatorDisplay[id] ?? id,
        color: chartPalette[index],
        raw: value('raw_proposal'),
        unique: value('deduplicated'),
        metricComplete: value('metric_complete'),
        safetyPass: value('safety_pass'),
        candidatePool: value('candidate_pool'),
        admitted: value('admitted'),
      }
    })
    : frameworkFixture.generators.map((item) => ({ ...item, admitted: item.candidatePool }))
  const totals = stageKey.map((key) => generators.reduce((sum, item) => sum + Number(item[key]), 0))
  const losses = totals.slice(1).map((value, index) => ({ index, rate: totals[index] ? (totals[index] - value) / totals[index] : 0 }))
  const primaryLoss = losses.sort((left, right) => right.rate - left.rate)[0]
  return (
    <div className="chart-with-summary">
      <Chart option={{
        animationDuration: 500,
        color: generators.map((item) => item.color),
        grid: { left: 42, right: 18, top: 24, bottom: 42 },
        tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${value} 条` },
        legend: { bottom: 0, icon: 'circle', itemWidth: 8, textStyle: { color: '#596579', fontSize: 11 } },
        xAxis: { type: 'category', data: stages, axisLine: { lineStyle: { color: '#dfe5ed' } }, axisTick: { show: false }, axisLabel: { color: '#687386', fontSize: 10 } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef1f5' } }, axisLabel: { color: '#748094', fontSize: 10 } },
        series: generators.map((generator) => ({
          name: generator.label,
          type: chart === 'bar' ? 'bar' : 'line',
          smooth: 0.28,
          symbolSize: 7,
          lineStyle: { width: 2 },
          areaStyle: { opacity: 0.035 },
          data: stageKey.map((key) => generator[key]),
        })),
      }} />
      <div className="card-insight"><ArrowDownRight /><span><b>主要损失段</b> {primaryLoss ? `${stages[primaryLoss.index]} → ${stages[primaryLoss.index + 1]} · −${(primaryLoss.rate * 100).toFixed(1)}%` : '暂无可计算阶段'}</span></div>
    </div>
  )
}

function DistributionCard({ generator, snapshot, chart, query }: { generator: string; snapshot: AnalysisSnapshot | null; chart: ChartType; query: CardQuerySpec }) {
  const metric = selectedMetric(query)
  const kernelResult = snapshot ? runKernel(snapshot, {
    schemaVersion: 'analysis-pivot-query.1',
    queryKey: 'metric_distribution_by_generator',
    dimensions: queryDimensions(query, ['generator', 'metric']).filter((dimension) => ['generator', 'origin_set', 'stage', 'metric', 'cohort', 'admission_status', 'ood_status'].includes(dimension)),
    metrics: [metric],
    filters: { generators: selectedGenerators(query) },
    chart: chart === 'bar' ? 'bar' : chart === 'heatmap' ? 'heatmap' : 'boxplot',
  }) : null
  const rows = snapshot && kernelResult
    ? (kernelResult.distributions ?? []).map((item) => ({
      generator: generatorDisplay[item.key.generator] ?? item.key.generator ?? item.key.origin_set ?? '全部候选',
      stage: item.key.stage ?? 'candidate_pool',
      group: item.key.admission_status ?? '全部候选',
      fiveNumberSummary: [item.summary.min ?? 0, item.summary.q1 ?? 0, item.summary.median ?? 0, item.summary.q3 ?? 0, item.summary.max ?? 0],
      count: item.summary.count,
    }))
    : frameworkFixture.distributions.filter((item) => generator === 'all' || item.generator === generator).map((item) => ({ ...item, group: '全部候选' }))
  const stageLabels: Record<string, string> = { raw_proposal: '原始', deduplicated: '去重', metric_complete: '评分完整', safety_pass: '安全通过', candidate_pool: '候选池', admitted: '结构资格' }
  const labels = rows.map((item) => `${item.generator}\n${stageLabels[item.stage] ?? '候选池'} · n=${item.count.toLocaleString()}`)
  const metricDescriptor = metric === 'llamp_log10_mic_um'
    ? { label: '对数抑菌浓度 ↓', short: '抑菌浓度' }
    : metric === 'macrel_hemolysis_probability'
      ? { label: '溶血概率 ↓', short: '溶血概率' }
      : metric === 'toxinpred3_hybrid_score'
        ? { label: '毒性评分 ↓', short: '毒性评分' }
        : metric === 'hydrophobic_moment_eisenberg'
          ? { label: '疏水矩', short: '疏水矩' }
          : { label: '抗菌概率 ↑', short: '抗菌概率' }
  const observedMin = rows.length ? Math.min(...rows.map((item) => item.fiveNumberSummary[0])) : 0
  const observedMax = rows.length ? Math.max(...rows.map((item) => item.fiveNumberSummary[4])) : 1
  const isProbability = ['macrel_amp_probability', 'macrel_hemolysis_probability'].includes(metric)
  const axisPadding = Math.max((observedMax - observedMin) * .08, .02)
  const axisMin = isProbability ? 0 : Math.floor((observedMin - axisPadding) * 10) / 10
  const axisMax = isProbability ? 1 : Math.ceil((observedMax + axisPadding) * 10) / 10
  const heatmapColumns = [...new Set(rows.map((item) => item.generator))]
  const heatmapGroups = [...new Set(rows.map((item) => item.group))]
  const admissionLabels: Record<string, string> = { mature_core: '成熟核心', promising_uncertain: '潜力待确认', rejected: '未入选', all: '全部候选' }
  const chartOption = chart === 'heatmap' ? {
    grid: { left: 68, right: 24, top: 18, bottom: 42 },
    tooltip: { formatter: (params: { data: number[] }) => `${heatmapColumns[params.data[0]]}<br/>${admissionLabels[heatmapGroups[params.data[1]]] ?? heatmapGroups[params.data[1]]}<br/>中位数 ${params.data[2].toFixed(3)}` },
    xAxis: { type: 'category', data: heatmapColumns, axisTick: { show: false }, axisLabel: { color: '#657186', fontSize: 10 } },
    yAxis: { type: 'category', data: heatmapGroups.map((item) => admissionLabels[item] ?? item), axisTick: { show: false }, axisLabel: { color: '#657186', fontSize: 10 } },
    visualMap: { min: axisMin, max: axisMax, calculable: false, orient: 'horizontal', left: 'center', bottom: 0, itemWidth: 8, itemHeight: 70, textStyle: { fontSize: 7, color: '#8590a1' }, inRange: { color: ['#eef4ff', '#91b2ef', '#3f6fce'] } },
    series: [{ type: 'heatmap', data: rows.map((item) => [heatmapColumns.indexOf(item.generator), heatmapGroups.indexOf(item.group), item.fiveNumberSummary[2]]), label: { show: true, formatter: (params: { data: number[] }) => params.data[2].toFixed(2), fontSize: 10, color: '#35435a' } }],
  } : {
    color: chartPalette,
    grid: { left: 54, right: 22, top: 30, bottom: 54 },
    tooltip: {
      trigger: 'item',
      formatter: (params: { name: string; value: number | number[] }) => {
        if (!Array.isArray(params.value)) {
          return `<b>${params.name.replace('\n', ' · ')}</b><br/>中位数 ${Number(params.value).toFixed(3)}<br/>参照线为全体候选中位数`
        }
        const values = params.value
        return `<b>${params.name.replace('\n', ' · ')}</b><br/>最小值 ${Number(values[0]).toFixed(3)}<br/>下四分位 ${Number(values[1] ?? values[0]).toFixed(3)}<br/>中位数 ${Number(values[2] ?? values[0]).toFixed(3)}<br/>上四分位 ${Number(values[3] ?? values[0]).toFixed(3)}<br/>最大值 ${Number(values[4] ?? values[0]).toFixed(3)}`
      },
    },
    xAxis: { type: 'category', data: labels, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe5ed' } }, axisLabel: { color: '#657186', fontSize: 10, lineHeight: 15 } },
    yAxis: { type: 'value', min: axisMin, max: axisMax, name: metricDescriptor.label, nameTextStyle: { color: '#778397', fontSize: 10 }, splitLine: { lineStyle: { color: '#eef1f5' } }, axisLabel: { color: '#748094', fontSize: 10 } },
    series: chart === 'bar'
      ? [{ name: '中位数', type: 'bar', barWidth: 28, data: rows.map((item, index) => ({ value: item.fiveNumberSummary[2], itemStyle: { color: chartPalette[index % chartPalette.length], borderRadius: [4, 4, 0, 0] } })) }]
      : [{ name: '五数概括', type: 'boxplot', data: rows.map((item, index) => ({ value: item.fiveNumberSummary, itemStyle: { color: `${chartPalette[index % chartPalette.length]}24`, borderColor: chartPalette[index % chartPalette.length], borderWidth: 1.5 } })), boxWidth: [14, 34] }],
  }
  return (
    <div className="distribution-layout">
      <Chart option={chartOption} />
    </div>
  )
}

function OriginCard({ snapshot }: { snapshot: AnalysisSnapshot | null }) {
  if (!snapshot) return null
  const statusLabels: Record<string, string> = { mature_core: '成熟核心', promising_uncertain: '潜力待核', rejected: '未入选' }
  const roots = new Map<string, Map<string, { eligible: number; ineligible: number }>>()
  for (const candidate of snapshot.candidates) {
    const origin = candidate.originSet.slice().sort().map((id) => generatorDisplay[id] ?? id).join(' + ')
    const byStatus = roots.get(origin) ?? new Map<string, { eligible: number; ineligible: number }>()
    const counts = byStatus.get(candidate.admission.status) ?? { eligible: 0, ineligible: 0 }
    if (candidate.admission.structureEligible) counts.eligible += 1
    else counts.ineligible += 1
    byStatus.set(candidate.admission.status, counts)
    roots.set(origin, byStatus)
  }
  const hierarchy = [...roots.entries()].map(([origin, statuses]) => ({
    name: origin,
    value: [...statuses.values()].reduce((sum, item) => sum + item.eligible + item.ineligible, 0),
    children: [...statuses.entries()].map(([status, counts]) => ({
      name: statusLabels[status] ?? status,
      value: counts.eligible + counts.ineligible,
      children: [
        counts.eligible ? { name: '具备结构资格', value: counts.eligible } : null,
        counts.ineligible ? { name: '未进入结构计算', value: counts.ineligible } : null,
      ].filter(Boolean),
    })).sort((left, right) => right.value - left.value),
  })).sort((left, right) => right.value - left.value)
  return <div className="origin-hierarchy">
    <Chart option={{
      color: ['#527ee3', '#55b8b5', '#9a7bd1', '#d59a68', '#7aa35c'],
      title: { text: snapshot.candidates.length.toLocaleString(), subtext: '唯一候选', left: 'center', top: '38%', textStyle: { color: '#2f405b', fontSize: 18, fontWeight: 700 }, subtextStyle: { color: '#7d899b', fontSize: 10 } },
      tooltip: { formatter: (params: { value: number; treePathInfo: Array<{ name: string }> }) => `${params.treePathInfo.slice(1).map((item) => item.name).join(' → ')}<br/><b>${params.value.toLocaleString()} 条唯一候选</b>` },
      series: [{
        type: 'sunburst',
        radius: ['27%', '94%'],
        center: ['50%', '49%'],
        sort: (left: { value: number }, right: { value: number }) => right.value - left.value,
        nodeClick: 'rootToNode',
        minAngle: 4,
        data: hierarchy,
        emphasis: { focus: 'ancestor' },
        label: { color: '#33445f', fontSize: 10, minAngle: 7, overflow: 'truncate' },
        itemStyle: { borderColor: '#fff', borderWidth: 1.5 },
        levels: [
          {},
          { r0: '27%', r: '51%', label: { rotate: 'tangential', fontWeight: 650 } },
          { r0: '51%', r: '75%', label: { rotate: 'radial' }, itemStyle: { colorSaturation: [.45, .75] } },
          { r0: '75%', r: '94%', label: { show: false }, itemStyle: { colorSaturation: [.25, .55] } },
        ],
      }],
    }} />
    <div className="origin-hierarchy-footer"><span>来源组合 → 候选分组 → 结构资格</span><b>按唯一序列计数 · 无重复归因</b></div>
  </div>
}

function SafetyCard({ generator, snapshot }: { generator: string; snapshot: AnalysisSnapshot | null }) {
  const generatorIds = snapshot ? [...new Set(snapshot.occurrences.map((item) => item.generator))] : []
  const selectedIds = generator === 'all' ? generatorIds : generatorIds.filter((id) => generatorDisplay[id] === generator)
  const labels = snapshot ? selectedIds.map((id) => generatorDisplay[id] ?? id) : generator === 'all' ? ['AMP Designer', 'AMPGAN', 'HydrAMP'] : [generator]
  const index = generator === 'AMP Designer' ? 0 : generator === 'AMPGAN v2' ? 1 : 2
  const values = snapshot ? {
    hemolysis: selectedIds.map((id) => {
      const rows = snapshot.candidates.filter((candidate) => candidate.originSet.includes(id))
      return rows.length ? Math.round(rows.filter((candidate) => candidate.metrics.macrel_hemolysis_label?.text === 'high').length / rows.length * 100) : 0
    }),
    toxicity: selectedIds.map((id) => {
      const rows = snapshot.candidates.filter((candidate) => candidate.originSet.includes(id))
      return rows.length ? Math.round(rows.filter((candidate) => candidate.metrics.toxinpred3_label?.text === 'Toxin').length / rows.length * 100) : 0
    }),
    ood: selectedIds.map((id) => {
      const rows = snapshot.candidates.filter((candidate) => candidate.originSet.includes(id))
      const total = rows.flatMap((candidate) => Object.values(candidate.metrics))
      return total.length ? Math.round(total.filter((metric) => metric.outOfDomain).length / total.length * 100) : 0
    }),
  } : generator === 'all'
    ? { hemolysis: [31, 38, 22], toxicity: [12, 17, 9], ood: [6, 7, 10] }
    : { hemolysis: [[31, 38, 22][index]], toxicity: [[12, 17, 9][index]], ood: [[6, 7, 10][index]] }
  const maximumObserved = Math.max(...values.hemolysis, ...values.toxicity, ...values.ood, 10)
  const axisMaximum = Math.min(100, Math.ceil((maximumObserved + 5) / 10) * 10)
  return <Chart option={{
    color: ['#ef8c7c', '#f1bc66', '#8d9aac'],
    grid: { left: 38, right: 12, top: 30, bottom: 28 },
    legend: { top: 0, icon: 'circle', itemWidth: 8, textStyle: { fontSize: 10, color: '#657186' } },
    tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${value}%` },
    xAxis: { type: 'category', data: labels, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe5ed' } }, axisLabel: { fontSize: 10, color: '#657186' } },
    yAxis: { type: 'value', max: axisMaximum, axisLabel: { formatter: '{value}%', fontSize: 10, color: '#748094' }, splitLine: { lineStyle: { color: '#eef1f5' } } },
    series: [
      { name: '溶血风险', type: 'bar', barWidth: 11, data: values.hemolysis, itemStyle: { borderRadius: [4, 4, 0, 0] } },
      { name: '毒性风险', type: 'bar', barWidth: 11, data: values.toxicity, itemStyle: { borderRadius: [4, 4, 0, 0] } },
      { name: '分布外', type: 'bar', barWidth: 11, data: values.ood, itemStyle: { borderRadius: [4, 4, 0, 0] } },
    ],
  }} />
}

function ParetoCard({ generator, snapshot, query }: { generator: string; snapshot: AnalysisSnapshot | null; query: CardQuerySpec }) {
  const selected = selectedGenerators(query)
  const records = snapshot?.candidates.filter((candidate) =>
    (!selected.length || candidate.originSet.some((origin) => selected.includes(origin)))
    && ['macrel_amp_probability', 'macrel_hemolysis_probability', 'toxinpred3_hybrid_score'].every((metric) => candidate.metrics[metric]?.value != null)
  ) ?? []
  const paletteByGenerator = new Map((snapshot
    ? [...new Set(snapshot.occurrences.map((item) => item.generator))].map((id, index) => ({ id, label: generatorDisplay[id] ?? id, color: chartPalette[index % chartPalette.length] }))
    : frameworkFixture.generators).map((item) => [item.label, item.color]))
  const points: ParetoPoint3D[] = snapshot
    ? records.map((candidate) => ({
      sequence: candidate.sequence,
      generator: generatorDisplay[candidate.originSet[0]] ?? candidate.originSet[0],
      activity: candidate.metrics.macrel_amp_probability?.value ?? 0,
      hemolysis: candidate.metrics.macrel_hemolysis_probability?.value ?? 0,
      toxicity: candidate.metrics.toxinpred3_hybrid_score?.value ?? 0,
      paretoRank: candidate.admission.paretoFront,
      structureEligible: candidate.admission.structureEligible,
      color: paletteByGenerator.get(generatorDisplay[candidate.originSet[0]] ?? candidate.originSet[0]) ?? chartPalette[0],
    })).filter((item) => generator === 'all' || item.generator === generator)
    : frameworkFixture.pareto.filter((item) => generator === 'all' || item.generator === generator).map((item, index) => ({
      sequence: item.id,
      generator: item.generator,
      activity: item.activity,
      hemolysis: item.hemolysis,
      toxicity: Math.min(1, .12 + index * .025),
      paretoRank: item.paretoRank,
      structureEligible: false,
      color: paletteByGenerator.get(item.generator) ?? chartPalette[0],
    }))
  return (
    <ParetoFront3D points={points} />
  )
}

interface StructureEnergySnapshot {
  structure?: { rosettaRuns?: Array<{ target?: string; scores?: Array<{ dG_separated?: number }> }> }
}

const structureTargetNames: Record<string, string> = {
  'DNA gyrase subunit A': 'DNA旋转酶A亚基',
  'PBP2a family beta-lactam-resistant peptidoglycan transpeptidase, partial': 'PBP2a耐药转肽酶',
}

function StructureEnergyCard({ data }: { data: StructureEnergySnapshot | null }) {
  const valuesByTarget = new Map<string, number[]>()
  for (const run of data?.structure?.rosettaRuns ?? []) {
    const target = structureTargetNames[run.target ?? ''] ?? run.target ?? '未标注靶点'
    const values = valuesByTarget.get(target) ?? []
    for (const score of run.scores ?? []) if (Number.isFinite(score.dG_separated)) values.push(Number(score.dG_separated))
    valuesByTarget.set(target, values)
  }
  const groups: EnergyGroup[] = [...valuesByTarget.entries()].map(([target, values], index) => ({ target, values, color: chartPalette[index % chartPalette.length] }))
  return <RosettaEnergyViolin groups={groups} />
}

function CandidateTable({ generator, snapshot, query }: { generator: string; snapshot: AnalysisSnapshot | null; query: CardQuerySpec }) {
  const [page, setPage] = useState(0)
  const pageSize = 6
  const kernelResult = snapshot ? runKernel(snapshot, {
    schemaVersion: 'analysis-pivot-query.1',
    queryKey: 'candidate_table',
    filters: { generators: selectedGenerators(query) },
    metrics: ['macrel_amp_probability', 'macrel_hemolysis_probability', 'toxinpred3_hybrid_score', 'net_charge_ph7_4'],
  }) : null
  const records = (kernelResult?.records ?? []) as Array<{
    sequence: string
    originSet: string[]
    admission: { paretoFront: number | null; status: string; structureEligible: boolean }
    metrics: Record<string, { value: number | null }>
  }>
  const unsortedRows = snapshot
    ? records.filter((candidate) => generator === 'all' || candidate.originSet.some((id) => generatorDisplay[id] === generator)).map((candidate) => ({
      sequence: candidate.sequence,
      originSet: candidate.originSet.map((id) => generatorDisplay[id] ?? id),
      activity: candidate.metrics.macrel_amp_probability?.value ?? 0,
      hemolysis: candidate.metrics.macrel_hemolysis_probability?.value ?? 0,
      toxicity: candidate.metrics.toxinpred3_hybrid_score?.value ?? 0,
      charge: candidate.metrics.net_charge_ph7_4?.value ?? 0,
      admissionStatus: candidate.admission.status,
      structureEligible: candidate.admission.structureEligible,
    }))
    : frameworkFixture.candidates.filter((item) => generator === 'all' || item.originSet.includes(generator)).map((item) => ({ ...item, admissionStatus: 'mature_core', structureEligible: true }))
  const cohortOrder: Record<string, number> = { mature_core: 0, promising_uncertain: 1, rejected: 2 }
  const allRows = unsortedRows
    .sort((left, right) => Number(right.structureEligible) - Number(left.structureEligible)
      || (cohortOrder[left.admissionStatus] ?? 3) - (cohortOrder[right.admissionStatus] ?? 3)
      || right.activity - left.activity
      || left.hemolysis - right.hemolysis)
    .map((candidate, index) => ({ ...candidate, id: `候选 ${String(index + 1).padStart(3, '0')}` }))
  const pageCount = Math.max(1, Math.ceil(allRows.length / pageSize))
  const safePage = Math.min(page, pageCount - 1)
  const rows = allRows.slice(safePage * pageSize, (safePage + 1) * pageSize)
  useEffect(() => { setPage(0) }, [generator, snapshot?.snapshotId])
  return (
    <div className="candidate-table-wrap">
      <table className="candidate-table">
        <thead><tr><th>候选序列</th><th>生成来源</th><th>抗菌活性 ↑</th><th>溶血风险 ↓</th><th>毒性风险 ↓</th><th>净电荷</th><th>候选分组</th><th>结构资格</th></tr></thead>
        <tbody>{rows.map((item) => (
          <tr key={item.id}>
            <td><b>{item.id}</b><code>{item.sequence}</code></td>
            <td><div className="origin-pills">{item.originSet.map((origin) => <span key={origin} title={generatorHelp[origin]}>{origin}</span>)}</div></td>
            <td><strong>{item.activity.toFixed(2)}</strong></td>
            <td>{item.hemolysis.toFixed(2)}</td>
            <td>{item.toxicity.toFixed(2)}</td>
            <td>{item.charge > 0 ? '+' : ''}{item.charge.toFixed(1)}</td>
            <td><span className={`candidate-cohort cohort-${item.admissionStatus}`}>{item.admissionStatus === 'mature_core' ? '成熟核心' : item.admissionStatus === 'promising_uncertain' ? '待核候选' : '未入选'}</span></td>
            <td>{item.structureEligible ? <span className="structure-eligible">已具备</span> : <span className="structure-ineligible">未进入</span>}</td>
          </tr>
        ))}</tbody>
      </table>
      <footer className="table-footer"><span>第 {safePage + 1} / {pageCount} 页 · 共 {allRows.length} 条唯一候选</span><div className="table-pagination"><button aria-label="第一页" disabled={safePage === 0} onClick={() => setPage(0)}><ChevronsLeft /></button><button aria-label="上一页" disabled={safePage === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft /></button><button aria-label="下一页" disabled={safePage >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}><ChevronRight /></button><button aria-label="最后一页" disabled={safePage >= pageCount - 1} onClick={() => setPage(pageCount - 1)}><ChevronsRight /></button></div></footer>
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

function CardContent({ id, query, snapshot, structureData }: { id: AnalysisQuestion; query: CardQuerySpec; snapshot: AnalysisSnapshot | null; structureData: StructureEnergySnapshot | null }) {
  const generator = generatorFromQuery(query)
  if (id === 'run_quality') return <RunQualityCard snapshot={snapshot} />
  if (id === 'lineage_and_yield') return <LineageCard snapshot={snapshot} chart={query.chart} query={query} />
  if (id === 'score_distribution') return <DistributionCard generator={generator} snapshot={snapshot} chart={query.chart} query={query} />
  if (id === 'generator_contribution') return <OriginCard snapshot={snapshot} />
  if (id === 'safety_profile') return <SafetyCard generator={generator} snapshot={snapshot} />
  if (id === 'multi_objective_conflict') return <ParetoCard generator={generator} snapshot={snapshot} query={query} />
  if (id === 'structure_energy') return <StructureEnergyCard data={structureData} />
  if (id === 'candidate_laboratory') return <CandidateTable generator={generator} snapshot={snapshot} query={query} />
  return <div className="card-placeholder">扩展接口已就绪</div>
}

export function AnalysisDashboard({ detail, seedNodeIds = [], apiBase = '' }: { detail?: RunDetail | null; seedNodeIds?: string[]; apiBase?: string }) {
  const [caseOpen, setCaseOpen] = useState(false)
  const [layout, setLayout] = useState<Layout[]>(readLayout)
  const [hiddenCards, setHiddenCards] = useState<Set<AnalysisQuestion>>(readHiddenCards)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [snapshot, setSnapshot] = useState<AnalysisSnapshot | null>(null)
  const [snapshotError, setSnapshotError] = useState<string | null>(null)
  const [snapshotRevision, setSnapshotRevision] = useState(0)
  const [queries, setQueries] = useState<Record<AnalysisQuestion, CardQuerySpec>>(readQueries)
  const [structureData, setStructureData] = useState<StructureEnergySnapshot | null>(null)
  const seedKey = seedNodeIds.join('|')

  useEffect(() => {
    window.localStorage.setItem(layoutStorageKey, JSON.stringify(layout))
  }, [layout])

  useEffect(() => {
    window.localStorage.setItem(queryStorageKey, JSON.stringify(queries))
  }, [queries])

  useEffect(() => {
    window.localStorage.setItem(hiddenStorageKey, JSON.stringify([...hiddenCards]))
  }, [hiddenCards])

  useEffect(() => {
    let cancelled = false
    setSnapshot(null)
    setSnapshotError(null)
    const liveAnalyticsEnabled = import.meta.env.VITE_ANALYTICS_API_ENABLED === 'true'
    void loadAnalysisSnapshot({ runId: liveAnalyticsEnabled ? detail?.run.id : undefined }).then((value) => {
      if (!cancelled) setSnapshot(value)
    }).catch(() => {
      if (!cancelled) setSnapshotError('分析快照校验失败')
    })
    return () => { cancelled = true }
  }, [detail?.run.id, snapshotRevision])

  useEffect(() => {
    let cancelled = false
    fetch('/data/candidate-case.snapshot.json', { headers: { Accept: 'application/json' } })
      .then((response) => response.ok ? response.json() as Promise<StructureEnergySnapshot> : Promise.reject(new Error(String(response.status))))
      .then((value) => { if (!cancelled) setStructureData(value) })
      .catch(() => { if (!cancelled) setStructureData(null) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!seedNodeIds.length) return
    const seeded = queriesFromNodes(seedNodeIds)
    if (!seeded.length) return
    setQueries((current) => ({ ...current, ...Object.fromEntries(seeded.map((query) => [query.cardId, query])) }))
    const seededIds = new Set(seeded.map((query) => query.cardId))
    setHiddenCards(new Set(cardRegistry.filter((card) => !seededIds.has(card.id)).map((card) => card.id)))
  }, [seedKey])

  const visibleCards = useMemo(() => cardRegistry.filter((card) => !hiddenCards.has(card.id)), [hiddenCards])
  const runLabel = snapshot
    ? `发布冻结轮次 · ${snapshot.summary.rawOccurrences.toLocaleString()} 次生成 · ${snapshot.summary.uniqueCandidates.toLocaleString()} 条唯一候选 · ${snapshot.summary.observedEvaluations.toLocaleString()} 项评分`
    : snapshotError ?? '正在校验只读分析快照…'

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
          <div className="analysis-eyebrow"><DatabaseZap /> 确定性科学分析 <span>{snapshot?.source === 'analytics_api' ? '实时只读' : '冻结快照'}</span></div>
          <h1>{caseOpen ? '候选案例' : '短肽分析'}</h1>
          <p>{caseOpen ? '单一候选的序列、活性、安全性、双靶点与结构证据' : runLabel}</p>
        </div>
        <div className="analysis-header-actions">
          <span className={`fixture-badge ${snapshot ? 'verified' : ''}`} title="发布快照已校验完整性并保留数据库来源。"><FlaskConical /> {snapshot ? '真实数据已校验' : '正在校验数据'}</span>
          <button className={caseOpen ? 'active' : ''} onClick={() => setCaseOpen((value) => !value)}><Microscope />{caseOpen ? '返回分析' : '候选案例'}</button>
          {!caseOpen && <div className="card-library-wrap">
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
          </div>}
          {!caseOpen && <button className="icon-only" onClick={resetLayout} title="重置布局"><RotateCcw /></button>}
        </div>
      </header>

      {caseOpen ? <CandidateCaseWorkbench apiBase={apiBase} /> : <>
      {!!seedNodeIds.length && <div className="analysis-orchestration compact-orchestration">
        <div className="orchestration-label"><SlidersHorizontal /><span><b>卡片独立分析</b><small>每张卡片拥有自己的字段、筛选和图表</small></span></div>
        <div className="orchestration-flow">
          <span className="flow-step active">概览多选</span><i />
          <span className="flow-step">自动编排字段</span><i />
          <span className="flow-step">拖动微调</span><i />
          <span className="flow-step">图表推荐</span>
        </div>
        <div className={`orchestration-result ${seedNodeIds.length ? 'has-selection' : ''}`}>
          <Sparkles />
          <span>{`已根据 ${seedNodeIds.length} 个流程节点生成 ${queriesFromNodes(seedNodeIds).length} 张分析卡片`}</span>
        </div>
      </div>}

      {!snapshot ? (
        <div className="snapshot-state-panel"><FlaskConical /><b>{snapshotError ?? '正在读取只读数据'}</b><span>{snapshotError ? '未显示任何分析数值。请重新校验发布快照。' : '校验记录数量、覆盖率与传输完整性。'}</span>{snapshotError && <button onClick={() => setSnapshotRevision((value) => value + 1)}>重新校验</button>}</div>
      ) : <>
        <div className="analysis-grid-shell">
        <DashboardGrid
          className="analysis-grid"
          layout={layout.filter((item) => !hiddenCards.has(item.i as AnalysisQuestion))}
          cols={12}
          rowHeight={50}
          margin={[10, 10]}
          containerPadding={[0, 0]}
          isDraggable
          isResizable
          draggableHandle=".analysis-card-header"
          draggableCancel="button, a, input, select, textarea"
          onLayoutChange={(visibleLayout) => setLayout((current) => [
            ...visibleLayout,
            ...current.filter((item) => hiddenCards.has(item.i as AnalysisQuestion)),
          ])}
        >
          {visibleCards.map((definition) => (
            <div key={definition.id}>
              <CardShell
                definition={definition}
                query={queries[definition.id]}
                onQueryChange={(query) => setQueries((current) => ({ ...current, [definition.id]: query }))}
                meta={queries[definition.id].sourceNodeIds.length ? <b>{`${queries[definition.id].sourceNodeIds.length} 个节点`}</b> : undefined}
              >
                <CardContent id={definition.id} query={queries[definition.id]} snapshot={snapshot} structureData={structureData} />
              </CardShell>
            </div>
          ))}
        </DashboardGrid>
        </div>

        <footer className="analysis-provenance-bar">
          <div><DatabaseZap /><span><b>来源</b> PostgreSQL 只读导出</span><span><b>评分覆盖</b> {`${snapshot.coverage.observed.toLocaleString()} / ${snapshot.coverage.expected.toLocaleString()}`}</span><span><b>轮次状态</b> {snapshot.run.status === 'cancelled' ? '已取消' : '读取中'}</span></div>
          <p>完成范围：序列生成、模型评分、候选决策</p>
        </footer>
      </>}
      </>}
    </section>
  )
}
