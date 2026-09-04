import type {
  CandidatePreview,
  GraphEdgeDetail,
  GraphStage,
  NodeDetail,
  RunDetail,
  TimelineEvent,
  ToolAttempt,
} from './types'

export interface RuntimeGraphStats {
  observedCalls: number
  observedEvents: number
  repeatedTools: number
  retries: number
  parallelGroups: number
  cycles: number
  unfinished: number
  generations: number
}

export interface RuntimeGraphModel {
  nodes: GraphStage[]
  edges: GraphEdgeDetail[]
  positions: Record<string, { x: number; y: number }>
  calls: Record<string, ToolAttempt>
  events: Record<string, TimelineEvent>
  gaps: string[]
  stats: RuntimeGraphStats
}

type Sources = Record<string, NodeDetail | undefined>

const callStatuses = new Set(['succeeded', 'completed'])
const activeStatuses = new Set(['running', 'started', 'queued', 'submitted'])
const stoppedStatuses = new Set(['failed', 'cancelled', 'stopped'])

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function text(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function observedAt(call: ToolAttempt) {
  return call.started_at ?? call.queued_at ?? call.finished_at
}

function nodeStatus(status: string): 'pending' | 'running' | 'completed' | 'stopped' {
  if (callStatuses.has(status)) return 'completed'
  if (activeStatuses.has(status)) return 'running'
  if (stoppedStatuses.has(status)) return 'stopped'
  return 'pending'
}

function gradeFor(status: string): 'good' | 'okay' | 'fair' | 'bad' | 'neutral' {
  if (callStatuses.has(status)) return 'good'
  if (activeStatuses.has(status)) return 'okay'
  if (stoppedStatuses.has(status)) return 'bad'
  return 'neutral'
}

function statusLabel(status: string) {
  if (callStatuses.has(status)) return '已完成'
  if (activeStatuses.has(status)) return '进行中'
  if (status === 'failed') return '失败'
  if (status === 'cancelled') return '已取消'
  if (status === 'stopped') return '已停止'
  return status || '待观测'
}

function collectCalls(sources: Sources) {
  const result: Record<string, ToolAttempt> = {}
  for (const detail of Object.values(sources)) {
    for (const call of detail?.calls ?? []) {
      result[call.id] ??= call
    }
  }
  return result
}

function relationIds(value: unknown, key = ''): string[] {
  const normalized = key.toLowerCase()
  const isRelationKey = /(parent|depend|upstream|source|previous|input_from|tool_call|call_id|event_id)/.test(normalized)
  if (typeof value === 'string' && isRelationKey && value.trim().length > 2) return [value]
  if (Array.isArray(value)) return value.flatMap((item) => relationIds(item, key))
  if (!value || typeof value !== 'object') return []
  return Object.entries(value as Record<string, unknown>).flatMap(([childKey, childValue]) => relationIds(childValue, childKey))
}

function addEdge(
  edges: GraphEdgeDetail[],
  seen: Set<string>,
  edge: GraphEdgeDetail,
) {
  if (edge.source === edge.target) return
  const key = `${edge.source}->${edge.target}`
  if (seen.has(key)) return
  seen.add(key)
  edges.push(edge)
}

function eventStatus(event: TimelineEvent) {
  if (event.type.endsWith('.succeeded') || event.type.endsWith('.completed')) return 'completed'
  if (event.type.endsWith('.started') || event.type.endsWith('.running') || event.type.endsWith('.progress')) return 'running'
  if (event.type.endsWith('.failed') || event.type.endsWith('.cancelled')) return 'stopped'
  return 'pending'
}

function eventNode(event: TimelineEvent): GraphStage {
  const status = eventStatus(event)
  return {
    id: `event:${event.sequence_no}`,
    label: event.type,
    kind: 'decision',
    group: 'observed',
    status,
    current: 1,
    total: 1,
    provenance: 'database',
    insight: {
      grade: status === 'completed' ? 'good' : status === 'stopped' ? 'bad' : status === 'running' ? 'okay' : 'neutral',
      verdict: statusLabel(status),
      reason: `${event.actor} · 序号 ${event.sequence_no}`,
      facts: [
        { label: '事件', value: event.type },
        { label: '序号', value: String(event.sequence_no) },
      ],
      source: 'observer_summary',
    },
    runtime: {
      node_type: 'lifecycle_event',
      source_id: String(event.sequence_no),
      observed_at: event.occurred_at,
      actor: event.actor,
      explicit_relation_count: 0,
    },
  }
}

function callNode(call: ToolAttempt): GraphStage {
  const status = nodeStatus(call.status)
  const artifactCount = call.artifacts.length
  return {
    id: `call:${call.id}`,
    label: call.tool_name,
    kind: 'tool',
    group: 'observed',
    status,
    current: callStatuses.has(call.status) ? 1 : 0,
    total: 1,
    provenance: 'database',
    insight: {
      grade: gradeFor(call.status),
      verdict: statusLabel(call.status),
      reason: `第 ${call.attempt} 次尝试 · ${call.tool_version}`,
      facts: [
        { label: '工具', value: call.tool_name },
        { label: '证据文件', value: String(artifactCount) },
      ],
      source: 'observer_summary',
    },
    runtime: {
      node_type: 'tool_call',
      source_id: call.id,
      observed_at: observedAt(call),
      tool_name: call.tool_name,
      attempt: call.attempt,
      explicit_relation_count: 0,
    },
  }
}

function generationNode(generation: number, count: number): GraphStage {
  return {
    id: `generation:${generation}`,
    label: `generation=${generation}`,
    kind: 'data',
    group: 'observed',
    status: 'completed',
    current: count,
    total: count,
    provenance: 'database',
    insight: {
      grade: 'okay',
      verdict: `${count} 条候选记录`,
      reason: '按候选记录中持久化的 generation 字段分组；不是预设流程阶段。',
      facts: [{ label: '候选预览', value: String(count) }, { label: '代际', value: String(generation) }],
      source: 'observer_summary',
    },
    runtime: {
      node_type: 'generation',
      source_id: String(generation),
      observed_at: null,
      candidate_count: count,
      explicit_relation_count: 0,
    },
  }
}

function candidateNode(candidate: CandidatePreview): GraphStage {
  const status = candidate.reasons.some((reason) => /reject|淘汰|失败/i.test(reason)) ? 'stopped' : 'completed'
  const rank = candidate.proposal_rank === null ? candidate.id.slice(0, 8) : `#${candidate.proposal_rank}`
  return {
    id: `candidate:${candidate.id}`,
    label: `候选 ${rank}`,
    kind: 'data',
    group: 'observed',
    status,
    current: 1,
    total: 1,
    provenance: 'database',
    insight: {
      grade: status === 'stopped' ? 'bad' : 'good',
      verdict: status === 'stopped' ? '已淘汰' : '已记录',
      reason: `${candidate.sequence.slice(0, 18)}${candidate.sequence.length > 18 ? '…' : ''} · ${candidate.length} 个氨基酸`,
      facts: [
        { label: '代际', value: candidate.generation === undefined ? '—' : String(candidate.generation) },
        { label: '来源', value: candidate.generator_call_id ? '工具调用' : '未返回' },
      ],
      source: 'observer_summary',
    },
    runtime: {
      node_type: 'generation',
      source_id: candidate.id,
      observed_at: null,
      candidate_count: 1,
      explicit_relation_count: 0,
    },
  }
}

function detectCycles(nodes: string[], edges: GraphEdgeDetail[]) {
  const adjacency = new Map<string, string[]>()
  for (const edge of edges) adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target])
  const visiting = new Set<string>()
  const visited = new Set<string>()
  let cycles = 0
  const visit = (id: string) => {
    if (visiting.has(id)) {
      cycles += 1
      return
    }
    if (visited.has(id)) return
    visiting.add(id)
    for (const next of adjacency.get(id) ?? []) visit(next)
    visiting.delete(id)
    visited.add(id)
  }
  nodes.forEach(visit)
  return cycles
}

