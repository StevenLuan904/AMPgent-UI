import { formatRunGenerationPopulation } from './generationPopulation'
import type { RunListItem } from './types'

export function formatRunTitle(run: RunListItem) {
  const candidateLabel = run.generation_population
    ? formatRunGenerationPopulation(run.generation_population)
    : `${run.candidate_count.toLocaleString()} 条候选`

  if (run.status === 'running') return `正在运行 · ${candidateLabel}`
  if (run.status === 'failed') return `运行异常终止 · ${candidateLabel}`
  if (run.status === 'cancelled') return `运行已取消 · ${candidateLabel}`
  if (run.structure_record_count > 0) return `结构证据轮次 · ${run.structure_record_count.toLocaleString()} 条记录`
  if (run.status === 'created') return `已创建 · ${candidateLabel}`
  if (run.status === 'submitted') return `已提交 · ${candidateLabel}`
  if (run.status === 'succeeded') return `已完成 · ${candidateLabel}`
  return `序列设计轮次 · ${candidateLabel}`
}
