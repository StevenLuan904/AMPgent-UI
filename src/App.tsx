import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type EdgeMouseHandler,
  type NodeMouseHandler,
} from '@xyflow/react'
import {
  Activity,
  ArrowLeft,
  Atom,
  Box,
  BrainCircuit,
  ChartNoAxesCombined,
  ChevronRight,
  Clock3,
  CircleDot,
  Database,
  Ellipsis,
  FileJson2,
  Fingerprint,
  FlaskConical,
  GitBranch,
  Layers3,
  PanelLeftClose,
  Route,
  RefreshCw,
  ScanSearch,
  Settings2,
  ShieldCheck,
  X,
} from 'lucide-react'
import { AnalysisDashboard } from './analysis/AnalysisDashboard'
import { loadAnalysisSnapshot, type AnalysisSnapshot } from './analysis/dataKernel'
import { EvidenceDashboard } from './analysis/EvidenceDashboard'
import { MoleculeViewer } from './MoleculeViewer'
import { candidateGenerationLabel, formatGenerationPopulation } from './generationPopulation'
import { formatQualityGateRule, qualityGateCountSteps, qualityGateStatusLabel } from './generationQualityGate'
import {
  distributionForStage,
  ResultDistribution,
  type ResultDistributionData,
} from './ResultDistribution'
import { LaneLabel, WorkflowNode, type LaneNode, type StageNode } from './WorkflowNode'
import { assertMatchingRunIdentity, type RunIdentity } from './runIdentity'
import { formatRunTitle } from './runPresentation'
import { buildRuntimeGraph, type RuntimeGraphModel } from './runtimeGraph'
import { schedulerHealthDescription, schedulerHealthPresentation } from './schedulerHealth'
import type {
  CandidatePreview,
  GenerationQualityGate,
  GraphEdgeDetail,
  GraphStage,
  MetricSummary,
  NodeDetail,
  RunDetail,
  RunListItem,
  RunListResponse,
  ToolAttempt,
  ViewerArtifact,
} from './types'

const nodeTypes = { stage: WorkflowNode, lane: LaneLabel }
const connectionStorageKey = 'ampgent.data-service.base.v1'
const selectedRunStorageKey = 'ampgent.observer.selected-run.v1'
const defaultApiBase = import.meta.env.VITE_API_BASE ?? ''

function normalizeApiBase(value: string) {
  return value.trim().replace(/\/+$/, '')
}

function readApiBase() {
  const stored = window.localStorage.getItem(connectionStorageKey)
  return stored === null ? defaultApiBase : stored
}

function getConfiguredApiBase() {
  return readApiBase()
}
const statusText: Record<string, string> = {
  created: '已创建', submitted: '已提交', running: '运行中', succeeded: '已完成', failed: '运行异常终止', cancelled: '已取消',
  completed: '已完成', stopped: '已停止', pending: '待写入',
}

const metricLabels: Record<string, string> = {
  llamp_log10_mic_um: '最小抑菌浓度预测',
  amp_read_log10_mic_um: '交叉模型最小抑菌浓度预测',
  macrel_amp_probability: '抗菌概率',
  macrel_hemolysis_label: '溶血风险类别',
  macrel_hemolysis_probability: '溶血概率',
  toxinpred3_hybrid_score: '毒性综合评分',
  toxinpred3_label: '毒性预测类别',
  hydrophobic_moment_eisenberg: '疏水矩',
  hydrophobic_ratio_modlamp: '疏水残基比例',
  maximum_hydrophobic_run: '最大连续疏水残基数',
  net_charge_ph7_4: '酸碱度7.4下净电荷',
}

const professionalTermHelp: Record<string, string> = {
  amp_designer: 'AMP Designer用于基于模型生成抗菌短肽候选序列。',
  ampgan: 'AMPGAN v2是用于生成抗菌肽候选的对抗生成模型。',
  hydramp: 'HydrAMP用于生成并优化抗菌肽候选序列。',
  amp_read: 'AMP read用于交叉复核候选短肽的抗菌活性预测。',
  boltz: 'Boltz 2用于预测蛋白质与短肽复合物的三维构象。',
  rosetta: 'Rosetta用于采样并评估蛋白质与短肽的界面构象。',
}

function formatTime(value: string | null) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function readableDataError(cause: unknown, fallback: string) {
  if (cause instanceof Error && /^(轮次|数据库|无法)/.test(cause.message)) return cause.message
  return fallback
}

function useRunData(enabled: boolean, apiBase: string) {
  const requestedRunId = new URLSearchParams(window.location.search).get('run')
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [selectedId, setSelectedIdState] = useState<string | null>(() => requestedRunId ?? window.localStorage.getItem(selectedRunStorageKey))
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [nodeDetails, setNodeDetails] = useState<Record<string, NodeDetail>>({})
  const runsInFlight = useRef(false)
  const detailInFlight = useRef(false)
  const alignedStructureCounts = useRef<Record<string, number>>({})
  const runIdentities = useRef<Record<string, RunIdentity>>({})

  const loadRuns = useCallback(async () => {
    if (runsInFlight.current) return
    runsInFlight.current = true
    try {
      const response = await fetch(`${apiBase}/v1/observer/runs?limit=12`)
      if (!response.ok) throw new Error(`轮次列表读取失败：${response.status}`)
      const payload = await response.json() as RunListResponse
      runIdentities.current = Object.fromEntries(payload.runs.map((run) => [run.id, {
        id: run.id,
        temporal_workflow_id: run.temporal_workflow_id,
        temporal_run_id: run.temporal_run_id,
      }]))
      setRuns(payload.runs.map((run) => ({
        ...run,
        structure_record_count: Math.max(run.structure_record_count, alignedStructureCounts.current[run.id] ?? 0),
      })))
      setSelectedIdState((current) => {
        const next = requestedRunId ?? (current && payload.runs.some((run) => run.id === current) ? current : payload.runs[0]?.id ?? null)
        if (next) window.localStorage.setItem(selectedRunStorageKey, next)
        return next
      })
    } finally {
      runsInFlight.current = false
    }
  }, [apiBase])

  const loadDetail = useCallback(async (runId: string, quiet = false) => {
    if (detailInFlight.current) return
    detailInFlight.current = true
    if (!quiet) setLoading(true)
    else setRefreshing(true)
    try {
      const response = await fetch(`${apiBase}/v1/observer/runs/${runId}`)
      if (!response.ok) throw new Error(`轮次详情读取失败：${response.status}`)
      const payload = await response.json() as RunDetail
      assertMatchingRunIdentity(runIdentities.current[runId] ?? { id: runId }, payload.run)
      // Keep the authoritative response intact. The runtime graph is built below from
      // observed calls/events; legacy stage reconciliation must not manufacture stages.
      setDetail(payload)
      setNodeDetails({})
      setError(null)
      setLoading(false)

      // The run summary is useful even when one legacy node-detail endpoint is slow.
      // Enrichment is deliberately best-effort and never blocks the event/candidate graph.
      const entries = await Promise.all((payload.graph?.nodes ?? []).map(async (stage) => {
        const controller = new AbortController()
        const timeout = window.setTimeout(() => controller.abort(), 20000)
        try {
          const nodeResponse = await fetch(`${apiBase}/v1/observer/runs/${runId}/nodes/${encodeURIComponent(stage.id)}`, { signal: controller.signal })
          return [stage.id, nodeResponse.ok ? await nodeResponse.json() as NodeDetail : undefined] as const
        } catch {
          return [stage.id, undefined] as const
        } finally {
          window.clearTimeout(timeout)
        }
      }))
      const details = Object.fromEntries(entries.filter((entry): entry is [string, NodeDetail] => entry[1] !== undefined))
      setNodeDetails(details)
    } catch (cause) {
      setError(readableDataError(cause, '无法连接观察器接口'))
    } finally {
      detailInFlight.current = false
      setLoading(false)
      setRefreshing(false)
    }
  }, [apiBase])

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return
    }
    setLoading(true)
    loadRuns().catch((cause) => {
      setError(readableDataError(cause, '无法连接观察器接口'))
      setLoading(false)
    })
  }, [enabled, loadRuns])

  useEffect(() => {
    if (!enabled || !selectedId) return
    void loadDetail(selectedId)
    const timer = window.setInterval(() => {
      void loadRuns()
      void loadDetail(selectedId, true)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [enabled, selectedId, loadDetail, loadRuns])

  const retry = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      await loadRuns()
      if (selectedId) await loadDetail(selectedId)
    } catch (cause) {
      setError(readableDataError(cause, '无法连接观察器接口'))
      setLoading(false)
    }
  }, [loadDetail, loadRuns, selectedId])

  const setSelectedId = useCallback((runId: string) => {
    window.localStorage.setItem(selectedRunStorageKey, runId)
    setSelectedIdState(runId)
  }, [])

  return { runs, selectedId, setSelectedId, detail, nodeDetails, error, loading, refreshing, retry, refresh: () => selectedId && loadDetail(selectedId, true) }
}