function computePositions(nodes: GraphStage[]) {
  const sorted = [...nodes].sort((left, right) => {
    const a = left.runtime?.observed_at ? Date.parse(left.runtime.observed_at) : Number.MAX_SAFE_INTEGER
    const b = right.runtime?.observed_at ? Date.parse(right.runtime.observed_at) : Number.MAX_SAFE_INTEGER
    return a - b || left.id.localeCompare(right.id)
  })
  const positions: Record<string, { x: number; y: number }> = {}
  // Preserve chronological order while bounding the canvas in both dimensions.
  // A timestamp gap must not create a new unbounded column: a real run can contain
  // dozens of events, and fit-to-view should remain legible instead of shrinking to
  // a single-pixel timeline.
  const rows = 6
  sorted.forEach((node, index) => {
    positions[node.id] = { x: 50 + Math.floor(index / rows) * 350, y: 45 + (index % rows) * 205 }
  })
  return positions
}

export function buildRuntimeGraph(detail: RunDetail, sources: Sources = {}): RuntimeGraphModel {
  const calls = collectCalls(sources)
  const events = Object.fromEntries([...detail.events].sort((a, b) => a.sequence_no - b.sequence_no).map((event) => [`event:${event.sequence_no}`, event]))
  const nodes = [
    ...Object.values(calls).map(callNode),
    ...Object.values(events).map(eventNode),
    ...detail.candidates.map(candidateNode),
  ]
  const countsByGeneration = new Map<number, number>()
  for (const candidate of detail.candidates) {
    if (candidate.generation === undefined) continue
    countsByGeneration.set(candidate.generation, (countsByGeneration.get(candidate.generation) ?? 0) + 1)
  }
  for (const [generation, count] of countsByGeneration) nodes.push(generationNode(generation, count))

  const nodeIds = new Set(nodes.map((node) => node.id))
  const edges: GraphEdgeDetail[] = []
  const seen = new Set<string>()
  const callIdToNode = (id: string) => nodeIds.has(`call:${id}`) ? `call:${id}` : id
  for (const call of Object.values(calls)) {
    const source = `call:${call.id}`
    const relationIdsFromCall = [...relationIds(call.inputs), ...relationIds(call.parameters)]
    for (const parentId of relationIdsFromCall) {
      const target = callIdToNode(parentId)
      if (target.startsWith('call:')) addEdge(edges, seen, { source: target, target: source, label: '显式依赖', rationale: '工具调用输入或参数中提供了上游工具调用标识。', provenance: 'database' })
    }
  }
  for (const event of Object.values(events)) {
    const source = `event:${event.sequence_no}`
    for (const reference of relationIds(event.payload)) {
      const target = nodeIds.has(`call:${reference}`) ? `call:${reference}` : nodeIds.has(`event:${reference}`) ? `event:${reference}` : null
      if (target) addEdge(edges, seen, { source, target, label: '生命周期', rationale: '事件 payload 显式引用该工具调用或事件。', provenance: 'database' })
    }
  }
  const candidateIds = new Set(detail.candidates.map((candidate) => candidate.id))
  for (const candidate of detail.candidates) {
    if (candidate.parent_id && candidateIds.has(candidate.parent_id)) {
      addEdge(edges, seen, { source: `candidate:${candidate.parent_id}`, target: `candidate:${candidate.id}`, label: '父子谱系', rationale: '候选记录显式提供 parent_id。', provenance: 'database' })
    }
    if (candidate.generator_call_id && nodeIds.has(`call:${candidate.generator_call_id}`)) {
      addEdge(edges, seen, { source: `call:${candidate.generator_call_id}`, target: `candidate:${candidate.id}`, label: '生成来源', rationale: '候选记录显式提供 generator_call_id。', provenance: 'database' })
    }
    if (candidate.generation !== undefined && nodeIds.has(`generation:${candidate.generation}`)) {
      addEdge(edges, seen, { source: `generation:${candidate.generation}`, target: `candidate:${candidate.id}`, label: '代际分组', rationale: '候选记录显式提供 generation；此边仅表示数据分组，不代表执行依赖。', provenance: 'derived' })
    }
  }

  const gaps: string[] = []
  if (!Object.keys(calls).length) gaps.push('接口未返回工具调用明细；当前仅能显示生命周期事件。')
  if (Object.keys(calls).length && !edges.some((edge) => edge.provenance === 'database' && edge.source.startsWith('call:'))) {
    gaps.push('接口未返回工具调用依赖边；未按时间顺序补画推断边。')
  }
  if (detail.candidates.length && !detail.candidates.some((candidate) => candidate.parent_id)) {
    gaps.push('候选预览未返回 parent_id；父子代际关系暂不可观测。')
  }
  if (detail.events.length >= 32) gaps.push('接口仅返回最近 32 条事件；历史事件可能未进入本次运行图。')
  if (Object.values(sources).some((source) => (source?.calls.length ?? 0) >= 40)) gaps.push('至少一个节点明细只返回 40 次工具调用；完整调用集合缺少分页契约。')
  if (!detail.graph.nodes.length) gaps.push('运行详情未提供阶段摘要；无法核对旧版兼容数据。')

  const toolCounts = new Map<string, number>()
  for (const call of Object.values(calls)) toolCounts.set(call.tool_name, (toolCounts.get(call.tool_name) ?? 0) + 1)
  const intervals = Object.values(calls).map((call) => ({
    start: Date.parse(call.queued_at),
    end: Date.parse(call.finished_at ?? call.started_at ?? call.queued_at),
  })).filter((item) => Number.isFinite(item.start))
  intervals.sort((a, b) => a.start - b.start)
  let parallelGroups = 0
  let activeEnd = Number.NEGATIVE_INFINITY
  for (let index = 0; index < intervals.length;) {
    const end = intervals[index].end
    let groupSize = 0
    while (index < intervals.length && intervals[index].start <= Math.max(activeEnd, end)) {
      activeEnd = Math.max(activeEnd, intervals[index].end)
      groupSize += 1
      index += 1
    }
    if (groupSize > 1) parallelGroups += 1
  }
  const stats: RuntimeGraphStats = {
    observedCalls: Object.keys(calls).length,
    observedEvents: Object.keys(events).length,
    repeatedTools: [...toolCounts.values()].filter((count) => count > 1).length,
    retries: Object.values(calls).filter((call) => call.attempt > 1).length,
    parallelGroups,
    cycles: detectCycles(nodes.map((node) => node.id), edges),
    unfinished: Object.values(calls).filter((call) => !callStatuses.has(call.status) && !stoppedStatuses.has(call.status)).length,
    generations: countsByGeneration.size,
  }
  return { nodes, edges, positions: computePositions(nodes), calls, events, gaps, stats }
}
