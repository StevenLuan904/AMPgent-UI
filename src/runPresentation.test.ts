import { describe, expect, it } from 'vitest'
import { formatCanvasRunTitle, formatRunSummary, formatRunTitle } from './runPresentation'
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
  it('使用显式轮次，否则显示产品指定的 Agent 迭代标题', () => {
    expect(formatCanvasRunTitle({ display_round: '第 39 轮' })).toBe('#39次Agent短肽迭代')
    expect(formatCanvasRunTitle({ display_round: null })).toBe('#36次Agent短肽迭代')
  })

  it('把运行失败与候选科学性质分开表达', () => {
    expect(formatRunTitle(run('failed', 0))).toBe('运行异常终止')
    expect(formatRunSummary(run('failed', 0))).toBe('候选 0')
  })

  it('将运行状态与科学计数拆开显示', () => {
    expect(formatRunTitle(run('running', 768))).toBe('正在运行')
    expect(formatRunSummary(run('running', 768))).toBe('候选 768')
    expect(formatRunSummary({ ...run('succeeded', 768), round_summary: { evaluation_count: 46711, operation_type_count: 10, scientific_card_count: 11 } })).toBe('候选 768 · 评估 46,711 · 科学操作 10')
  })
})
