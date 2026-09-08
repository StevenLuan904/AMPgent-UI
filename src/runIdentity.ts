export interface RunIdentity {
  id: string
  temporal_workflow_id?: string | null
  temporal_run_id?: string | null
  workflow_id?: string | null
}

export function assertMatchingRunIdentity(expected: RunIdentity, actual: RunIdentity) {
  if (actual.id !== expected.id) {
    throw new Error('运行身份校验失败：PostgreSQL 运行编号不一致')
  }
  if (
    actual.temporal_workflow_id
    && actual.workflow_id
    && actual.temporal_workflow_id !== actual.workflow_id
  ) {
    throw new Error('运行身份校验失败：Temporal 工作流编号不一致')
  }
  for (const field of ['temporal_workflow_id', 'temporal_run_id'] as const) {
    if (expected[field] !== undefined && expected[field] !== actual[field]) {
      throw new Error(`运行身份校验失败：${field === 'temporal_workflow_id' ? 'Temporal 工作流编号' : 'Temporal 运行编号'}不一致`)
    }
  }
}
