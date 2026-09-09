import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import {
  Atom,
  Braces,
  Check,
  CircleStop,
  Database,
  Layers3,
  Orbit,
  ScanSearch,
  Sparkles,
  BrainCircuit,
  BookOpenText,
  Gauge,
  ShieldCheck,
  Target,
  ChevronRight,
} from 'lucide-react'
import { MoleculeViewer } from './MoleculeViewer'
import { ResultDistribution, type ResultDistributionData } from './ResultDistribution'
import { qualityGateNodeSummary } from './generationQualityGate'
import type { Branch, GraphStage, ViewerArtifact } from './types'

export interface StageNodeData extends Record<string, unknown> {
  stage: GraphStage
  branches: Branch[]
  viewer: ViewerArtifact | null
  selected: boolean
  distribution: ResultDistributionData | null
  onToggleGroup?: (id: string) => void
}

export type StageNode = Node<StageNodeData, 'stage'>

export interface LaneNodeData extends Record<string, unknown> {
  label: string
  index: string
  description?: string
}

export type LaneNode = Node<LaneNodeData, 'lane'>

export function LaneLabel({ data }: NodeProps<LaneNode>) {
  const description = data.label.startsWith('Boltz')
    ? 'Boltz 2用于预测蛋白质与短肽复合物的三维构象。'
    : data.label.startsWith('Rosetta')
      ? 'Rosetta用于采样并评估蛋白质与短肽的界面构象。'
      : undefined
  return <div className="lane-label" title={description}><span>{data.index}</span><div><b>{data.label}</b>{data.description && <small>{data.description}</small>}</div></div>
}

const iconById = {
  target_data: Database,
  knowledge: BookOpenText,
  amp_designer: Sparkles,
  ampgan: BrainCircuit,
  hydramp: BrainCircuit,
  candidate_pool: Database,
  mic: Gauge,
  amp_read: ScanSearch,
  hemolysis: ScanSearch,
  toxicity: ScanSearch,
  developability: ScanSearch,
  admission: Braces,
  targets: Target,
  boltz: Orbit,
  rosetta: Atom,
  portfolio: Layers3,
}

const termDescriptions: Record<string, string> = {
  amp_designer: 'AMP Designer用于基于模型生成抗菌短肽候选序列。',
  ampgan: 'AMPGAN v2是用于生成抗菌肽候选的对抗生成模型。',
  hydramp: 'HydrAMP用于生成并优化抗菌肽候选序列。',
  amp_read: 'AMP read用于交叉复核候选短肽的抗菌活性预测。',
  boltz: 'Boltz 2用于预测蛋白质与短肽复合物的三维构象。',
  rosetta: 'Rosetta用于采样并评估蛋白质与短肽的界面构象。',
}

const runtimeTermDescriptions: Record<string, string> = {
  amp_designer: 'AMP Designer：基于模型生成抗菌短肽候选序列。',
  ampgan: 'AMPGAN v2：用于生成抗菌肽候选的对抗生成模型。',
  hydramp: 'HydrAMP：用于生成并优化抗菌肽候选序列。',
  amp_read: 'AMP read：交叉复核候选短肽的抗菌活性预测。',
  'v38-metric-mic_potency': 'MIC：估计候选短肽的最小抑菌浓度。',
  'v38-metric-mic_potency_amp_read': 'AMP read：交叉复核最小抑菌浓度。',
  'v38-metric-hemolysis_risk': '溶血风险：观察红细胞相容性信号。',
  boltz: 'Boltz 2：预测蛋白质与短肽复合物的三维构象。',
  rosetta: 'Rosetta：采样并评估蛋白质与短肽的界面构象。',
}

function compactRuntimeTitle(label: string) {
  return label
    .replace(/^智能体决策已记录(?=\s*·)/, '智能体决策')
    .replace(/(\d+)\s*次调用/g, '$1次')
}

