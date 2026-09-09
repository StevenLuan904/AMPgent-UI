import type { GraphEdgeDetail, GraphStage } from './types'

export type CardSize = { width: number; height: number }
export type CardMeasurements = Readonly<Record<string, Partial<CardSize> | undefined>>
export type CardPosition = { x: number; y: number }

export interface AutoCardLayoutOptions {
  availableWidth?: number
  maxColumns?: number
  expandedGroups?: ReadonlySet<string>
  previousPositions?: Readonly<Record<string, CardPosition | undefined>>
  xStart?: number
  yStart?: number
  columnGap?: number
  rowGap?: number
  clusterGap?: number
  auditStartY?: number
}

const expandedTypes = new Set<string>([
  'tool_group', 'event_group', 'batch_group', 'tool_summary_group', 'candidate_group',
])
const summaryTypes = new Set<string>(['tool_summary', 'tool_summary_group'])
const causalRelations = new Set<NonNullable<GraphEdgeDetail['relation_kind']>>(['dependency', 'retry', 'fallback'])

export function defaultCardSize(node: Pick<GraphStage, 'id' | 'kind' | 'runtime'>): CardSize {
  const runtimeType = node.runtime?.node_type
  if (node.kind === 'structure' || node.runtime?.has_viewer) return { width: 280, height: 250 }
  if (node.id === 'targets') return { width: 280, height: 224 }
  if (runtimeType && expandedTypes.has(runtimeType)) return { width: 280, height: 214 }
  return { width: 280, height: 156 }
}

function finitePositive(value: unknown, fallback: number) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : fallback
}

function sizeFor(node: GraphStage, measurements: CardMeasurements): CardSize {
  const fallback = defaultCardSize(node)
  const measured = measurements[node.id]
  return {
    width: finitePositive(measured?.width, fallback.width),
    height: finitePositive(measured?.height, fallback.height),
  }
}

function layoutNodeIdCandidates(group: GraphStage, rawId: string) {
  if (rawId.startsWith('event:') || rawId.startsWith('call:') || rawId.startsWith('candidate:') || rawId.startsWith('tool-summary:')) return [rawId]
  const prefix = group.runtime?.node_type === 'candidate_group' ? 'candidate:' : 'call:'
  return [
    `${prefix}${rawId}`,
    `event:${rawId}`,
    `call:${rawId}`,
    `candidate:${rawId}`,
    `tool-summary:${rawId}`,
    `tool-summary:${encodeURIComponent(rawId)}`,
    rawId,
  ]
}

/** Resolves the persisted child references without guessing from display labels. */
export function resolveLayoutChildIds(nodes: ReadonlyArray<GraphStage>, group: GraphStage) {
  const nodeIds = new Set(nodes.map((node) => node.id))
  const rawIds = [...(group.runtime?.child_ids ?? []), ...(group.runtime?.event_ids ?? [])]
  return [...new Set(rawIds.flatMap((rawId) => {
    const match = layoutNodeIdCandidates(group, rawId).find((candidate) => nodeIds.has(candidate))
    return match ? [match] : []
  }))]
}

function observedTime(node: GraphStage) {
  const timestamp = node.runtime?.observed_at ? Date.parse(node.runtime.observed_at) : Number.NaN
  return Number.isFinite(timestamp) ? timestamp : Number.MAX_SAFE_INTEGER
}

function generationOrder(node: GraphStage) {
  if (node.runtime?.node_type !== 'candidate_group') return Number.MAX_SAFE_INTEGER
  const parsed = Number(node.runtime.source_id)
  return Number.isInteger(parsed) ? parsed : Number.MAX_SAFE_INTEGER
}

