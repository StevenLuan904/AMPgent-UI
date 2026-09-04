const activeRunStatuses = new Set(['created', 'submitted', 'running'])

export const nodeDetailCacheTtlMs = 60_000
export const observerSnapshotCacheVersion = 1
export const observerSnapshotCacheTtlMs = 24 * 60 * 60 * 1_000
export const observerSnapshotCacheMaxBytes = 2_000_000

function normalizedApiBase(apiBase: string) {
  return apiBase.trim().replace(/\/+$/, '') || 'local'
}

export function observerPollingIntervalMs(status: string | undefined) {
  return activeRunStatuses.has(status ?? '') ? 15_000 : 60_000
}

export function observerInitialPrefetchCount(stageCount: number) {
  return Math.min(4, Math.max(0, stageCount))
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
