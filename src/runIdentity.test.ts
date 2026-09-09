import { describe, expect, it } from 'vitest'
import { assertMatchingRunIdentity, groupRunsByAuthoritativeRound, preserveSelectedRunOnListRefresh } from './runIdentity'
import type { RunListItem } from './types'

const identity = {
  id: 'postgres-run',
  temporal_workflow_id: 'temporal-workflow',
  temporal_run_id: 'temporal-run',
}

const run = (id: string, extra: Partial<RunListItem> = {}): RunListItem => ({
  id, name: id, kind: 'test', schema_version: null, status: 'succeeded', created_at: '2026-09-04T00:00:00Z', started_at: null, finished_at: null,
  candidate_count: 0, tool_call_count: 0, structure_record_count: 0, ...extra,
})

describe('运行三元身份校验', () => {
  it('只按显式根轮次字段聚合生成与后补证据运行', () => {
    const result = groupRunsByAuthoritativeRound([
      run('evidence', { root_generation_run_id: 'generator', run_role: 'evidence', display_round: '第 39 轮' }),
      run('generator', { root_generation_run_id: 'generator', run_role: 'generator', display_round: '第 39 轮' }),
      run('unrelated', { name: 'Rosetta 后补' }),
    ])
    expect(result).toHaveLength(2)
    expect(result[0]).toMatchObject({ key: 'root:generator', rootRunId: 'generator', basis: 'root_generation_run_id', displayRound: '第 39 轮' })
    expect((result[0] as { runs: RunListItem[] }).runs.map((item) => item.id)).toEqual(['generator', 'evidence'])
    expect(result[1]).toMatchObject({ id: 'unrelated' })
  })

  it('支持显式 member_run_ids，不因标题或时间接近合并', () => {
    const result = groupRunsByAuthoritativeRound([
      run('generator', { member_run_ids: ['generator', 'boltz'], run_role: 'generator' }),
      run('boltz', { run_role: 'evidence', source_run_id: 'generator' }),
      run('same-name', { name: 'Rosetta', created_at: '2026-09-04T00:00:00Z' }),
    ])
    expect(result).toHaveLength(2)
    expect(result[0]).toMatchObject({ basis: 'member_run_ids', rootRunId: 'generator' })
    expect((result[0] as { runs: RunListItem[] }).runs.map((item) => item.id)).toEqual(['generator', 'boltz'])
    expect(result[1]).toMatchObject({ id: 'same-name' })
  })

  it('把根运行声明的成员和 evidence source_run_id 保持在同一显式组', () => {
    const result = groupRunsByAuthoritativeRound([
      run('generator', { root_generation_run_id: 'generator', member_run_ids: ['generator', 'boltz'], run_role: 'generator' }),
      run('boltz', { run_role: 'evidence', source_run_id: 'generator' }),
    ])
    expect(result).toHaveLength(1)
    expect((result[0] as { key: string; runs: RunListItem[] }).key).toBe('root:generator')
    expect((result[0] as { runs: RunListItem[] }).runs.map((item) => item.id)).toEqual(['generator', 'boltz'])
  })

  it('旧接口缺少根轮次字段时保持逐条运行，不按证据工具猜测', () => {
    const result = groupRunsByAuthoritativeRound([
      run('generator', { name: '序列生成' }),
      run('rosetta', { name: 'Rosetta 结构补证', run_role: 'evidence' }),
    ])
    expect(result.map((item) => 'runs' in item)).toEqual([false, false])
  })

  it('列表刷新时保留用户当前选择，即使深链运行不在最近列表', () => {
    expect(preserveSelectedRunOnListRefresh('deep-linked-run', ['newest-run'])).toBe('deep-linked-run')
    expect(preserveSelectedRunOnListRefresh('user-selected-run', [])).toBe('user-selected-run')
    expect(preserveSelectedRunOnListRefresh(null, ['newest-run'])).toBe('newest-run')
    expect(preserveSelectedRunOnListRefresh(null, [])).toBeNull()
  })

  it('接受完全一致的 PostgreSQL 与 Temporal 身份', () => {
    expect(() => assertMatchingRunIdentity(identity, { ...identity, workflow_id: 'temporal-workflow' })).not.toThrow()
  })

  it.each([
    [{ ...identity, id: 'other-postgres-run' }, 'PostgreSQL 运行编号'],
    [{ ...identity, temporal_workflow_id: 'other-workflow' }, 'Temporal 工作流编号'],
    [{ ...identity, temporal_run_id: 'other-temporal-run' }, 'Temporal 运行编号'],
  ])('拒绝三元身份中的任一漂移', (actual, message) => {
    expect(() => assertMatchingRunIdentity(identity, actual)).toThrow(message)
  })

  it('兼容尚未返回 Temporal 字段的旧列表', () => {
    expect(() => assertMatchingRunIdentity({ id: identity.id }, identity)).not.toThrow()
  })

  it('拒绝兼容别名与正式工作流编号不一致', () => {
    expect(() => assertMatchingRunIdentity(identity, { ...identity, workflow_id: 'other-workflow' })).toThrow('Temporal 工作流编号')
  })
})
