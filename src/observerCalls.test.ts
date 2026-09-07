import { describe, expect, it } from 'vitest'
import { mergeNodeDetailCalls, nodeCallsWindowLabel, observerNodeCallsUrl } from './observerCalls'
import type { NodeDetail, ToolAttempt } from './types'

const call = (id: string, relation?: ToolAttempt['relations']): ToolAttempt => ({
  id,
  tool_name: 'tool-a',
  tool_version: 'v1',
  status: 'succeeded',
  attempt: 1,
  queued_at: '2026-09-04T00:00:00Z',
  started_at: '2026-09-04T00:00:00Z',
  finished_at: '2026-09-04T00:00:01Z',
  duration_seconds: 1,
  random_seed: null,
  model_uri: null,
  weights_sha256: null,
  environment_sha256: 'env',
  input_sha256: 'input',
  output_sha256: null,
  inputs: {},
  parameters: {},
  error: null,
  artifacts: [],
  ...(relation ? { relations: relation } : {}),
})

const detail = (calls: ToolAttempt[], calls_window?: NodeDetail['calls_window']): NodeDetail => ({
  source: 'postgresql',
  read_only: true,
  node_id: 'mic',
  narrative: [],
  calls,
  ...(calls_window ? { calls_window } : {}),
  metrics: {},
  reasoning: { decisions: [], status_counts: {}, reason_counts: {}, considered: 0, admitted: 0 },
  structure_results: [],
})

describe('observer node call pagination', () => {
  it('builds an opaque cursor URL without depending on browser globals', () => {
    expect(observerNodeCallsUrl('/v1/observer/runs/run-1/nodes/mic', { cursor: 'opaque/2217', limit: 100 }))
      .toBe('/v1/observer/runs/run-1/nodes/mic?calls_limit=100&calls_cursor=opaque%2F2217')
  })

  it('keeps older calls when a fresh head page arrives and de-duplicates by call id', () => {
    const existing = detail([call('old'), call('shared', [{ direction: 'upstream', related_call_id: 'root', relation_type: 'dependency' }])], { limit: 40, next_cursor: 'cursor-old', has_more: true, remaining: 80, total: 120 })
    const incoming = detail([call('new'), call('shared', [{ direction: 'downstream', related_call_id: 'child', relation_type: 'retry' }])], { limit: 40, next_cursor: 'cursor-head', has_more: true, remaining: 118, total: 120 })
    const merged = mergeNodeDetailCalls(existing, incoming, 'head')
    expect(merged.calls.map((item) => item.id)).toEqual(['new', 'shared', 'old'])
    expect(merged.calls.find((item) => item.id === 'shared')?.relations).toHaveLength(2)
    expect(merged.calls_window).toMatchObject({ next_cursor: 'cursor-old', has_more: true, total: 120, remaining: 117 })
  })

  it('advances the cursor when an older page is loaded', () => {
    const existing = detail([call('new')], { limit: 40, next_cursor: 'cursor-old', has_more: true, remaining: 2, total: 3 })
    const older = detail([call('old')], { limit: 40, next_cursor: null, has_more: false, remaining: 0, total: 3 })
    const merged = mergeNodeDetailCalls(existing, older, 'older')
    expect(merged.calls.map((item) => item.id)).toEqual(['new', 'old'])
    expect(merged.calls_window).toMatchObject({ next_cursor: null, has_more: false, remaining: 1, total: 3 })
  })

  it('is honest for legacy windows and explicit pagination', () => {
    expect(nodeCallsWindowLabel(detail(Array.from({ length: 40 }, (_, index) => call(String(index)))))).toBe('仅当前窗口')
    expect(nodeCallsWindowLabel(detail([call('one')], { limit: 40, next_cursor: 'next', has_more: true, remaining: 39, total: 40 }))).toBe('已载入 1/40 条 · 仍有 39 条更早调用')
    expect(nodeCallsWindowLabel(detail([call('one')], { limit: 40, next_cursor: null, has_more: false, remaining: 0, total: 1 }))).toBe('已载入全部 1 条调用')
  })
})

