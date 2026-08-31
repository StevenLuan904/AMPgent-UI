import type { SchedulerErrorCategory, TemporalObservability } from './types'

const errorLabels: Record<SchedulerErrorCategory, string> = {
  timeout: '响应超时',
  connectivity: '连接异常',
  unavailable: '服务不可用',
  permission: '权限异常',
  not_found: '记录缺失',
  cancelled: '请求已取消',
  application_error: '应用错误',
  unknown: '原因未分类',
}

const historyLabels: Record<TemporalObservability['history_read_status'], string> = {
  succeeded: '历史读取正常',
  failed: '历史读取失败',
  unavailable: '历史读取不可用',
  not_queried: '历史读取未查询',
}

export function schedulerHealthPresentation(observability: TemporalObservability) {
  if (observability.is_stale) return { label: '调度状态过期', tone: 'warning' as const }
  if (observability.status === 'healthy') return { label: '调度正常', tone: 'healthy' as const }
  if (observability.status === 'degraded') return { label: '调度延迟', tone: 'warning' as const }
  if (observability.status === 'unavailable') return { label: '调度不可用', tone: 'error' as const }
  return { label: '调度待观测', tone: 'neutral' as const }
}

export function schedulerHealthDescription(observability: TemporalObservability) {
  const details = [historyLabels[observability.history_read_status]]
  const category = observability.history_read_error_category ?? observability.scheduler_error_category
  if (category) details.push(errorLabels[category])
  details.push('数据库运维证据')
  return details.join(' · ')
}
