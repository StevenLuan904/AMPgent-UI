import { describe, expect, it } from 'vitest'
import { assertMatchingRunIdentity } from './runIdentity'

const identity = {
  id: 'postgres-run',
  temporal_workflow_id: 'temporal-workflow',
  temporal_run_id: 'temporal-run',
}

describe('运行三元身份校验', () => {
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
