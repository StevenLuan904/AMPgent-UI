import type { GraphStage } from './types'

type ReadableRuntimeNode = Pick<GraphStage, 'id' | 'status'> & {
  runtime?: Pick<NonNullable<GraphStage['runtime']>, 'node_type' | 'observed_at' | 'expanded' | 'child_ids' | 'raw_label' | 'activity_retry_count'>
}

export type RuntimeNodePosition = { x: number; y: number }
export type RuntimeNodePositions = Readonly<Record<string, RuntimeNodePosition>>

export type ExpandedClusterLayoutMember = {
  id: string
  position?: RuntimeNodePosition
  width?: number
  height?: number
}

/**
 * Produces a stable revision for the visible members of an expanded cluster.
 * The revision intentionally includes measured dimensions: React Flow can
 * first mount a fallback-sized card and measure its real distribution later.
 */
export function expandedClusterLayoutRevision(
  groupId: string,
  members: ReadonlyArray<ExpandedClusterLayoutMember>,
) {
  return `${groupId}|${[...members]
    .sort((left, right) => left.id.localeCompare(right.id))
    .map((member) => [
      member.id,
      member.position ? `${Math.round(member.position.x)},${Math.round(member.position.y)}` : 'unplaced',
      Math.round(member.width ?? 0),
      Math.round(member.height ?? 0),
    ].join(':'))
    .join('|')}`
}

/**
 * A cluster needs another bounded focus pass only when its real layout
 * changed. A scientist who has panned or zoomed after the last pass owns the
 * viewport, so hydration must not take it back.
 */
export function shouldRefocusExpandedCluster(
  previousRevision: string | null,
  nextRevision: string,
  userMovedViewport: boolean,
) {
  return Boolean(nextRevision) && nextRevision !== previousRevision && !userMovedViewport
}

/**
 * Compresses only the selected reading surface. Hidden historical nodes keep
 * their original layout, but no longer reserve empty columns in the opening
 * view. Nodes that intentionally share a source column remain a vertical
 * parallel or expanded-batch cluster.
 */
export function compactReadableRuntimePositions(
  ids: ReadonlyArray<string>,
  positions: RuntimeNodePositions,
  options: { xStart?: number; xGap?: number; singleY?: number; clusterCenterY?: number; rowGap?: number } = {},
) {
  const xStart = options.xStart ?? 190
  const xGap = options.xGap ?? 330
  const singleY = options.singleY ?? 220
  const clusterCenterY = options.clusterCenterY ?? 300
  const rowGap = options.rowGap ?? 190
  const positioned = ids
    .map((id) => ({ id, position: positions[id] }))
    .filter((item): item is { id: string; position: RuntimeNodePosition } => Boolean(item.position))
  const columns = [...new Set(positioned.map(({ position }) => position.x))].sort((left, right) => left - right)
  const output: Record<string, RuntimeNodePosition> = {}
  columns.forEach((sourceX, columnIndex) => {
    const members = positioned
      .filter(({ position }) => position.x === sourceX)
      .sort((left, right) => left.position.y - right.position.y || left.id.localeCompare(right.id))
    members.forEach(({ id }, rowIndex) => {
      const y = members.length === 1
        ? singleY
        : clusterCenterY + (rowIndex - (members.length - 1) / 2) * rowGap
      output[id] = { x: xStart + columnIndex * xGap, y }
    })
  })
  return output
}

const eventTypes = new Set(['lifecycle_event', 'event_group'])
const toolTypes = new Set(['tool_call', 'tool_group', 'batch_group'])
const structureTypes = new Set(['structure_evidence'])
const candidateTypes = new Set(['generation', 'candidate_group', 'candidate_preview'])
const populationTypes = new Set(['population_summary'])
const summaryTypes = new Set(['tool_summary_group', 'tool_summary'])

function laneFor(node: ReadableRuntimeNode) {
  const type = node.runtime?.node_type
  if (eventTypes.has(type ?? '')) return 'events'
  if (toolTypes.has(type ?? '')) return 'tools'
  if (structureTypes.has(type ?? '')) return 'structure'
  if (summaryTypes.has(type ?? '')) return 'summary'
  if (populationTypes.has(type ?? '')) return 'population'
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
  return observedTime(left) - observedTime(right)
    || Number(Boolean(right.runtime?.expanded)) - Number(Boolean(left.runtime?.expanded))
    || (left.status === 'running' ? -1 : 0) - (right.status === 'running' ? -1 : 0)
    || left.id.localeCompare(right.id)
}

function readableSemanticOrder(left: ReadableRuntimeNode, right: ReadableRuntimeNode) {
  return Number(Boolean(right.runtime?.expanded)) - Number(Boolean(left.runtime?.expanded))
    || observedTime(right) - observedTime(left)
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
export function selectReadableRuntimeNodeIds(nodes: ReadonlyArray<ReadableRuntimeNode>, positionsOrLimit?: RuntimeNodePositions | number, requestedLimit = 7) {
  let positions = positionsOrLimit
  let limit = requestedLimit
  // Preserve the old two-argument call shape for callers that pass a limit.
  if (typeof positions === 'number') {
    limit = positions
    positions = undefined
  }
  const eligible = nodes.filter((node) => laneFor(node) !== null)
  if (!eligible.length || limit <= 0) return []
  const target = Math.min(Math.max(5, limit), eligible.length)
  const selected = new Set<string>()
  const protectedContext = new Set<string>()
  const spatialOrder = [...eligible].sort((left, right) => readableOrder(left, right, positions))
  // Keep one contiguous recent window on the spine. This prevents an old
  // event quota from pulling the readable view back into a table of lanes.
  spatialOrder.slice(-target).forEach((node) => selected.add(node.id))
  const ensureContext = (predicate: (node: ReadableRuntimeNode) => boolean) => {
    const existing = [...selected].find((id) => predicate(eligible.find((node) => node.id === id)!))
    if (existing) {
      protectedContext.add(existing)
      return
    }
    const candidate = [...eligible].reverse().find(predicate)
    if (!candidate) return
    const replace = [...selected]
      .filter((id) => !protectedContext.has(id))
      .sort((left, right) => readableOrder(eligible.find((node) => node.id === left)!, eligible.find((node) => node.id === right)!, positions))[0]
    if (!replace) return
    selected.delete(replace)
    selected.add(candidate.id)
    protectedContext.add(candidate.id)
  }
  ensureContext((node) => node.runtime?.raw_label === 'agent_decision.recorded')
  ensureContext((node) => (node.runtime?.activity_retry_count ?? 0) > 0)
  ensureContext((node) => laneFor(node) === 'structure')
  ensureContext((node) => laneFor(node) === 'summary')
  ensureContext((node) => laneFor(node) === 'population')
  ensureContext((node) => laneFor(node) === 'candidates')
  ensureContext((node) => laneFor(node) === 'events')
  return [...selected]
}

export function readableRuntimeNodeCount(nodes: ReadonlyArray<ReadableRuntimeNode>) {
  return nodes.filter((node) => laneFor(node) !== null).length
}
