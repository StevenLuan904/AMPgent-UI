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

    expect(result.nodes.map((node) => node.id)).toEqual(expect.arrayContaining(['call:call-1', 'call:call-2', 'event:1']))
    expect(result.stats).toMatchObject({ observedCalls: 2, observedEvents: 1, repeatedTools: 1, retries: 1, unfinished: 1 })
    expect(result.edges).toEqual(expect.arrayContaining([expect.objectContaining({ source: 'event:1', target: 'call:call-1', provenance: 'database' })]))
    expect(result.gaps).toContain('接口未返回工具调用依赖边；未按时间顺序补画推断边。')
  })

  it('preserves explicit dependency cycles instead of flattening them', () => {
    const first = call('call-1', 'tool-a', '2026-09-04T00:00:00Z', { inputs: { parent_call_id: 'call-2' } })
    const second = call('call-2', 'tool-b', '2026-09-04T00:00:01Z', { inputs: { parent_call_id: 'call-1' } })
    const result = buildRuntimeGraph(detail(), { worker: nodeDetail([first, second]) })
    expect(result.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'call:call-1', target: 'call:call-2', provenance: 'database' }),
      expect.objectContaining({ source: 'call:call-2', target: 'call:call-1', provenance: 'database' }),
    ]))
    expect(result.stats.cycles).toBeGreaterThan(0)
  })
})