export function WorkflowNode({ data }: NodeProps<StageNode>) {
  const { stage, branches, viewer, selected, distribution, onToggleGroup } = data
  const progress = stage.total > 0 ? Math.min(100, Math.round((stage.current / stage.total) * 100)) : 0
  const stateIcon = stage.status === 'completed' ? <Check /> : stage.status === 'stopped' ? <CircleStop /> : null
  const isStructure = stage.kind === 'structure'
  const isRuntime = Boolean(stage.runtime)
  const runtimeType = stage.runtime?.node_type ?? 'stage'
  const Icon = runtimeType === 'structure_evidence' ? Orbit : iconById[stage.id as keyof typeof iconById] ?? Database
  const hasStructureEvidence = isStructure || Boolean(stage.runtime?.has_viewer)
  const isCandidatePreview = runtimeType === 'candidate_group' || runtimeType === 'candidate_preview'
  const isRuntimeGroup = runtimeType === 'tool_group' || runtimeType === 'event_group' || runtimeType === 'batch_group' || runtimeType === 'tool_summary_group' || runtimeType === 'candidate_group'
  const isScientificStage = runtimeType === 'scientific_stage'
  const runtimeTitle = stage.runtime?.tool_name
    ? `${runtimeTermDescriptions[stage.runtime.tool_name] ?? '工具调用事实'} 原始键：${stage.runtime.tool_name}`
    : stage.runtime?.raw_label ? `原始键：${stage.runtime.raw_label}` : termDescriptions[stage.id]
  const showsTargets = stage.id === 'targets' && branches.length > 0
  const qualityGate = stage.id === 'candidate_pool' ? stage.generation_quality_gate : undefined
  const hasEvidenceDistribution = Boolean(distribution?.values.length)
  const compactFacts = stage.insight.facts
    .filter((fact) => !/^(0\s*\/\s*0|0\/0|暂无结果|—)$/.test(String(fact.value).trim()))
    .filter((fact) => !isRuntime || !['序号', '运行参与者', '语义'].includes(fact.label))
    // Runtime titles already carry an explicit round/generation when the
    // backend supplied it. Do not render that same persisted fact twice.
    .filter((fact) => !(isRuntime
      && ['代际', '轮次', '尝试'].includes(fact.label)
      && stage.label.includes(String(fact.value))))
    .slice(0, isRuntime ? 1 : 2)
  const hasPrimaryVisual = hasStructureEvidence || hasEvidenceDistribution || showsTargets
  const showCompactFacts = compactFacts.length > 0 && (!isRuntime || isScientificStage || (!hasPrimaryVisual && !isRuntimeGroup && runtimeType !== 'tool_summary'))
  // Runtime titles already carry the persisted event/tool meaning. Repeating
  // the same verdict or generation inside the card makes one observation
  // look like two independent facts.
  const showVerdict = !isRuntime && !hasEvidenceDistribution
  const visibleTitle = isRuntime ? compactRuntimeTitle(stage.label) : stage.label
  return (
    <div className={`workflow-node stage-${stage.id} kind-${stage.kind} grade-${stage.insight.grade} node-${stage.status}${isRuntime ? ` is-runtime-node runtime-${runtimeType}${isRuntimeGroup && stage.runtime?.expanded ? ' runtime-group-expanded' : ''}` : ''}${selected ? ' is-selected' : ''}`}>
      {isRuntimeGroup && <span className="runtime-group-hit-area" aria-hidden="true" />}
      {isRuntimeGroup && <span className="runtime-group-front-surface" aria-hidden="true" />}
      <Handle type="target" position={Position.Left} className="flow-handle" />
      <div className="node-heading">
        <span className="node-icon"><Icon /></span>
        <span className="node-title" title={runtimeTitle}>{visibleTitle}</span>
        <span className="node-state-icon">{stage.insight.grade === 'neutral' ? null : stateIcon}</span>
      </div>
      {showVerdict && <div
          className="node-verdict"
          title={stage.insight.source === 'persisted_decision' ? '来自数据库中的智能体决策' : '根据数据库结果生成的节点结论'}
        >
          <span className={`verdict-chip ${stage.insight.grade}`}><i />{stage.insight.verdict}</span>
          <b title={stage.insight.reason}>{stage.insight.reason}</b>
        </div>}
      {qualityGate && (
        <div className={`node-quality-gate state-${qualityGate.status}`} title="计数仅来自当前数据库运行；规则提案、谱系入库和完成评估分别统计。">
          <span><ShieldCheck />新生序列</span>
          <b>{qualityGateNodeSummary(qualityGate)}</b>
        </div>
      )}
      {hasStructureEvidence && viewer && stage.runtime?.viewer_key !== 'rosetta' && <MoleculeViewer key={viewer.artifact_sha256} artifact={viewer} compact interactive={false} />}
      {hasStructureEvidence && viewer && stage.runtime?.viewer_key === 'rosetta' && <div className="structure-unavailable-chip" title="Rosetta 结构文件由只读接口返回，但当前制品不可读取；打开详情可重试。">结构文件暂不可读</div>}
      {distribution && (!isRuntime || hasEvidenceDistribution) && <ResultDistribution data={distribution} compact />}
      {showsTargets ? (
        <div className="node-targets">
          {branches.slice(0, 2).map((branch) => (
            <span key={branch.key}>
              <small title={`标准靶点名称：${branch.target_name}；保留原始命名以保证可追溯性。`}>{branch.target_name}</small>
              <code title={branch.sequence}>{branch.sequence.slice(0, 12)}…{branch.sequence.slice(-6)}</code>
              <i>{branch.sequence_length} 个氨基酸</i>
            </span>
          ))}
        </div>
      ) : showCompactFacts ? (
        <div className="node-facts">
          {compactFacts.map((fact) => (
            <span key={fact.label}><small>{fact.label}</small><strong title={fact.value}>{fact.value}</strong></span>
          ))}
        </div>
      ) : null}
      {!isRuntime && <div className="node-meta">
        <span>{`${stage.current.toLocaleString()} / ${stage.total.toLocaleString()}`}</span>
        {hasEvidenceDistribution && <span className={`evidence-chip ${stage.provenance}`}>结果分布</span>}
      </div>}
      {isRuntimeGroup && onToggleGroup && <button className="node-group-toggle" onClick={(event) => { event.stopPropagation(); onToggleGroup(stage.id) }}><span>{stage.runtime?.expanded ? '收起明细' : runtimeType === 'tool_summary_group' ? `展开 ${stage.runtime?.child_ids?.length ?? 0} 类工具 · ${stage.total} 次调用` : runtimeType === 'candidate_group' ? `展开 ${stage.runtime?.child_ids?.length ?? 0} 条预览` : `展开 ${(stage.runtime?.child_ids?.length ?? 0) + (stage.runtime?.event_ids?.length ?? 0)} 项观测`}</span><ChevronRight /></button>}
      {!isRuntime && <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>}
      <Handle type="source" position={Position.Right} className="flow-handle" />
    </div>
  )
}
