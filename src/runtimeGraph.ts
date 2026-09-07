import type {
  CandidatePreview,
  GraphEdgeDetail,
  GraphStage,
  NodeDetail,
  RunDetail,
  RuntimeSummaryTool,
  TimelineEvent,
  ToolAttempt,
} from './types'

export interface RuntimeGraphStats {
  observedCalls: number
  observedEvents: number
  openActivities: number
  repeatedTools: number
  toolRetries: number
  activityRetries: number
  /** @deprecated Use toolRetries; this compatibility field never includes lifecycle activity retries. */
  retries: number
  parallelGroups: number
  cycles: number
  unfinished: number
  generations: number
  toolSummaryRecords: number
  toolSummaryMaterialized: number
  toolSummaryMissing: number
  eventWindowAtLimit: boolean
}

export interface RuntimeEventWindow {
  returned: number
  limit: number
  atLimit: boolean
  mayBeTruncated: boolean
  remaining?: number
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
  eventWindow: RuntimeEventWindow
}

/** The read-only observer currently hard-limits run events to the newest 32 rows. */
export const runtimeEventWindowLimit = 32

export function runtimeEventWindow(events: TimelineEvent[], metadata?: RunDetail['event_window']): RuntimeEventWindow {
  const returned = events.length
  const limit = Number.isInteger(metadata?.limit) && (metadata?.limit ?? 0) > 0 ? metadata!.limit! : runtimeEventWindowLimit
  const atLimit = returned >= limit
  const mayBeTruncated = metadata?.has_more === false ? false : metadata?.has_more === true ? true : atLimit
  const remaining = Number.isInteger(metadata?.remaining) && (metadata?.remaining ?? 0) > 0 ? metadata?.remaining : undefined
  return remaining === undefined ? { returned, limit, atLimit, mayBeTruncated } : { returned, limit, atLimit, mayBeTruncated, remaining }
}

export function nextExpandedRuntimeGroups(current: ReadonlySet<string>, id: string) {
  return current.has(id) ? new Set<string>() : new Set([id])
}

export interface RuntimeGraphOptions {
  expandedGroups?: ReadonlySet<string>
  /** Width of the actual graph viewport in CSS pixels; never read from window here. */
  availableWidth?: number
  /** Explicit override for deterministic callers and tests. */
  layoutColumns?: number
  sourceFetch?: {
    requested: number
    loaded: number
    failed: number
    deferred?: number
  }
}

type Sources = Record<string, NodeDetail | undefined>

export interface ToolSummaryGap extends RuntimeSummaryTool {}

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

type ToolSummaryRow = RuntimeSummaryTool

function normalizedSummaryStatus(status: string) {
  return status.trim().toLowerCase()
}

function summaryStatusLabel(status: string) {
  const known = new Set(['queued', 'submitted', 'started', 'running', 'progress', 'succeeded', 'completed', 'failed', 'cancelled', 'stopped'])
  return known.has(status) ? statusLabel(status) : '未识别状态'
}

function toolSummaryRows(toolSummary: RunDetail['tool_summary'] | undefined, calls: ToolAttempt[]) {
  const materialized = new Map<string, number>()
  for (const call of calls) {
    // Lifecycle-only observations are evidence of an event, not a materialized
    // ToolAttempt row and must not consume run-level summary counts.
    if (call.tool_name.startsWith('observed_lifecycle:')) continue
    const key = `${call.tool_name}\u0000${normalizedSummaryStatus(call.status)}`
    materialized.set(key, (materialized.get(key) ?? 0) + 1)
  }
  const rows: ToolSummaryRow[] = []
  for (const [toolName, statusCountsValue] of Object.entries(toolSummary ?? {})) {
    if (!statusCountsValue || typeof statusCountsValue !== 'object') continue
    const normalizedCounts = new Map<string, number>()
    let summaryCount = 0
    for (const [rawStatus, rawCount] of Object.entries(statusCountsValue)) {
      if (typeof rawCount !== 'number' || !Number.isInteger(rawCount) || rawCount < 0) continue
      const status = normalizedSummaryStatus(rawStatus)
      normalizedCounts.set(status, (normalizedCounts.get(status) ?? 0) + rawCount)
      summaryCount += rawCount
    }
    const statusCounts = Object.fromEntries(normalizedCounts)
    const materializedCount = [...normalizedCounts.entries()].reduce((total, [status, count]) => total + Math.min(count, materialized.get(`${toolName}\u0000${status}`) ?? 0), 0)
    const missingCount = Math.max(0, summaryCount - materializedCount)
    if (summaryCount > 0) rows.push({
      tool_name: toolName,
      display_name: displayToolName(toolName),
      summary_count: summaryCount,
      materialized_count: materializedCount,
      missing_count: missingCount,
      status_counts: statusCounts,
    })
  }
  return rows
}

export function deriveToolSummaryGaps(toolSummary: RunDetail['tool_summary'] | undefined, calls: ToolAttempt[]) {
  return toolSummaryRows(toolSummary, calls).filter((row) => row.missing_count > 0)
}

function toolSummaryCoverage(toolSummary: RunDetail['tool_summary'] | undefined, calls: ToolAttempt[]) {
  return toolSummaryRows(toolSummary, calls).reduce((coverage, row) => ({
    total: coverage.total + row.summary_count,
    materialized: coverage.materialized + row.materialized_count,
    missing: coverage.missing + row.missing_count,
  }), { total: 0, materialized: 0, missing: 0 })
}

