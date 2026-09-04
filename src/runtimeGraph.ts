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
  toolGroups: Record<string, string[]>
  sourceFetch?: { requested: number; loaded: number; failed: number; deferred?: number }
  gaps: string[]
  stats: RuntimeGraphStats
}

export interface RuntimeGraphOptions {
  expandedGroups?: ReadonlySet<string>
  sourceFetch?: {
    requested: number
    loaded: number
    failed: number
    deferred?: number
  }
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
  return !status || status === 'pending' ? '待观测' : status
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

const dependencyKeys = new Set([
  'parent_call_id',
  'parent_call_ids',
  'depends_on_call_id',
  'depends_on_call_ids',
  'dependency_call_id',
  'dependency_call_ids',
  'upstream_call_id',
  'upstream_call_ids',
  'previous_call_id',
  'previous_call_ids',
  'input_from_call_id',
  'input_from_call_ids',
])

const retryKeys = new Set(['retry_of_call_id', 'retry_of_call_ids', 'retried_call_id', 'retried_call_ids', 'recovery_of_call_id', 'recovery_of_call_ids'])
const fallbackKeys = new Set(['fallback_from_call_id', 'fallback_from_call_ids'])
const parallelKeys = new Set(['parallel_group_id', 'parallel_group_ids'])

const associationKeys = new Set(['tool_call_id', 'tool_call_ids', 'associated_call_id', 'associated_call_ids', 'event_id', 'event_ids'])

function idsForKeys(value: unknown, keys: Set<string>, key = ''): string[] {
  const normalized = key.toLowerCase()
  if (typeof value === 'string' && keys.has(normalized) && value.trim().length > 2) return [value.trim()]
  if (Array.isArray(value)) return value.flatMap((item) => idsForKeys(item, keys, key))
  if (!value || typeof value !== 'object') return []
  return Object.entries(value as Record<string, unknown>).flatMap(([childKey, childValue]) => idsForKeys(childValue, keys, childKey))
}

function dependencyIds(value: unknown) {
  return idsForKeys(value, dependencyKeys)
}

function retryIds(value: unknown) {
  return idsForKeys(value, retryKeys)
}

function fallbackIds(value: unknown) {
  return idsForKeys(value, fallbackKeys)
}

function parallelGroupIds(value: unknown) {
  return idsForKeys(value, parallelKeys)
}

function associationIds(value: unknown) {
  return idsForKeys(value, associationKeys)
}

const batchKeys = new Set(['batch_id', 'batch', 'iteration', 'iteration_id', 'generation', 'action_plan', 'action_plan_id', 'parent_call_id'])

function explicitBatchIdentity(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
    const normalized = key.toLowerCase()
    if (batchKeys.has(normalized) && (typeof nested === 'string' || typeof nested === 'number')) return `${normalized}=${nested}`
    const child = explicitBatchIdentity(nested)
    if (child) return child
  }
  return null
}

function callBatchIdentity(call: ToolAttempt) {
  return explicitBatchIdentity(call.inputs) ?? explicitBatchIdentity(call.parameters)
}

function firstParallelGroupId(call: ToolAttempt) {
  return [...parallelGroupIds(call.inputs), ...parallelGroupIds(call.parameters)][0] ?? null
}

function addEdge(
  edges: GraphEdgeDetail[],
  seen: Set<string>,
  edge: GraphEdgeDetail,
) {
  if (edge.source === edge.target) return
  const key = `${edge.source}->${edge.target}:${edge.relation_kind ?? 'unknown'}`
  if (seen.has(key)) return
  seen.add(key)
  edges.push(edge)
}

export function runtimeEventStatus(event: TimelineEvent) {
  const terminalSuffixes = ['succeeded', 'completed', 'persisted', 'materialized', 'recorded', 'accepted', 'rejected', 'created']
  if (terminalSuffixes.some((suffix) => event.type.endsWith(`.${suffix}`))) return 'completed'
  if (event.type.endsWith('.started') || event.type.endsWith('.running') || event.type.endsWith('.progress')) return 'running'
  if (event.type.endsWith('.failed') || event.type.endsWith('.cancelled')) return 'stopped'
  return 'pending'
}

const toolLabels: Record<string, string> = {
  amp_designer: 'AMP Designer',
  ampgan: 'AMPGAN v2',
  hydramp: 'HydrAMP',
  amp_read: 'AMP read',
  boltz: 'Boltz 2',
  rosetta: 'Rosetta',
}

