import { describe, expect, it } from 'vitest'
import { buildRuntimeGraph } from './runtimeGraph'
import type { NodeDetail, RunDetail, ToolAttempt } from './types'

const call = (id: string, toolName: string, queuedAt: string, overrides: Partial<ToolAttempt> = {}): ToolAttempt => ({
  id,
  tool_name: toolName,
  tool_version: 'test.1',
  status: 'succeeded',
  attempt: 1,
  queued_at: queuedAt,
  started_at: queuedAt,
  finished_at: new Date(Date.parse(queuedAt) + 10_000).toISOString(),
  duration_seconds: 10,
  random_seed: null,
  model_uri: null,
  weights_sha256: null,
  environment_sha256: 'e'.repeat(64),
  input_sha256: 'i'.repeat(64),
  output_sha256: 'o'.repeat(64),
  inputs: {},
  parameters: {},
  error: null,
  artifacts: [],
  ...overrides,
})

const detail = (events: RunDetail['events'] = [], candidates: RunDetail['candidates'] = []): RunDetail => ({
  source: 'postgresql',
  read_only: true,
  updated_at: '2026-09-04T00:00:00Z',
  run: { id: 'run-1', name: 'test', kind: 'test', schema_version: null, status: 'running', created_at: '2026-09-04T00:00:00Z', started_at: null, finished_at: null, candidate_count: candidates.length, tool_call_count: 0, structure_record_count: 0, spec_sha256: 's'.repeat(64) },
  counts: {},
  branches: [],
  admission: {},
  tool_summary: {},
  structure_counts: {},
  checkpoints: [],
  graph: { nodes: [{ id: 'legacy', label: '不应作为运行节点', kind: 'model', group: 'design', status: 'pending', current: 0, total: 0, provenance: 'missing', insight: { grade: 'neutral', verdict: '—', reason: '—', facts: [], source: 'observer_summary' } }], edges: [] },
  candidates,
  viewer: null,
  viewers: {},
  events,
})

const nodeDetail = (calls: ToolAttempt[]): NodeDetail => ({ source: 'postgresql', read_only: true, node_id: 'dynamic', narrative: [], calls, metrics: {}, reasoning: { decisions: [], status_counts: {}, reason_counts: {}, considered: 0, admitted: 0 }, structure_results: [] })

