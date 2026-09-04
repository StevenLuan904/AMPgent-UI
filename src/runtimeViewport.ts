import type { GraphStage } from './types'

type ReadableRuntimeNode = Pick<GraphStage, 'id' | 'status'> & {
  runtime?: Pick<NonNullable<GraphStage['runtime']>, 'node_type' | 'observed_at' | 'expanded'>
}

export type RuntimeNodePosition = { x: number; y: number }
export type RuntimeNodePositions = Readonly<Record<string, RuntimeNodePosition>>

const eventTypes = new Set(['lifecycle_event', 'event_group'])
const toolTypes = new Set(['tool_call', 'tool_group', 'batch_group'])
const candidateTypes = new Set(['generation'])

function laneFor(node: ReadableRuntimeNode) {
  const type = node.runtime?.node_type
  if (eventTypes.has(type ?? '')) return 'events'
  if (toolTypes.has(type ?? '')) return 'tools'
  if (candidateTypes.has(type ?? '')) return 'candidates'
  return null
}

function observedTime(node: ReadableRuntimeNode) {
  const value = node.runtime?.observed_at ? Date.parse(node.runtime.observed_at) : Number.MAX_SAFE_INTEGER
  return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER
}

function readableOrder(left: ReadableRuntimeNode, right: ReadableRuntimeNode, positions?: RuntimeNodePositions) {
  const leftPosition = positions?.[left.id]
  const rightPosition = positions?.[right.id]
  // The readable window is a spatial window, not just the first records in
  // the data array. This keeps lane context contiguous when late candidates
  // or generation summaries are positioned far to the right.
  if (leftPosition && rightPosition) {
    return leftPosition.x - rightPosition.x
      || leftPosition.y - rightPosition.y
      || readableSemanticOrder(left, right)
  }
  return readableSemanticOrder(left, right)
}

function readableSemanticOrder(left: ReadableRuntimeNode, right: ReadableRuntimeNode) {
  return Number(Boolean(right.runtime?.expanded)) - Number(Boolean(left.runtime?.expanded))
    || observedTime(left) - observedTime(right)
    || (left.status === 'running' ? -1 : 0) - (right.status === 'running' ? -1 : 0)
    || left.id.localeCompare(right.id)
}

/**
 * Selects a bounded readable window without changing the underlying graph.
 * The per-lane quotas keep the initial view contextual; remaining runtime
 * nodes stay in the canvas for explicit panning or a later fit.
 */
export function selectReadableRuntimeNodeIds(nodes: ReadonlyArray<ReadableRuntimeNode>, limit?: number): string[]
export function selectReadableRuntimeNodeIds(nodes: ReadonlyArray<ReadableRuntimeNode>, positions: RuntimeNodePositions, limit?: number): string[]
export function selectReadableRuntimeNodeIds(nodes: ReadonlyArray<ReadableRuntimeNode>, positionsOrLimit?: RuntimeNodePositions | number, requestedLimit = 9) {
  let positions = positionsOrLimit
  let limit = requestedLimit
  // Preserve the old two-argument call shape for callers that pass a limit.
  if (typeof positions === 'number') {
    limit = positions
    positions = undefined
  }
  const eligible = nodes.filter((node) => laneFor(node) !== null)
  if (!eligible.length || limit <= 0) return []
  const target = Math.min(10, Math.max(6, limit), eligible.length)
  const selected = new Set<string>()
  const quotas: Array<[string, number]> = [['events', 3], ['tools', 4], ['candidates', 2]]

  for (const [lane, quota] of quotas) {
    eligible.filter((node) => laneFor(node) === lane).sort((left, right) => readableOrder(left, right, positions)).slice(0, quota).forEach((node) => selected.add(node.id))
  }
  if (selected.size < target) {
    eligible.sort((left, right) => readableOrder(left, right, positions)).forEach((node) => {
      if (selected.size < target) selected.add(node.id)
    })
  }
  return [...selected]
}

export function readableRuntimeNodeCount(nodes: ReadonlyArray<ReadableRuntimeNode>) {
  return nodes.filter((node) => laneFor(node) !== null).length
}