function RunList({ runs, selectedId, onSelect }: { runs: RunListItem[]; selectedId: string | null; onSelect: (id: string) => void }) {
  if (!runs.length) {
    return <div className="run-list"><div className="run-list-empty"><Database /><span><b>暂无可用运行数据</b><small>观察器接口未返回可展示的 PostgreSQL 运行记录。</small></span></div></div>
  }
  return (
    <div className="run-list">
      {runs.map((run) => (
        <button key={run.id} className={`run-row ${run.id === selectedId ? 'active' : ''}`} onClick={() => onSelect(run.id)}>
          <span className={`run-status-dot status-${run.status}`} />
          <span className="run-row-copy">
                <strong>{formatRunTitle(run)}</strong>
            <small>{formatTime(run.created_at)} · {run.tool_call_count} 次工具运行</small>
          </span>
          <ChevronRight />
        </button>
      ))}
    </div>
  )
}

function Sidebar({
  runs,
  selectedId,
  structureRun,
  activeView,
  onView,
  onSelect,
  onOpenStructureEvidence,
}: {
  runs: RunListItem[]
  selectedId: string | null
  structureRun: RunListItem | null
  activeView: 'overview' | 'analysis' | 'evidence'
  onView: (view: 'overview' | 'analysis' | 'evidence') => void
  onSelect: (id: string) => void
  onOpenStructureEvidence: () => void
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar-label">概览与数据</div>
      <nav className="primary-nav">
        <button className={activeView === 'overview' ? 'active' : ''} onClick={() => onView('overview')}><Layers3 />概览</button>
        <button className={activeView === 'analysis' ? 'active' : ''} onClick={() => onView('analysis')}><ChartNoAxesCombined />分析</button>
        <button className={activeView === 'evidence' ? 'active' : ''} onClick={() => onView('evidence')}><Database />证据库</button>
      </nav>
      <div className="sidebar-label runs-label">轮次 · 科学运行</div>
      <RunList runs={runs} selectedId={selectedId} onSelect={onSelect} />
      <div className="sidebar-sections">
        <button><span><SparkIcon icon="sequence" /></span><b>序列设计</b><small>生成模型与十一项指标</small></button>
        <button><span><GitBranch /></span><b>多靶点</b><small>原位与错误口袋对照</small></button>
        <button
          className={`structure-evidence-link${structureRun?.id === selectedId && activeView === 'overview' ? ' active' : ''}`}
          title="Boltz 2预测复合物构象；Rosetta进行界面精修与评分。"
          disabled={!structureRun}
          onClick={onOpenStructureEvidence}
        >
          <span><Atom /></span><b>结构证据</b>
          <small>{structureRun ? `最近轮次 · ${structureRun.structure_record_count.toLocaleString()} 条记录` : '数据库中尚无结构记录'}</small>
        </button>
        <button><span><ShieldCheck /></span><b>科学评审</b><small>证据来源追踪</small></button>
      </div>
    </aside>
  )
}

function SparkIcon({ icon }: { icon: string }) {
  return icon === 'sequence' ? <Activity /> : <CircleDot />
}

