const activeRunStatuses = new Set(['created', 'submitted', 'running'])

export const nodeDetailCacheTtlMs = 60_000
export const observerListTimeoutMs = 30_000
export const observerRunDetailTimeoutMs = 30_000
export const observerNodeDetailTimeoutMs = 20_000
export const observerIdlePrefetchDelayMs = 8_000
export const observerSnapshotCacheVersion = 1
export const observerSnapshotCacheTtlMs = 24 * 60 * 60 * 1_000
export const observerSnapshotCacheMaxBytes = 2_000_000

function normalizedApiBase(apiBase: string) {
  return apiBase.trim().replace(/\/+$/, '') || 'local'
}

export function observerPollingIntervalMs(status: string | undefined) {
  return activeRunStatuses.has(status ?? '') ? 30_000 : 90_000
}

export function observerInitialPrefetchCount(stageCount: number) {
  return Math.min(2, Math.max(0, stageCount))
}

type PrefetchStage = { status: string; current: number; total: number }

export function observerStageProgressScore(stage: PrefetchStage) {
  if (stage.status !== 'pending') return 2
  if (stage.current > 0 || stage.total > 0) return 1
  return 0
}

export function observerPrefetchStageOrder<T extends PrefetchStage>(stages: T[]) {
  return stages
    .map((stage, index) => ({ stage, index }))
    .sort((left, right) => observerStageProgressScore(right.stage) - observerStageProgressScore(left.stage) || left.index - right.index)
    .map(({ stage }) => stage)
}

export function observerNodeDetailCacheKey(apiBase: string, runId: string, stageId: string) {
  return `${normalizedApiBase(apiBase)}:${runId}:${stageId}`
}

export function observerRunListCacheKey(apiBase: string) {
  return `${normalizedApiBase(apiBase)}:runs`
}

export function observerRunDetailCacheKey(apiBase: string, runId: string) {
  return `${normalizedApiBase(apiBase)}:run:${runId}`
}
