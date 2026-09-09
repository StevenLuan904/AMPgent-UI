import type { RunListItem } from './types'

export function formatRunTitle(run: RunListItem) {
  if (run.status === 'running') return '正在运行'
  if (run.status === 'failed') return '运行异常终止'
  if (run.status === 'cancelled') return '运行已取消'
  if (run.status === 'created') return '已创建'
  if (run.status === 'submitted') return '已提交'
  if (run.status === 'succeeded') return '已完成'
  return '序列设计轮次'
}

export function formatRunSummary(run: Pick<RunListItem, 'candidate_count' | 'round_summary'>) {
  const summary = run.round_summary
  const parts = [`候选 ${run.candidate_count.toLocaleString()}`]
  const evaluationCount = summary?.evaluation_count
  const operationTypeCount = summary?.operation_type_count
  const scientificCardCount = summary?.scientific_card_count
  if (typeof evaluationCount === 'number' && Number.isInteger(evaluationCount)) parts.push(`评估 ${evaluationCount.toLocaleString()}`)
  if (typeof operationTypeCount === 'number' && Number.isInteger(operationTypeCount)) parts.push(`科学操作 ${operationTypeCount}`)
  else if (typeof scientificCardCount === 'number' && Number.isInteger(scientificCardCount)) parts.push(`科学节点 ${scientificCardCount}`)
  return parts.join(' · ')
}

/** Product-facing canvas identity; an explicit backend display round wins when available. */
export function formatCanvasRunTitle(run: Pick<RunListItem, 'display_round'>) {
  const explicitRound = run.display_round?.match(/\d+/)?.[0]
  return explicitRound ? `#${explicitRound}次Agent短肽迭代` : '#36次Agent短肽迭代'
}
