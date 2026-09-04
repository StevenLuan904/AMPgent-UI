const activeRunStatuses = new Set(['created', 'submitted', 'running'])

export const nodeDetailCacheTtlMs = 60_000
export const observerListTimeoutMs = 30_000
export const observerRunDetailTimeoutMs = 30_000
export const observerNodeDetailTimeoutMs = 20_000
export const observerIdlePrefetchDelayMs = 8_000
export const observerSnapshotCacheVersion = 1
export const observerSnapshotCacheTtlMs = 24 * 60 * 60 * 1_000
export const observerSnapshotCacheMaxBytes = 2_000_000
export const observerStaleRetryDelayMs = 3_000

export function observerResponseIsStale(cacheState: string | null) {
  return cacheState === 'restored-stale' || cacheState === 'stale-refresh'
}

function normalizedApiBase(apiBase: string) {
  return apiBase.trim().replace(/\/+$/, '') || 'local'
}

export function observerPollingIntervalMs(status: string | undefined) {
  return activeRunStatuses.has(status ?? '') ? 30_000 : 90_000
}

export function observerInitialPrefetchCount(stageCount: number) {
  return Math.min(2, Math.max(0, stageCount))
}

export function observerPrefetchRefreshExpired(scope: 'initial' | 'idle') {
  return scope === 'initial'
}

type PrefetchStage = { status: string; current: number; total: number }
export type ObserverPrefetchStage = PrefetchStage & { id: string }
export type ObserverPrefetchQueue = { runId: string; orderedStageIds: string[]; nextIndex: number; retryCounts?: Record<string, number>; epoch?: number }

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

/**
 * Create a queue once for a run. The first slice is reserved for the
 * interactive graph; the remaining ids are consumed by the idle pump.
 */
export function observerCreatePrefetchQueue(runId: string, stages: ObserverPrefetchStage[], initialCount = observerInitialPrefetchCount(stages.length)): ObserverPrefetchQueue {
  const orderedStageIds = observerPrefetchStageOrder(stages).map((stage) => stage.id)
  return { runId, orderedStageIds, nextIndex: Math.min(Math.max(0, initialCount), orderedStageIds.length) }
}

/**
 * Detail polling may reveal new stages, but it must not rewind an existing
 * queue. Existing order is retained so a 30-second refresh cannot starve the
 * tail of a run by repeatedly re-adding the first two nodes.
 */
export function observerMergePrefetchQueue(runId: string, stages: ObserverPrefetchStage[], previous: ObserverPrefetchQueue | null, initialCount = observerInitialPrefetchCount(stages.length)): ObserverPrefetchQueue {
  if (!previous || previous.runId !== runId) return observerCreatePrefetchQueue(runId, stages, initialCount)
  const known = new Set(previous.orderedStageIds)
  const additions = observerPrefetchStageOrder(stages).map((stage) => stage.id).filter((id) => !known.has(id))
  return { ...previous, orderedStageIds: [...previous.orderedStageIds, ...additions] }
}

export function observerNextPrefetchStage(queue: ObserverPrefetchQueue, cachedStageIds: ReadonlySet<string>, inFlightStageIds: ReadonlySet<string> = new Set()): { queue: ObserverPrefetchQueue; stageId: string | null } {
  let nextIndex = queue.nextIndex
  while (nextIndex < queue.orderedStageIds.length) {
    const stageId = queue.orderedStageIds[nextIndex]
    nextIndex += 1
    if (!cachedStageIds.has(stageId) && !inFlightStageIds.has(stageId)) return { queue: { ...queue, nextIndex }, stageId }
  }
  return { queue: { ...queue, nextIndex }, stageId: null }
}

/** A failed idle read gets one automatic retry; later attempts stay user-driven. */
export function observerRequeuePrefetchStage(queue: ObserverPrefetchQueue, stageId: string, maxAutomaticRetries = 1): ObserverPrefetchQueue {
  const index = queue.orderedStageIds.indexOf(stageId)
  if (index < 0) return queue
  const retryCounts = queue.retryCounts ?? {}
  const retries = retryCounts[stageId] ?? 0
  if (retries >= maxAutomaticRetries) return queue
  return { ...queue, nextIndex: Math.min(queue.nextIndex, index), retryCounts: { ...retryCounts, [stageId]: retries + 1 } }
}

export function observerPrefetchInFlightKey(runId: string, stageId: string) {
  return `${runId}:${stageId}`
}

export function observerInFlightStageIds(runId: string, inFlightKeys: ReadonlySet<string>) {
  const prefix = `${runId}:`
  return new Set([...inFlightKeys].filter((key) => key.startsWith(prefix)).map((key) => key.slice(prefix.length)))
}

export function observerPrefetchQueueMatches(queue: ObserverPrefetchQueue | null, runId: string, epoch: number) {
  return queue !== null && queue.runId === runId && queue.epoch === epoch
}

export function observerPendingPrefetchCount(queue: ObserverPrefetchQueue, cachedStageIds: ReadonlySet<string>) {
  return queue.orderedStageIds.slice(queue.nextIndex).filter((stageId) => !cachedStageIds.has(stageId)).length
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
