import { describe, expect, it } from 'vitest'
import { nodeDetailCacheTtlMs, observerIdlePrefetchDelayMs, observerInitialPrefetchCount, observerListTimeoutMs, observerNodeDetailCacheKey, observerNodeDetailTimeoutMs, observerPollingIntervalMs, observerPrefetchStageOrder, observerRunDetailCacheKey, observerRunDetailTimeoutMs, observerRunListCacheKey, observerSnapshotCacheMaxBytes, observerSnapshotCacheTtlMs, observerSnapshotCacheVersion } from './observerPolling'

describe('observer refresh policy', () => {
  it('refreshes active runs more often than terminal runs', () => {
    expect(observerPollingIntervalMs('running')).toBe(30_000)
    expect(observerPollingIntervalMs('submitted')).toBe(30_000)
    expect(observerPollingIntervalMs('succeeded')).toBe(90_000)
    expect(observerPollingIntervalMs('failed')).toBe(90_000)
  })

  it('limits initial stage prefetch and keeps a bounded detail cache TTL', () => {
    expect(observerInitialPrefetchCount(16)).toBe(2)
    expect(observerInitialPrefetchCount(2)).toBe(2)
    expect(observerInitialPrefetchCount(0)).toBe(0)
    expect(nodeDetailCacheTtlMs).toBe(60_000)
  })

  it('uses a remote-read budget that tolerates slow aggregate queries', () => {
    expect(observerListTimeoutMs).toBe(30_000)
    expect(observerRunDetailTimeoutMs).toBe(30_000)
    expect(observerNodeDetailTimeoutMs).toBe(20_000)
    expect(observerIdlePrefetchDelayMs).toBe(8_000)
  })

  it('prioritizes observable progress without changing the server order for ties', () => {
    const pending = { status: 'pending', current: 0, total: 0, id: 'empty' }
    const completed = { status: 'pending', current: 2, total: 4, id: 'progress' }
    const running = { status: 'running', current: 0, total: 1, id: 'active' }
    expect(observerPrefetchStageOrder([pending, completed, running]).map((stage) => stage.id)).toEqual(['active', 'progress', 'empty'])
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