const eventLabels: Record<string, string> = {
  'run.created': '运行已创建',
  'run.started': '运行开始',
  'run.succeeded': '运行完成',
  'run.failed': '运行失败',
  'run.cancelled': '运行已取消',
  'tool_call.started': '工具调用开始',
  'tool_call.completed': '工具调用完成',
  'tool_call.succeeded': '工具调用成功',
  'tool_call.failed': '工具调用失败',
  'candidate.created': '候选已记录',
  'candidate.scored': '候选已评分',
  'candidate.rejected': '候选已淘汰',
}

function displayToolName(toolName: string) {
  if (toolLabels[toolName]) return toolLabels[toolName]
  const normalized = toolName.toLowerCase()
  if (/v38[-_.]?generate.*ampgan|ampgan.*generate/.test(normalized)) return 'AMPGAN v2 生成'
  if (/v38[-_.]?generate.*hydramp|hydramp.*generate/.test(normalized)) return 'HydrAMP 生成'
  if (/boltz|multitarget.*structure/.test(normalized)) return 'Boltz 2 结构预测'
  if (/rosetta|interface.*refin/.test(normalized)) return 'Rosetta 界面精修'
  if (/generate|design/.test(normalized)) return '候选生成'
  if (/score|rank|admission/.test(normalized)) return '候选评分与筛选'
  return '工具调用'
}

function displayEventName(type: string) {
  if (eventLabels[type]) return eventLabels[type]
  const normalized = type.toLowerCase()
  const suffix = type.split('.').at(-1) ?? type
  const suffixLabels: Record<string, string> = { started: '开始', running: '进行中', progress: '进度更新', completed: '完成', succeeded: '成功', failed: '失败', cancelled: '已取消', created: '已创建', persisted: '已持久化', materialized: '已物化', recorded: '已记录', accepted: '已接受', rejected: '已淘汰' }
  const state = suffixLabels[suffix] ?? '事件'
  if (/multitarget.*structure/.test(normalized)) return `结构证据 · ${state}`
  if (/scored.*lineage|lineage.*scored/.test(normalized)) return `评分谱系 · ${state}`
  if (/operational_run/.test(normalized)) return `运行记录 · ${state}`
  if (/operational\.call/.test(normalized)) return `工具调用 · ${state}`
  return `生命周期 · ${suffixLabels[suffix] ?? '事件'}`
}

function displayActor(actor: string) {
  const normalized = actor.toLowerCase()
  if (/human|scientist|user/.test(normalized)) return '科研人员'
  if (/observer|writer|persist|database|postgres/.test(normalized)) return '观察器记录器'
  if (/scheduler|temporal/.test(normalized)) return '任务调度器'
  if (/worker|agent|orchestrator|workflow/.test(normalized)) return '运行编排器'
  return '运行参与者'
}