function causalLevel(nodes: ReadonlyArray<GraphStage>, edges: ReadonlyArray<GraphEdgeDetail>) {
  const nodeIds = new Set(nodes.map((node) => node.id))
  const predecessors = new Map<string, string[]>()
  for (const edge of edges) {
    if (!causalRelations.has(edge.relation_kind ?? 'dependency') || !nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue
    predecessors.set(edge.target, [...(predecessors.get(edge.target) ?? []), edge.source])
  }
  const levels = new Map<string, number>()
  const visiting = new Set<string>()
  const visit = (id: string): number => {
    const cached = levels.get(id)
    if (cached !== undefined) return cached
    if (visiting.has(id)) return 0
    visiting.add(id)
    const level = Math.max(0, ...(predecessors.get(id) ?? []).map((parent) => visit(parent) + 1))
    visiting.delete(id)
    levels.set(id, level)
    return level
  }
  nodes.forEach((node) => visit(node.id))
  return levels
}

function orderedNodes(nodes: ReadonlyArray<GraphStage>, edges: ReadonlyArray<GraphEdgeDetail>) {
  const levels = causalLevel(nodes, edges)
  return [...nodes].sort((left, right) =>
    (levels.get(left.id) ?? 0) - (levels.get(right.id) ?? 0)
    || observedTime(left) - observedTime(right)
    || generationOrder(left) - generationOrder(right)
    || left.id.localeCompare(right.id),
  )
}

function laneOrder(node: GraphStage) {
  const type = node.runtime?.node_type
  if (type === 'lifecycle_event' || type === 'event_group') return 0
  if (type === 'tool_call' || type === 'tool_group' || type === 'batch_group') return 1
  if (type === 'scientific_stage') return 2
  if (type === 'structure_evidence') return 3
  if (type === 'candidate_group' || type === 'candidate_preview' || type === 'generation') return 4
  if (type === 'population_summary') return 5
  return 6
}

function semanticOrder(node: GraphStage) {
  const label = `${node.runtime?.raw_label ?? ''} ${node.label}`.toLowerCase()
  if (node.runtime?.distribution_key === 'mic' || /mic|抑菌/.test(label)) return 0
  if (node.runtime?.distribution_key === 'amp_read') return 1
  if (node.runtime?.distribution_key === 'hemolysis') return 2
  if (node.runtime?.distribution_key === 'toxicity') return 3
  if (node.runtime?.distribution_key === 'developability') return 4
  return 10
}

function median(values: number[]) {
  if (!values.length) return Number.MAX_SAFE_INTEGER
  const sorted = [...values].sort((left, right) => left - right)
  return sorted[Math.floor((sorted.length - 1) / 2)]
}

function requestedClusterColumns(availableWidth: number | undefined, maxColumns: number | undefined, childCount: number, childWidth: number, columnGap: number, isSummary: boolean) {
  if (childCount <= 0) return 0
  if (isSummary) return Math.min(3, childCount)
  const widthColumns = !Number.isFinite(availableWidth) || (availableWidth ?? 0) <= 0
    ? 5
    : Math.max(1, Math.floor((availableWidth as number) / Math.max(1, childWidth + columnGap)))
  const capacity = Math.max(1, Math.min(maxColumns ?? widthColumns, widthColumns))
  return Math.min(capacity, childCount)
}

type Column = { width: number; nodes: string[] }

/**
 * Computes a deterministic left-to-right card layout from observed topology.
 * Top-level cards occupy a time/dependency spine; an expanded group consumes
 * the physical columns immediately after its aggregate card. Column widths,
 * row heights, and cluster wrapping are derived from the measured cards.
 */
export function autoCardLayout(
  nodes: ReadonlyArray<GraphStage>,
  edges: ReadonlyArray<GraphEdgeDetail> = [],
  measurements: CardMeasurements = {},
  options: AutoCardLayoutOptions = {},
): Record<string, CardPosition> {
  if (!nodes.length) return {}
  const xStart = options.xStart ?? 190
  const yStart = options.yStart ?? 220
  const columnGap = options.columnGap ?? 35
  const rowGap = options.rowGap ?? 24
  const clusterGap = options.clusterGap ?? 32
  const auditStartY = options.auditStartY ?? 660
  const nodeById = new Map(nodes.map((node) => [node.id, node] as const))
  const sizes = new Map(nodes.map((node) => [node.id, sizeFor(node, measurements)] as const))
  const occupiedSize = (node: GraphStage): CardSize => {
    const size = sizes.get(node.id) ?? defaultCardSize(node)
    // Folded stack cards render two additional pseudo-cards at +12 and +24px.
    // Reserve that collision footprint even though React Flow measures only
    // the front DOM element.
    return !node.runtime?.expanded && expandedTypes.has(node.runtime?.node_type ?? '')
      ? { width: size.width + 24, height: size.height + 24 }
      : size
  }
  const expandedGroups = nodes.filter((node) => (options.expandedGroups?.has(node.id) ?? node.runtime?.expanded) && expandedTypes.has(node.runtime?.node_type ?? '')).map((group) => ({
    group,
    children: resolveLayoutChildIds(nodes, group),
  })).filter(({ children }) => children.length > 0)
  const memberToGroup = new Map<string, string>()
  for (const { group, children } of expandedGroups) for (const childId of children) memberToGroup.set(childId, group.id)
  // An expanded summary group becomes an active reading surface and therefore
  // belongs on the main spine; its folded state remains on the audit rail.
  const summaries = nodes.filter((node) => summaryTypes.has(node.runtime?.node_type ?? '') && !node.runtime?.expanded && !memberToGroup.has(node.id))
  const mainNodes = nodes.filter((node) => !summaries.includes(node) && !memberToGroup.has(node.id))
  const levels = causalLevel(mainNodes, edges)
  const mainOrder = new Map(mainNodes.map((node, index) => [node.id, index] as const))
  const ordered = orderedNodes(mainNodes, edges).sort((left, right) => {
    const predecessorMedian = (node: GraphStage) => median(edges
      .filter((edge) => edge.target === node.id && causalRelations.has(edge.relation_kind ?? 'dependency'))
      .map((edge) => mainOrder.get(edge.source) ?? Number.MAX_SAFE_INTEGER))
    const leftPrevious = options.previousPositions?.[left.id]
    const rightPrevious = options.previousPositions?.[right.id]
    return (levels.get(left.id) ?? 0) - (levels.get(right.id) ?? 0)
      || predecessorMedian(left) - predecessorMedian(right)
      || (leftPrevious?.x ?? Number.MAX_SAFE_INTEGER) - (rightPrevious?.x ?? Number.MAX_SAFE_INTEGER)
      || observedTime(left) - observedTime(right)
      || generationOrder(left) - generationOrder(right)
      || semanticOrder(left) - semanticOrder(right)
      || laneOrder(left) - laneOrder(right)
      || left.id.localeCompare(right.id)
  })

  // Every non-parallel top-level node starts a new logical column. Explicit
  // parallel groups share one column and are vertically stacked.
  const baseColumnByNode = new Map<string, number>()
  const parallelColumn = new Map<string, number>()
  let nextColumn = 0
  for (const node of ordered) {
    const parallelId = node.runtime?.parallel_group_id
    if (parallelId && parallelColumn.has(parallelId)) {
      baseColumnByNode.set(node.id, parallelColumn.get(parallelId)!)
    } else {
      baseColumnByNode.set(node.id, nextColumn)
      if (parallelId) parallelColumn.set(parallelId, nextColumn)
      nextColumn += 1
    }
  }

  const clusterInfo = expandedGroups.map(({ group, children }) => ({
    group,
    children,
    baseColumn: baseColumnByNode.get(group.id) ?? 0,
    columns: requestedClusterColumns(
      options.availableWidth,
      options.maxColumns,
      children.length,
      Math.max(...children.map((id) => sizes.get(id)?.width ?? 280), 280),
      columnGap,
      group.runtime?.node_type === 'tool_summary_group',
    ),
  }))
  const shiftFor = (baseColumn: number) => clusterInfo
    .filter(({ baseColumn: clusterColumn }) => clusterColumn < baseColumn)
    .reduce((total, cluster) => total + cluster.columns, 0)
  const physicalColumn = (baseColumn: number) => baseColumn + shiftFor(baseColumn)
  const columns = new Map<number, Column>()
  const addToColumn = (column: number, id: string) => {
    const size = nodeById.get(id) ? occupiedSize(nodeById.get(id)!) : sizes.get(id)
    if (!size) return
    const current = columns.get(column) ?? { width: 0, nodes: [] }
    current.width = Math.max(current.width, size.width)
    current.nodes.push(id)
    columns.set(column, current)
  }
  for (const node of ordered) addToColumn(physicalColumn(baseColumnByNode.get(node.id) ?? 0), node.id)
  for (const cluster of clusterInfo) {
    const groupColumn = physicalColumn(cluster.baseColumn)
    addToColumn(groupColumn, cluster.group.id)
    cluster.children.forEach((id, index) => addToColumn(groupColumn + 1 + (index % cluster.columns), id))
  }
  const xByColumn = new Map<number, number>()
  let nextX = xStart
  for (const column of [...columns.keys()].sort((left, right) => left - right)) {
    xByColumn.set(column, nextX)
    nextX += (columns.get(column)?.width ?? 0) + columnGap
  }

  const positions: Record<string, CardPosition> = {}
  const orderedByColumn = new Map<number, GraphStage[]>()
  for (const node of ordered) {
    const column = physicalColumn(baseColumnByNode.get(node.id) ?? 0)
    orderedByColumn.set(column, [...(orderedByColumn.get(column) ?? []), node])
  }
  for (const [column, members] of orderedByColumn) {
    const hasParallelRows = members.some((node) => node.runtime?.parallel_group_id)
    if (!hasParallelRows || members.length === 1) {
      members.forEach((node) => { positions[node.id] = { x: xByColumn.get(column) ?? xStart, y: yStart } })
      continue
    }
    const totalHeight = members.reduce((sum, node) => sum + occupiedSize(node).height, 0) + rowGap * Math.max(0, members.length - 1)
    let y = yStart - totalHeight / 2
    for (const node of members) {
      positions[node.id] = { x: xByColumn.get(column) ?? xStart, y }
      y += occupiedSize(node).height + rowGap
    }
  }

  // Expanded members use a stable ID order inherited from the persisted child
  // array. Their row positions use the real height of each row, so a tall
  // structure or distribution card cannot overlap its neighbour.
  for (const cluster of clusterInfo) {
    const groupPosition = positions[cluster.group.id]
    if (!groupPosition) continue
    const groupSize = sizes.get(cluster.group.id) ?? defaultCardSize(cluster.group)
    const rowHeights: number[] = []
    cluster.children.forEach((id, index) => {
      const row = Math.floor(index / cluster.columns)
      rowHeights[row] = Math.max(rowHeights[row] ?? 0, sizes.get(id)?.height ?? 0)
    })
    let rowTop = groupPosition.y + groupSize.height + clusterGap
    cluster.children.forEach((id, index) => {
      const row = Math.floor(index / cluster.columns)
      const column = physicalColumn(cluster.baseColumn) + 1 + (index % cluster.columns)
      positions[id] = { x: xByColumn.get(column) ?? (groupPosition.x + groupSize.width + columnGap), y: rowTop }
      if (index % cluster.columns === cluster.columns - 1) rowTop += (rowHeights[row] ?? 0) + rowGap
    })
  }

  // Summaries are audit evidence, not execution steps. They use a compact
  // lower rail whose row height follows the actual cards.
  let auditX = xStart
  let auditY = Math.max(auditStartY, ...Object.entries(positions).map(([id, position]) => position.y + (nodeById.get(id) ? occupiedSize(nodeById.get(id)!).height : sizes.get(id)?.height ?? 0) + 80))
  for (const node of summaries.sort((left, right) => left.id.localeCompare(right.id))) {
    const size = occupiedSize(node)
    positions[node.id] = { x: auditX, y: auditY }
    auditX += size.width + columnGap
    if (auditX > xStart + Math.max(1, options.availableWidth ?? 1) - size.width && auditX > xStart + size.width + columnGap) {
      auditX = xStart
      auditY += size.height + rowGap
    }
  }
  // A defensive fallback keeps every node addressable even if a future node
  // type is neither on the main spine nor on the audit rail.
  for (const node of nodeById.values()) positions[node.id] ??= { x: xStart, y: yStart }
  return positions
}