function CanvasHeader({ detail, refreshing, selectionMode, selectedCount, onRefresh, onToggleSelection }: {
  detail: RunDetail
  refreshing: boolean
  selectionMode: boolean
  selectedCount: number
  onRefresh: () => void
  onToggleSelection: () => void
}) {
  const isStructureReview = (detail.counts.boltz_poses ?? 0) > 0 || (detail.counts.rosetta_decoys ?? 0) > 0
  const displayCandidateCount = detail.display_population?.candidate_count ?? detail.counts.candidates
  const excludedCandidateCount = detail.display_population?.excluded_candidate_count ?? detail.counts.excluded_candidates ?? detail.candidate_exclusions?.length ?? 0
  const generationSummary = detail.generation_population
    ? formatGenerationPopulation(detail.generation_population)
    : `${displayCandidateCount.toLocaleString()} 个候选`
  const scientificStatus = detail.run.scientific_run_status?.status ?? detail.run.status
  const temporalObservability = detail.run.temporal_observability
  const schedulerHealth = temporalObservability ? schedulerHealthPresentation(temporalObservability) : null
  const schedulerHealthTitle = temporalObservability
    ? `${schedulerHealthDescription(temporalObservability)}${temporalObservability.observed_at ? ` · ${formatTime(temporalObservability.observed_at)} 观测` : ''}`
    : ''
  return (
    <header className="canvas-header">
      <div className="canvas-title-block">
        <div className="eyebrow"><span>轮次 · 正式科学运行</span></div>
        <h1>{isStructureReview ? '短肽结构证据复核' : '序列优先的短肽设计'}</h1>
        <div className="round-meta">
          <span>{formatTime(detail.run.created_at)} 创建</span><i />
          <span>{generationSummary}</span><i />
          {excludedCandidateCount > 0 && <><span title="历史运行中已存在的生成子代，仅保留审计记录。">{excludedCandidateCount.toLocaleString()} 个历史重放已排除</span><i /></>}
          <span>{detail.counts.admitted.toLocaleString()} 个进入结构阶段</span><i />
          <span>{detail.branches.length} 个靶点</span><i />
          <span>{detail.counts.boltz_poses.toLocaleString()} 个复合物构象 · {detail.counts.rosetta_decoys.toLocaleString()} 个界面精修样本</span>
        </div>
      </div>
      <div className="header-actions">
        <button className={`analysis-select-button ${selectionMode ? 'active' : ''}`} onClick={onToggleSelection}>
          <ChartNoAxesCombined /><span>{selectionMode ? `已选 ${selectedCount} 个节点` : '组合分析'}</span>
        </button>
        <span className={`run-pill status-${scientificStatus}`} title="科学运行状态来自权威数据库。"><i />{statusText[scientificStatus] ?? scientificStatus}</span>
        {schedulerHealth && <span className={`observability-pill tone-${schedulerHealth.tone}`} title={schedulerHealthTitle}>{schedulerHealth.label}</span>}
        <button className="icon-button" onClick={onRefresh} title="立即刷新"><RefreshCw className={refreshing ? 'spin' : ''} /></button>
        <button className="icon-button"><Ellipsis /></button>
      </div>
    </header>
  )
}

function GraphView({
  detail,
  runtimeGraph,
  analysisSnapshot,
  persistedDistributions,
  selectedStage,
  selectedEdge,
  selectionMode,
  analysisSelection,
  onSelect,
  onToggleAnalysis,
  onSelectEdge,
}: {
  detail: RunDetail
  runtimeGraph: RuntimeGraphModel
  analysisSnapshot: AnalysisSnapshot | null
  persistedDistributions: Record<string, ResultDistributionData>
  selectedStage: string | null
  selectedEdge: GraphEdgeDetail | null
  selectionMode: boolean
  analysisSelection: string[]
  onSelect: (id: string) => void
  onToggleAnalysis: (id: string) => void
  onSelectEdge: (edge: GraphEdgeDetail) => void
}) {
  const nodes = useMemo<Array<StageNode | LaneNode>>(() => [
    ...runtimeGraph.nodes.map((stage): StageNode => ({
      id: stage.id,
      type: 'stage',
      position: runtimeGraph.positions[stage.id] ?? { x: 0, y: 0 },
      data: {
        stage,
        branches: detail.branches,
        viewer: detail.viewers?.[stage.id] ?? (stage.kind === 'structure' ? detail.viewer : null),
        distribution: persistedDistributions[stage.id]
          ?? distributionForStage(analysisSnapshot, detail, stage.id)
          ?? { label: '节点结果', unit: '条', values: [], source: '尚无数值结果', direction: 'neutral' },
        selected: selectionMode ? analysisSelection.includes(stage.id) : selectedStage === stage.id,
      },
      draggable: false,
    })),
  ], [analysisSelection, analysisSnapshot, detail, persistedDistributions, runtimeGraph, selectedStage, selectionMode])
  const stageById = useMemo(() => Object.fromEntries(runtimeGraph.nodes.map((node) => [node.id, node])), [runtimeGraph.nodes])
  const edges = useMemo<Edge[]>(() => runtimeGraph.edges.map((edge, index) => {
    const source = stageById[edge.source] as GraphStage | undefined
    const active = source?.status === 'completed' || source?.status === 'running'
    const isSelected = selectedEdge?.source === edge.source && selectedEdge?.target === edge.target
    return {
      id: `${edge.source}-${edge.target}-${index}`,
      source: edge.source,
      target: edge.target,
      type: 'default',
      pathOptions: { curvature: 0.32 },
      animated: source?.status === 'running',
      label: edge.provenance === 'derived' ? undefined : edge.label ?? undefined,
      labelStyle: { fill: '#536176', fontSize: 8, fontWeight: 600 },
      labelBgStyle: { fill: '#ffffff', fillOpacity: 0.94 },
      labelBgPadding: [6, 4],
      labelBgBorderRadius: 5,
      data: { detail: edge },
      markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: isSelected ? '#2257ee' : active ? '#111827' : '#cbd1dc' },
      style: { stroke: isSelected ? '#2257ee' : active ? '#111827' : '#cbd1dc', strokeWidth: isSelected ? 2.8 : active ? 1.8 : 1.3, strokeDasharray: edge.provenance === 'derived' ? '5 4' : undefined },
    }
  }), [runtimeGraph.edges, selectedEdge, stageById])
  const handleNodeClick: NodeMouseHandler = (_, node) => {
    if (node.type !== 'stage') return
    if (selectionMode) onToggleAnalysis(node.id)
    else onSelect(node.id)
  }
  const handleEdgeClick: EdgeMouseHandler = (_, edge) => {
    const edgeDetail = (edge.data as { detail?: GraphEdgeDetail } | undefined)?.detail
    if (edgeDetail) onSelectEdge(edgeDetail)
  }

  return (
    <div className="graph-area">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        onEdgeClick={handleEdgeClick}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 0.85 }}
        defaultViewport={{ x: 22, y: 92, zoom: 0.72 }}
        minZoom={0.2}
        maxZoom={1.35}
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
        elementsSelectable
      >
        <Background variant={BackgroundVariant.Dots} gap={26} size={1} color="#e8ebf1" />
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
      <div className="runtime-graph-summary" role="status">
        <div className="runtime-graph-summary-head"><span className="runtime-live-dot" /><b>真实运行图</b><small>节点 {runtimeGraph.nodes.length} · 显式边 {runtimeGraph.edges.length}</small></div>
        <div className="runtime-graph-stats"><span>调用 {runtimeGraph.stats.observedCalls}</span><span>事件 {runtimeGraph.stats.observedEvents}</span><span>重复 {runtimeGraph.stats.repeatedTools}</span><span>重试 {runtimeGraph.stats.retries}</span><span>并行组 {runtimeGraph.stats.parallelGroups}</span>{runtimeGraph.stats.cycles > 0 && <span>循环 {runtimeGraph.stats.cycles}</span>}</div>
        {!!runtimeGraph.gaps.length && <p title={runtimeGraph.gaps.join('；')}>数据契约缺口 {runtimeGraph.gaps.length} 项 · 未补画未知关系</p>}
      </div>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string | number | null | undefined }) {
  return <div className="fact"><span>{label}</span><strong>{value ?? '—'}</strong></div>
}

