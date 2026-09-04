import { describe, expect, it } from 'vitest'
import { nodeDetailCacheTtlMs, observerInitialPrefetchCount, observerNodeDetailCacheKey, observerPollingIntervalMs, observerRunDetailCacheKey, observerRunListCacheKey, observerSnapshotCacheMaxBytes, observerSnapshotCacheTtlMs, observerSnapshotCacheVersion } from './observerPolling'

describe('observer refresh policy', () => {
  it('refreshes active runs more often than terminal runs', () => {
    expect(observerPollingIntervalMs('running')).toBe(15_000)
    expect(observerPollingIntervalMs('submitted')).toBe(15_000)
    expect(observerPollingIntervalMs('succeeded')).toBe(60_000)
    expect(observerPollingIntervalMs('failed')).toBe(60_000)
  })

  it('limits initial stage prefetch and keeps a bounded detail cache TTL', () => {
    expect(observerInitialPrefetchCount(16)).toBe(4)
    expect(observerInitialPrefetchCount(2)).toBe(2)
    expect(observerInitialPrefetchCount(0)).toBe(0)
    expect(nodeDetailCacheTtlMs).toBe(60_000)
  })

  it('isolates cached stage details by normalized observer base URL', () => {
    expect(observerNodeDetailCacheKey('  http://observer.test/// ', 'run-1', 'stage-a')).toBe('http://observer.test:run-1:stage-a')
    expect(observerNodeDetailCacheKey('http://other-observer.test', 'run-1', 'stage-a')).not.toBe(observerNodeDetailCacheKey('http://observer.test', 'run-1', 'stage-a'))
    expect(observerRunListCacheKey('http://observer.test///')).toBe('http://observer.test:runs')
    expect(observerRunDetailCacheKey('http://observer.test///', 'run-1')).toBe('http://observer.test:run:run-1')
    expect(observerRunDetailCacheKey('http://other-observer.test', 'run-1')).not.toBe(observerRunDetailCacheKey('http://observer.test', 'run-1'))
    expect(observerSnapshotCacheVersion).toBe(1)
    expect(observerSnapshotCacheTtlMs).toBe(86_400_000)
    expect(observerSnapshotCacheMaxBytes).toBe(2_000_000)
  })
})