function collectCalls(sources: Sources, lifecycleEvents: TimelineEvent[] = []) {
  const result: Record<string, ToolAttempt> = {}
  for (const detail of Object.values(sources)) {
    for (const call of detail?.calls ?? []) {
      result[call.id] ??= call
    }
  }
  for (const observedCall of deriveLifecycleToolCalls(lifecycleEvents)) {
    const materialized = result[observedCall.id]
    if (!materialized) {
      result[observedCall.id] = observedCall
      continue
    }
    // Keep the node-detail ToolAttempt authoritative, but retain only the
    // structured relation fields observed on lifecycle payloads so explicit
    // edges are not lost when the two read paths overlap.
    const observedInputs = record(observedCall.inputs)
    const materializedInputs = record(materialized.inputs)
    result[observedCall.id] = {
      ...materialized,
      inputs: { ...observedInputs, ...materializedInputs },
      activity_type: materialized.activity_type ?? observedCall.activity_type,
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

function lifecycleStatus(event: TimelineEvent) {
  const payloadStatus = text(record(event.payload).status).trim().toLowerCase()
  if (['queued', 'submitted', 'started', 'running', 'progress', 'succeeded', 'completed', 'failed', 'cancelled', 'stopped'].includes(payloadStatus)) return payloadStatus
  const observedStatus = runtimeEventStatus(event)
  if (observedStatus === 'completed') return 'succeeded'
  if (observedStatus === 'running') return 'running'
  if (observedStatus === 'stopped') return 'failed'
  return 'pending'
}

function lifecycleRelationInputs(payload: Record<string, unknown>) {
  const result: Record<string, unknown> = {}
  const visit = (value: unknown) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return
    for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
      const normalized = key.toLowerCase()
      if (dependencyKeys.has(normalized) || retryKeys.has(normalized) || fallbackKeys.has(normalized) || parallelKeys.has(normalized) || batchKeys.has(normalized)) result[key] = nested
      else visit(nested)
    }
  }
  visit(payload)
  return result
}

/**
 * Materializes only tool_call_id-bearing lifecycle observations. This is not
 * a replacement for node-detail ToolAttempt rows: it is a read-only coverage
 * bridge for explicit IDs, with unknown tool/attempt fields left unknown.
 */
export function deriveLifecycleToolCalls(events: TimelineEvent[]): ToolAttempt[] {
  const byId = new Map<string, ToolAttempt>()
  const ordered = [...events].sort((left, right) => {
    const leftTime = Date.parse(left.occurred_at)
    const rightTime = Date.parse(right.occurred_at)
    return (Number.isFinite(leftTime) ? leftTime : Number.MAX_SAFE_INTEGER) - (Number.isFinite(rightTime) ? rightTime : Number.MAX_SAFE_INTEGER) || left.sequence_no - right.sequence_no
  })
  for (const event of ordered) {
    const payload = record(event.payload)
    const id = text(payload.tool_call_id).trim()
    if (!id) continue
    const status = lifecycleStatus(event)
    const attempt = integerField(payload.attempt)
    const activityType = text(payload.activity_type).trim() || undefined
    const isActive = activeStatuses.has(status)
    const isTerminal = callStatuses.has(status) || stoppedStatuses.has(status)
    const existing = byId.get(id)
    if (!existing) {
      byId.set(id, {
        id,
        tool_name: activityType ?? `observed_lifecycle:${event.type}`,
        activity_type: activityType,
        attempt: attempt ?? 1,
        attempt_observed: attempt !== null,
        tool_version: text(payload.tool_version) || '未返回',
        status,
        queued_at: event.occurred_at,
        started_at: isActive ? event.occurred_at : null,
        finished_at: isTerminal ? event.occurred_at : null,
        duration_seconds: null,
        random_seed: null,
        model_uri: null,
        weights_sha256: null,
        environment_sha256: '',
        input_sha256: '',
        output_sha256: null,
        inputs: lifecycleRelationInputs(payload),
        parameters: {},
        error: payload.error_message ?? payload.error_type ?? null,
        artifacts: [],
      })
      continue
    }
    const existingAttempt = integerField(existing.attempt)
    const startedAt = existing.started_at ?? (isActive ? event.occurred_at : null)
    const finishedAt = isTerminal ? event.occurred_at : existing.finished_at
    byId.set(id, {
      ...existing,
      activity_type: existing.activity_type ?? activityType,
      attempt: Math.max(existingAttempt ?? 1, attempt ?? 1),
      attempt_observed: existing.attempt_observed === true || attempt !== null,
      status,
      started_at: startedAt,
      finished_at: finishedAt,
      inputs: { ...record(existing.inputs), ...lifecycleRelationInputs(payload) },
      error: payload.error_message ?? payload.error_type ?? existing.error,
    })
  }
  return [...byId.values()]
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
  // A lifecycle-only observation has no tool name to justify a fallback
  // segment. Keep it isolated by its explicit tool_call_id instead of
  // claiming that neighboring observations share a batch.
  if (!call.activity_type && call.tool_name.startsWith('observed_lifecycle:')) return `observed_tool_call_id=${call.id}`
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
  if (event.type.toLowerCase().includes('recovery_scheduled')) return 'completed'
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
  'autoresearch-frozen-action-executor': '冻结动作执行',
  'autoresearch-multi-front-archive': '多前沿归档',
  'autoresearch-multi-front-rule-planner': '多前沿规则规划',
  'autoresearch-replay-bundle': '重放证据包',
  'v38-metric-hemolysis_risk': '溶血风险评估',
  'v38-metric-mic_potency': 'MIC 活性预测',
  'v38-metric-mic_potency_amp_read': 'AMP read 活性复核',
  'v38-metric-physicochemical_developability': '理化可开发性评估',
  'v38-metric-toxicity_risk': '毒性风险评估',
}

const eventLabels: Record<string, string> = {
  'run.created': '运行已创建',
  'run.started': '运行开始',
  'run.succeeded': '运行完成',
  'run.failed': '运行失败',
  'run.cancelled': '运行已取消',
  'mvp_human.autoresearch.recovery_scheduled': '恢复调度',
  'tool_call.started': '工具调用开始',
  'tool_call.completed': '工具调用完成',
  'tool_call.succeeded': '工具调用成功',
  'tool_call.failed': '工具调用失败',
  'candidate.created': '候选已记录',
  'candidate.scored': '候选已评分',
  'candidate.rejected': '候选已淘汰',
  'agent_decision.recorded': '智能体决策已记录',
  'autoresearch.action.recorded': '生成动作已记录',
  'autoresearch.archive.updated': '多前沿归档已更新',
  'autoresearch.checkpoint.recorded': '迭代检查点已记录',
  'v38.sequence_metric.persisted': '序列指标已持久化',
}

const eventStateLabels: Record<string, string> = { started: '开始', running: '进行中', progress: '进度更新', completed: '完成', succeeded: '成功', failed: '失败', cancelled: '已取消', created: '已创建', persisted: '已持久化', materialized: '已物化', recorded: '已记录', accepted: '已接受', rejected: '已淘汰' }

function displayEventState(type: string) {
  const suffix = type.split('.').at(-1) ?? type
  return eventStateLabels[suffix] ?? '事件'
}

export function displayToolName(toolName: string) {
  if (toolLabels[toolName]) return toolLabels[toolName]
  const normalized = toolName.toLowerCase()
  if (normalized.startsWith('observed_lifecycle:')) return '生命周期观测'
  if (/v38[-_.]?generate.*ampgan|ampgan.*generate/.test(normalized)) return 'AMPGAN v2 生成'
  if (/v38[-_.]?generate.*hydramp|hydramp.*generate/.test(normalized)) return 'HydrAMP 生成'
  if (/boltz|multitarget.*structure/.test(normalized)) return 'Boltz 2 结构预测'
  if (/rosetta|interface.*refin/.test(normalized)) return 'Rosetta 界面精修'
  if (/generate|design/.test(normalized)) return '候选生成'
  if (/score|rank|admission/.test(normalized)) return '候选评分与筛选'
  return '未命名工具'
}

export function displayEventName(type: string) {
  if (eventLabels[type]) return eventLabels[type]
  const normalized = type.toLowerCase()
  const state = displayEventState(type)
  if (/multitarget.*structure/.test(normalized)) return `结构证据 · ${state}`
  if (/scored.*lineage|lineage.*scored/.test(normalized)) return `评分谱系 · ${state}`
  if (/operational_run/.test(normalized)) return `运行记录 · ${state}`
  if (/operational\.call/.test(normalized)) return `工具调用 · ${state}`
  return `未命名事件 · ${state}`
}

const eventSemanticLabels: Record<string, string> = {
  'run.created': '运行',
  'run.started': '运行',
  'run.succeeded': '运行',
  'run.failed': '运行',
  'run.cancelled': '运行',
  'mvp_human.autoresearch.recovery_scheduled': '恢复调度',
  'tool_call.started': '工具调用',
  'tool_call.completed': '工具调用',
  'tool_call.succeeded': '工具调用',
  'tool_call.failed': '工具调用',
  'candidate.created': '候选',
  'candidate.scored': '候选评分',
  'candidate.rejected': '候选筛选',
  'agent_decision.recorded': '智能体决策',
  'autoresearch.action.recorded': '生成动作',
  'autoresearch.archive.updated': '多前沿归档',
  'autoresearch.checkpoint.recorded': '迭代检查点',
  'v38.sequence_metric.persisted': '序列指标',
}

/** Stable scientific subject used for aggregate display; raw event type stays in metadata. */
export function displayEventSemanticName(type: string) {
  const normalized = type.toLowerCase()
  if (/operational\.call|tool_call\./.test(normalized)) return `工具调用 · ${displayEventState(type)}`
  if (eventSemanticLabels[type]) return eventSemanticLabels[type]
  if (/multitarget.*structure/.test(normalized)) return '结构证据'
  if (/scored.*lineage|lineage.*scored/.test(normalized)) return '评分谱系'
  if (/operational_run/.test(normalized)) return '运行记录'
  if (/operational\.call/.test(normalized)) return `工具调用 · ${displayEventState(type)}`
  if (/tool_call\./.test(normalized)) return `工具调用 · ${displayEventState(type)}`
  return '未命名事件'
}

function displayActor(actor: string) {
  const normalized = actor.toLowerCase()
  if (/human|scientist|user/.test(normalized)) return '科研人员'
  if (/observer|writer|persist|database|postgres/.test(normalized)) return '观察器记录器'
  if (/scheduler|temporal/.test(normalized)) return '任务调度器'
  if (/worker|agent|orchestrator|workflow/.test(normalized)) return '运行编排器'
  return '运行参与者'
}

function integerField(value: unknown) {
  if (typeof value === 'number' && Number.isInteger(value) && Number.isFinite(value) && value >= 0) return value
  if (typeof value === 'string' && /^\d+$/.test(value.trim())) return Number(value)
  return null
}

function recoveryAttemptLabel(event: TimelineEvent) {
  if (!event.type.toLowerCase().includes('recovery')) return null
  const attempt = integerField(record(event.payload).recovery_attempt)
  return attempt !== null && attempt > 0 ? `第 ${attempt} 次恢复调度` : null
}

function activityAttempt(event: TimelineEvent) {
  const attempt = integerField(record(event.payload).attempt)
  return attempt !== null && attempt > 0 ? attempt : null
}

function activityAttemptLabel(event: TimelineEvent) {
  const attempt = activityAttempt(event)
  return attempt === null ? null : `第 ${attempt} 次尝试`
}

function displayActivityType(activityType: string) {
  const labels: Record<string, string> = {
    mark_run_started: '运行启动',
    persist_autoresearch_score_all_bundle: '评分汇总持久化',
    plan_autoresearch_actions: '生成规划',
    persist_autoresearch_action_plan: '规划持久化',
    execute_autoresearch_action_batch: '执行操作批次',
    persist_autoresearch_children: '候选持久化',
    generate_v38_sequence_cell: '序列生成',
    persist_v38_score_all_generation: '代际评分持久化',
    evaluate_v38_sequence_metric: '序列指标计算',
    persist_v38_sequence_metric: '指标持久化',
    evaluate_v38_sequence_admission: '候选准入评估',
    persist_v38_sequence_admission: '准入结果持久化',
    refine_v38_sequences_with_knowledge: '知识引导精修',
    persist_v38_refinement_children: '精修候选持久化',
    plan_v38_multitarget_structure: '结构预测规划',
    predict_v38_multitarget_structure: 'Boltz 结构预测',
    persist_v38_multitarget_boltz: 'Boltz 结构持久化',
    score_v38_multitarget_rosetta: 'Rosetta 界面评分',
    persist_v38_multitarget_rosetta: 'Rosetta 结果持久化',
    persist_v38_final_portfolio_replay: '最终组合复核',
    finalize_autoresearch_iteration: '迭代收束',
    mark_run_succeeded: '运行完成',
    mark_run_failed: '运行失败',
    mark_run_cancelled: '运行取消',
  }
  return labels[activityType]
}

export function displayEventContext(payload?: unknown) {
  const value = record(payload)
  const context: string[] = []
  const iteration = integerField(value.iteration_no)
  const generation = integerField(value.generation)
  if (iteration !== null) context.push(`第 ${iteration} 轮`)
  if (generation !== null) context.push(`第 ${generation} 代`)
  return context
}

/** Use the persisted activity type for lifecycle event detail labels. */
export function displayObservedEventName(type: string, payload?: unknown) {
  const activityType = text(record(payload).activity_type).trim()
  const base = activityType
    ? `${displayActivityType(activityType) ?? '活动'} · ${displayEventState(type)}`
    : type.toLowerCase().startsWith('activity.')
    ? `活动 · ${displayEventState(type)}`
    : displayEventName(type)
  const context = displayEventContext(payload)
  return context.length ? `${base} · ${context.join(' · ')}` : base
}

function displayErrorCategory(errorCategory: string) {
  const labels: Record<string, string> = {
    timeout: '超时',
    statement_timeout: '数据库超时',
    timeouterror: '超时',
    workertimeout: '工作器超时',
    connectivity: '连接异常',
    connectionerror: '连接异常',
    operationalerror: '服务连接异常',
    unavailable: '服务不可用',
    permission: '权限不足',
    permissionerror: '权限不足',
    worker_execution_not_authorized: '工作器未获授权',
    not_found: '未找到',
    notfounderror: '未找到',
    application_error: '应用错误',
    valueerror: '应用错误',
    runtimeerror: '应用错误',
    activityerror: '活动执行错误',
    cancelled: '已取消',
    cancellederror: '已取消',
    unknown: '未知错误',
  }
  return labels[errorCategory.trim().toLowerCase()]
}

function isTerminalActivityEvent(event: TimelineEvent) {
  const payload = record(event.payload)
  const activityId = payload.activity_id
  const hasActivityIdentity = Boolean(text(payload.workflow_run_id)) && (typeof activityId === 'string' || typeof activityId === 'number')
  const suffix = event.type.toLowerCase().split('.').at(-1)
  return hasActivityIdentity && ['succeeded', 'completed', 'failed', 'cancelled'].includes(suffix ?? '')
}

function activityLogicalIdentity(event: TimelineEvent) {
  const payload = record(event.payload)
  const workflowRunId = text(payload.workflow_run_id).trim()
  const activityId = payload.activity_id
  if (!workflowRunId || (typeof activityId !== 'string' && typeof activityId !== 'number')) return null
  return `${workflowRunId}:activity=${activityId}`
}

function activityBoundaryIdentity(event: TimelineEvent) {
  const identity = activityLogicalIdentity(event)
  const attempt = integerField(record(event.payload).attempt)
  if (!identity || attempt === null || attempt < 1) return null
  return `${identity}:attempt=${attempt}`
}

function activityBoundaryEvents(events: TimelineEvent[]) {
  return [...events].sort((left, right) => {
    const leftTime = Date.parse(left.occurred_at)
    const rightTime = Date.parse(right.occurred_at)
    return (Number.isFinite(leftTime) ? leftTime : Number.MAX_SAFE_INTEGER) - (Number.isFinite(rightTime) ? rightTime : Number.MAX_SAFE_INTEGER) || left.sequence_no - right.sequence_no
  })
}

export function countOpenActivities(events: TimelineEvent[]) {
  const states = new Map<string, boolean>()
  for (const event of activityBoundaryEvents(events)) {
    const identity = activityBoundaryIdentity(event)
    if (!identity) continue
    const suffix = event.type.toLowerCase().split('.').at(-1)
    if (suffix === 'started' || suffix === 'running') states.set(identity, true)
    else if (['succeeded', 'completed', 'failed', 'cancelled'].includes(suffix ?? '')) states.set(identity, false)
  }
  return [...states.values()].filter(Boolean).length
}

function activityAttemptsByIdentity(events: TimelineEvent[]) {
  const attempts = new Map<string, number>()
  for (const event of events) {
    const identity = activityLogicalIdentity(event)
    const attempt = identity ? integerField(record(event.payload).attempt) : null
    if (!identity || attempt === null) continue
    attempts.set(identity, Math.max(attempts.get(identity) ?? 0, attempt))
  }
  return attempts
}

export function countActivityRetries(events: TimelineEvent[]) {
  return [...activityAttemptsByIdentity(events).values()].filter((attempt) => attempt > 1).length
}

export function runtimeRetrySummary(toolRetries: number, activityRetries: number) {
  const labels: string[] = []
  if (Number.isInteger(activityRetries) && activityRetries > 0) labels.push(`活动重试 ${activityRetries}`)
  if (Number.isInteger(toolRetries) && toolRetries > 0) labels.push(`工具重试 ${toolRetries}`)
  return labels
}

/**
 * Distinguishes the authoritative run-level tool record count from the
 * subset currently materialized from node-detail responses. The graph must
 * not present a partial detail read as the complete call set.
 */
export function runtimeCallSummary(observedCalls: number, authoritativeToolRecords?: number) {
  const observed = Number.isInteger(observedCalls) && observedCalls >= 0 ? observedCalls : 0
  const total = typeof authoritativeToolRecords === 'number' && Number.isInteger(authoritativeToolRecords) && authoritativeToolRecords >= observed
    ? authoritativeToolRecords
    : null
  if (total === null || total === observed) return `调用 ${observed}`
  return `调用 ${observed}/${total}（已映射）`
}

export function runtimeObservationSummary(observedCalls: number, materializedToolCalls: number, hasToolSummary: boolean) {
  if (!hasToolSummary) return runtimeCallSummary(observedCalls)
  const observed = Number.isInteger(observedCalls) && observedCalls >= 0 ? observedCalls : 0
  const materialized = Number.isInteger(materializedToolCalls) && materializedToolCalls >= 0 ? materializedToolCalls : 0
  return `图中观测 ${observed} · 工具明细 ${materialized} · 生命周期观测 ${Math.max(0, observed - materialized)}`
}

export function runtimeActivitySummary(runStatus: string, openActivities: number, eventWindowAtLimit = false) {
  const normalizedCount = Number.isInteger(openActivities) && openActivities >= 0 ? openActivities : 0
  if (runStatus === 'running' && normalizedCount === 0) {
    return eventWindowAtLimit ? '等待后续活动观测 · 更早事件未确认' : '等待后续活动观测'
  }
  if (runStatus === 'running' && eventWindowAtLimit) return `未闭合观测 ${normalizedCount} · 更早事件未确认`
  return `开放活动 ${normalizedCount}`
}

/**
 * Labels an activity boundary that is still open using only persisted
 * identity, status, and attempt fields. This is an execution observation,
 * not a scheduler-health or scientific-failure conclusion.
 */
export function runtimeOpenActivityLabel(events: TimelineEvent[], eventWindowAtLimit = false) {
  const open = new Map<string, TimelineEvent>()
  for (const event of activityBoundaryEvents(events)) {
    const identity = activityBoundaryIdentity(event)
    if (!identity) continue
    const suffix = event.type.toLowerCase().split('.').at(-1)
    if (suffix === 'started' || suffix === 'running') open.set(identity, event)
    else if (['succeeded', 'completed', 'failed', 'cancelled'].includes(suffix ?? '')) open.delete(identity)
  }
  if (!open.size) return null
  const labels = new Map<string, number>()
  let latestAttempt = 1
  for (const event of open.values()) {
    const payload = record(event.payload)
    const label = displayActivityType(text(payload.activity_type)) ?? '活动'
    labels.set(label, (labels.get(label) ?? 0) + 1)
    latestAttempt = Math.max(latestAttempt, integerField(payload.attempt) ?? 1)
  }
  const labelText = labels.size === 1
    ? [...labels.entries()].map(([label, count]) => count > 1 ? `${label} ${count} 项` : label).join('')
    : `${open.size} 项活动`
  return `${eventWindowAtLimit ? '未闭合观测' : '正在执行'} · ${labelText}${latestAttempt > 1 ? ` · 第 ${latestAttempt} 次尝试` : ''}${eventWindowAtLimit ? ' · 更早事件未确认' : ''}`
}

function latestTerminalActivityEvent(events: TimelineEvent[]) {
  return events.filter(isTerminalActivityEvent).sort((left, right) => {
    const leftTime = Date.parse(left.occurred_at)
    const rightTime = Date.parse(right.occurred_at)
    return (Number.isFinite(leftTime) ? leftTime : Number.MIN_SAFE_INTEGER) - (Number.isFinite(rightTime) ? rightTime : Number.MIN_SAFE_INTEGER) || left.sequence_no - right.sequence_no
  }).at(-1)
}

function executionFacts(events: TimelineEvent[]) {
  if (!events.some((event) => eventExecutionIdentity(event.payload))) return []
  const latest = latestTerminalActivityEvent(events)
  if (!latest) return []
  const payload = record(latest.payload)
  const facts: Array<{ label: string; value: string }> = []
  const activityType = text(payload.activity_type)
  const activityLabel = activityType ? displayActivityType(activityType) : undefined
  if (activityLabel) facts.push({ label: stoppedStatuses.has(runtimeEventStatus(latest)) ? '停止位置' : '最近活动', value: activityLabel })
  const errorCategory = text(payload.error_category) || text(payload.error_type)
  const errorLabel = errorCategory ? displayErrorCategory(errorCategory) : undefined
  if (errorLabel) facts.push({ label: '错误类别', value: errorLabel })
  const completed = integerField(payload.completed)
  const expected = integerField(payload.expected)
  if (completed !== null && expected !== null) facts.push({ label: '进度', value: `${completed}/${expected}` })
  return facts
}

function activityRetryStats(events: TimelineEvent[]) {
  const attempts = activityAttemptsByIdentity(events)
  const retriedActivities = [...attempts.values()].filter((attempt) => attempt > 1)
  return {
    count: retriedActivities.length,
    maxAttempt: attempts.size ? Math.max(...attempts.values()) : 1,
  }
}

function retryEvidenceEvents(events: TimelineEvent[]) {
  const attempts = activityAttemptsByIdentity(events)
  const retried = [...attempts.entries()].filter(([, attempt]) => attempt > 1)
  if (!retried.length) return []
  const latestIdentity = retried
    .map(([identity]) => ({
      identity,
      observedAt: Math.max(...events.filter((event) => activityLogicalIdentity(event) === identity).map((event) => Date.parse(event.occurred_at)).filter(Number.isFinite)),
    }))
    .sort((left, right) => right.observedAt - left.observedAt)[0]?.identity
  if (!latestIdentity) return []
  return activityBoundaryEvents(events).filter((event) => activityLogicalIdentity(event) === latestIdentity && isTerminalActivityEvent(event))
}

function activityAttemptFacts(events: TimelineEvent[]) {
  const retry = activityRetryStats(events)
  return retry.count ? [{ label: '活动重试', value: `${retry.count} 个活动 · 最高第 ${retry.maxAttempt} 次` }] : []
}

function eventNode(event: TimelineEvent): GraphStage {
  const recoveryLabel = recoveryAttemptLabel(event)
  const status = runtimeEventStatus(event)
  const baseEventName = displayObservedEventName(event.type, event.payload)
  const eventName = activityAttemptLabel(event) ? `${baseEventName} · ${activityAttemptLabel(event)}` : baseEventName
  const distributionKey = distributionKeyForActivity(record(event.payload).activity_type, event.payload)
  return {
    id: `event:${event.sequence_no}`,
    label: recoveryLabel ?? eventName,
    kind: 'decision',
    group: 'observed',
    status,
    current: 1,
    total: 1,
    provenance: 'database',
    insight: {
      grade: recoveryLabel ? 'okay' : status === 'completed' ? 'good' : status === 'stopped' ? 'bad' : status === 'running' ? 'okay' : 'neutral',
      verdict: recoveryLabel ? '已调度' : statusLabel(status),
      reason: recoveryLabel ? '恢复调度已记录' : baseEventName,
      facts: [
        { label: '语义', value: baseEventName },
        ...displayEventContext(event.payload).map((value) => ({ label: value.endsWith('轮') ? '轮次' : '代际', value })),
        { label: '序号', value: String(event.sequence_no) },
        ...(activityAttemptLabel(event) ? [{ label: '尝试', value: activityAttemptLabel(event)! }] : []),
        ...(recoveryLabel ? [{ label: '恢复', value: recoveryLabel }] : []),
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
      ...(distributionKey ? { evidence_key: distributionKey, distribution_key: distributionKey } : {}),
      parallel_group_id: parallelGroupIds(event.payload)[0],
    },
  }
}

function callNode(call: ToolAttempt): GraphStage {
  const status = nodeStatus(call.status)
  const artifactCount = call.artifacts?.length ?? 0
  const observedAttempt = call.attempt_observed !== false
  const semanticLabel = call.activity_type ? displayActivityType(call.activity_type) : undefined
  return {
    id: `call:${call.id}`,
    label: semanticLabel ?? displayToolName(call.tool_name),
    kind: 'tool',
    group: 'observed',
    status,
    current: callStatuses.has(call.status) ? 1 : 0,
    total: 1,
    provenance: 'database',
    insight: {
      grade: gradeFor(call.status),
      verdict: statusLabel(call.status),
        reason: `${observedAttempt ? `第 ${call.attempt} 次尝试 · ` : ''}已记录工具证据`,
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
      ...(distributionKeyForTool(call.tool_name) ? {
        evidence_key: distributionKeyForTool(call.tool_name),
        distribution_key: distributionKeyForTool(call.tool_name),
      } : {}),
      parallel_group_id: parallelGroupIds(call.inputs)[0],
    },
  }
}

type ViewerEntry = [key: string, artifact: NonNullable<RunDetail['viewer']>]

function viewerEntries(detail: RunDetail, sources: Sources) {
  const entries: ViewerEntry[] = []
  const artifactHashes = new Set<string>()
  const addEntry = (key: string, artifact: NonNullable<RunDetail['viewer']> | null | undefined) => {
    if (!artifact || entries.some(([entryKey]) => entryKey === key)) return
    if (artifact.artifact_sha256 && artifactHashes.has(artifact.artifact_sha256)) return
    if (artifact.artifact_sha256) artifactHashes.add(artifact.artifact_sha256)
    entries.push([key, artifact])
  }
  for (const [key, artifact] of Object.entries(detail.viewers ?? {})) {
    addEntry(key, artifact)
  }
  addEntry('__default__', detail.viewer)
  for (const source of Object.values(sources)) {
    for (const [key, artifact] of Object.entries(source?.viewers ?? {})) {
      addEntry(key, artifact)
    }
    if (source?.node_id) addEntry(source.node_id, source.viewer)
  }
  return entries
}

function viewerMappingForTool(toolName: string, sourceNodeId: string | undefined, entries: ViewerEntry[]) {
  const normalizedTool = toolName.toLowerCase()
  const directSource = sourceNodeId && entries.find(([key]) => key === sourceNodeId)
  if (directSource) return { key: directSource[0], basis: '后端节点 viewer' as const }
  const directTool = entries.find(([key]) => key !== '__default__' && key.toLowerCase() === normalizedTool)
  if (directTool) return { key: directTool[0], basis: '后端 viewer 键' as const }
  // Historical payloads do not always expose the source node key. The only
  // permitted compatibility mapping is an explicit Boltz/Rosetta tool name;
  // generic labels and UI text never participate in this decision.
  const allowedToolFamily = /(^|[-_.:])(boltz|rosetta)([-_.:]|$)/.exec(normalizedTool)?.[2]
  if (!allowedToolFamily) return undefined
  const familyEntry = entries.find(([key]) => key !== '__default__' && key.toLowerCase().includes(allowedToolFamily))
  if (familyEntry) return { key: familyEntry[0], basis: '限定工具名映射' as const }
  const defaultEntry = entries.find(([key]) => key === '__default__')
  return defaultEntry ? { key: defaultEntry[0], basis: '限定工具名映射' as const } : undefined
}

/**
 * Maps only the persisted metric tool identities that have a corresponding
 * ResultDistribution key. This is deliberately separate from viewer_key:
 * structure artifacts and numeric result artifacts are different evidence.
 */
export function distributionKeyForTool(toolName: string | undefined) {
  const normalized = toolName?.trim().toLowerCase() ?? ''
  if (normalized === 'v38-metric-mic_potency_amp_read' || normalized.endsWith(':mic_potency_amp_read') || normalized.endsWith('-mic_potency_amp_read')) return 'amp_read'
  if (normalized === 'v38-metric-mic_potency' || normalized.endsWith(':mic_potency') || normalized.endsWith('-mic_potency')) return 'mic'
  if (normalized === 'v38-metric-hemolysis_risk' || normalized.endsWith(':hemolysis_risk') || normalized.endsWith('-hemolysis_risk')) return 'hemolysis'
  if (normalized === 'v38-metric-toxicity_risk' || normalized.endsWith(':toxicity_risk') || normalized.endsWith('-toxicity_risk')) return 'toxicity'
  if (normalized === 'v38-metric-physicochemical_developability' || normalized.endsWith(':physicochemical_developability') || normalized.endsWith('-physicochemical_developability')) return 'developability'
  if (normalized.includes('generator') || normalized.includes('action-executor') || normalized.includes('sequence-generation')) return 'candidate_pool'
  return undefined
}

function distributionKeyForActivity(activityType: unknown, payload: unknown) {
  const normalized = text(activityType).trim().toLowerCase()
  const context = record(payload)
  const logicalStage = text(context.logical_stage).trim().toLowerCase()
  const displayCategory = text(context.display_category).trim().toLowerCase()
  if (normalized.includes('generate') || normalized.includes('materialize') || logicalStage.includes('generation') || displayCategory === 'generation') return 'candidate_pool'
  if (normalized.includes('evaluate') || normalized.includes('score') || logicalStage.includes('metric') || displayCategory === 'evaluation') return 'mic'
  return undefined
}

function structureEvidenceNode(key: string, artifact: NonNullable<RunDetail['viewer']>): GraphStage {
  const normalizedKey = key.toLowerCase()
  const label = normalizedKey.includes('boltz')
    ? 'Boltz 结构证据'
    : normalizedKey.includes('rosetta')
      ? 'Rosetta 结构证据'
      : '结构证据'
  return {
    id: `structure-evidence:${encodeURIComponent(key)}`,
    label,
    kind: 'structure',
    group: 'structure',
    status: 'completed',
    current: 1,
    total: 1,
    provenance: 'database',
    insight: {
      grade: 'good',
      verdict: '结构已记录',
      reason: '后端返回结构 viewer 证据',
      facts: [
        ...(artifact.target_name ? [{ label: '靶点', value: artifact.target_name }] : []),
        ...(artifact.media_type ? [{ label: '格式', value: artifact.media_type }] : []),
      ],
      source: 'observer_summary',
    },
    runtime: {
      node_type: 'structure_evidence',
      source_id: key,
      observed_at: null,
      raw_label: key,
      has_viewer: true,
      viewer_key: key,
      viewer_mapping_basis: '后端 viewer 键',
      explicit_relation_count: 0,
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
    if (call.tool_name.startsWith('observed_lifecycle:')) continue
    const name = call.activity_type ? displayActivityType(call.activity_type) ?? displayToolName(call.tool_name) : displayToolName(call.tool_name)
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
    const name = recoveryAttemptLabel(event) ?? (activityType ? displayActivityType(activityType) ?? '运行活动' : displayEventName(event.type))
    counts.set(name, (counts.get(name) ?? 0) + 1)
  }
  return [...counts.entries()].map(([name, count]) => `${name} ${count}`).join(' · ')
}

function eventContextFacts(events: TimelineEvent[]) {
  const iterations = new Set<string>()
  const generations = new Set<string>()
  for (const event of events) {
    for (const context of displayEventContext(event.payload)) {
      if (context.endsWith('轮')) iterations.add(context)
      if (context.endsWith('代')) generations.add(context)
    }
  }
  return [
    ...(iterations.size ? [{ label: '轮次', value: [...iterations].join('、') }] : []),
    ...(generations.size ? [{ label: '代际', value: [...generations].join('、') }] : []),
  ]
}

function aggregateSemanticLabel(calls: ToolAttempt[], events: TimelineEvent[]) {
  const callIds = new Set(calls.map((call) => call.id))
  const materializedCalls = calls.filter((call) => !call.tool_name.startsWith('observed_lifecycle:'))
  if (!materializedCalls.length && calls.length > 0 && !events.length) return '生命周期观测'
  const callLabels = materializedCalls
    .map((call) => call.activity_type ? displayActivityType(call.activity_type) ?? displayToolName(call.tool_name) : displayToolName(call.tool_name))
    .filter((label) => label !== '未命名工具')
  const eventLabelsForAggregate = events
    .filter((event) => !callIds.has(text(record(event.payload).tool_call_id)) || materializedCalls.length === 0)
    .map((event) => displayActivityType(text(record(event.payload).activity_type)) ?? displayEventSemanticName(event.type))
    .filter((label) => label !== '未命名事件')
  const labels = callLabels.length > 0 ? callLabels : eventLabelsForAggregate
  const uniqueLabels = [...new Set(labels)]
  return uniqueLabels.length === 1 ? uniqueLabels[0] : null
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

function runtimeGroupNode(groupedCalls: ToolAttempt[], groupedEvents: TimelineEvent[], expanded: boolean, groupId: string, groupingBasis: string, displayBatchLabel: string, eventWindowAtLimit = false): GraphStage {
  const groupedCallIds = new Set(groupedCalls.map((call) => call.id))
  const associatedEvents = groupedEvents.filter((event) => groupedCallIds.has(text(record(event.payload).tool_call_id)))
  const independentEvents = groupedEvents.filter((event) => !groupedCallIds.has(text(record(event.payload).tool_call_id)))
  const eventStatuses = eventOutcomeStatuses(independentEvents)
  const statuses = [...groupedCalls.map((call) => call.status), ...eventStatuses]
  const hasActive = statuses.some((status) => activeStatuses.has(status))
  const hasStopped = statuses.some((status) => stoppedStatuses.has(status))
  const hasCompleted = statuses.some((status) => callStatuses.has(status))
  const status = hasActive ? 'running' : hasStopped ? 'stopped' : statuses.length > 0 && statuses.every((item) => callStatuses.has(item)) ? 'completed' : 'pending'
  const grade = hasActive ? 'okay' : hasStopped && hasCompleted ? 'fair' : hasStopped ? 'bad' : statuses.length > 0 && statuses.every((item) => callStatuses.has(item)) ? 'good' : 'neutral'
  const retryCount = relationCount(groupedCalls, retryIds)
  const fallbackCount = relationCount(groupedCalls, fallbackIds)
  const parallelGroupIdsForBucket = [...new Set(groupedCalls.flatMap((call) => parallelGroupIds(call.inputs)))]
  const total = groupedCalls.length + eventStatuses.length
  const operationSummary = [operationComposition(groupedCalls), eventComposition(independentEvents), associatedEvents.length ? `关联事件 ${associatedEvents.length}` : ''].filter(Boolean).join(' · ')
  const statusSummary = statuses.map(statusLabel).reduce((counts, item) => counts.set(item, (counts.get(item) ?? 0) + 1), new Map<string, number>())
  const statusText = [...statusSummary.entries()].map(([label, count]) => `${label} ${count}`).join(' · ')
  const observedDates = [...groupedCalls.map((call) => observedAt(call)), ...groupedEvents.map((event) => event.occurred_at)].filter((value): value is string => Boolean(value)).sort()
  const observedSpanText = groupingBasis.startsWith('同工具 + 状态')
    ? '分散观测 · 不合并时间段'
    : observedDates.length > 1
      ? `时间跨度 ${Math.max(0, Math.round((Date.parse(observedDates.at(-1)!) - Date.parse(observedDates[0])) / 1000))} 秒`
      : '时间范围不完整'
  const recoveryLabels = [...new Set(groupedEvents.map(recoveryAttemptLabel).filter((value): value is string => Boolean(value)))]
  const executionFactList = executionFacts(groupedEvents)
  const activityRetry = activityRetryStats(groupedEvents)
  const retryEvidence = retryEvidenceEvents(groupedEvents)
  const openActivityLabel = runtimeOpenActivityLabel(independentEvents, eventWindowAtLimit)
  const progressFacts = executionFactList.filter(({ label }) => label === '进度')
  const salientFacts = [
    ...recoveryLabels.map((value) => ({ label: '恢复', value })),
    ...eventContextFacts(groupedEvents),
    ...executionFactList.filter(({ label }) => label !== '进度'),
    ...activityAttemptFacts(groupedEvents),
    ...progressFacts,
  ]
  const runtimeType = groupedEvents.length && !groupedCalls.length ? 'event_group' : groupedEvents.length ? 'batch_group' : 'tool_group'
  const distributionKeys = [...new Set(groupedCalls.map((call) => distributionKeyForTool(call.tool_name)).filter((key): key is NonNullable<ReturnType<typeof distributionKeyForTool>> => Boolean(key)))]
  const eventDistributionKeys = [...new Set(groupedEvents.map((event) => distributionKeyForActivity(record(event.payload).activity_type, event.payload)).filter((key): key is NonNullable<ReturnType<typeof distributionKeyForActivity>> => Boolean(key)))]
  const availableDistributionKeys = [...new Set([...distributionKeys, ...eventDistributionKeys])]
  const primaryDistributionKey = availableDistributionKeys.includes('mic') ? 'mic' : availableDistributionKeys[0]
  const countLabel = groupedCalls.length && eventStatuses.length ? `${groupedCalls.length} 次调用 · ${eventStatuses.length} 项活动` : groupedCalls.length ? `${groupedCalls.length} 次调用` : `${eventStatuses.length} 项活动`
  const cardConclusion = groupingBasis.startsWith('后端执行字段')
    ? '同一执行标识下的已观测活动'
    : groupingBasis.startsWith('后端字段')
      ? '同一结构化批次的已观测操作'
      : groupingBasis.startsWith('事件关联字段')
        ? '同一工具调用的关联观测'
        : groupingBasis.startsWith('同工具 + 状态')
          ? '同工具同状态的展示聚合'
          : '连续观测聚合'
  const latestRetryOutcome = latestTerminalActivityEvent(groupedEvents)
  const retryOutcomeStatus = latestRetryOutcome ? runtimeEventStatus(latestRetryOutcome) : status
  const retryTitle = activityRetry.count > 0
    ? `活动重试 · 第 ${activityRetry.maxAttempt} 次${retryOutcomeStatus === 'stopped' ? '失败' : retryOutcomeStatus === 'completed' ? '完成' : '进行中'}`
    : null
  const primaryLabel = openActivityLabel ?? retryTitle ?? `${displayBatchLabel} · ${countLabel}`
  return {
    id: groupId,
    label: primaryLabel,
    kind: 'tool',
    group: 'observed',
    status,
    current: statuses.filter((item) => callStatuses.has(item)).length,
    total,
    provenance: 'derived',
    insight: {
      grade,
       verdict: countLabel,
      reason: openActivityLabel ?? cardConclusion,
      facts: [
          ...salientFacts,
          ...(operationSummary ? [{ label: '操作构成', value: operationSummary }] : []),
          ...(statusText ? [{ label: '状态', value: statusText }] : []),
          ...(observedSpanText ? [{ label: '时间', value: observedSpanText }] : []),
          ...((retryCount > 0 || fallbackCount > 0) ? [{ label: '关系', value: `重试 ${retryCount} · 回退 ${fallbackCount}` }] : []),
       ],
      source: 'observer_summary',
    },
    runtime: {
       node_type: runtimeType,
       source_id: groupId,
       tool_name: groupedCalls[0]?.tool_name,
       observed_at: retryTitle ? observedDates.at(-1) ?? null : observedDates[0] ?? null,
       child_ids: groupedCalls.map((call) => call.id),
       event_ids: (retryEvidence.length ? retryEvidence : groupedEvents).map((event) => `event:${event.sequence_no}`),
       grouping_basis: groupingBasis,
       expanded,
       status_breakdown: statusText,
       observed_span: observedSpanText,
      raw_label: groupedCalls[0]?.tool_name ?? groupedEvents[0]?.type,
      ...(primaryDistributionKey ? { evidence_key: primaryDistributionKey, distribution_key: primaryDistributionKey } : {}),
      candidate_count: groupedCalls.length,
      explicit_relation_count: 0,
      activity_retry_count: activityRetry.count,
      max_activity_attempt: activityRetry.maxAttempt,
      parallel_group_id: parallelGroupIdsForBucket.length === 1 ? parallelGroupIdsForBucket[0] : undefined,
    },
  }
}

function summaryStatusBreakdown(tools: RuntimeSummaryTool[]) {
  const counts = new Map<string, number>()
  for (const tool of tools) for (const [status, count] of Object.entries(tool.status_counts)) {
    counts.set(summaryStatusLabel(status), (counts.get(summaryStatusLabel(status)) ?? 0) + count)
  }
  return [...counts.entries()].map(([label, count]) => `${label} ${count}`).join(' · ') || '未返回状态'
}

function summaryOperationComposition(tools: RuntimeSummaryTool[]) {
  return tools.map((tool) => `${tool.display_name} ${tool.summary_count}`).join(' · ')
}

function toolSummaryGroupNode(tools: RuntimeSummaryTool[], coverage: { total: number; materialized: number; missing: number }, expanded: boolean, latestIteration: number | null, observedAt: string | null): GraphStage {
  const summaryCount = coverage.total
  const groupId = 'tool-summary-group'
  const statusCounts = tools.reduce<Record<string, number>>((result, tool) => {
    for (const [status, count] of Object.entries(tool.status_counts)) result[status] = (result[status] ?? 0) + count
    return result
  }, {})
  const status: GraphStage['status'] = Object.entries(statusCounts).some(([key, count]) => count > 0 && ['failed', 'cancelled', 'stopped'].includes(key))
    ? 'stopped'
    : Object.entries(statusCounts).some(([key, count]) => count > 0 && ['queued', 'running', 'pending'].includes(key)) ? 'running' : 'completed'
  const iterationLabel = latestIteration === null ? null : `第 ${latestIteration} 轮`
  const distributionKeys = tools.map((tool) => distributionKeyForTool(tool.tool_name)).filter((key): key is NonNullable<ReturnType<typeof distributionKeyForTool>> => Boolean(key))
  const primaryDistributionKey = distributionKeys.includes('mic') ? 'mic' : distributionKeys[0]
  return {
    id: groupId,
    label: iterationLabel ? `迭代工具链 · ${iterationLabel}` : `工具链汇总 · ${summaryCount} 次`,
    kind: 'tool',
    group: 'observed',
    status,
    current: summaryCount,
    total: summaryCount,
    provenance: 'database',
    insight: {
      grade: 'neutral',
      verdict: `${tools.length} 类工具 · ${summaryCount} 次调用`,
      reason: iterationLabel ? `${iterationLabel}运行观测` : '运行工具调用汇总',
       facts: [
         ...(iterationLabel ? [{ label: '最近轮次', value: iterationLabel }] : []),
         { label: '调用规模', value: `${tools.length} 类工具 · ${summaryCount} 次` },
         { label: '操作构成', value: summaryOperationComposition(tools) },
         { label: '状态构成', value: summaryStatusBreakdown(tools) },
      ],
      source: 'observer_summary',
    },
    runtime: {
      node_type: 'tool_summary_group',
      source_id: groupId,
      observed_at: observedAt,
      child_ids: tools.map((tool) => `tool-summary:${encodeURIComponent(tool.tool_name)}`),
      grouping_basis: '数据库工具状态汇总；按工具与状态核对逐次记录',
      expanded,
      status_breakdown: summaryStatusBreakdown(tools),
      summary_tools: tools,
      summary_only: true,
      latest_iteration: latestIteration ?? undefined,
      explicit_relation_count: 0,
      ...(primaryDistributionKey ? { evidence_key: primaryDistributionKey, distribution_key: primaryDistributionKey } : {}),
    },
  }
}

function toolSummaryNode(tool: RuntimeSummaryTool): GraphStage {
  const status: GraphStage['status'] = Object.entries(tool.status_counts).some(([key, count]) => count > 0 && ['failed', 'cancelled', 'stopped'].includes(key))
    ? 'stopped'
    : Object.entries(tool.status_counts).some(([key, count]) => count > 0 && ['queued', 'running', 'pending'].includes(key)) ? 'running' : 'completed'
  const distributionKey = distributionKeyForTool(tool.tool_name)
  return {
    id: `tool-summary:${encodeURIComponent(tool.tool_name)}`,
    label: tool.display_name,
    kind: 'tool',
    group: 'observed',
    status,
    current: tool.materialized_count,
    total: tool.summary_count,
    provenance: 'derived',
    insight: {
      grade: 'neutral',
      verdict: summaryStatusBreakdown([tool]),
       reason: `${tool.summary_count} 次调用`,
      facts: [
        { label: '状态构成', value: Object.entries(tool.status_counts).map(([status, count]) => `${summaryStatusLabel(status)} ${count}`).join(' · ') },
        { label: '统计总量', value: String(tool.summary_count) },
        { label: '逐次明细', value: `已有 ${tool.materialized_count} · 缺少 ${tool.missing_count}` },
      ],
      source: 'observer_summary',
    },
    runtime: {
      node_type: 'tool_summary',
      source_id: tool.tool_name,
      observed_at: null,
      tool_name: tool.tool_name,
      raw_label: tool.tool_name,
      summary_tools: [tool],
      summary_only: true,
      explicit_relation_count: 0,
      ...(distributionKey ? { evidence_key: distributionKey, distribution_key: distributionKey } : {}),
    },
  }
}

function nonNegativeInteger(value: unknown) {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

export function candidatePreviewDenominator(detail: Pick<RunDetail, 'display_population' | 'counts' | 'run'>) {
  const displayPopulation = detail.display_population
  if (displayPopulation) {
    return nonNegativeInteger(displayPopulation.candidate_count)
  }
  return nonNegativeInteger(detail.counts.candidates) ?? nonNegativeInteger(detail.run.candidate_count)
}

export function candidatePreviewLabel(index: number, total: number | null) {
  return total === null || !Number.isInteger(index) || index < 1 || total < index || total <= 0 ? `已返回第 ${index} 条` : `${index}/${total}`
}

export function candidatePreviewCountLabel(count: number, total: number | null) {
  return total !== null && total >= count && (total > 0 || count === 0) ? `${count}/${total} 条` : `已返回 ${count} 条`
}

function populationSummaryNode(detail: RunDetail, previewTotal: number | null): GraphStage | null {
  const generation = detail.generation_population
  const display = detail.display_population
  if (!generation && !display) return null
  const previewCount = detail.candidates.length
  const facts: Array<{ label: string; value: string }> = [
    { label: '候选预览', value: candidatePreviewCountLabel(previewCount, previewTotal) },
  ]
  if (generation) {
    facts.push(
      { label: '种群计数', value: `基线 ${generation.baseline_candidate_count} · 新生 ${generation.descendant_candidate_count} · 最高第 ${generation.max_generation} 代` },
    )
  }
  if (display && display.excluded_candidate_count > 0) {
    facts.push({ label: '排除记录', value: String(display.excluded_candidate_count) })
  }
  if (previewTotal !== null && previewTotal < previewCount) {
    facts.push({ label: '数据状态', value: '接口计数不一致' })
  }
  if (generation && display && generation.baseline_candidate_count + generation.descendant_candidate_count !== display.candidate_count && !facts.some((fact) => fact.label === '数据状态')) {
    facts.push({ label: '数据状态', value: '接口计数不一致' })
  }
  const scope = generation && display ? 'mixed' : generation ? 'generation_population' : 'display_population'
  const total = previewTotal !== null && previewTotal >= previewCount ? previewTotal : previewCount
  return {
    id: 'population-summary',
    label: '种群汇总',
    kind: 'data',
    group: 'observed',
    status: 'pending',
    current: previewCount,
    total,
    provenance: 'database',
    insight: {
      grade: 'neutral',
      verdict: '权威种群计数',
      reason: generation
        ? `基线 ${generation.baseline_candidate_count} · 新生 ${generation.descendant_candidate_count} · 最高第 ${generation.max_generation} 代`
        : `可展示候选 ${total} 条`,
      facts,
      source: 'observer_summary',
    },
    runtime: {
      node_type: 'population_summary',
      source_id: 'population-summary',
      observed_at: null,
      candidate_count: previewCount,
      preview_total: previewTotal,
      population_scope: scope,
      evidence_key: 'candidate_pool',
      distribution_key: 'candidate_pool',
      explicit_relation_count: 0,
    },
  }
}

function generationNode(generation: number, candidates: CandidatePreview[], expanded: boolean, previewTotal: number | null): GraphStage {
  const count = candidates.length
  return {
    id: `generation:${generation}`,
    label: `第 ${generation} 代预览 · ${count} 条`,
    kind: 'data',
    group: 'observed',
    status: 'completed',
    current: count,
    // A generation preview has no per-generation denominator in the API.
    // Keep the count factual, but let the renderer suppress progress semantics.
    total: 0,
    provenance: 'database',
    insight: {
      grade: 'okay',
      verdict: `${count} 条预览记录`,
      reason: '按当前返回预览中的 generation 字段分组；不代表完整代际数量。',
      facts: [{ label: '预览记录', value: candidatePreviewCountLabel(count, previewTotal) }, { label: '代际', value: `第 ${generation} 代` }, { label: '展开', value: expanded ? `${count} 条个体` : '收起' }],
      source: 'observer_summary',
    },
    runtime: {
      node_type: 'candidate_group',
      source_id: String(generation),
      observed_at: null,
      candidate_count: count,
      child_ids: candidates.map((candidate) => candidate.id),
      grouping_basis: '候选记录明确 generation 字段',
      preview_total: previewTotal,
      expanded,
      explicit_relation_count: 0,
    },
  }
}

function candidateNode(candidate: CandidatePreview, previewIndex: number, previewTotal: number | null): GraphStage {
  const rank = candidate.proposal_rank === null ? candidate.id.slice(0, 8) : `#${candidate.proposal_rank}`
  return {
    id: `candidate:${candidate.id}`,
    label: `候选预览 ${rank}`,
    kind: 'data',
    group: 'observed',
    status: 'completed',
    current: 1,
    // One returned preview is not a completed one-item task. The population
    // denominator belongs to the population summary, not to this card.
    total: 0,
    provenance: 'database',
    insight: {
      grade: 'neutral',
      verdict: '已记录',
      reason: candidate.sequence,
      facts: [
        { label: '代际', value: candidate.generation === undefined ? '—' : `第 ${candidate.generation} 代` },
        { label: '长度', value: `${candidate.length} 个残基` },
        { label: '预览记录', value: candidatePreviewLabel(previewIndex, previewTotal) },
        { label: '来源', value: candidate.generator_call_id ? '工具调用' : '未返回' },
      ],
      source: 'observer_summary',
    },
    runtime: {
      node_type: 'candidate_preview',
      source_id: candidate.id,
      observed_at: null,
      candidate_count: 1,
      preview_index: previewIndex,
      preview_total: previewTotal,
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

export function layoutColumnsForWidth(availableWidth: number | undefined) {
  if (!Number.isFinite(availableWidth) || (availableWidth ?? 0) <= 0) return 5
  return Math.max(5, Math.min(7, Math.round((availableWidth as number) / 315)))
}

function computePositions(nodes: GraphStage[], requestedColumns?: number, availableWidth?: number) {
  const positions: Record<string, { x: number; y: number }> = {}
  const maximumColumns = Math.max(1, Math.min(7, Math.round(requestedColumns ?? layoutColumnsForWidth(availableWidth))))
  const groupMembers = new Map<string, string>()
  for (const group of nodes) {
    const childIds = [...(group.runtime?.child_ids ?? []), ...(group.runtime?.event_ids ?? [])]
    if (!group.runtime?.expanded || !childIds.length) continue
    for (const childId of childIds) {
      const prefix = group.runtime.node_type === 'candidate_group' ? 'candidate:' : 'call:'
      const nodeId = childId.startsWith('event:') || childId.startsWith('call:') || childId.startsWith('candidate:') || childId.startsWith('tool-summary:') ? childId : `${prefix}${childId}`
      groupMembers.set(nodeId, group.id)
    }
  }
  const summaryNodes = nodes.filter((node) => node.runtime?.node_type === 'tool_summary')
  const mainNodes = nodes.filter((node) => !summaryNodes.includes(node) && !groupMembers.has(node.id))
  const observedTime = (node: GraphStage) => {
    const value = node.runtime?.observed_at ? Date.parse(node.runtime.observed_at) : Number.NaN
    return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER
  }
  const ordered = [...mainNodes].sort((left, right) => observedTime(left) - observedTime(right) || left.id.localeCompare(right.id))
  // The default canvas is a decision spine, not a four-row table. Evidence
  // types remain in metadata and details; only explicit parallel groups get
  // a vertical branch. Summary-only evidence is placed on a separate audit
  // rail below the spine and never controls the readable viewport.
  const mainY = 220
  const auditY = 660
  const columnByNode = new Map<string, number>()
  const firstColumnByParallelGroup = new Map<string, number>()
  let nextColumn = 0
  for (const node of ordered) {
    const parallelGroup = node.runtime?.parallel_group_id
    if (parallelGroup && firstColumnByParallelGroup.has(parallelGroup)) {
      columnByNode.set(node.id, firstColumnByParallelGroup.get(parallelGroup)!)
      continue
    }
    columnByNode.set(node.id, nextColumn)
    if (parallelGroup) firstColumnByParallelGroup.set(parallelGroup, nextColumn)
    nextColumn += 1
  }
  const groupById = new Map(nodes.filter((node) => node.runtime?.expanded).map((node) => [node.id, node] as const))
  const expandedClusters = [...groupById.values()].flatMap((group) => {
    const childIds = [...(group.runtime?.child_ids ?? []), ...(group.runtime?.event_ids ?? [])]
    const baseColumn = columnByNode.get(group.id)
    if (baseColumn === undefined || !childIds.length) return []
    const clusterColumns = group.runtime?.node_type === 'tool_summary_group' ? 3 : childIds.length <= 3 ? 1 : maximumColumns
    return [{ groupId: group.id, baseColumn, clusterColumns }]
  })
  // An expanded cluster occupies the columns immediately after its group.
  // Shift later spine nodes by that occupied span so the local fan-out never
  // sits underneath the next scientific step.
  const shiftedColumn = (baseColumn: number) => baseColumn + expandedClusters
    .filter(({ baseColumn: clusterColumn }) => clusterColumn < baseColumn)
    .reduce((total, { clusterColumns }) => total + clusterColumns, 0)
  for (const [childId, groupId] of groupMembers) {
    const groupColumn = columnByNode.get(groupId)
    if (groupColumn === undefined) continue
    const group = groupById.get(groupId)
    const childIds = group ? [...(group.runtime?.child_ids ?? []), ...(group.runtime?.event_ids ?? [])] : []
    const index = childIds.indexOf(childId.startsWith('tool-summary:') ? childId : childId.replace(/^call:/, '').replace(/^event:/, '').replace(/^candidate:/, ''))
    const clusterColumns = group?.runtime?.node_type === 'tool_summary_group' ? 3 : childIds.length <= 3 ? 1 : maximumColumns
    columnByNode.set(childId, shiftedColumn(groupColumn) + 1 + Math.max(0, index) % clusterColumns)
  }
  const place = (node: GraphStage, column: number, row = 0) => {
    const isSummary = node.runtime?.node_type === 'tool_summary' && !groupMembers.has(node.id)
    positions[node.id] = { x: 190 + column * 315, y: isSummary ? auditY + row * 190 : mainY + row * 190 }
  }
  for (const node of ordered) {
    const parallelGroup = node.runtime?.parallel_group_id
    const parallelMembers = parallelGroup ? ordered.filter((candidate) => candidate.runtime?.parallel_group_id === parallelGroup) : []
    const parallelIndex = parallelGroup ? parallelMembers.findIndex((candidate) => candidate.id === node.id) : 0
    const parallelOffset = parallelMembers.length > 1 ? parallelIndex - (parallelMembers.length - 1) / 2 : 0
    const baseColumn = columnByNode.get(node.id) ?? 0
    const isExpandedChild = groupMembers.has(node.id)
    positions[node.id] = { x: 190 + (isExpandedChild ? baseColumn : shiftedColumn(baseColumn)) * 315, y: mainY + parallelOffset * 170 }
  }
  for (const node of nodes.filter((candidate) => groupMembers.has(candidate.id))) {
    const groupId = groupMembers.get(node.id)
    const group = groupId ? groupById.get(groupId) : undefined
    const childIds = group ? [...(group.runtime?.child_ids ?? []), ...(group.runtime?.event_ids ?? [])] : []
    const childIndex = childIds.indexOf(node.id.startsWith('tool-summary:') ? node.id : node.id.replace(/^call:/, '').replace(/^event:/, '').replace(/^candidate:/, ''))
    const clusterColumns = group?.runtime?.node_type === 'tool_summary_group' ? 3 : childIds.length <= 3 ? 1 : maximumColumns
    place(node, columnByNode.get(node.id) ?? 0, Math.floor(Math.max(0, childIndex) / clusterColumns) + 1)
  }
  // Summary-only evidence is a separate audit rail. It never consumes a
  // timeline column and has no execution edge.
  summaryNodes.filter((node) => !groupMembers.has(node.id)).forEach((node, index) => place(node, index, 0))
  return positions
}

export function buildRuntimeGraph(detail: RunDetail, sources: Sources = {}, options: RuntimeGraphOptions = {}): RuntimeGraphModel {
  const eventWindow = runtimeEventWindow(detail.events, detail.event_window)
  const calls = collectCalls(sources, detail.events)
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
  // Large unkeyed runs can contain dozens of repeated metric calls spread
  // across iterations. A display aggregate keeps the folded graph readable;
  // it is explicitly not a batch or a causal execution group. Expansion still
  // exposes every materialized attempt in the original observation set.
  const displayAggregateBuckets = new Map<string, ToolAttempt[]>()
  for (const call of orderedCalls) {
    if (callBatchIdentity(call)) continue
    const key = `${call.tool_name}\u0000${call.status}`
    displayAggregateBuckets.set(key, [...(displayAggregateBuckets.get(key) ?? []), call])
  }
  const displayAggregateGroups = [...displayAggregateBuckets.entries()]
    .filter(([, grouped]) => grouped.length >= 4)
    .map(([key, grouped]) => ({
      key: `display:${key}`,
      grouped,
      basis: '同工具 + 状态的展示聚合（无显式批次，不表达因果）',
    }))
  const displayAggregateCallIds = new Set(displayAggregateGroups.flatMap(({ grouped }) => grouped.map((call) => call.id)))
  const explicitGroups = [...explicitBuckets.entries()].map(([identity, grouped]) => ({
    key: identity,
    grouped: grouped.sort((left, right) => (Date.parse(observedAt(left) ?? '') - Date.parse(observedAt(right) ?? '')) || left.id.localeCompare(right.id)),
    basis: identity.startsWith('observed_tool_call_id=')
      ? `事件关联字段 tool_call_id=${identity.slice('observed_tool_call_id='.length)}`
      : `后端字段 ${identity}`,
  }))
  const fallbackGroupRecords = fallbackGroups
    .map((grouped, index) => ({ key: `fallback:${grouped[0].id}`, grouped: grouped.filter((call) => !displayAggregateCallIds.has(call.id)), basis: fallbackBases[index] }))
    .filter(({ grouped }) => grouped.length > 0)
  const callGroupRecords = [...explicitGroups, ...displayAggregateGroups, ...fallbackGroupRecords].sort((left, right) => {
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
  const callNodes = bucketRecords.flatMap((bucket) => {
    const total = bucket.calls.length + bucket.events.length
    if (total === 1) return [...bucket.calls.map(callNode), ...bucket.events.map(eventNode)]
    const groupId = groupIdFor(bucket)
    const expanded = expandedGroups.has(groupId)
    const groupingBasis = bucket.basis
    const displayBatchLabel = aggregateSemanticLabel(bucket.calls, bucket.events) ?? '混合观测组'
    const retryEvidence = retryEvidenceEvents(bucket.events)
    const expandedEvents = retryEvidence.length ? retryEvidence : bucket.events
    return [runtimeGroupNode(bucket.calls, bucket.events, expanded, groupId, groupingBasis, displayBatchLabel, eventWindow.mayBeTruncated), ...(expanded ? [...bucket.calls.map(callNode), ...expandedEvents.map(eventNode)] : [])]
  })
  const summaryTools = toolSummaryRows(detail.tool_summary, Object.values(calls))
  const summaryGaps = summaryTools.filter((tool) => tool.missing_count > 0)
  const summaryCoverage = toolSummaryCoverage(detail.tool_summary, Object.values(calls))
  const explicitIterations = detail.events
    .map((event) => Number(event.payload.iteration_no))
    .filter((value) => Number.isInteger(value) && value >= 0)
  const latestIteration = explicitIterations.length ? Math.max(...explicitIterations) : null
  const latestObservedAt = detail.events.reduce<string | null>((latest, event) => !latest || Date.parse(event.occurred_at) > Date.parse(latest) ? event.occurred_at : latest, null)
  const previewTotal = candidatePreviewDenominator(detail)
  const populationSummary = populationSummaryNode(detail, previewTotal)
  const candidatesByGeneration = new Map<number, CandidatePreview[]>()
  const ungroupedCandidates: CandidatePreview[] = []
  for (const candidate of detail.candidates) {
    if (candidate.generation === undefined) ungroupedCandidates.push(candidate)
    else candidatesByGeneration.set(candidate.generation, [...(candidatesByGeneration.get(candidate.generation) ?? []), candidate])
  }
  const previewIndexById = new Map(detail.candidates.map((candidate, index) => [candidate.id, index + 1] as const))
  const generationPreviewNodes = [...candidatesByGeneration.entries()].flatMap(([generation, candidates]) => [
    generationNode(generation, candidates, expandedGroups.has(`generation:${generation}`), previewTotal),
    ...(expandedGroups.has(`generation:${generation}`)
      ? candidates.map((candidate) => candidateNode(candidate, previewIndexById.get(candidate.id) ?? 1, previewTotal))
      : []),
  ])
  const viewerEntriesForRun = viewerEntries(detail, sources)
  const structureEvidenceNodes = viewerEntriesForRun.map(([key, artifact]) => structureEvidenceNode(key, artifact))
  const nodes = [
    ...callNodes,
    ...(summaryGaps.length ? [toolSummaryGroupNode(summaryTools, summaryCoverage, expandedGroups.has('tool-summary-group'), latestIteration, latestObservedAt), ...(expandedGroups.has('tool-summary-group') ? summaryTools.map(toolSummaryNode) : [])] : []),
    ...structureEvidenceNodes,
    ...(populationSummary ? [populationSummary] : []),
    ...ungroupedCandidates.map((candidate) => candidateNode(candidate, previewIndexById.get(candidate.id) ?? 1, previewTotal)),
    ...generationPreviewNodes,
  ]
  const sourceNodeByCallId = new Map<string, string>()
  for (const source of Object.values(sources)) {
    if (!source) continue
    for (const call of source?.calls ?? []) sourceNodeByCallId.set(call.id, source.node_id)
  }
  for (const node of nodes) {
    if (!node.runtime?.tool_name) continue
    const mapping = viewerMappingForTool(node.runtime.tool_name, sourceNodeByCallId.get(node.runtime.source_id), viewerEntriesForRun)
    if (mapping) node.runtime = { ...node.runtime, has_viewer: true, viewer_key: mapping.key, viewer_mapping_basis: mapping.basis }
  }
  const countsByGeneration = new Map<number, number>()
  for (const candidate of detail.candidates) {
    if (candidate.generation === undefined) continue
    countsByGeneration.set(candidate.generation, (countsByGeneration.get(candidate.generation) ?? 0) + 1)
  }

  const nodeIds = new Set(nodes.map((node) => node.id))
  const edges: GraphEdgeDetail[] = []
  const seen = new Set<string>()
  for (const structureNode of nodes.filter((node) => node.runtime?.node_type === 'structure_evidence')) {
    if (!nodeIds.has('population-summary')) continue
    addEdge(edges, seen, {
      source: 'population-summary',
      target: structureNode.id,
      label: '同轮次证据',
      rationale: '同一运行同时返回种群汇总与结构 viewer；这是证据关联，不表示生成、依赖或执行先后。',
      provenance: 'derived',
      relation_kind: 'association',
    })
  }
  for (const group of nodes.filter((node) => node.runtime?.expanded)) {
    const members = [...(group.runtime?.child_ids ?? []), ...(group.runtime?.event_ids ?? [])]
    let labeled = false
    for (const member of members) {
      if (group.runtime?.node_type === 'tool_summary_group' && labeled) continue
      const target = member.startsWith('event:') || member.startsWith('call:') || member.startsWith('candidate:') || member.startsWith('tool-summary:')
        ? member
        : group.runtime?.node_type === 'candidate_group' ? `candidate:${member}` : `call:${member}`
      if (!nodeIds.has(target)) continue
      addEdge(edges, seen, {
        source: group.id,
        target,
        label: labeled ? null : '批次明细',
        rationale: `依据数据库返回的${group.runtime?.grouping_basis ?? '批次成员字段'}展开；表示同组观测，不表示执行依赖。`,
        provenance: 'database',
        relation_kind: 'grouping',
      })
      labeled = true
    }
  }
  const bucketNodeId = (bucket: { key: string; calls: ToolAttempt[]; events: TimelineEvent[] }) => {
    if (bucket.calls.length + bucket.events.length > 1) return groupIdFor(bucket)
    if (bucket.calls[0]) return `call:${bucket.calls[0].id}`
    return bucket.events[0] ? `event:${bucket.events[0].sequence_no}` : null
  }
  const executionSequence = bucketRecords.filter((bucket) => (
    bucket.basis.startsWith('后端执行字段')
    || bucket.events.some((event) => event.type.toLowerCase().includes('recovery_scheduled'))
  ))
  executionSequence.slice(1).forEach((bucket, index) => {
    const source = bucketNodeId(executionSequence[index])
    const target = bucketNodeId(bucket)
    if (!source || !target || !nodeIds.has(source) || !nodeIds.has(target)) return
    addEdge(edges, seen, {
      source,
      target,
      label: index === 0 ? '观测先后' : null,
      rationale: '仅按同一运行中持久化时间与事件序号排列工作流执行簇和恢复调度记录；表示随后观测到，不表示依赖、重试、回退或触发。',
      provenance: 'derived',
      relation_kind: 'sequence',
    })
  })
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
  const addParallelGroup = (grouped: ToolAttempt[], rationale: string) => {
    if (grouped.length < 2) return
    const signature = grouped.map((call) => call.id).sort().join('|')
    if (parallelRelationSignatures.has(signature)) return
    parallelRelationSignatures.add(signature)
    let labeled = false
    const anchor = callIdToNode(grouped[0].id)
    for (const call of grouped.slice(1)) {
      const target = callIdToNode(call.id)
      const edgeLabel = labeled ? null : '并行观测组'
      addEdge(edges, seen, { source: anchor, target, label: edgeLabel, rationale, provenance: 'database', relation_kind: 'parallel' })
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
    addParallelGroup(grouped, `工具调用字段明确提供 parallel_group_id=${parallelId}；此边表示同组并行观测，不代表调度依赖。`)
  }
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
    const candidateNodeId = `candidate:${candidate.id}`
    const candidateVisible = nodeIds.has(candidateNodeId)
    if (candidate.parent_id && candidateIds.has(candidate.parent_id) && candidateVisible && nodeIds.has(`candidate:${candidate.parent_id}`)) {
      addEdge(edges, seen, { source: `candidate:${candidate.parent_id}`, target: candidateNodeId, label: '父子谱系', rationale: '候选记录显式提供 parent_id；这是候选谱系，不是执行依赖。', provenance: 'database', relation_kind: 'lineage' })
    }
    if (candidate.generator_call_id && candidateVisible) {
      const source = callIdToNode(candidate.generator_call_id)
      if (nodeIds.has(source)) addEdge(edges, seen, { source, target: candidateNodeId, label: '生成来源', rationale: '候选记录显式提供 generator_call_id；这是来源关联，不表示该调用的执行依赖。', provenance: 'database', relation_kind: 'association' })
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
  if (detail.candidates.length && previewTotal === null) {
    gaps.push(`候选预览已返回 ${detail.candidates.length} 条；接口未返回可展示总数。`)
  } else if (previewTotal !== null && detail.candidates.length < previewTotal) {
    gaps.push(`候选预览已返回 ${detail.candidates.length}/${previewTotal} 条；其余候选未进入运行图。`)
  } else if (previewTotal !== null && detail.candidates.length > previewTotal) {
    gaps.push(`候选预览 ${detail.candidates.length} 条超过接口展示口径 ${previewTotal} 条；保留原始记录。`)
  }
  if (detail.display_population && detail.generation_population) {
    const displayTotal = detail.display_population.candidate_count
    const populationTotal = detail.generation_population.baseline_candidate_count + detail.generation_population.descendant_candidate_count
    if (displayTotal !== populationTotal) gaps.push(`接口种群口径不一致：展示 ${displayTotal} 条；基线与新生子代合计 ${populationTotal} 条。`)
  }
  if (eventWindow.mayBeTruncated) gaps.push(eventWindow.remaining !== undefined
    ? `已加载 ${eventWindow.returned} 条；仍有至少 ${eventWindow.remaining} 条更早记录。`
    : `已加载 ${eventWindow.returned} 条；已达最近 ${eventWindow.limit} 条窗口上限，更早记录未确认。`)
  if (Object.values(sources).some((source) => (source?.calls.length ?? 0) >= 40)) gaps.push('至少一个节点明细只返回 40 次工具调用；完整调用集合缺少分页契约。')
  if (options.sourceFetch && options.sourceFetch.failed > 0) gaps.push(`节点明细仅加载 ${options.sourceFetch.loaded}/${options.sourceFetch.requested} 个；${options.sourceFetch.failed} 个读取失败或超时，当前运行图不完整。`)
  else if (options.sourceFetch && options.sourceFetch.loaded < options.sourceFetch.requested && (options.sourceFetch.deferred ?? 0) > 0) gaps.push(`节点明细已加载 ${options.sourceFetch.loaded}/${options.sourceFetch.requested} 个；其余 ${options.sourceFetch.deferred} 个按需读取，当前运行图仍不完整。`)
  else if (options.sourceFetch && options.sourceFetch.loaded < options.sourceFetch.requested) gaps.push(`节点明细正在加载 ${options.sourceFetch.loaded}/${options.sourceFetch.requested} 个；当前运行图仍不完整。`)
  if (detail.graph.nodes.length) gaps.push('详情中的固定拓扑摘要仅用于兼容核对，未纳入运行图；当前图仅使用真实事件、工具调用、候选与显式关系。')
  else gaps.push('运行详情未提供阶段摘要；无法核对旧版兼容数据。')

  const toolCounts = new Map<string, number>()
  for (const call of Object.values(calls)) toolCounts.set(call.tool_name, (toolCounts.get(call.tool_name) ?? 0) + 1)
  const parallelGroups = parallelRelationSignatures.size
  const toolRetries = Object.values(calls).filter((call) => call.attempt_observed !== false && call.attempt > 1).length
  const activityRetries = countActivityRetries(Object.values(events))
  const stats: RuntimeGraphStats = {
    observedCalls: Object.keys(calls).length,
    observedEvents: Object.keys(events).length,
    openActivities: countOpenActivities(Object.values(events)),
    repeatedTools: [...toolCounts.values()].filter((count) => count > 1).length,
    toolRetries,
    activityRetries,
    retries: toolRetries,
    parallelGroups,
    cycles: detectCycles(nodes.map((node) => node.id), edges),
    unfinished: Object.values(calls).filter((call) => !callStatuses.has(call.status) && !stoppedStatuses.has(call.status)).length,
    generations: countsByGeneration.size,
    toolSummaryRecords: summaryCoverage.total,
    toolSummaryMaterialized: summaryCoverage.materialized,
    toolSummaryMissing: summaryCoverage.missing,
    eventWindowAtLimit: eventWindow.mayBeTruncated,
  }
  return { nodes, edges, positions: computePositions(nodes, options.layoutColumns, options.availableWidth), calls, events, toolGroups, sourceFetch: options.sourceFetch, gaps, stats, eventWindow }
}