function CandidateCard({ candidate }: { candidate: CandidatePreview }) {
  const visibleMetrics = candidate.metrics.filter((metric) => metric.value !== null).slice(0, 3)
  const cohort = candidate.cohort === 'mature_core' ? '成熟核心' : candidate.cohort === 'exploration' ? '探索组' : '候选组'
  return (
    <div className="candidate-card">
      <div><span className="cohort-chip">{cohort}</span>{candidate.generation !== undefined && <small>{candidateGenerationLabel(candidate.generation)}</small>}<small>{candidate.length} 个氨基酸</small></div>
      <code>{candidate.sequence}</code>
      <div className="candidate-metrics">
        {visibleMetrics.map((metric) => <span key={metric.name}><b>{metric.value?.toFixed(2)}</b>{metricLabels[metric.name] ?? '计算指标'}</span>)}
      </div>
    </div>
  )
}

function formatMetricValue(value: number | null) {
  if (value === null) return '—'
  const magnitude = Math.abs(value)
  if (magnitude > 0 && (magnitude < 0.001 || magnitude >= 10000)) return value.toExponential(2)
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(value)
}

function MetricTable({ metrics }: { metrics: Record<string, MetricSummary> }) {
  const rows = Object.entries(metrics)
  if (!rows.length) return <div className="empty-evidence">数值评估记录：0</div>
  return (
    <div className="metric-table-wrap">
      <table className="metric-table">
        <thead><tr><th>指标</th><th>代表值</th><th>范围</th><th>样本 / 超出适用域</th></tr></thead>
        <tbody>
          {rows.map(([name, metric]) => {
            const isLogMic = name.includes('log10_mic') && metric.mean !== null
            const mean = isLogMic ? 10 ** metric.mean! : metric.mean
            const minimum = isLogMic && metric.min !== null ? 10 ** metric.min : metric.min
            const maximum = isLogMic && metric.max !== null ? 10 ** metric.max : metric.max
            return (
              <tr key={name}>
                <td><b>{metricLabels[name] ?? '计算指标'}</b><small>{isLogMic ? '微摩尔 · 对数预测值反变换' : metric.unit ?? '无单位'}</small></td>
                <td>{formatMetricValue(mean)}</td>
                <td>{formatMetricValue(minimum)} — {formatMetricValue(maximum)}</td>
                <td>{metric.count.toLocaleString()} / {metric.out_of_domain}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function recordValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function omitAuditHashes(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(omitAuditHashes)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !key.toLowerCase().includes('sha256'))
      .map(([key, nested]) => [key, omitAuditHashes(nested)]))
  }
  return value
}

const decisionStatusLabels: Record<string, string> = {
  mature_core: '成熟核心',
  promising_uncertain: '有潜力但不确定',
  rejected: '淘汰',
}

const decisionReasonLabels: Record<string, string> = {
  rank_instability: '排名不稳定',
  'label_gate_failed:macrel_hemolysis_label': '溶血标签门槛未通过 · MACREL',
  'label_gate_failed:toxinpred3_label': '毒性标签门槛未通过 · ToxinPred3',
  outside_frozen_structure_budget: '超出冻结的结构计算预算',
  selected_by_deterministic_nonweighted_pareto_front: '由确定性非加权 Pareto 前沿选中',
  selected_within_fixed_exploration_budget: '固定探索预算内入选',
}

function ToolAttemptDisclosure({ call }: { call: ToolAttempt }) {
  const context = call.structure_context?.[0]
  const inputs = recordValue(call.inputs)
  const parameters = recordValue(call.parameters)
  const lane = context?.lane === 'wrong_pocket' ? '错误口袋对照' : context?.lane === 'native' ? '原位' : context?.lane
  const facts = [
    ['靶点', context?.target],
    ['通道', lane],
    ['候选序列', context?.candidate_sequence],
    ['结果', context ? `${context.records} 条结构记录` : undefined],
    ['评分函数', parameters.score_function ?? inputs.score_function],
    ['构象数量', parameters.nstruct ?? inputs.nstruct],
    ['并行精修样本', parameters.parallel_decoys ?? inputs.parallel_decoys],
    ['证据等级', parameters.evidence_grade],
    ['短肽链', inputs.peptide_chain],
    ['插件', inputs.plugin],
    ['阶段', inputs.stage],
    ['候选数', parameters.score_all_candidate_count],
    ['随机种子', call.random_seed],
    ['模型', context ? undefined : call.model_uri],
  ].filter((item): item is [string, unknown] => item[1] !== null && item[1] !== undefined)
  return (
    <details className="tool-attempt">
      <summary>
        <span className={`attempt-state ${call.status}`} aria-hidden="true" />
        <span className="attempt-name">
          <b title="专业计算工具名称；其输出在此作为可追溯的计算证据。">{call.tool_name}</b>
          <small title={context ? `${context.target} · ${lane}` : call.tool_version}>
            {context ? `${context.target} · ${lane}` : inputs.plugin ? String(inputs.plugin) : '持久化运行'} <i /> 第 {call.attempt} 次尝试
          </small>
        </span>
        <span className="attempt-duration"><Clock3 />{call.duration_seconds === null ? '—' : call.duration_seconds < 0.1 ? '小于0.1秒' : `${call.duration_seconds.toFixed(1)}秒`}</span>
        <ChevronRight />
      </summary>
      <div className="attempt-body">
        <div className="science-fact-grid">
          {facts.map(([label, value]) => <div key={label}><span>{label}</span><b title={String(value)}>{String(value).replaceAll('_', ' ')}</b></div>)}
        </div>
        <details className="json-disclosure"><summary><FileJson2 />完整输入与参数</summary><pre>{JSON.stringify(omitAuditHashes({ inputs: call.inputs, parameters: call.parameters }), null, 2)}</pre></details>
        {!!call.artifacts.length && (
          <details className="artifact-disclosure">
            <summary><FileJson2 /><span>证据文件</span><b>{call.artifacts.length}</b><ChevronRight /></summary>
            <div className="artifact-list">
              {call.artifacts.map((artifact) => (
                <a key={`${artifact.sha256}-${artifact.role}`} href={`${getConfiguredApiBase()}${artifact.url}`} target="_blank" rel="noreferrer">
                  <FileJson2 />
                  <span><b title={artifact.role}>{artifact.role}</b><small>{artifact.media_type} · {(artifact.size_bytes / 1024).toFixed(1)} KB</small></span>
                </a>
              ))}
            </div>
          </details>
        )}
      </div>
    </details>
  )
}

function ReasoningPanel({ nodeDetail }: { nodeDetail: NodeDetail }) {
  const reasons = Object.entries(nodeDetail.reasoning.reason_counts)
  const maximum = Math.max(1, ...reasons.map(([, count]) => count))
  const showAdmissionTrace = nodeDetail.node_id === 'admission' || nodeDetail.node_id === 'portfolio'
  return (
    <div className="reasoning-panel">
      <div className="analysis-kicker"><BrainCircuit />持久化分析</div>
      {nodeDetail.narrative.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
      {showAdmissionTrace && !!Object.keys(nodeDetail.reasoning.status_counts).length && (
        <div className="decision-stats">
          {Object.entries(nodeDetail.reasoning.status_counts).map(([status, count]) => <div key={status}><b>{count}</b><span>{decisionStatusLabels[status] ?? status.replaceAll('_', ' ')}</span></div>)}
        </div>
      )}
      {showAdmissionTrace && !!reasons.length && (
        <div className="reason-bars">
          {reasons.slice(0, 10).map(([reason, count]) => (
            <div key={reason}><span><b>{decisionReasonLabels[reason] ?? reason.replaceAll('_', ' ').replaceAll(':', ' · ')}</b><i>{count}</i></span><em><i style={{ width: `${(count / maximum) * 100}%` }} /></em></div>
          ))}
        </div>
      )}
      {nodeDetail.reasoning.decisions.map((decision, index) => (
        <details className="policy-disclosure" key={index}>
          <summary><Fingerprint />决策依据 <ChevronRight /></summary>
          <div className="decision-provenance">
            <Fact label="智能体" value={String(decision.agent_name ?? '—')} />
            <Fact label="决策类型" value={String(decision.type ?? '—')} />
          </div>
          <pre>{JSON.stringify(decision.policy ?? {}, null, 2)}</pre>
        </details>
      ))}
    </div>
  )
}

function QualityGatePanel({ gate }: { gate: GenerationQualityGate }) {
  const steps = qualityGateCountSteps(gate)
  return (
    <section className={`quality-gate-panel state-${gate.status}`}>
      <header>
        <span><ShieldCheck /><span><h3>新生序列质量门</h3><small title="规则序列生成器第二版：在生成与评估前执行确定性序列质量预筛。">规则序列生成器 · 第二版</small></span></span>
        <b>{qualityGateStatusLabel(gate)}</b>
      </header>
      <div className="quality-gate-counts" aria-label="新生序列质量门分层计数">
        {steps.map((step) => <div key={step.label}><span>{step.label}</span><strong>{step.value.toLocaleString()}</strong></div>)}
      </div>
      <div className="quality-gate-rules">
        {gate.rules.map((rule) => {
          const formatted = formatQualityGateRule(rule)
          return <div key={rule.metric_key}><span>{formatted.label}</span><strong>{formatted.value}</strong></div>
        })}
      </div>
      <footer><span>{gate.status === 'applied' ? '当前运行的持久化记录' : '当前运行尚无第二版预筛记录'}</span><b>数据库直读</b></footer>
    </section>
  )
}

function Inspector({ detail, stageId, analysisSnapshot, distributionOverride, onClose }: { detail: RunDetail; stageId: string; analysisSnapshot: AnalysisSnapshot | null; distributionOverride?: ResultDistributionData; onClose: () => void }) {
  const stage = detail.graph.nodes.find((item) => item.id === stageId) ?? detail.graph.nodes[0]
  const groupLabel = { inputs: '输入', design: '设计', evaluation: '评估', decision: '决策', structure: '结构', review: '评审', observed: '运行观测' }[stage.group]
  const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const qualityGate = nodeDetail?.generation_quality_gate ?? stage.generation_quality_gate ?? detail.generation_quality_gate
  const distribution = distributionOverride
    ?? distributionForStage(analysisSnapshot, detail, stageId)
    ?? { label: '节点结果', unit: '条', values: [], source: '尚无数值结果', direction: 'neutral' }

  useEffect(() => {
    const controller = new AbortController()
    const load = async () => {
      try {
        const response = await fetch(`${getConfiguredApiBase()}/v1/observer/runs/${detail.run.id}/nodes/${stageId}`, { signal: controller.signal })
        if (!response.ok) throw new Error(`node evidence: ${response.status}`)
        setNodeDetail(await response.json() as NodeDetail)
        setDetailError(null)
      } catch (cause) {
        if (!controller.signal.aborted) setDetailError(cause instanceof Error ? cause.message : '节点证据读取失败')
      }
    }
    setNodeDetail(null)
    void load()
    const timer = window.setInterval(load, 5000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [detail.run.id, stageId])

  return (
    <aside className="inspector expanded-inspector">
      <div className="inspector-header">
        <div><small>节点详情 · {groupLabel}</small><h2 title={professionalTermHelp[stage.id]}>{stage.label}</h2></div>
        <button className="icon-button" onClick={onClose}><X /></button>
      </div>
      {(stageId === 'boltz' || stageId === 'rosetta') && (() => {
        const structureViewer = detail.viewers?.[stageId] ?? detail.viewer
        return (
        <section className="viewer-section">
          <MoleculeViewer artifact={structureViewer} />
          {structureViewer && <div className="structure-caption"><code>{structureViewer.sequence}</code><span title="标准靶点名称保留数据库原始命名，以保证可追溯性。">{structureViewer.target_name} · {structureViewer.lane === 'native' ? '原位' : '错误口袋对照'} · 随机种子 {structureViewer.seed}</span></div>}
        </section>
        )
      })()}
      <section className={`scientific-summary grade-${stage.insight.grade}`}>
        <div>
          <small>{stage.insight.source === 'persisted_decision' ? '持久化智能体决策' : '数据库结果'}</small>
          <strong><i />{stage.insight.verdict}</strong>
        </div>
        <p>{stage.insight.reason}</p>
        <span>{stage.insight.facts.map((fact) => `${fact.label} ${fact.value}`).join(' · ')}</span>
      </section>
      {stageId === 'candidate_pool' && qualityGate && <QualityGatePanel gate={qualityGate} />}
      {distribution && <section className="inspector-distribution-section"><ResultDistribution data={distribution} /></section>}
      <section className="inspector-section evidence-overview">
        <div className="section-title"><h3>数据概览</h3><span className={`stage-badge ${stage.status}`}>{statusText[stage.status] ?? stage.status}</span></div>
        <div className="fact-grid four-up">
          <Fact label="持久化记录" value={stage.current.toLocaleString()} />
          <Fact label="计划总量" value={stage.total.toLocaleString()} />
          <Fact label="数据来源" value={stage.provenance === 'database' ? '数据库直读' : stage.provenance === 'derived' ? '证据推导' : '等待写入'} />
          <Fact label="更新时间" value={formatTime(detail.updated_at)} />
        </div>
        {stage.provenance === 'derived' && <p className="inference-note"><ScanSearch />进度来源：持久化证据与流程拓扑</p>}
      </section>
      {detailError && <div className="detail-loading error">{detailError}</div>}
      {!nodeDetail && !detailError && <div className="detail-loading"><RefreshCw className="spin" />正在组合节点证据…</div>}
      {nodeDetail && (
        <>
          <section className="inspector-section"><ReasoningPanel nodeDetail={nodeDetail} /></section>
          {!!nodeDetail.structure_results.length && (
            <details className="detail-disclosure" open>
              <summary><Route />结构结果分布 <span>{nodeDetail.structure_results.reduce((sum, row) => sum + row.records, 0)} 条</span><ChevronRight /></summary>
              <div className="structure-result-grid">{nodeDetail.structure_results.map((row) => <div key={`${row.target}-${row.lane}`}><b title={row.target}>{row.target}</b><span>{row.lane === 'native' ? '原位' : '错误口袋对照'}</span><strong>{row.records.toLocaleString()}</strong><small>{row.seeds} 个随机种子 · {row.kind === 'boltz_pose' ? '复合物构象' : '界面精修样本'}</small></div>)}</div>
            </details>
          )}
          {!!Object.keys(nodeDetail.metrics).length && <details className="detail-disclosure" open>
            <summary><Activity />计算指标 <span>{Object.keys(nodeDetail.metrics).length} 项</span><ChevronRight /></summary>
            <MetricTable metrics={nodeDetail.metrics} />
          </details>}
          {stageId === 'targets' && <details className="detail-disclosure" open><summary><Route />冻结靶点面板 <span>{detail.branches.length} 个靶点</span><ChevronRight /></summary><div className="detail-content">{detail.branches.map((branch) => <div className="branch-card target-branch-card" key={branch.key}><TargetGlyph /><div><b title="标准靶点名称保留数据库原始命名，以保证可追溯性。">{branch.target_name}</b><span title="生物学物种名称保留数据库原始命名，以保证可追溯性。">{branch.organism}</span><small>{branch.sequence_length} 个氨基酸 · 合格靶点 · {branch.accession}</small><details className="target-sequence"><summary>查看完整氨基酸序列 <ChevronRight /></summary><code>{branch.sequence}</code></details></div></div>)}</div></details>}
          {stageId === 'admission' && <details className="detail-disclosure"><summary><Layers3 />入选候选示例 <span>{detail.counts.admitted}</span><ChevronRight /></summary><div className="candidate-list detail-content">{detail.candidates.slice(0, 8).map((candidate) => <CandidateCard key={candidate.id} candidate={candidate} />)}</div></details>}
          {!!nodeDetail.calls.length && <details className="detail-disclosure" open>
            <summary><FileJson2 />工具运行记录 <span>{nodeDetail.calls.length}</span><ChevronRight /></summary>
            <div className="tool-attempt-list">
              {nodeDetail.calls.length ? nodeDetail.calls.slice(0, nodeDetail.calls.length > 8 ? 3 : 6).map((call) => <ToolAttemptDisclosure key={call.id} call={call} />) : <div className="empty-evidence">持久化工具调用：0</div>}
              {nodeDetail.calls.length > (nodeDetail.calls.length > 8 ? 3 : 6) && (
                <details className="remaining-attempts">
                  <summary>查看其余 {nodeDetail.calls.length - (nodeDetail.calls.length > 8 ? 3 : 6)} 次运行 <ChevronRight /></summary>
                  <div>{nodeDetail.calls.slice(nodeDetail.calls.length > 8 ? 3 : 6).map((call) => <ToolAttemptDisclosure key={call.id} call={call} />)}</div>
                </details>
              )}
            </div>
          </details>}
        </>
      )}
      <details className="detail-disclosure timeline-disclosure">
        <summary><Clock3 />数据库事件 <span>{detail.events.length}</span><ChevronRight /></summary>
        <div className="event-list detail-content">{detail.events.slice(0, 12).map((event) => <div className="event-row" key={event.sequence_no}><i /><div><b>{event.type.replaceAll('.', ' / ')}</b><span>{event.actor} · {formatTime(event.occurred_at)}</span></div></div>)}</div>
      </details>
    </aside>
  )
}

function EdgeInspector({ graph, edge, onClose }: { graph: RuntimeGraphModel; edge: GraphEdgeDetail; onClose: () => void }) {
  const source = graph.nodes.find((node) => node.id === edge.source)
  const target = graph.nodes.find((node) => node.id === edge.target)
  return (
    <aside className="inspector edge-inspector">
      <div className="inspector-header"><div><small>运行关系 · {edge.provenance === 'database' ? '数据库显式字段' : '图上聚合字段'}</small><h2>{edge.label ?? '运行关系'}</h2></div><button className="icon-button" aria-label="关闭运行关系详情" onClick={onClose}><X /></button></div>
      <section className="edge-route"><div><span>{source?.label ?? edge.source}</span><small>{source ? `${source.current.toLocaleString()} 条记录` : '节点未返回'}</small></div><i><Route /></i><div><span>{target?.label ?? edge.target}</span><small>{target ? statusText[target.status] : '节点未返回'}</small></div></section>
      <section className="inspector-section">
        <div className="analysis-kicker"><BrainCircuit />决策上下文</div>
        <p className="edge-rationale">{edge.rationale}</p>
        <div className="runtime-provenance-chip">{edge.provenance === 'database' ? '关系来自显式数据库字段' : '关系来自候选代际分组，仅用于阅读'}</div>
      </section>
    </aside>
  )
}

function RuntimeInspector({ detail, graph, nodeId, onClose }: { detail: RunDetail; graph: RuntimeGraphModel; nodeId: string; onClose: () => void }) {
  const node = graph.nodes.find((item) => item.id === nodeId)
  if (!node) return null
  const call = nodeId.startsWith('call:') ? graph.calls[nodeId.slice(5)] : undefined
  const event = nodeId.startsWith('event:') ? graph.events[nodeId.slice(6)] : undefined
  const candidate = nodeId.startsWith('candidate:') ? detail.candidates.find((item) => item.id === nodeId.slice(10)) : undefined
  const generation = nodeId.startsWith('generation:') ? nodeId.slice(11) : undefined
  return (
    <aside className="inspector expanded-inspector runtime-inspector">
      <div className="inspector-header">
        <div><small>运行节点 · 数据库直读</small><h2 title={node.label}>{node.label}</h2></div>
        <button className="icon-button" aria-label="关闭运行节点详情" onClick={onClose}><X /></button>
      </div>
      <section className={`scientific-summary grade-${node.insight.grade}`}>
        <div><small>{node.runtime?.node_type === 'tool_call' ? '工具调用事实' : node.runtime?.node_type === 'lifecycle_event' ? '生命周期事件事实' : '候选数据事实'}</small><strong><i />{node.insight.verdict}</strong></div>
        <p>{node.insight.reason}</p>
        <span>{node.insight.facts.map((fact) => `${fact.label} ${fact.value}`).join(' · ')}</span>
      </section>
      {call && <section className="inspector-section"><div className="section-title"><h3>工具调用与证据</h3><span className={`stage-badge ${node.status}`}>{statusText[call.status] ?? call.status}</span></div><ToolAttemptDisclosure call={call} /></section>}
      {event && <section className="inspector-section"><div className="analysis-kicker"><Clock3 />事件 payload</div><div className="runtime-event-meta"><b>{event.actor}</b><span>序号 {event.sequence_no} · {formatTime(event.occurred_at)}</span></div><pre className="runtime-json">{JSON.stringify(event.payload, null, 2)}</pre></section>}
      {candidate && <section className="inspector-section"><div className="analysis-kicker"><GitBranch />候选记录</div><code className="runtime-sequence">{candidate.sequence}</code><div className="fact-grid"><Fact label="代际" value={candidate.generation ?? '—'} /><Fact label="父候选" value={candidate.parent_id ?? '未返回'} /><Fact label="生成调用" value={candidate.generator_call_id ?? '未返回'} /><Fact label="序列长度" value={candidate.length} /></div></section>}
      {generation && <section className="inspector-section"><div className="analysis-kicker"><Layers3 />代际分组</div><p className="runtime-note">此节点由候选记录中明确的 <code>generation={generation}</code> 字段聚合而成；它不是预设阶段，也不代表执行依赖。</p></section>}
      <section className="inspector-section runtime-provenance"><div className="analysis-kicker"><Database />图构造契约</div><p>可见节点来自本次运行详情返回的工具调用、生命周期事件、候选记录和显式字段。未返回的依赖关系不在图中补画。</p><ul>{graph.gaps.slice(0, 5).map((gap) => <li key={gap}>{gap}</li>)}</ul></section>
    </aside>
  )
}

function TargetGlyph() {
  return <span className="target-glyph"><Box /></span>
}

function LoadingScreen({ error, onRetry, onOpenAnalysis }: { error: string | null; onRetry: () => void; onOpenAnalysis: () => void }) {
  return <div className="loading-screen">
    <div className="loading-mark"><FlaskConical /></div>
    <h2>{error ? '观察器接口暂时不可用' : '正在读取权威数据库…'}</h2>
    <p>{error ?? '同步实时运行记录'}</p>
    {error && <>
      <div className="service-status-grid" aria-label="实时服务状态">
        <div className="status-unavailable"><i /><b>观察器接口</b><span>不可达</span></div>
        <div className="status-unverified"><i /><b title="用于保存运行、候选与评测记录的权威数据库。">PostgreSQL 权威库</b><span>等待接口核验</span></div>
        <div className="status-unavailable"><i /><b title="用于追踪科学工作流进度的调度系统。">Temporal 可观测性</b><span>当前不可观测</span></div>
      </div>
      <small className="service-status-note">运行结论以 PostgreSQL 权威记录为准</small>
      <div className="loading-actions"><button onClick={onRetry}>重新读取</button><button className="primary" onClick={onOpenAnalysis}>查看冻结分析</button></div>
    </>}
  </div>
}

function DataConnectionDialog({ value, onClose, onSave }: { value: string; onClose: () => void; onSave: (value: string) => void }) {
  const [mode, setMode] = useState<'local' | 'custom'>(value ? 'custom' : 'local')
  const [customBase, setCustomBase] = useState(value || 'http://127.0.0.1:8081')
  const [testState, setTestState] = useState<'idle' | 'testing' | 'success' | 'error'>('idle')
  const [testMessage, setTestMessage] = useState('')
  const candidateBase = mode === 'local' ? '' : normalizeApiBase(customBase)

  const testConnection = async () => {
    setTestState('testing')
    setTestMessage('正在检查数据服务…')
    try {
      const response = await fetch(`${candidateBase}/healthz`, { headers: { Accept: 'application/json' } })
      if (!response.ok) throw new Error(`服务返回 ${response.status}`)
      const payload = await response.json() as { status?: string }
      if (payload.status !== 'ok') throw new Error('健康状态异常')
      setTestState('success')
      setTestMessage('观察器接口已响应；运行数据将在读取时核验。')
    } catch (cause) {
      setTestState('error')
      setTestMessage(cause instanceof Error && /^服务|^健康/.test(cause.message) ? cause.message : '无法连接此数据服务。')
    }
  }

  return createPortal(
    <div className="connection-dialog-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="connection-dialog" role="dialog" aria-modal="true" aria-labelledby="connection-dialog-title">
        <header><div><span className="connection-icon"><Database /></span><span><h2 id="connection-dialog-title">数据连接</h2><p>配置只读数据服务，不在浏览器保存数据库口令。</p></span></div><button aria-label="关闭数据连接设置" onClick={onClose}><X /></button></header>
        <div className="connection-options">
          <button className={mode === 'local' ? 'selected' : ''} onClick={() => { setMode('local'); setTestState('idle') }}>
            <i>{mode === 'local' && <span />}</i><span><b>本机默认</b><small>随开发命令自动启动</small><code>127.0.0.1:8081</code></span>
          </button>
          <button className={mode === 'custom' ? 'selected' : ''} onClick={() => { setMode('custom'); setTestState('idle') }}>
            <i>{mode === 'custom' && <span />}</i><span><b>自定义服务</b><small>连接其他只读数据接口</small><code>可配置地址</code></span>
          </button>
        </div>
        <label className="connection-field"><span>数据服务地址</span><input aria-label="数据服务地址" disabled={mode === 'local'} value={mode === 'local' ? 'http://127.0.0.1:8081' : customBase} onChange={(event) => { setCustomBase(event.target.value); setTestState('idle') }} /></label>
        <div className={`connection-test-state state-${testState}`}><span className="connection-status-dot" /><p>{testState === 'idle' ? '保存前可先检查服务与数据库是否可读。' : testMessage}</p></div>
        <footer><button onClick={testConnection} disabled={testState === 'testing'}><RefreshCw className={testState === 'testing' ? 'spin' : ''} />检查连接</button><span /><button onClick={onClose}>取消</button><button className="primary" disabled={mode === 'custom' && !normalizeApiBase(customBase)} onClick={() => onSave(candidateBase)}>保存并应用</button></footer>
      </section>
    </div>,
    document.body,
  )
}

export default function App() {
  const [activeView, setActiveView] = useState<'overview' | 'analysis' | 'evidence'>('overview')
  const [apiBase, setApiBase] = useState(readApiBase)
  const [connectionOpen, setConnectionOpen] = useState(false)
  const data = useRunData(true, apiBase)
  const [selectedStage, setSelectedStage] = useState<string | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<GraphEdgeDetail | null>(null)
  const [selectionMode, setSelectionMode] = useState(false)
  const [analysisSelection, setAnalysisSelection] = useState<string[]>([])
  const [analysisSnapshot, setAnalysisSnapshot] = useState<AnalysisSnapshot | null>(null)
  const [persistedDistributions, setPersistedDistributions] = useState<Record<string, ResultDistributionData>>({})
  const structureRun = useMemo(() => data.runs.find((run) => run.structure_record_count > 0) ?? null, [data.runs])
  const runtimeGraph = useMemo(() => data.detail ? buildRuntimeGraph(data.detail, data.nodeDetails) : null, [data.detail, data.nodeDetails])
  useEffect(() => {
    let cancelled = false
    const liveAnalyticsEnabled = import.meta.env.VITE_ANALYTICS_API_ENABLED === 'true'
    void loadAnalysisSnapshot({ runId: liveAnalyticsEnabled ? data.detail?.run.id : undefined, apiBase }).then((snapshot) => {
      if (!cancelled) setAnalysisSnapshot(snapshot)
    }).catch(() => {
      if (!cancelled) setAnalysisSnapshot(null)
    })
    return () => { cancelled = true }
  }, [apiBase, data.detail?.run.id])
  useEffect(() => {
    const detail = data.detail
    if (!detail) {
      setPersistedDistributions({})
      return
    }
    // Runtime overview nodes already carry their own observed facts. Do not
    // eagerly dereference every artifact just to fill the legacy distribution
    // widget; missing artifact storage must remain an explicit user action.
    setPersistedDistributions({})
  }, [apiBase, data.detail?.run.id])
  const selectedAnalysisNodes = useMemo(() => runtimeGraph?.nodes.filter((node) => analysisSelection.includes(node.id)) ?? [], [analysisSelection, runtimeGraph])
  const toggleAnalysisNode = (id: string) => setAnalysisSelection((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])
  return (
    <div className="app-shell">
      <div className="topbar"><button><ArrowLeft /></button><div className="brand"><span><FlaskConical /></span>AMPgent <i>科学分析</i></div><button className={`source-state ${activeView === 'overview' && data.error ? 'has-error' : ''}`} onClick={() => setConnectionOpen(true)} title="查看或修改只读数据连接"><Database /><span>{activeView !== 'overview' ? '分析数据 · 只读' : data.detail ? '数据库已连接' : data.error ? '观察器不可用' : '正在连接'}</span><span className="live-dot" /><Settings2 /></button></div>
      <div className="workspace">
        <Sidebar
          runs={data.runs}
          selectedId={data.selectedId}
          structureRun={structureRun}
          activeView={activeView}
          onView={(view) => { setActiveView(view); setSelectedStage(null); setSelectedEdge(null) }}
          onSelect={(id) => { data.setSelectedId(id); setSelectedStage(null); setSelectedEdge(null); setAnalysisSelection([]); setSelectionMode(false) }}
          onOpenStructureEvidence={() => {
            if (!structureRun) return
            data.setSelectedId(structureRun.id)
            setActiveView('overview')
            setSelectedStage('boltz')
            setSelectedEdge(null)
            setAnalysisSelection([])
            setSelectionMode(false)
          }}
        />
        {activeView === 'analysis' ? (
          <AnalysisDashboard detail={data.detail} seedNodeIds={analysisSelection} apiBase={apiBase} />
        ) : activeView === 'evidence' ? (
          <EvidenceDashboard runId={data.detail?.run.id} />
        ) : data.detail && !data.loading ? (
          <>
            <main className="main-canvas">
              <CanvasHeader
                detail={data.detail}
                refreshing={data.refreshing}
                selectionMode={selectionMode}
                selectedCount={analysisSelection.length}
                onRefresh={data.refresh}
                onToggleSelection={() => {
                  setSelectionMode((value) => !value)
                  setSelectedStage(null)
                  setSelectedEdge(null)
                }}
              />
              <GraphView
                detail={data.detail}
                runtimeGraph={runtimeGraph!}
                analysisSnapshot={analysisSnapshot}
                persistedDistributions={persistedDistributions}
                selectedStage={selectedStage}
                selectedEdge={selectedEdge}
                selectionMode={selectionMode}
                analysisSelection={analysisSelection}
                onSelect={(id) => { setSelectedStage(id); setSelectedEdge(null) }}
                onToggleAnalysis={toggleAnalysisNode}
                onSelectEdge={(edge) => { setSelectedEdge(edge); setSelectedStage(null) }}
              />
              {selectionMode && (
                <div className="analysis-selection-bar">
                  <div className="selection-summary">
                    <ChartNoAxesCombined />
                    <span><b>组合分析</b><small>{analysisSelection.length ? '已按节点语义准备分析条件' : '选择需要联合分析的节点'}</small></span>
                  </div>
                  <div className="selection-chips">
                    {selectedAnalysisNodes.map((node) => <button key={node.id} onClick={() => toggleAnalysisNode(node.id)}>{node.label}<X /></button>)}
                    {!selectedAnalysisNodes.length && <span>可连续选择多张流程卡片</span>}
                  </div>
                  {!!analysisSelection.length && <button className="clear-selection" onClick={() => setAnalysisSelection([])}>清除</button>}
                  <button className="build-analysis" disabled={!analysisSelection.length} onClick={() => { setActiveView('analysis'); setSelectionMode(false) }}>生成分析卡片</button>
                </div>
              )}
              {!selectionMode && <div className="canvas-footnote"><PanelLeftClose />拖拽画布 · 点击节点 · 5 秒更新</div>}
            </main>
            {selectedStage && <RuntimeInspector detail={data.detail} graph={runtimeGraph!} nodeId={selectedStage} onClose={() => setSelectedStage(null)} />}
            {selectedEdge && <EdgeInspector graph={runtimeGraph!} edge={selectedEdge} onClose={() => setSelectedEdge(null)} />}
          </>
        ) : (
          <main className="main-canvas"><LoadingScreen error={data.error} onRetry={() => { void data.retry() }} onOpenAnalysis={() => setActiveView('analysis')} /></main>
        )}
      </div>
      {connectionOpen && <DataConnectionDialog value={apiBase} onClose={() => setConnectionOpen(false)} onSave={(value) => { window.localStorage.setItem(connectionStorageKey, value); setApiBase(value); setConnectionOpen(false) }} />}
    </div>
  )
}
