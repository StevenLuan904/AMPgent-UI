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

    expect(result.nodes.map((node) => node.id)).toEqual(expect.arrayContaining(['tool-group:ampgan:call-1', 'event:1']))
    expect(result.nodes.map((node) => node.id)).not.toEqual(expect.arrayContaining(['call:call-1', 'call:call-2']))
    expect(result.stats).toMatchObject({ observedCalls: 2, observedEvents: 1, repeatedTools: 1, retries: 1, unfinished: 1 })
    expect(result.edges).toEqual(expect.arrayContaining([expect.objectContaining({ source: 'event:1', target: 'tool-group:ampgan:call-1', provenance: 'database', relation_kind: 'association' })]))
    expect(result.gaps).toContain('接口未返回工具调用依赖边；未按时间顺序补画推断边。')
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
    expect(result.nodes.find((node) => node.id === 'event:1')).toMatchObject({ status: 'completed', label: '结构证据 · 已持久化' })
    expect(result.edges).toEqual(expect.arrayContaining([expect.objectContaining({ relation_kind: 'association', label: '关联' })]))
    expect(result.stats.cycles).toBe(0)
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
})
