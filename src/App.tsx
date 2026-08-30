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
import {
  distributionForStage,
  loadPersistedRunDistributions,
  ResultDistribution,
  type ResultDistributionData,
} from './ResultDistribution'
import { LaneLabel, WorkflowNode, type LaneNode, type StageNode } from './WorkflowNode'
import type {
  CandidatePreview,
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
const nodePositions: Record<string, { x: number; y: number }> = {
  target_data: { x: 20, y: 350 },
  knowledge: { x: 440, y: 350 },
  amp_designer: { x: 860, y: 40 },
  ampgan: { x: 860, y: 290 },
  hydramp: { x: 860, y: 540 },
  candidate_pool: { x: 1280, y: 350 },
  mic: { x: 1700, y: -100 },
  amp_read: { x: 1700, y: 150 },
  hemolysis: { x: 1700, y: 400 },
  toxicity: { x: 1700, y: 650 },
  developability: { x: 1700, y: 900 },
  admission: { x: 2120, y: 350 },
  targets: { x: 2540, y: 350 },
  boltz: { x: 2960, y: 305 },
  rosetta: { x: 3380, y: 305 },
  portfolio: { x: 3800, y: 350 },
}
const laneLabels: Array<{ id: string; label: string; x: number }> = [
  { id: 'data', label: '靶点数据', x: 20 },
  { id: 'knowledge', label: '知识证据', x: 440 },
  { id: 'design', label: '设计模型', x: 860 },
  { id: 'candidates', label: '候选集合', x: 1280 },
  { id: 'evaluation', label: '模型评估', x: 1700 },
  { id: 'decision', label: '候选决策', x: 2120 },
  { id: 'targets', label: '靶点分派', x: 2540 },
  { id: 'boltz', label: 'Boltz 2', x: 2960 },
  { id: 'rosetta', label: 'Rosetta', x: 3380 },
  { id: 'review', label: '科学评审', x: 3800 },
]

const statusText: Record<string, string> = {
  created: '已创建', submitted: '已提交', running: '运行中', succeeded: '已完成', failed: '失败', cancelled: '已取消',
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

function runTitle(run: RunListItem) {
  if (run.status === 'running') return `正在运行 · ${run.candidate_count.toLocaleString()} 条候选`
  if (run.structure_record_count > 0) return `结构证据轮次 · ${run.structure_record_count.toLocaleString()} 条记录`
  return `序列设计轮次 · ${run.candidate_count.toLocaleString()} 条候选`
}

function readableDataError(cause: unknown, fallback: string) {
  if (cause instanceof Error && /^(轮次|数据库|无法)/.test(cause.message)) return cause.message
  return fallback
}

function readableTargetName(value: unknown) {
  const name = typeof value === 'string' ? value : ''
  if (name.includes('PBP2a')) return 'PBP2a耐β-内酰胺转肽酶'
  if (name.includes('DNA gyrase')) return 'DNA旋转酶A亚基'
  return name || '结构靶点'
}

function reconcilePersistedStructure(detail: RunDetail, nodeDetails: Partial<Record<'boltz' | 'rosetta', NodeDetail>>) {
  const createdPayload = detail.events.find((event) => event.type === 'run.created')?.payload ?? {}
  const target = recordValue(createdPayload.target)
  const sourceCandidateIds = Array.isArray(createdPayload.source_candidate_ids) ? createdPayload.source_candidate_ids : []
  const successfulCalls = (node: NodeDetail | undefined) => node?.calls.filter((call) => call.status === 'succeeded') ?? []
  const boltzCalls = successfulCalls(nodeDetails.boltz)
  const rosettaCalls = successfulCalls(nodeDetails.rosetta)
  if (!boltzCalls.length && !rosettaCalls.length) return detail
  const boltzStructures = boltzCalls.flatMap((call) => call.artifacts)
    .filter((artifact) => artifact.media_type === 'chemical/x-cif' || artifact.media_type === 'chemical/x-pdb').length
  const rosettaPdbArtifacts = rosettaCalls.flatMap((call) => call.artifacts)
    .filter((artifact) => artifact.media_type === 'chemical/x-pdb').length
  const persistedRosettaResults = nodeDetails.rosetta?.structure_results
    .reduce((total, result) => total + result.records, 0) ?? 0
  const rosettaAnalyzerCalls = rosettaCalls.filter((call) => call.tool_name.includes('interface-analyzer'))
  const plannedRosettaResults = rosettaAnalyzerCalls.reduce((total, call) => {
    const nstruct = Number(recordValue(call.inputs).nstruct)
    return total + (Number.isFinite(nstruct) ? nstruct : 0)
  }, 0)
  const rosettaStructures = persistedRosettaResults || plannedRosettaResults || rosettaPdbArtifacts

  const stageUpdates: Partial<Record<string, { current: number; total: number; verdict: string; reason: string }>> = {
    boltz: boltzCalls.length ? {
      current: boltzCalls.length,
      total: nodeDetails.boltz?.calls.length ?? boltzCalls.length,
      verdict: '复合物构象已写入',
      reason: `${boltzCalls.length} 次结构预测均可追溯至节点明细。`,
    } : undefined,
    rosetta: rosettaCalls.length ? {
      current: rosettaStructures || rosettaCalls.length,
      total: rosettaStructures || rosettaCalls.length,
      verdict: '界面精修已写入',
      reason: `${rosettaAnalyzerCalls.length || rosettaCalls.length} 次界面评估生成 ${rosettaStructures} 份精修构象。`,
    } : undefined,
  }
  if (target.name) {
    stageUpdates.targets = {
      current: 1,
      total: 1,
      verdict: '靶点口袋已锁定',
      reason: `${readableTargetName(target.name)} · ${Array.isArray(target.pocket_residues) ? target.pocket_residues.length : 0} 个口袋残基。`,
    }
  }
  if (sourceCandidateIds.length) {
    stageUpdates.candidate_pool = {
      current: sourceCandidateIds.length,
      total: sourceCandidateIds.length,
      verdict: '结构候选已载入',
      reason: `${sourceCandidateIds.length} 条来源候选进入本轮结构复核。`,
    }
  }

  const graphNodes = detail.graph.nodes.map((stage) => {
    const update = stageUpdates[stage.id]
    if (!update || (stage.current > 0 && stage.status !== 'pending')) return stage
    return {
      ...stage,
      status: 'completed' as const,
      current: update.current,
      total: update.total,
      provenance: 'database' as const,
      insight: {
        ...stage.insight,
        grade: 'good' as const,
        verdict: update.verdict,
        reason: update.reason,
        facts: [
          { label: '成功', value: String(update.current) },
          { label: '记录', value: String(update.total) },
          { label: '来源', value: '节点明细' },
        ],
        source: 'observer_summary' as const,
      },
    }
  })

  const viewerFromCall = (call: ToolAttempt | undefined): ViewerArtifact | null => {
    if (!call) return null
    const artifact = call.artifacts.find((item) => item.media_type === 'chemical/x-cif' || item.media_type === 'chemical/x-pdb')
    if (!artifact) return null
    const inputs = recordValue(call.inputs)
    const sequence = typeof inputs.peptide_sequence === 'string' ? inputs.peptide_sequence : ''
    return {
      candidate_id: `${detail.run.id}:${call.id}`,
      sequence,
      target_id: typeof createdPayload.target_id === 'string' ? createdPayload.target_id : '',
      target_name: readableTargetName(target.name),
      lane: 'native',
      seed: call.random_seed ?? 0,
      artifact_sha256: artifact.sha256,
      media_type: artifact.media_type,
      artifact_url: artifact.url,
    }
  }
  const persistedViewer = viewerFromCall(boltzCalls[0])
  const viewers = { ...detail.viewers }
  if (persistedViewer && !viewers.boltz) viewers.boltz = persistedViewer
  if (persistedViewer && !viewers.rosetta) viewers.rosetta = persistedViewer

  const persistedBranch = target.name ? {
    order: 1,
    key: typeof createdPayload.target_branch_key === 'string' ? createdPayload.target_branch_key : '结构靶点',
    role: '原位口袋',
    status: 'completed',
    target_id: typeof createdPayload.target_id === 'string' ? createdPayload.target_id : '',
    target_name: readableTargetName(target.name),
    organism: typeof target.organism === 'string' ? target.organism : null,
    accession: typeof target.accession === 'string' ? target.accession : null,
    sequence: typeof target.sequence === 'string' ? target.sequence : '',
    sequence_length: typeof target.sequence === 'string' ? target.sequence.length : 0,
    evidence_namespace: typeof target.source_database === 'string' ? target.source_database : '数据库靶点记录',
    coordinate_sha256: '',
  } : null

  return {
    ...detail,
    run: {
      ...detail.run,
      structure_record_count: Math.max(
        Number.isFinite(detail.run.structure_record_count) ? detail.run.structure_record_count : 0,
        boltzStructures + rosettaPdbArtifacts,
      ),
    },
    counts: {
      ...detail.counts,
      admitted: (detail.counts.admitted ?? 0) > 0 ? detail.counts.admitted : sourceCandidateIds.length,
      boltz_poses: (detail.counts.boltz_poses ?? 0) > 0 ? detail.counts.boltz_poses : boltzStructures,
      rosetta_decoys: (detail.counts.rosetta_decoys ?? 0) > 0 ? detail.counts.rosetta_decoys : rosettaStructures,
    },
    branches: detail.branches.length || !persistedBranch ? detail.branches : [persistedBranch],
    graph: { ...detail.graph, nodes: graphNodes },
    viewer: detail.viewer ?? persistedViewer,
    viewers,
  }
}

function useRunData(enabled: boolean, apiBase: string) {
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [selectedId, setSelectedIdState] = useState<string | null>(() => window.localStorage.getItem(selectedRunStorageKey))
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const alignedStructureCounts = useRef<Record<string, number>>({})

  const loadRuns = useCallback(async () => {
    const response = await fetch(`${apiBase}/v1/observer/runs?limit=100`)
    if (!response.ok) throw new Error(`轮次列表读取失败：${response.status}`)
    const payload = await response.json() as RunListResponse
    setRuns(payload.runs.map((run) => ({
      ...run,
      structure_record_count: Math.max(run.structure_record_count, alignedStructureCounts.current[run.id] ?? 0),
    })))
    setSelectedIdState((current) => {
      const next = current && payload.runs.some((run) => run.id === current) ? current : payload.runs[0]?.id ?? null
      if (next) window.localStorage.setItem(selectedRunStorageKey, next)
      return next
    })
  }, [apiBase])

  const loadDetail = useCallback(async (runId: string, quiet = false) => {
    if (!quiet) setLoading(true)
    else setRefreshing(true)
    try {
      const response = await fetch(`${apiBase}/v1/observer/runs/${runId}`)
      if (!response.ok) throw new Error(`轮次详情读取失败：${response.status}`)
      const payload = await response.json() as RunDetail
      const hasPersistedStructureEvents = payload.events.some((event) => event.actor === 'boltz2' || event.actor.includes('rosetta'))
      if (!hasPersistedStructureEvents) {
        setDetail(payload)
      } else {
        const entries = await Promise.all((['boltz', 'rosetta'] as const).map(async (nodeId) => {
          const nodeResponse = await fetch(`${apiBase}/v1/observer/runs/${runId}/nodes/${nodeId}`)
          return [nodeId, nodeResponse.ok ? await nodeResponse.json() as NodeDetail : undefined] as const
        }))
        const reconciled = reconcilePersistedStructure(payload, Object.fromEntries(entries))
        setDetail(reconciled)
        if (reconciled.run.structure_record_count !== payload.run.structure_record_count) {
          alignedStructureCounts.current[runId] = reconciled.run.structure_record_count
          setRuns((current) => current.map((run) => run.id === runId
            ? { ...run, structure_record_count: reconciled.run.structure_record_count }
            : run))
        }
      }
      setError(null)
    } catch (cause) {
      setError(readableDataError(cause, '无法连接观察器接口'))
    } finally {
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

  return { runs, selectedId, setSelectedId, detail, error, loading, refreshing, retry, refresh: () => selectedId && loadDetail(selectedId, true) }
}

function RunList({ runs, selectedId, onSelect }: { runs: RunListItem[]; selectedId: string | null; onSelect: (id: string) => void }) {
  if (!runs.length) {
    return <div className="run-list"><div className="run-row active frozen-run-row"><span className="run-status-dot status-cancelled" /><span className="run-row-copy"><strong>发布冻结轮次 · 773 条候选</strong><small>8月19日 21:46 · 已取消</small></span></div></div>
  }
  return (
    <div className="run-list">
      {runs.map((run) => (
        <button key={run.id} className={`run-row ${run.id === selectedId ? 'active' : ''}`} onClick={() => onSelect(run.id)}>
          <span className={`run-status-dot status-${run.status}`} />
          <span className="run-row-copy">
            <strong>{runTitle(run)}</strong>
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
  return (
    <header className="canvas-header">
      <div className="canvas-title-block">
        <div className="eyebrow"><span>轮次 · 正式科学运行</span></div>
        <h1>{isStructureReview ? '短肽结构证据复核' : '序列优先的短肽设计'}</h1>
        <div className="round-meta">
          <span>{formatTime(detail.run.created_at)} 创建</span><i />
          <span>{displayCandidateCount.toLocaleString()} 个候选</span><i />
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
        <span className={`run-pill status-${detail.run.status}`}><i />{statusText[detail.run.status] ?? detail.run.status}</span>
        <button className="icon-button" onClick={onRefresh} title="立即刷新"><RefreshCw className={refreshing ? 'spin' : ''} /></button>
        <button className="icon-button"><Ellipsis /></button>
      </div>
    </header>
  )
}

function GraphView({
  detail,
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
    ...laneLabels.map((lane, index): LaneNode => ({
      id: `lane-${lane.id}`,
      type: 'lane',
      position: { x: lane.x, y: -68 },
      data: { label: lane.label, index: String(index + 1).padStart(2, '0') },
      draggable: false,
      selectable: false,
      focusable: false,
    })),
    ...detail.graph.nodes.map((stage): StageNode => ({
      id: stage.id,
      type: 'stage',
      position: nodePositions[stage.id] ?? { x: 0, y: 0 },
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
  ], [analysisSelection, analysisSnapshot, detail, persistedDistributions, selectedStage, selectionMode])
  const stageById = useMemo(() => Object.fromEntries(detail.graph.nodes.map((node) => [node.id, node])), [detail])
  const edges = useMemo<Edge[]>(() => detail.graph.edges.map((edge, index) => {
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
      label: edge.label ?? undefined,
      labelStyle: { fill: '#536176', fontSize: 8, fontWeight: 600 },
      labelBgStyle: { fill: '#ffffff', fillOpacity: 0.94 },
      labelBgPadding: [6, 4],
      labelBgBorderRadius: 5,
      data: { detail: edge },
      markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: isSelected ? '#2257ee' : active ? '#111827' : '#cbd1dc' },
      style: { stroke: isSelected ? '#2257ee' : active ? '#111827' : '#cbd1dc', strokeWidth: isSelected ? 2.8 : active ? 1.8 : 1.3 },
    }
  }), [detail.graph.edges, selectedEdge, stageById])
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
        defaultViewport={{ x: 22, y: 92, zoom: 0.82 }}
        minZoom={0.34}
        maxZoom={1.25}
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
        elementsSelectable
      >
        <Background variant={BackgroundVariant.Dots} gap={26} size={1} color="#e8ebf1" />
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
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
      <div><span className="cohort-chip">{cohort}</span><small>{candidate.length} 个氨基酸</small></div>
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

function Inspector({ detail, stageId, analysisSnapshot, distributionOverride, onClose }: { detail: RunDetail; stageId: string; analysisSnapshot: AnalysisSnapshot | null; distributionOverride?: ResultDistributionData; onClose: () => void }) {
  const stage = detail.graph.nodes.find((item) => item.id === stageId) ?? detail.graph.nodes[0]
  const groupLabel = { inputs: '输入', design: '设计', evaluation: '评估', decision: '决策', structure: '结构', review: '评审' }[stage.group]
  const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
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

function EdgeInspector({ detail, edge, onClose }: { detail: RunDetail; edge: GraphEdgeDetail; onClose: () => void }) {
  const source = detail.graph.nodes.find((node) => node.id === edge.source)
  const target = detail.graph.nodes.find((node) => node.id === edge.target)
  return (
    <aside className="inspector edge-inspector">
      <div className="inspector-header"><div><small>决策证据边 · 格式化上下文</small><h2>{edge.label ?? '证据依赖关系'}</h2></div><button className="icon-button" onClick={onClose}><X /></button></div>
      <section className="edge-route"><div><span>{source?.label}</span><small>{source?.current.toLocaleString()} 条已持久化</small></div><i><Route /></i><div><span>{target?.label}</span><small>{target ? statusText[target.status] : '—'}</small></div></section>
      <section className="inspector-section">
        <div className="analysis-kicker"><BrainCircuit />决策上下文</div>
        <p className="edge-rationale">{edge.rationale}</p>
      </section>
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
  const [activeView, setActiveView] = useState<'overview' | 'analysis' | 'evidence'>('analysis')
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
    const controller = new AbortController()
    setPersistedDistributions({})
    void loadPersistedRunDistributions(apiBase, detail, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setPersistedDistributions(value) })
      .catch(() => { if (!controller.signal.aborted) setPersistedDistributions({}) })
    return () => controller.abort()
  }, [apiBase, data.detail?.run.id])
  const selectedAnalysisNodes = useMemo(() => data.detail?.graph.nodes.filter((node) => analysisSelection.includes(node.id)) ?? [], [analysisSelection, data.detail])
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
            {selectedStage && <Inspector detail={data.detail} stageId={selectedStage} analysisSnapshot={analysisSnapshot} distributionOverride={persistedDistributions[selectedStage]} onClose={() => setSelectedStage(null)} />}
            {selectedEdge && <EdgeInspector detail={data.detail} edge={selectedEdge} onClose={() => setSelectedEdge(null)} />}
          </>
        ) : (
          <main className="main-canvas"><LoadingScreen error={data.error} onRetry={() => { void data.retry() }} onOpenAnalysis={() => setActiveView('analysis')} /></main>
        )}
      </div>
      {connectionOpen && <DataConnectionDialog value={apiBase} onClose={() => setConnectionOpen(false)} onSave={(value) => { window.localStorage.setItem(connectionStorageKey, value); setApiBase(value); setConnectionOpen(false) }} />}
    </div>
  )
}
