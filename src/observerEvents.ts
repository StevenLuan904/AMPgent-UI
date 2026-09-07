import type { RunDetail, TimelineEvent } from './types'

export const observerEventPageLimit = 32
/** Bound each automatic/user-triggered batch; continuation stays explicit. */
export const observerEventPageMax = 4

export type ObserverEventWindow = NonNullable<RunDetail['event_window']>

export type ObserverEventPage = Pick<RunDetail, 'events' | 'event_window'>

export function observerEventPageUrl(detailUrl: string, cursor: string, limit = observerEventPageLimit) {
  const url = new URL(detailUrl, typeof window === 'undefined' ? 'http://observer.invalid' : window.location.origin)
  url.searchParams.set('events_cursor', cursor)
  url.searchParams.set('events_limit', String(limit))
  return url.toString()
}

export function shouldFetchOlderObserverEvents(window: ObserverEventWindow | undefined): window is ObserverEventWindow & { has_more: true; next_cursor: string } {
  return window?.has_more === true && typeof window.next_cursor === 'string' && window.next_cursor.length > 0
}

export function mergeObserverEventPage(current: TimelineEvent[], next: TimelineEvent[]) {
  const bySequence = new Map<number, TimelineEvent>()
  for (const event of [...current, ...next]) bySequence.set(event.sequence_no, event)
  return [...bySequence.values()].sort((left, right) => left.sequence_no - right.sequence_no)
}

export async function loadObserverEventHistory(
  detailUrl: string,
  initial: { payload: RunDetail; cacheState?: string | null },
  fetchPage: (url: string) => Promise<{ payload: ObserverEventPage; cacheState?: string | null }>,
  isStale: (cacheState: string | null | undefined) => boolean,
  maxPages = 1,
) {
  let payload = initial.payload
  let cacheState = initial.cacheState ?? null
  let cursorWindow = payload.event_window
  const seenCursors = new Set<string>()
  let pagesLoaded = 1
  const pageLimit = Math.max(1, Math.min(observerEventPageMax, Math.floor(maxPages)))
  for (let page = 0; page < pageLimit && shouldFetchOlderObserverEvents(cursorWindow); page += 1) {
    const cursor = cursorWindow.next_cursor
    if (seenCursors.has(cursor)) break
    seenCursors.add(cursor)
    try {
      const response = await fetchPage(observerEventPageUrl(detailUrl, cursor, cursorWindow.limit ?? observerEventPageLimit))
      payload = {
        ...payload,
        events: mergeObserverEventPage(payload.events, response.payload.events),
        // A page without window metadata is not proof that history is complete.
        // Preserve the prior cursor and keep the UI honest until the server
        // explicitly returns has_more=false.
        event_window: response.payload.event_window ?? cursorWindow,
      }
      cursorWindow = payload.event_window
      cacheState = isStale(response.cacheState) ? response.cacheState ?? cacheState : cacheState
      pagesLoaded += 1
    } catch {
      // The first page is still authoritative for the fields it contains. Keep
      // the cursor contract so the graph remains honest about older history.
      break
    }
  }
  const hasMore = shouldFetchOlderObserverEvents(payload.event_window)
  return {
    payload,
    cacheState,
    pagesLoaded,
    hasMore,
    nextCursor: hasMore ? payload.event_window?.next_cursor ?? null : null,
    remaining: payload.event_window?.remaining,
  }
}
