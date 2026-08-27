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
  Target,
} from 'lucide-react'
import { MoleculeViewer } from './MoleculeViewer'
import { ResultDistribution, type ResultDistributionData } from './ResultDistribution'
import type { Branch, GraphStage, ViewerArtifact } from './types'

export interface StageNodeData extends Record<string, unknown> {
  stage: GraphStage
  branches: Branch[]
  viewer: ViewerArtifact | null
  selected: boolean
  distribution: ResultDistributionData | null
}

export type StageNode = Node<StageNodeData, 'stage'>

export interface LaneNodeData extends Record<string, unknown> {
  label: string
  index: string
}

export type LaneNode = Node<LaneNodeData, 'lane'>

export function LaneLabel({ data }: NodeProps<LaneNode>) {
  const description = data.label.startsWith('Boltz')
    ? 'Boltz 2用于预测蛋白质与短肽复合物的三维构象。'
    : data.label.startsWith('Rosetta')
      ? 'Rosetta用于采样并评估蛋白质与短肽的界面构象。'
      : undefined
  return <div className="lane-label" title={description}><span>{data.index}</span>{data.label}</div>
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

export function WorkflowNode({ data }: NodeProps<StageNode>) {
  const { stage, branches, viewer, selected, distribution } = data
  const Icon = iconById[stage.id as keyof typeof iconById] ?? Database
  const progress = stage.total > 0 ? Math.min(100, Math.round((stage.current / stage.total) * 100)) : 0
  const stateIcon = stage.status === 'completed' ? <Check /> : stage.status === 'stopped' ? <CircleStop /> : null
  const isStructure = stage.kind === 'structure'
  const showsTargets = stage.id === 'targets' && branches.length > 0
  const evidenceLabel = distribution?.values.length ? '结果分布' : '暂无结果'
  return (
    <div className={`workflow-node stage-${stage.id} kind-${stage.kind} grade-${stage.insight.grade} node-${stage.status}${selected ? ' is-selected' : ''}`}>
      <Handle type="target" position={Position.Left} className="flow-handle" />
      <div className="node-heading">
        <span className="node-icon"><Icon /></span>
        <span className="node-title" title={termDescriptions[stage.id]}>{stage.label}</span>
        <span className="node-state-icon">{stateIcon}</span>
      </div>
      <div
        className="node-verdict"
        title={stage.insight.source === 'persisted_decision' ? '来自数据库中的智能体决策' : '根据数据库结果生成的节点结论'}
      >
        <span className={`verdict-chip ${stage.insight.grade}`}><i />{stage.insight.verdict}</span>
        <b title={stage.insight.reason}>{stage.insight.reason}</b>
      </div>
      {isStructure && <MoleculeViewer key={viewer?.artifact_sha256 ?? 'empty'} artifact={viewer} compact autoRotate />}
      {distribution && <ResultDistribution data={distribution} compact />}
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
      ) : (
        <div className="node-facts">
          {stage.insight.facts.slice(0, 2).map((fact) => (
            <span key={fact.label}><small>{fact.label}</small><strong title={fact.value}>{fact.value}</strong></span>
          ))}
        </div>
      )}
      <div className="node-meta">
        <span>{stage.current.toLocaleString()} / {stage.total.toLocaleString()}</span>
        <span className={`evidence-chip ${stage.provenance}`}>{evidenceLabel}</span>
      </div>
      <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
      <Handle type="source" position={Position.Right} className="flow-handle" />
    </div>
  )
}