function eventNode(event: TimelineEvent): GraphStage {
  const status = runtimeEventStatus(event)
  return {
    id: `event:${event.sequence_no}`,
    label: displayEventName(event.type),
    kind: 'decision',
    group: 'observed',
    status,
    current: 1,
    total: 1,
    provenance: 'database',
    insight: {
      grade: status === 'completed' ? 'good' : status === 'stopped' ? 'bad' : status === 'running' ? 'okay' : 'neutral',
      verdict: statusLabel(status),
      reason: `${displayActor(event.actor)} · 序号 ${event.sequence_no}`,
      facts: [
        { label: '语义', value: displayEventName(event.type) },
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
      raw_label: event.type,
    },
  }
}

function callNode(call: ToolAttempt): GraphStage {
  const status = nodeStatus(call.status)
  const artifactCount = call.artifacts.length
  return {
    id: `call:${call.id}`,
    label: displayToolName(call.tool_name),
    kind: 'tool',
    group: 'observed',
    status,
    current: callStatuses.has(call.status) ? 1 : 0,
    total: 1,
    provenance: 'database',
    insight: {
      grade: gradeFor(call.status),
      verdict: statusLabel(call.status),
        reason: `第 ${call.attempt} 次尝试 · 已记录工具证据`,
      facts: [
        { label: '状态', value: statusLabel(call.status) },
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
      raw_label: call.tool_name,
    },
  }
}

function statusBreakdown(calls: ToolAttempt[]) {
  const counts = new Map<string, number>()
  for (const call of calls) counts.set(call.status, (counts.get(call.status) ?? 0) + 1)
  return [...counts.entries()].map(([status, count]) => `${statusLabel(status)} ${count}`).join(' · ') || '待观测'
}

function observedSpan(calls: ToolAttempt[]) {
  const dates = calls.flatMap((call) => [call.queued_at, call.finished_at ?? call.started_at ?? call.queued_at]).map((value) => Date.parse(value)).filter(Number.isFinite)
  if (dates.length < 2) return '时间范围不完整'
  const seconds = Math.max(0, Math.round((Math.max(...dates) - Math.min(...dates)) / 1000))
  return `时间跨度 ${seconds} 秒`
}

function operationComposition(calls: ToolAttempt[]) {
  const counts = new Map<string, number>()
  for (const call of calls) {
    const name = displayToolName(call.tool_name)
    counts.set(name, (counts.get(name) ?? 0) + 1)
  }
  return [...counts.entries()].map(([name, count]) => `${name} ${count}`).join(' · ')
}

function eventComposition(events: TimelineEvent[]) {
  const counts = new Map<string, number>()
  const activities = new Map<string, TimelineEvent>()
  for (const event of events) activities.set(eventActivityIdentity(event) ?? `event:${event.sequence_no}`, event)
  for (const event of activities.values()) {
    const activityType = text(record(event.payload).activity_type)
    const name = activityType.includes('plan_autoresearch') ? '生成规划'
      : activityType.includes('persist_autoresearch_action_plan') ? '规划持久化'
        : activityType.includes('evaluate_v38_sequence_metric') ? '序列指标计算'
          : activityType.includes('persist_v38_sequence_metric') ? '指标持久化'
            : activityType ? '运行活动' : displayEventName(event.type)
    counts.set(name, (counts.get(name) ?? 0) + 1)
  }
  return [...counts.entries()].map(([name, count]) => `${name} ${count}`).join(' · ')
}

function relationCount(calls: ToolAttempt[], collect: (value: unknown) => string[]) {
  return calls.reduce((total, call) => total + new Set([...collect(call.inputs), ...collect(call.parameters)]).size, 0)
}

function eventActivityIdentity(event: TimelineEvent) {
  const payload = record(event.payload)
  const workflowRunId = text(payload.workflow_run_id)
  const activityId = payload.activity_id
  const attempt = payload.attempt
  if (!workflowRunId || (typeof activityId !== 'string' && typeof activityId !== 'number')) return null
  return `${workflowRunId}:activity=${activityId}:attempt=${typeof attempt === 'string' || typeof attempt === 'number' ? attempt : 1}`
}

function eventExecutionIdentity(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
    if (key.toLowerCase() === 'workflow_run_id' && typeof nested === 'string' && nested.trim()) return `workflow_run_id=${nested.trim()}`
    const child = eventExecutionIdentity(nested)
    if (child) return child
  }
  return null
}

function eventOutcomeStatuses(events: TimelineEvent[]) {
  const outcomes = new Map<string, string>()
  for (const event of events) outcomes.set(eventActivityIdentity(event) ?? `event:${event.sequence_no}`, runtimeEventStatus(event))
  return [...outcomes.values()]
}

function runtimeGroupNode(groupedCalls: ToolAttempt[], groupedEvents: TimelineEvent[], expanded: boolean, groupId: string, groupingBasis: string, displayBatchLabel: string): GraphStage {
  const eventStatuses = eventOutcomeStatuses(groupedEvents)
  const statuses = [...groupedCalls.map((call) => call.status), ...eventStatuses]
  const hasActive = statuses.some((status) => activeStatuses.has(status))
  const hasStopped = statuses.some((status) => stoppedStatuses.has(status))
  const hasCompleted = statuses.some((status) => callStatuses.has(status))
  const status = hasActive ? 'running' : hasStopped ? 'stopped' : statuses.length > 0 && statuses.every((item) => callStatuses.has(item)) ? 'completed' : 'pending'
  const grade = hasActive ? 'okay' : hasStopped && hasCompleted ? 'fair' : hasStopped ? 'bad' : statuses.length > 0 && statuses.every((item) => callStatuses.has(item)) ? 'good' : 'neutral'
  const retryCount = relationCount(groupedCalls, retryIds)
  const fallbackCount = relationCount(groupedCalls, fallbackIds)
  const total = groupedCalls.length + eventStatuses.length
  const operationSummary = [operationComposition(groupedCalls), eventComposition(groupedEvents)].filter(Boolean).join(' · ')
  const statusSummary = statuses.map(statusLabel).reduce((counts, item) => counts.set(item, (counts.get(item) ?? 0) + 1), new Map<string, number>())
  const statusText = [...statusSummary.entries()].map(([label, count]) => `${label} ${count}`).join(' · ')
  const observedDates = [...groupedCalls.map((call) => observedAt(call)), ...groupedEvents.map((event) => event.occurred_at)].filter((value): value is string => Boolean(value)).sort()
  const observedSpanText = observedDates.length > 1 ? `时间跨度 ${Math.max(0, Math.round((Date.parse(observedDates.at(-1)!) - Date.parse(observedDates[0])) / 1000))} 秒` : '时间范围不完整'
  const runtimeType = groupedEvents.length && !groupedCalls.length ? 'event_group' : groupedEvents.length ? 'batch_group' : 'tool_group'
  const countLabel = groupedCalls.length && eventStatuses.length ? `${groupedCalls.length} 次调用 · ${eventStatuses.length} 项活动` : groupedCalls.length ? `${groupedCalls.length} 次调用` : `${eventStatuses.length} 项活动`
  return {
    id: groupId,
    label: `${displayBatchLabel} · ${total} 项活动`,
    kind: 'tool',
    group: 'observed',
    status,
    current: statuses.filter((item) => callStatuses.has(item)).length,
    total,
    provenance: 'derived',
    insight: {
      grade,
       verdict: countLabel,
       reason: groupingBasis.startsWith('后端执行字段') ? '持久化执行标识 · 同次活动汇总' : groupingBasis.startsWith('后端字段') ? '结构化批次字段 · 同批操作汇总' : '连续同类观测汇总 · 不代表执行因果',
       facts: [
         { label: '操作构成', value: operationSummary },
         { label: '状态', value: statusText },
         { label: '时间', value: observedSpanText },
         { label: '关系', value: `重试 ${retryCount} · 回退 ${fallbackCount}` },
      ],
      source: 'observer_summary',
    },
    runtime: {
       node_type: runtimeType,
       source_id: groupId,
       tool_name: groupedCalls[0]?.tool_name,
       observed_at: observedDates[0] ?? null,
       child_ids: groupedCalls.map((call) => call.id),
       event_ids: groupedEvents.map((event) => `event:${event.sequence_no}`),
       grouping_basis: groupingBasis,
       expanded,
       status_breakdown: statusText,
       observed_span: observedSpanText,
       raw_label: groupedCalls[0]?.tool_name ?? groupedEvents[0]?.type,
       candidate_count: groupedCalls.length,
      explicit_relation_count: 0,
    },
  }
}

function generationNode(generation: number, count: number): GraphStage {
  return {
    id: `generation:${generation}`,
    label: `第 ${generation} 代`,
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
  const rank = candidate.proposal_rank === null ? candidate.id.slice(0, 8) : `#${candidate.proposal_rank}`
  return {
    id: `candidate:${candidate.id}`,
    label: `候选 ${rank}`,
    kind: 'data',
    group: 'observed',
    status: 'completed',
    current: 1,
    total: 1,
    provenance: 'database',
    insight: {
      grade: 'neutral',
      verdict: '已记录',
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
  for (const edge of edges) {
    if (edge.relation_kind !== 'dependency') continue
    adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target])
  }
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
    const leftPriority = ['tool_group', 'event_group', 'batch_group'].includes(left.runtime?.node_type ?? '') ? 0 : 1
    const rightPriority = ['tool_group', 'event_group', 'batch_group'].includes(right.runtime?.node_type ?? '') ? 0 : 1
    return a - b || leftPriority - rightPriority || left.id.localeCompare(right.id)
  })
  const positions: Record<string, { x: number; y: number }> = {}
  const laneByType: Record<string, number> = { lifecycle_event: 0, event_group: 0, tool_group: 1, batch_group: 1, tool_call: 1, generation: 2 }
  // Compute each lane's start from its actual wrapped row count. A fixed y
  // offset lets a second event row collide with the first tool row.
  const laneCounts = [0, 0, 0]
  for (const node of sorted) {
    const lane = laneByType[node.runtime?.node_type ?? 'generation'] ?? 2
    laneCounts[lane] += 1
  }
  const laneRows = laneCounts.map((count) => count === 0 ? 0 : Math.ceil(count / 5))
  // Expanded aggregate cards contain their own summary and are taller than
  // ordinary cards. Give that lane a larger row step so its local member grid
  // cannot collide with the aggregate card or the following lane.
  const laneRowSteps = laneCounts.map((_, lane) => sorted.some((node) => {
    const nodeLane = laneByType[node.runtime?.node_type ?? 'generation'] ?? 2
    return nodeLane === lane && node.runtime?.expanded
  }) ? 320 : sorted.some((node) => {
    const nodeLane = laneByType[node.runtime?.node_type ?? 'generation'] ?? 2
    return nodeLane === lane && ['tool_group', 'event_group', 'batch_group'].includes(node.runtime?.node_type ?? '')
  }) ? 280 : 190)
  const laneY = [
    150,
    150 + laneRows[0] * laneRowSteps[0] + 65,
    150 + laneRows[0] * laneRowSteps[0] + 65 + laneRows[1] * laneRowSteps[1] + 65,
  ]
  const laneCounters = new Map<number, number>()
  sorted.forEach((node) => {
    const lane = laneByType[node.runtime?.node_type ?? 'generation'] ?? 2
    const index = laneCounters.get(lane) ?? 0
    laneCounters.set(lane, index + 1)
    const column = index % 5
    const row = Math.floor(index / 5)
    positions[node.id] = { x: 155 + column * 315, y: (laneY[lane] ?? laneY[2]) + row * (laneRowSteps[lane] ?? 190) }
  })
  return positions
}

