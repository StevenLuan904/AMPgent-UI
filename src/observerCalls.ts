import type { NodeDetail, ToolCallRelation } from './types'

export const observerCallPageLimit = 40

export type NodeCallsWindow = NonNullable<NodeDetail['calls_window']>

export function observerNodeCallsUrl(detailUrl: string, options: { cursor?: string | null; limit?: number } = {}) {
  const url = new URL(detailUrl, 'http://observer.invalid')
  const limit = Math.min(100, Math.max(1, Math.trunc(options.limit ?? observerCallPageLimit)))
  url.searchParams.set('calls_limit', String(limit))
  if (options.cursor) url.searchParams.set('calls_cursor', options.cursor)
  return url.origin === 'http://observer.invalid' && detailUrl.startsWith('/')
    ? `${url.pathname}${url.search}`
    : url.toString()
}

function mergeRelations(existing: ToolCallRelation[] | undefined, incoming: ToolCallRelation[] | undefined) {
  const seen = new Set<string>()
  const merged: ToolCallRelation[] = []
  for (const relation of [...(incoming ?? []), ...(existing ?? [])]) {
    const key = `${relation.direction}:${relation.related_call_id}:${relation.relation_type}`
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(relation)
  }
  return merged
}

function mergeCalls(existing: NodeDetail['calls'], incoming: NodeDetail['calls'], mode: 'head' | 'older') {
  const byId = new Map<string, NodeDetail['calls'][number]>()
  const ordered = mode === 'older' ? [...existing, ...incoming] : [...incoming, ...existing]
  for (const call of ordered) {
    const previous = byId.get(call.id)
    byId.set(call.id, previous
      ? { ...previous, ...call, relations: mergeRelations(previous.relations, call.relations) }
      : call)
  }
  return [...byId.values()]
}

function mergeWindow(existing: NodeCallsWindow | undefined, incoming: NodeCallsWindow | undefined, loadedCount: number, mode: 'head' | 'older'): NodeCallsWindow | undefined {
  if (!incoming && !existing) return undefined
  if (!incoming) return existing
  const total = Number.isInteger(incoming.total) ? incoming.total : existing?.total
  const preserveOlderCursor = mode === 'head' && Boolean(existing?.has_more && existing.next_cursor && existing.total !== undefined)
  const nextCursor = preserveOlderCursor ? existing?.next_cursor : incoming.next_cursor
  const hasMore = preserveOlderCursor ? Boolean(existing?.has_more) : Boolean(incoming.has_more)
  const remaining = total === undefined
    ? (preserveOlderCursor ? existing?.remaining : incoming.remaining)
    : Math.max(0, total - loadedCount)
  return {
    limit: incoming.limit ?? existing?.limit,
    next_cursor: nextCursor,
    has_more: hasMore,
    ...(remaining === undefined ? {} : { remaining }),
    ...(total === undefined ? {} : { total }),
  }
}

/**
 * Merge a head refresh or an older cursor page without treating either page
 * as the complete calls collection. The API's call id is the only identity
 * used for de-duplication.
 */
export function mergeNodeDetailCalls(existing: NodeDetail | undefined, incoming: NodeDetail, mode: 'head' | 'older' = 'head'): NodeDetail {
  const calls = mergeCalls(existing?.calls ?? [], incoming.calls ?? [], mode)
  return {
    ...(existing ?? incoming),
    ...incoming,
    calls,
    calls_window: mergeWindow(existing?.calls_window, incoming.calls_window, calls.length, mode),
  }
}

export function nodeCallsWindowLabel(detail: NodeDetail | undefined) {
  const window = detail?.calls_window
  if (!window) return detail && detail.calls.length >= observerCallPageLimit ? '仅当前窗口' : null
  if (window.has_more) {
    return window.remaining === undefined
      ? `已载入 ${detail?.calls.length ?? 0} 条 · 仍有更早调用`
      : `已载入 ${detail?.calls.length ?? 0}/${window.total ?? '—'} 条 · 仍有 ${window.remaining} 条更早调用`
  }
  return window.total === undefined ? `已载入 ${detail?.calls.length ?? 0} 条` : `已载入全部 ${window.total} 条调用`
}
