import { describe, expect, it } from 'vitest'
import { formatRunTitle } from './runPresentation'
import type { RunListItem } from './types'

function run(status: RunListItem['status'], baseline: number): RunListItem {
  return {
    id: 'run-1',
    name: '运行',
    kind: 'autoresearch',
    schema_version: '0017',
    status,
    generation_population: { baseline_candidate_count: baseline, descendant_candidate_count: 0, max_generation: 0 },
    created_at: '2026-08-31T00:00:00Z',
    started_at: null,
    finished_at: null,
    candidate_count: baseline,
    tool_call_count: 0,
    structure_record_count: 0,
  }
}

describe('运行列表标题', () => {
  it('把运行失败与候选科学性质分开表达', () => {
    expect(formatRunTitle(run('failed', 0))).toBe('运行异常终止 · 尚无可展示候选')
  })

  it('保留运行中基线候选口径', () => {
    expect(formatRunTitle(run('running', 768))).toBe('正在运行 · 768 条基线候选')
  })
})