export function buildRuntimeGraph(detail: RunDetail, sources: Sources = {}, options: RuntimeGraphOptions = {}): RuntimeGraphModel {
  const calls = collectCalls(sources)
  const events = Object.fromEntries([...detail.events].sort((a, b) => a.sequence_no - b.sequence_no).map((event) => [`event:${event.sequence_no}`, event]))
  const orderedCalls = Object.values(calls).sort((left, right) => {
    const leftTime = Date.parse(observedAt(left) ?? '')
    const rightTime = Date.parse(observedAt(right) ?? '')
    return (Number.isFinite(leftTime) ? leftTime : Number.MAX_SAFE_INTEGER) - (Number.isFinite(rightTime) ? rightTime : Number.MAX_SAFE_INTEGER) || left.id.localeCompare(right.id)
  })
  const explicitBuckets = new Map<string, ToolAttempt[]>()
  const fallbackGroups: ToolAttempt[][] = []
  const fallbackBases: string[] = []
  let activeFallback: ToolAttempt[] | null = null
  for (const call of orderedCalls) {
    const previousCall = activeFallback?.at(-1)
    const gap = previousCall && observedAt(previousCall) && observedAt(call) ? Date.parse(observedAt(call)!) - Date.parse(observedAt(previousCall)!) : Number.POSITIVE_INFINITY
    const currentBatch = callBatchIdentity(call)
    if (currentBatch) {
      explicitBuckets.set(currentBatch, [...(explicitBuckets.get(currentBatch) ?? []), call])
      // An explicitly keyed call separates fallback observations in the raw
      // timeline; it must not accidentally bridge two unkeyed segments.
      activeFallback = null
      continue
    }
    const sameObservedSegment = Boolean(activeFallback && previousCall?.tool_name === call.tool_name && Number.isFinite(gap) && gap >= 0 && gap <= 5 * 60 * 1000)
    if (sameObservedSegment) activeFallback!.push(call)
    else {
      activeFallback = [call]
      fallbackGroups.push(activeFallback)
      fallbackBases.push('工具名 + 连续相邻观测时间')
    }
  }
  const explicitGroups = [...explicitBuckets.entries()].map(([identity, grouped]) => ({ key: identity, grouped: grouped.sort((left, right) => (Date.parse(observedAt(left) ?? '') - Date.parse(observedAt(right) ?? '')) || left.id.localeCompare(right.id)), basis: `后端字段 ${identity}` }))
  const fallbackGroupRecords = fallbackGroups.map((grouped, index) => ({ key: `fallback:${grouped[0].id}`, grouped, basis: fallbackBases[index] }))
  const callGroupRecords = [...explicitGroups, ...fallbackGroupRecords].sort((left, right) => {
    const leftTime = Date.parse(observedAt(left.grouped[0]) ?? '')
    const rightTime = Date.parse(observedAt(right.grouped[0]) ?? '')
    return (Number.isFinite(leftTime) ? leftTime : Number.MAX_SAFE_INTEGER) - (Number.isFinite(rightTime) ? rightTime : Number.MAX_SAFE_INTEGER) || left.grouped[0].id.localeCompare(right.grouped[0].id)
  })
  const callRecordById = new Map(callGroupRecords.flatMap((record) => record.grouped.map((call) => [call.id, record] as const)))
  const eventGroups = new Map<string, { key: string; grouped: TimelineEvent[]; basis: string }>()
  let activeEventGroup: { key: string; grouped: TimelineEvent[]; basis: string } | null = null
  for (const event of Object.values(events)) {
    const explicitIdentity = explicitBatchIdentity(event.payload)
    const relatedCallId = associationIds(event.payload).find((id) => calls[id])
    const linkedRecord = relatedCallId ? callRecordById.get(relatedCallId) : undefined
    const executionIdentity = eventExecutionIdentity(event.payload)
    const key = explicitIdentity ?? linkedRecord?.key ?? executionIdentity
    const basis = explicitIdentity ? `后端字段 ${explicitIdentity}` : linkedRecord ? `后端关联字段 tool_call_id=${relatedCallId}` : executionIdentity ? `后端执行字段 ${executionIdentity}` : null
    if (key && basis) {
      const existing = eventGroups.get(key) ?? { key, grouped: [], basis }
      existing.grouped.push(event)
      eventGroups.set(key, existing)
      activeEventGroup = null
      continue
    }
    const previous = activeEventGroup?.grouped.at(-1)
    const gap = previous ? Date.parse(event.occurred_at) - Date.parse(previous.occurred_at) : Number.POSITIVE_INFINITY
    if (!previous || previous.type !== event.type || previous.actor !== event.actor || !Number.isFinite(gap) || gap < 0 || gap > 5 * 60 * 1000) {
      activeEventGroup = { key: `event-observation:${event.sequence_no}`, grouped: [event], basis: '连续同类观测：事件类型 + 角色 + 相邻时间' }
      eventGroups.set(activeEventGroup.key, activeEventGroup)
    } else if (activeEventGroup) {
      activeEventGroup.grouped.push(event)
    }
  }
  const runtimeBuckets = new Map<string, { key: string; calls: ToolAttempt[]; events: TimelineEvent[]; basis: string }>()
  for (const record of callGroupRecords) runtimeBuckets.set(record.key, { key: record.key, calls: record.grouped, events: [], basis: record.basis })
  for (const record of eventGroups.values()) {
    const bucket = runtimeBuckets.get(record.key)
    if (bucket) bucket.events.push(...record.grouped)
    else runtimeBuckets.set(record.key, { key: record.key, calls: [], events: record.grouped, basis: record.basis })
  }
  const bucketRecords = [...runtimeBuckets.values()].sort((left, right) => {
    const leftTime = Date.parse((left.calls[0] ? observedAt(left.calls[0]) : left.events[0]?.occurred_at) ?? '')
    const rightTime = Date.parse((right.calls[0] ? observedAt(right.calls[0]) : right.events[0]?.occurred_at) ?? '')
    return (Number.isFinite(leftTime) ? leftTime : Number.MAX_SAFE_INTEGER) - (Number.isFinite(rightTime) ? rightTime : Number.MAX_SAFE_INTEGER) || left.key.localeCompare(right.key)
  })
  const groupIdFor = (bucket: { key: string; calls: ToolAttempt[]; events: TimelineEvent[] }) => bucket.calls.length > 1
    ? `tool-group:${encodeURIComponent(bucket.calls[0].tool_name)}:${encodeURIComponent(bucket.calls[0].id)}`
    : `batch-group:${encodeURIComponent(bucket.key)}`
  const groupedBuckets = bucketRecords.filter((bucket) => bucket.calls.length + bucket.events.length > 1)
  const toolGroups = Object.fromEntries(groupedBuckets.filter((bucket) => bucket.calls.length > 0).map((bucket) => [groupIdFor(bucket), bucket.calls.map((call) => call.id)]))
  const groupIdByCall = new Map<string, string>()
  const groupIdByEvent = new Map<string, string>()
  for (const bucket of groupedBuckets) {
    const groupId = groupIdFor(bucket)
    for (const call of bucket.calls) groupIdByCall.set(call.id, groupId)
    for (const event of bucket.events) groupIdByEvent.set(`event:${event.sequence_no}`, groupId)
  }
  const expandedGroups = options.expandedGroups ?? new Set<string>()
  let aggregateIndex = 0
  const callNodes = bucketRecords.flatMap((bucket) => {
    const total = bucket.calls.length + bucket.events.length
    if (total === 1) return [...bucket.calls.map(callNode), ...bucket.events.map(eventNode)]
    const groupId = groupIdFor(bucket)
    const expanded = expandedGroups.has(groupId)
    const groupingBasis = bucket.basis
    aggregateIndex += 1
    const displayBatchLabel = groupingBasis.startsWith('后端执行字段') ? `第 ${aggregateIndex} 次执行` : groupingBasis.startsWith('后端字段') ? `第 ${aggregateIndex} 批` : `观测组 ${aggregateIndex}`
    return [runtimeGroupNode(bucket.calls, bucket.events, expanded, groupId, groupingBasis, displayBatchLabel), ...(expanded ? [...bucket.calls.map(callNode), ...bucket.events.map(eventNode)] : [])]
  })
  const nodes = [
    ...callNodes,
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
  const callIdToNode = (id: string) => {
    const groupId = groupIdByCall.get(id)
    if (groupId && !expandedGroups.has(groupId)) return groupId
    return nodeIds.has(`call:${id}`) ? `call:${id}` : id
  }
  const isCallEndpoint = (id: string) => id.startsWith('call:') || id.startsWith('tool-group:') || id.startsWith('batch-group:')
  const explicitParallelBuckets = new Map<string, ToolAttempt[]>()
  for (const call of Object.values(calls)) {
    const parallelId = firstParallelGroupId(call)
    if (parallelId) explicitParallelBuckets.set(parallelId, [...(explicitParallelBuckets.get(parallelId) ?? []), call])
  }
  const parallelRelationSignatures = new Set<string>()
  const addParallelGroup = (grouped: ToolAttempt[], provenance: 'database' | 'derived', rationale: string) => {
    if (grouped.length < 2) return
    const signature = grouped.map((call) => call.id).sort().join('|')
    if (parallelRelationSignatures.has(signature)) return
    parallelRelationSignatures.add(signature)
    let labeled = false
    const anchor = callIdToNode(grouped[0].id)
    for (const call of grouped.slice(1)) {
      const target = callIdToNode(call.id)
      const edgeLabel = labeled ? null : provenance === 'database' ? '并行观测组' : '并行观测组 · 观测'
      addEdge(edges, seen, { source: anchor, target, label: edgeLabel, rationale, provenance, relation_kind: 'parallel' })
      if (anchor !== target) labeled = true
    }
  }
  for (const call of Object.values(calls)) {
    const source = callIdToNode(call.id)
    const relationIdsFromCall = [...dependencyIds(call.inputs), ...dependencyIds(call.parameters)]
    for (const parentId of relationIdsFromCall) {
      const target = callIdToNode(parentId)
      if (isCallEndpoint(target)) addEdge(edges, seen, { source: target, target: source, label: '依赖', rationale: '工具调用输入或参数中的结构化依赖字段提供了上游调用标识；聚合端点仅代表其子调用集合。', provenance: 'database', relation_kind: 'dependency' })
    }
    const typedRelations: Array<{ ids: string[]; kind: 'retry' | 'fallback'; label: string; rationale: string }> = [
      { ids: [...retryIds(call.inputs), ...retryIds(call.parameters)], kind: 'retry', label: '重试/恢复', rationale: '工具调用字段明确提供 retry_of_call_id、retried_call_id 或 recovery_of_call_id；此边表示重试/恢复关系，不由 attempt 数字或时间推断。' },
      { ids: [...fallbackIds(call.inputs), ...fallbackIds(call.parameters)], kind: 'fallback', label: '回退', rationale: '工具调用字段明确提供 fallback_from_call_id；此边表示回退来源，不由失败状态或时间推断。' },
    ]
    for (const relation of typedRelations) for (const upstreamId of relation.ids) {
      const target = callIdToNode(upstreamId)
      if (isCallEndpoint(target)) addEdge(edges, seen, { source: target, target: source, label: relation.label, rationale: relation.rationale, provenance: 'database', relation_kind: relation.kind })
    }
  }
  for (const [parallelId, grouped] of explicitParallelBuckets) {
    addParallelGroup(grouped, 'database', `工具调用字段明确提供 parallel_group_id=${parallelId}；此边表示同组并行观测，不代表调度依赖。`)
  }
  const observedIntervals = Object.values(calls).map((call) => ({
    call,
    start: Date.parse(call.queued_at),
    end: Date.parse(call.finished_at ?? call.started_at ?? call.queued_at),
  })).filter((item) => Number.isFinite(item.start) && Number.isFinite(item.end)).sort((left, right) => left.start - right.start)
  let overlapGroup: ToolAttempt[] = []
  let overlapEnd = Number.NEGATIVE_INFINITY
  const flushOverlapGroup = () => {
    if (overlapGroup.length > 1) addParallelGroup(overlapGroup, 'derived', '调用时间区间存在重叠；这是观察到的并行区间，不代表后端调度依赖。')
    overlapGroup = []
    overlapEnd = Number.NEGATIVE_INFINITY
  }
  for (const item of observedIntervals) {
    if (overlapGroup.length && item.start <= overlapEnd) {
      overlapGroup.push(item.call)
      overlapEnd = Math.max(overlapEnd, item.end)
    } else {
      flushOverlapGroup()
      overlapGroup = [item.call]
      overlapEnd = item.end
    }
  }
  flushOverlapGroup()
  const eventIdToNode = (id: string) => {
    const groupId = groupIdByEvent.get(id)
    if (groupId && !expandedGroups.has(groupId)) return groupId
    return nodeIds.has(id) ? id : null
  }
  for (const event of Object.values(events)) {
    const source = eventIdToNode(`event:${event.sequence_no}`)
    if (!source) continue
    for (const reference of associationIds(event.payload)) {
      const target = calls[reference] ? callIdToNode(reference) : eventIdToNode(`event:${reference}`)
      if (target) addEdge(edges, seen, { source, target, label: '关联', rationale: '事件 payload 仅提供结构化关联标识；这不表示事件产生、触发或依赖该工具调用。', provenance: 'database', relation_kind: 'association' })
    }
  }
  const candidateIds = new Set(detail.candidates.map((candidate) => candidate.id))
  for (const candidate of detail.candidates) {
    if (candidate.parent_id && candidateIds.has(candidate.parent_id)) {
      addEdge(edges, seen, { source: `candidate:${candidate.parent_id}`, target: `candidate:${candidate.id}`, label: '父子谱系', rationale: '候选记录显式提供 parent_id；这是候选谱系，不是执行依赖。', provenance: 'database', relation_kind: 'lineage' })
    }
    if (candidate.generator_call_id) {
      const source = callIdToNode(candidate.generator_call_id)
      if (nodeIds.has(source)) addEdge(edges, seen, { source, target: `candidate:${candidate.id}`, label: '生成来源', rationale: '候选记录显式提供 generator_call_id；这是来源关联，不表示该调用的执行依赖。', provenance: 'database', relation_kind: 'association' })
    }
    if (candidate.generation !== undefined && nodeIds.has(`generation:${candidate.generation}`)) {
      addEdge(edges, seen, { source: `generation:${candidate.generation}`, target: `candidate:${candidate.id}`, label: '代际分组', rationale: '候选记录显式提供 generation；此边仅表示数据分组，不代表执行依赖或时间顺序。', provenance: 'derived', relation_kind: 'grouping' })
    }
  }

  const gaps: string[] = []
  if (!Object.keys(calls).length) gaps.push('接口未返回工具调用明细；当前仅能显示生命周期事件。')
  if (Object.keys(calls).length && !edges.some((edge) => edge.provenance === 'database' && ['dependency', 'retry', 'fallback'].includes(edge.relation_kind ?? ''))) {
    gaps.push('接口未返回工具调用依赖、重试或回退关系；未按时间顺序补画推断边。')
  }
  if (detail.candidates.length && !detail.candidates.some((candidate) => candidate.parent_id)) {
    gaps.push('候选预览未返回 parent_id；父子代际关系暂不可观测。')
  }
  if (detail.events.length >= 32) gaps.push('接口仅返回最近 32 条事件；历史事件可能未进入本次运行图。')
  if (Object.values(sources).some((source) => (source?.calls.length ?? 0) >= 40)) gaps.push('至少一个节点明细只返回 40 次工具调用；完整调用集合缺少分页契约。')
  if (options.sourceFetch && options.sourceFetch.failed > 0) gaps.push(`节点明细仅加载 ${options.sourceFetch.loaded}/${options.sourceFetch.requested} 个；${options.sourceFetch.failed} 个读取失败或超时，当前运行图不完整。`)
  else if (options.sourceFetch && options.sourceFetch.loaded < options.sourceFetch.requested && (options.sourceFetch.deferred ?? 0) > 0) gaps.push(`节点明细已加载 ${options.sourceFetch.loaded}/${options.sourceFetch.requested} 个；其余 ${options.sourceFetch.deferred} 个按需读取，当前运行图仍不完整。`)
  else if (options.sourceFetch && options.sourceFetch.loaded < options.sourceFetch.requested) gaps.push(`节点明细正在加载 ${options.sourceFetch.loaded}/${options.sourceFetch.requested} 个；当前运行图仍不完整。`)
  if (!detail.graph.nodes.length) gaps.push('运行详情未提供阶段摘要；无法核对旧版兼容数据。')

  const toolCounts = new Map<string, number>()
  for (const call of Object.values(calls)) toolCounts.set(call.tool_name, (toolCounts.get(call.tool_name) ?? 0) + 1)
  const parallelGroups = parallelRelationSignatures.size
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
  return { nodes, edges, positions: computePositions(nodes), calls, events, toolGroups, sourceFetch: options.sourceFetch, gaps, stats }
}