describe('buildRuntimeGraph', () => {
  it('builds observed call/event nodes and reports missing dependency contract', () => {
    const first = call('call-1', 'ampgan', '2026-09-04T00:00:00Z', { attempt: 2 })
    const second = call('call-2', 'ampgan', '2026-09-04T00:00:01Z', { status: 'running', finished_at: null })
    const result = buildRuntimeGraph(detail([{ sequence_no: 1, type: 'tool_call.started', actor: 'worker', payload: { tool_call_id: 'call-1' }, occurred_at: '2026-09-04T00:00:00Z' }]), { worker: nodeDetail([first, second]) })

    expect(result.nodes.map((node) => node.id)).toEqual(expect.arrayContaining(['tool-group:ampgan:call-1']))
    expect(result.nodes.find((node) => node.id === 'tool-group:ampgan:call-1')?.runtime?.event_ids).toEqual(['event:1'])
    expect(result.nodes.map((node) => node.id)).not.toEqual(expect.arrayContaining(['call:call-1', 'call:call-2']))
    expect(result.stats).toMatchObject({ observedCalls: 2, observedEvents: 1, repeatedTools: 1, retries: 1, unfinished: 1 })
    const expanded = buildRuntimeGraph(detail([{ sequence_no: 1, type: 'tool_call.started', actor: 'worker', payload: { tool_call_id: 'call-1' }, occurred_at: '2026-09-04T00:00:00Z' }]), { worker: nodeDetail([first, second]) }, { expandedGroups: new Set(['tool-group:ampgan:call-1']) })
    expect(expanded.edges).toEqual(expect.arrayContaining([expect.objectContaining({ source: 'event:1', target: 'call:call-1', provenance: 'database', relation_kind: 'association' })]))
    expect(result.gaps).toContain('接口未返回工具调用依赖、重试或回退关系；未按时间顺序补画推断边。')
  })

  it('preserves explicit dependency cycles instead of flattening them', () => {
    const first = call('call-1', 'tool-a', '2026-09-04T00:00:00Z', { inputs: { parent_call_id: 'call-2' } })
    const second = call('call-2', 'tool-b', '2026-09-04T00:00:01Z', { inputs: { parent_call_id: 'call-1' } })
    const result = buildRuntimeGraph(detail(), { worker: nodeDetail([first, second]) })
    expect(result.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'call:call-1', target: 'call:call-2', provenance: 'database', relation_kind: 'dependency' }),
      expect.objectContaining({ source: 'call:call-2', target: 'call:call-1', provenance: 'database', relation_kind: 'dependency' }),
    ]))
    expect(result.stats.cycles).toBeGreaterThan(0)
  })

  it('groups only adjacent same-tool observations and keeps candidates neutral', () => {
    const first = call('call-1', 'ampgan', '2026-09-04T00:00:00Z')
    const second = call('call-2', 'ampgan', '2026-09-04T00:00:01Z')
    const interleaved = call('call-3', 'rosetta', '2026-09-04T00:00:02Z')
    const later = call('call-4', 'ampgan', '2026-09-04T00:00:03Z', { inputs: { source: 'call-1', call_id: 'call-2' } })
    const candidate = { id: 'candidate-1', sequence: 'KKLL', length: 4, proposal_rank: 1, cohort: 'exploration', pareto_front: null, reasons: ['rejected by an external note'], metrics: [] }
    const result = buildRuntimeGraph(detail([], [candidate]), { worker: nodeDetail([first, second, interleaved, later]) })

    expect(Object.keys(result.toolGroups)).toEqual(['tool-group:ampgan:call-1'])
    expect(result.toolGroups['tool-group:ampgan:call-1']).toEqual(['call-1', 'call-2'])
    expect(result.nodes.find((node) => node.id === 'candidate:candidate-1')).toMatchObject({ status: 'completed', insight: { grade: 'neutral', verdict: '已记录' } })
    expect(result.edges).not.toEqual(expect.arrayContaining([expect.objectContaining({ source: 'call:call-2', target: 'call:call-4' })]))
  })

  it('keeps association edges out of dependency cycle detection and maps persisted events to completed', () => {
    const observed = call('call-1', 'boltz', '2026-09-04T00:00:00Z')
    const result = buildRuntimeGraph(detail([
      { sequence_no: 1, type: 'v38.multitarget_structure.persisted', actor: 'worker', payload: { tool_call_id: 'call-1' }, occurred_at: '2026-09-04T00:00:01Z' },
    ]), { worker: nodeDetail([observed]) })
    const group = result.nodes.find((node) => node.runtime?.node_type === 'batch_group')
    expect(group).toMatchObject({ status: 'completed', label: '观测组 1 · 2 项活动' })
    expect(group?.runtime?.event_ids).toEqual(['event:1'])
    const expanded = buildRuntimeGraph(detail([
      { sequence_no: 1, type: 'v38.multitarget_structure.persisted', actor: 'worker', payload: { tool_call_id: 'call-1' }, occurred_at: '2026-09-04T00:00:01Z' },
    ]), { worker: nodeDetail([observed]) }, { expandedGroups: new Set([group!.id]) })
    expect(expanded.nodes.find((node) => node.id === 'event:1')).toMatchObject({ status: 'completed', label: '结构证据 · 已持久化' })
    expect(expanded.edges).toEqual(expect.arrayContaining([expect.objectContaining({ relation_kind: 'association', label: '关联' })]))
    expect(result.stats.cycles).toBe(0)
  })

  it('does not draw a folded association self-loop when event and call share a cluster', () => {
    const observed = call('call-1', 'boltz', '2026-09-04T00:00:00Z')
    const result = buildRuntimeGraph(detail([
      { sequence_no: 1, type: 'tool_call.completed', actor: 'worker', payload: { tool_call_id: 'call-1' }, occurred_at: '2026-09-04T00:00:01Z' },
      { sequence_no: 2, type: 'tool_call.succeeded', actor: 'worker', payload: { tool_call_id: 'call-1' }, occurred_at: '2026-09-04T00:00:02Z' },
    ]), { worker: nodeDetail([observed]) })
    expect(result.edges.some((edge) => edge.source === edge.target)).toBe(false)
    expect(result.nodes.some((node) => node.runtime?.event_ids?.length === 2 && node.insight.verdict.includes('2 项活动'))).toBe(true)
  })

  it('uses a concise Chinese role on event cards while retaining the raw actor in runtime metadata', () => {
    const result = buildRuntimeGraph(detail([
      { sequence_no: 1, type: 'run.started', actor: 'v38-workflow-observer-writer', payload: {}, occurred_at: '2026-09-04T00:00:01Z' },
    ]))
    const event = result.nodes.find((node) => node.id === 'event:1')
    expect(event?.insight.reason).toContain('观察器记录器')
    expect(event?.insight.reason).not.toContain('v38-workflow-observer-writer')
    expect(event?.runtime?.actor).toBe('v38-workflow-observer-writer')
  })

  it('can expand an aggregate without changing its source-of-truth calls', () => {
    const first = call('call-1', 'ampgan', '2026-09-04T00:00:00Z')
    const second = call('call-2', 'ampgan', '2026-09-04T00:00:01Z')
    const result = buildRuntimeGraph(detail(), { worker: nodeDetail([first, second]) }, { expandedGroups: new Set(['tool-group:ampgan:call-1']) })
    expect(result.nodes.map((node) => node.id)).toEqual(expect.arrayContaining(['call:call-1', 'call:call-2']))
    expect(result.nodes.map((node) => node.id)).toContain('tool-group:ampgan:call-1')
    expect(result.nodes.find((node) => node.id === 'tool-group:ampgan:call-1')?.runtime).toMatchObject({ expanded: true })
    expect(result.calls).toEqual({ 'call-1': first, 'call-2': second })
  })

  it('uses an explicit batch identity across different tools and keeps batches separate', () => {
    const generated = call('call-1', 'ampgan', '2026-09-04T00:00:00Z', { inputs: { batch_id: 'batch-a' } })
    const scored = call('call-2', 'amp_read', '2026-09-04T00:20:00Z', { inputs: { batch_id: 'batch-a' } })
    const otherBatch = call('call-3', 'ampgan', '2026-09-04T00:21:00Z', { inputs: { batch_id: 'batch-b' } })
    const result = buildRuntimeGraph(detail(), { worker: nodeDetail([generated, scored, otherBatch]) })
    const groups = Object.values(result.toolGroups)
    expect(groups).toHaveLength(1)
    expect(groups[0]).toEqual(['call-1', 'call-2'])
    const group = result.nodes.find((node) => node.runtime?.node_type === 'tool_group')
    expect(group?.insight.facts[0]).toMatchObject({ label: '操作构成', value: expect.stringContaining('AMPGAN v2 1') })
    expect(group?.insight.facts[0].value).toContain('AMP read 1')
  })

  it('keeps retry and fallback relations distinct from explicit dependencies', () => {
    const original = call('call-1', 'tool-a', '2026-09-04T00:00:00Z')
    const retried = call('call-2', 'tool-a', '2026-09-04T00:00:02Z', { inputs: { retry_of_call_id: 'call-1' } })
    const fallback = call('call-3', 'tool-b', '2026-09-04T00:00:04Z', { inputs: { fallback_from_call_id: 'call-1' } })
    const dependent = call('call-4', 'tool-c', '2026-09-04T00:00:06Z', { inputs: { depends_on_call_id: 'call-1' } })
    const sources = { worker: nodeDetail([original, retried, fallback, dependent]) }
    const result = buildRuntimeGraph(detail(), sources)
    const group = result.nodes.find((node) => node.id === 'tool-group:tool-a:call-1')
    expect(group?.insight.facts.find((fact) => fact.label === '关系')?.value).toBe('重试 1 · 回退 0')
    expect(result.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'tool-group:tool-a:call-1', target: 'call:call-3', label: '回退', relation_kind: 'fallback', provenance: 'database' }),
      expect.objectContaining({ source: 'tool-group:tool-a:call-1', target: 'call:call-4', label: '依赖', relation_kind: 'dependency', provenance: 'database' }),
    ]))
    const expanded = buildRuntimeGraph(detail(), sources, { expandedGroups: new Set(['tool-group:tool-a:call-1']) })
    expect(expanded.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'call:call-1', target: 'call:call-2', label: '重试/恢复', relation_kind: 'retry', provenance: 'database' }),
    ]))
    expect(expanded.stats.cycles).toBe(0)
  })

  it('preserves dependency edges when a call is folded with its lifecycle event', () => {
    const original = call('call-1', 'tool-a', '2026-09-04T00:00:00Z')
    const dependent = call('call-2', 'tool-b', '2026-09-04T00:00:03Z', { inputs: { depends_on_call_id: 'call-1' } })
    const result = buildRuntimeGraph(detail([
      { sequence_no: 1, type: 'tool_call.completed', actor: 'worker', payload: { tool_call_id: 'call-1' }, occurred_at: '2026-09-04T00:00:01Z' },
    ]), { worker: nodeDetail([original, dependent]) })
    const folded = result.nodes.find((node) => node.runtime?.node_type === 'batch_group')
    expect(folded).toBeDefined()
    expect(result.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: folded!.id, target: 'call:call-2', relation_kind: 'dependency', provenance: 'database' }),
    ]))
  })

  it('marks a terminal partially failed batch as stopped instead of pending', () => {
    const succeeded = call('call-1', 'tool-a', '2026-09-04T00:00:00Z', { inputs: { batch_id: 'batch-terminal' } })
    const failed = call('call-2', 'tool-b', '2026-09-04T00:00:01Z', { inputs: { batch_id: 'batch-terminal' }, status: 'failed', error: 'failed', finished_at: '2026-09-04T00:00:02Z' })
    const result = buildRuntimeGraph(detail(), { worker: nodeDetail([succeeded, failed]) })
    const batch = result.nodes.find((node) => node.runtime?.child_ids?.length === 2)
    expect(batch).toMatchObject({ status: 'stopped', insight: { grade: 'fair' } })
  })

  it('shows explicit parallel groups and derived overlap without treating either as dependency', () => {
    const explicitA = call('call-1', 'tool-a', '2026-09-04T00:00:00Z', { inputs: { parallel_group_id: 'pg-1' }, finished_at: '2026-09-04T00:00:01Z' })
    const explicitB = call('call-2', 'tool-b', '2026-09-04T00:05:00Z', { inputs: { parallel_group_id: 'pg-1' }, finished_at: '2026-09-04T00:05:01Z' })
    const overlapA = call('call-3', 'tool-c', '2026-09-04T00:10:00Z', { finished_at: '2026-09-04T00:10:05Z' })
    const overlapB = call('call-4', 'tool-d', '2026-09-04T00:10:02Z', { finished_at: '2026-09-04T00:10:06Z' })
    const result = buildRuntimeGraph(detail(), { worker: nodeDetail([explicitA, explicitB, overlapA, overlapB]) })
    expect(result.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: '并行观测组', relation_kind: 'parallel', provenance: 'database' }),
      expect.objectContaining({ label: '并行观测组 · 观测', relation_kind: 'parallel', provenance: 'derived' }),
    ]))
    expect(result.stats.parallelGroups).toBe(2)
    expect(result.stats.cycles).toBe(0)
  })

  it('groups non-adjacent lifecycle events across event types only with explicit batch identity', () => {
    const result = buildRuntimeGraph(detail([
      { sequence_no: 1, type: 'tool_call.started', actor: 'worker', payload: { batch_id: 'batch-1' }, occurred_at: '2026-09-04T00:00:00Z' },
      { sequence_no: 2, type: 'candidate.scored', actor: 'scorer', payload: { batch_id: 'batch-2' }, occurred_at: '2026-09-04T00:00:01Z' },
      { sequence_no: 3, type: 'tool_call.completed', actor: 'worker', payload: { batch_id: 'batch-1' }, occurred_at: '2026-09-04T00:00:02Z' },
    ]))
    const groups = result.nodes.filter((node) => node.runtime?.node_type === 'event_group')
    expect(groups).toHaveLength(1)
    expect(groups[0].runtime?.event_ids).toEqual(['event:1', 'event:3'])
    expect(result.nodes.map((node) => node.id)).toContain('event:2')
    expect(result.nodes.map((node) => node.id)).not.toContain('event:1')
  })

  it('folds explicit batch calls and lifecycle events into one cross-tool observation cluster', () => {
    const first = call('call-1', 'ampgan', '2026-09-04T00:00:00Z', { inputs: { batch_id: 'batch-z' } })
    const second = call('call-2', 'amp_read', '2026-09-04T00:05:00Z', { inputs: { batch_id: 'batch-z' } })
    const result = buildRuntimeGraph(detail([
      { sequence_no: 1, type: 'tool_call.started', actor: 'worker', payload: { batch_id: 'batch-z' }, occurred_at: '2026-09-04T00:00:01Z' },
      { sequence_no: 2, type: 'candidate.scored', actor: 'scorer', payload: { batch_id: 'batch-z' }, occurred_at: '2026-09-04T00:05:01Z' },
    ]), { worker: nodeDetail([first, second]) })
    const group = result.nodes.find((node) => node.runtime?.node_type === 'batch_group')
    expect(group?.runtime?.child_ids).toEqual(['call-1', 'call-2'])
    expect(group?.runtime?.event_ids).toEqual(['event:1', 'event:2'])
    expect(group?.label).toBe('第 1 批 · 4 项活动')
  })

  it('folds real lifecycle boundaries by exact workflow execution and uses the latest boundary as activity status', () => {
    const execution = 'workflow-run-a'
    const activity = (sequence_no: number, type: string, activity_id: number, activity_type: string) => ({
      sequence_no,
      type,
      actor: 'observer-writer',
      payload: { workflow_run_id: execution, activity_id, attempt: 1, activity_type },
      occurred_at: new Date(Date.parse('2026-09-04T00:00:00Z') + sequence_no * 1_000).toISOString(),
    })
    const result = buildRuntimeGraph(detail([
      activity(1, 'activity.started', 1, 'plan_autoresearch_actions'),
      activity(2, 'activity.succeeded', 1, 'plan_autoresearch_actions'),
      activity(3, 'activity.started', 2, 'persist_autoresearch_action_plan'),
      activity(4, 'activity.succeeded', 2, 'persist_autoresearch_action_plan'),
    ]))
    const executionGroup = result.nodes.find((node) => node.runtime?.node_type === 'event_group')
    expect(executionGroup).toMatchObject({
      label: '第 1 次执行 · 2 项活动',
      status: 'completed',
      current: 2,
      total: 2,
      runtime: { event_ids: ['event:1', 'event:2', 'event:3', 'event:4'] },
    })
    expect(executionGroup?.insight.facts.find((fact) => fact.label === '操作构成')?.value).toContain('生成规划 1')
    expect(executionGroup?.insight.facts.find((fact) => fact.label === '操作构成')?.value).toContain('规划持久化 1')
  })

  it('keeps separate workflow executions in separate expandable groups', () => {
    const events = ['workflow-run-a', 'workflow-run-b'].flatMap((workflow_run_id, runIndex) => [
      { sequence_no: runIndex * 2 + 1, type: 'activity.started', actor: 'observer-writer', payload: { workflow_run_id, activity_id: 1, attempt: 1 }, occurred_at: `2026-09-04T00:00:0${runIndex * 2 + 1}Z` },
      { sequence_no: runIndex * 2 + 2, type: 'activity.failed', actor: 'observer-writer', payload: { workflow_run_id, activity_id: 1, attempt: 1 }, occurred_at: `2026-09-04T00:00:0${runIndex * 2 + 2}Z` },
    ])
    const result = buildRuntimeGraph(detail(events))
    const groups = result.nodes.filter((node) => node.runtime?.node_type === 'event_group')
    expect(groups).toHaveLength(2)
    expect(groups.every((node) => node.status === 'stopped' && node.total === 1)).toBe(true)
  })

  it('uses continuous same-type observation groups only as an explicitly derived fallback', () => {
    const result = buildRuntimeGraph(detail([
      { sequence_no: 1, type: 'candidate.scored', actor: 'scorer', payload: {}, occurred_at: '2026-09-04T00:00:00Z' },
      { sequence_no: 2, type: 'candidate.scored', actor: 'scorer', payload: {}, occurred_at: '2026-09-04T00:00:01Z' },
      { sequence_no: 3, type: 'run.started', actor: 'worker', payload: {}, occurred_at: '2026-09-04T00:00:02Z' },
      { sequence_no: 4, type: 'candidate.scored', actor: 'scorer', payload: {}, occurred_at: '2026-09-04T00:10:00Z' },
    ]))
    const group = result.nodes.find((node) => node.runtime?.node_type === 'event_group')
    expect(group?.runtime?.event_ids).toEqual(['event:1', 'event:2'])
    expect(group?.runtime?.grouping_basis).toContain('连续同类观测')
    expect(result.nodes.map((node) => node.id)).toContain('event:4')
  })

  it('starts each lane after its wrapped rows instead of a fixed y offset', () => {
    const eventTypes = ['run.started', 'run.completed', 'run.failed', 'run.cancelled', 'run.progress', 'candidate.scored']
    const events = eventTypes.map((type, index) => ({
      sequence_no: index + 1,
      type,
      actor: `actor-${index}`,
      payload: {},
      occurred_at: new Date(Date.parse('2026-09-04T00:00:00Z') + index * 1_000).toISOString(),
    }))
    const result = buildRuntimeGraph(detail(events), { worker: nodeDetail([call('call-1', 'boltz', '2026-09-04T00:00:10Z')]) })
    const eventPositions = events.map((_, index) => result.positions[`event:${index + 1}`].y)
    const toolPosition = result.positions['call:call-1'].y
    expect(eventPositions).toEqual([150, 150, 150, 150, 150, 340])
    expect(toolPosition).toBeGreaterThan(eventPositions.at(-1)! + 200)
  })

  it('does not reserve a full empty tool row between events and candidates', () => {
    const candidate = { id: 'candidate-1', sequence: 'KKLL', length: 4, proposal_rank: 1, cohort: 'exploration', pareto_front: null, reasons: [], metrics: [], generation: 1 }
    const result = buildRuntimeGraph(detail([
      { sequence_no: 1, type: 'run.started', actor: 'worker', payload: {}, occurred_at: '2026-09-04T00:00:00Z' },
    ], [candidate]))
    const eventY = result.positions['event:1'].y
    const candidateY = result.positions['candidate:candidate-1'].y
    expect(candidateY - eventY).toBeLessThanOrEqual(320)
  })

  it('reserves a taller local row step for expanded aggregate members', () => {
    const calls = Array.from({ length: 6 }, (_, index) => call(`call-${index + 1}`, `tool-${index + 1}`, `2026-09-04T00:00:${String(index).padStart(2, '0')}Z`, { inputs: { batch_id: 'batch-local' } }))
    const collapsed = buildRuntimeGraph(detail(), { worker: nodeDetail(calls) })
    const group = collapsed.nodes.find((node) => node.runtime?.child_ids?.length === 6)
    expect(group).toBeDefined()
    const result = buildRuntimeGraph(detail(), { worker: nodeDetail(calls) }, { expandedGroups: new Set([group!.id]) })
    const groupPosition = result.positions[group!.id]
    const wrappedMemberPosition = result.positions['call:call-5']
    expect(groupPosition).toBeDefined()
    expect(wrappedMemberPosition.y - groupPosition.y).toBeGreaterThanOrEqual(300)
  })
})
