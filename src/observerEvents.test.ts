import { describe, expect, it } from 'vitest'
import { loadObserverEventHistory, mergeObserverEventPage, observerEventPageUrl, shouldFetchOlderObserverEvents } from './observerEvents'
import type { RunDetail } from './types'

const event = (sequence_no: number): RunDetail['events'][number] => ({
  sequence_no,
  type: `run.note.${sequence_no}`,
  actor: 'observer',
  payload: {},
  occurred_at: `2026-09-07T00:00:${String(sequence_no).padStart(2, '0')}Z`,
})

const detail = (events: RunDetail['events'], event_window?: RunDetail['event_window']) => ({ events, event_window } as Pick<RunDetail, 'events' | 'event_window'>)

describe('observer event history contract', () => {
  it('builds a backward-compatible cursor URL without changing the detail route', () => {
    expect(observerEventPageUrl('http://observer.test/v1/observer/runs/run-1', 'cursor/older', 32)).toBe('http://observer.test/v1/observer/runs/run-1?events_cursor=cursor%2Folder&events_limit=32')
  })

  it('requires both explicit has_more and a cursor before requesting history', () => {
    expect(shouldFetchOlderObserverEvents({ has_more: true, next_cursor: 'older' })).toBe(true)
    expect(shouldFetchOlderObserverEvents({ has_more: true, next_cursor: null })).toBe(false)
    expect(shouldFetchOlderObserverEvents({ has_more: false, next_cursor: 'older' })).toBe(false)
    expect(shouldFetchOlderObserverEvents(undefined)).toBe(false)
  })

  it('merges pages by persisted sequence and removes overlap', () => {
    expect(mergeObserverEventPage([event(4), event(5)], [event(2), event(4), event(3)])).toEqual([event(2), event(3), event(4), event(5)])
  })

  it('loads explicit older pages, stops at has_more=false, and leaves legacy payloads untouched', async () => {
    const initial = { payload: { ...detail([event(4), event(5)], { limit: 2, has_more: true, next_cursor: 'older-1' }) } as RunDetail, cacheState: null }
    const requested: string[] = []
    const result = await loadObserverEventHistory(
      'http://observer.test/v1/observer/runs/run-1',
      initial,
      async (url) => {
        requested.push(url)
        return requested.length === 1
          ? { payload: detail([event(2), event(3)], { limit: 2, has_more: false, next_cursor: null }) }
          : { payload: detail([]) }
      },
      () => false,
    )
    expect(requested).toEqual(['http://observer.test/v1/observer/runs/run-1?events_cursor=older-1&events_limit=2'])
    expect(result.pagesLoaded).toBe(2)
    expect(result.hasMore).toBe(false)
    expect(result.payload.events.map((item) => item.sequence_no)).toEqual([2, 3, 4, 5])
    expect(result.payload.event_window).toEqual({ limit: 2, has_more: false, next_cursor: null })
    const legacy = await loadObserverEventHistory('http://observer.test/v1/observer/runs/run-1', { payload: { ...detail([event(1)]) } as RunDetail }, async () => { throw new Error('must not fetch') }, () => false)
    expect(legacy.pagesLoaded).toBe(1)
    expect(legacy.payload.events).toEqual([event(1)])
  })

  it('keeps an explicit cursor after the bounded first load and exposes the remaining count', async () => {
    const initial = { payload: { ...detail([event(6)], { limit: 2, has_more: true, next_cursor: 'older-1', remaining: 63 }) } as RunDetail, cacheState: null }
    const requested: string[] = []
    const result = await loadObserverEventHistory(
      'http://observer.test/v1/observer/runs/run-1',
      initial,
      async (url) => {
        requested.push(url)
        return { payload: detail([event(5)], { limit: 2, has_more: true, next_cursor: 'older-2', remaining: 62 }) }
      },
      () => false,
    )
    expect(requested).toHaveLength(1)
    expect(result.pagesLoaded).toBe(2)
    expect(result.hasMore).toBe(true)
    expect(result.nextCursor).toBe('older-2')
    expect(result.remaining).toBe(62)
    expect(result.payload.event_window?.has_more).toBe(true)
  })

  it('supports an explicit continuation batch without claiming history is complete', async () => {
    const initial = { payload: { ...detail([event(6)], { limit: 2, has_more: true, next_cursor: 'older-1', remaining: 3 }) } as RunDetail, cacheState: null }
    let page = 0
    const result = await loadObserverEventHistory(
      'http://observer.test/v1/observer/runs/run-1',
      initial,
      async () => {
        page += 1
        return page < 2
          ? { payload: detail([event(6 - page)], { limit: 2, has_more: true, next_cursor: `older-${page + 1}`, remaining: 3 - page }) }
          : { payload: detail([event(3), event(4)], { limit: 2, has_more: false, next_cursor: null, remaining: 0 }) }
      },
      () => false,
      4,
    )
    expect(result.pagesLoaded).toBe(3)
    expect(result.hasMore).toBe(false)
    expect(result.remaining).toBe(0)
    expect(result.payload.events.map((item) => item.sequence_no)).toEqual([3, 4, 5, 6])
  })

  it('does not turn a page without metadata into a false complete claim', async () => {
    const initial = { payload: { ...detail([event(2)], { limit: 2, has_more: true, next_cursor: 'older-1' }) } as RunDetail, cacheState: null }
    const result = await loadObserverEventHistory(
      'http://observer.test/v1/observer/runs/run-1',
      initial,
      async () => ({ payload: detail([event(1)]) }),
      () => false,
    )
    expect(result.hasMore).toBe(true)
    expect(result.payload.event_window?.next_cursor).toBe('older-1')
  })
})
