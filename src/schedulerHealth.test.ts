import { describe, expect, it } from 'vitest'
import { schedulerHealthDescription, schedulerHealthPresentation } from './schedulerHealth'
import type { TemporalObservability } from './types'

const base: TemporalObservability = {
  status: 'healthy',
  source: 'postgresql_operational_evidence',
  observed_at: '2026-08-31T08:00:00+08:00',
  history_read_status: 'not_queried',
  history_read_error_category: null,
  scheduler_error_category: null,
  stale_after_seconds: 300,
  is_stale: false,
  postgresql_run_id: 'run-1',
  temporal_workflow_id: 'workflow-1',
  temporal_run_id: 'temporal-run-1',
  evidence_type: 'activity.succeeded',
  affects_scientific_run_status: false,
}

describe('调度健康展示', () => {
  it('不会把过期的正常证据继续显示为正常', () => {
    expect(schedulerHealthPresentation({ ...base, is_stale: true })).toEqual({ label: '调度状态过期', tone: 'warning' })
  })

  it('区分降级与不可用', () => {
    expect(schedulerHealthPresentation({ ...base, status: 'degraded' }).label).toBe('调度延迟')
    expect(schedulerHealthPresentation({ ...base, status: 'unavailable' }).label).toBe('调度不可用')
  })

  it('仅展示分类后的错误，不返回原始错误内容', () => {
    expect(schedulerHealthDescription({ ...base, history_read_status: 'failed', history_read_error_category: 'timeout' }))
      .toBe('历史读取失败 · 响应超时 · 数据库运维证据')
  })
})
