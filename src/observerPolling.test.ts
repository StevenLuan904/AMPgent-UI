import { describe, expect, it } from 'vitest'
import { nodeDetailCacheTtlMs, observerCreatePrefetchQueue, observerIdlePrefetchDelayMs, observerInitialPrefetchCount, observerInFlightStageIds, observerListTimeoutMs, observerMergePrefetchQueue, observerNextPrefetchStage, observerNodeDetailCacheKey, observerNodeDetailTimeoutMs, observerPendingPrefetchCount, observerPollingIntervalMs, observerPrefetchQueueMatches, observerPrefetchInFlightKey, observerPrefetchRefreshExpired, observerPrefetchStageOrder, observerRequeuePrefetchStage, observerResponseIsStale, observerRunDetailCacheKey, observerRunDetailTimeoutMs, observerRunListCacheKey, observerSnapshotCacheMaxBytes, observerSnapshotCacheTtlMs, observerSnapshotCacheVersion, observerStaleRetryDelayMs } from './observerPolling'

describe('observer refresh policy', () => {
  it('refreshes active runs more often than terminal runs', () => {
    expect(observerPollingIntervalMs('running')).toBe(30_000)
    expect(observerPollingIntervalMs('submitted')).toBe(30_000)
    expect(observerPollingIntervalMs('succeeded')).toBe(300_000)
    expect(observerPollingIntervalMs('failed')).toBe(300_000)
  })

  it('limits initial stage prefetch and keeps a bounded detail cache TTL', () => {
    expect(observerInitialPrefetchCount(16)).toBe(1)
    expect(observerInitialPrefetchCount(2)).toBe(1)
    expect(observerInitialPrefetchCount(0)).toBe(0)
    expect(nodeDetailCacheTtlMs).toBe(60_000)
  })

  it('refreshes expired cache for the initial slice but not the idle tail', () => {
    expect(observerPrefetchRefreshExpired('initial')).toBe(true)
    expect(observerPrefetchRefreshExpired('idle')).toBe(false)
  })

  it('uses a remote-read budget that tolerates slow aggregate queries', () => {
    expect(observerListTimeoutMs).toBe(30_000)
    expect(observerRunDetailTimeoutMs).toBe(30_000)
    expect(observerNodeDetailTimeoutMs).toBe(20_000)
    expect(observerIdlePrefetchDelayMs).toBe(8_000)
    expect(observerStaleRetryDelayMs).toBe(3_000)
  })

  it('treats restored and background-refresh responses as stale, but not fresh hits', () => {
    expect(observerResponseIsStale('restored-stale')).toBe(true)
    expect(observerResponseIsStale('stale-refresh')).toBe(true)
    expect(observerResponseIsStale('hit')).toBe(false)
    expect(observerResponseIsStale(null)).toBe(false)
  })

  it('prioritizes observable progress without changing the server order for ties', () => {
    const pending = { status: 'pending', current: 0, total: 0, id: 'empty' }
    const completed = { status: 'pending', current: 2, total: 4, id: 'progress' }
    const running = { status: 'running', current: 0, total: 1, id: 'active' }
    expect(observerPrefetchStageOrder([pending, completed, running]).map((stage) => stage.id)).toEqual(['active', 'progress', 'empty'])
  })

  it('keeps the idle queue cursor across same-run detail refreshes', () => {
    const stages = [
      { id: 'first', status: 'running', current: 1, total: 2 },
      { id: 'second', status: 'pending', current: 0, total: 0 },
      { id: 'third', status: 'pending', current: 0, total: 0 },
      { id: 'fourth', status: 'pending', current: 0, total: 0 },
    ]
    const initial = observerCreatePrefetchQueue('run-1', stages)
    expect(initial.nextIndex).toBe(1)
    const afterSecond = observerNextPrefetchStage(initial, new Set()).queue
    expect(afterSecond.nextIndex).toBe(2)
    const refreshed = observerMergePrefetchQueue('run-1', stages, afterSecond)
    expect(refreshed.nextIndex).toBe(2)
    const third = observerNextPrefetchStage(refreshed, new Set())
    expect(third.stageId).toBe('third')
    const fourth = observerNextPrefetchStage(third.queue, new Set())
    expect(fourth.stageId).toBe('fourth')
    expect(observerPendingPrefetchCount(fourth.queue, new Set())).toBe(0)
  })

  it('skips cached tail nodes without rewinding or issuing a duplicate prefetch', () => {
    const stages = [
      { id: 'first', status: 'running', current: 1, total: 2 },
      { id: 'second', status: 'pending', current: 0, total: 0 },
      { id: 'third', status: 'pending', current: 0, total: 0 },
      { id: 'fourth', status: 'pending', current: 0, total: 0 },
    ]
    const queue = observerCreatePrefetchQueue('run-1', stages)
    const next = observerNextPrefetchStage(queue, new Set(['second', 'third']))
    expect(next.stageId).toBe('fourth')
    expect(next.queue.nextIndex).toBe(4)
  })

  it('restarts a completed queue when a later detail adds a new stage', () => {
    const stages = [
      { id: 'first', status: 'running', current: 1, total: 2 },
      { id: 'second', status: 'pending', current: 0, total: 0 },
      { id: 'third', status: 'pending', current: 0, total: 0 },
    ]
    const complete = observerNextPrefetchStage(
      observerNextPrefetchStage(observerCreatePrefetchQueue('run-1', stages), new Set(['third'])).queue,
      new Set(['third', 'first', 'second']),
    ).queue
    const merged = observerMergePrefetchQueue('run-1', [...stages, { id: 'new-stage', status: 'running', current: 1, total: 1 }], complete)
    expect(merged.nextIndex).toBe(3)
    expect(observerNextPrefetchStage(merged, new Set(['first', 'second', 'third'])).stageId).toBe('new-stage')
  })

  it('does not select an in-flight item and retries a failed item once', () => {
    const queue = observerCreatePrefetchQueue('run-1', [
      { id: 'first', status: 'running', current: 1, total: 1 },
      { id: 'second', status: 'pending', current: 0, total: 0 },
      { id: 'third', status: 'pending', current: 0, total: 0 },
    ])
    const blocked = observerNextPrefetchStage(queue, new Set(), new Set(['second', 'third']))
    expect(blocked.stageId).toBeNull()
    const afterFailure = observerRequeuePrefetchStage({ ...queue, nextIndex: 3 }, 'third')
    expect(afterFailure.nextIndex).toBe(2)
    expect(observerRequeuePrefetchStage(afterFailure, 'third').nextIndex).toBe(2)
  })

  it('isolates unfinished prefetches when switching from run A to run B', () => {
    const runAQueue = { ...observerCreatePrefetchQueue('run-a', [{ id: 'shared', status: 'pending', current: 0, total: 0 }]), epoch: 1 }
    const runBQueue = { ...observerCreatePrefetchQueue('run-b', [{ id: 'shared', status: 'pending', current: 0, total: 0 }]), epoch: 2 }
    const inFlight = new Set([observerPrefetchInFlightKey('run-a', 'shared'), observerPrefetchInFlightKey('run-b', 'shared')])
    expect(observerInFlightStageIds('run-a', inFlight)).toEqual(new Set(['shared']))
    expect(observerInFlightStageIds('run-b', inFlight)).toEqual(new Set(['shared']))
    expect(observerPrefetchQueueMatches(runAQueue, 'run-b', 2)).toBe(false)
    expect(observerPrefetchQueueMatches(runBQueue, 'run-b', 2)).toBe(true)
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
