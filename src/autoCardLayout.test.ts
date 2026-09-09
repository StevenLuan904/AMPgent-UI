import { describe, expect, it } from 'vitest'
import { autoCardLayout, type CardMeasurements } from './autoCardLayout'
import type { GraphEdgeDetail, GraphStage } from './types'

function node(id: string, observedAt: string | null, overrides: Partial<GraphStage> = {}): GraphStage {
  return {
    id,
    label: id,
    kind: 'tool',
    group: 'observed',
    status: 'completed',
    current: 1,
    total: 1,
    provenance: 'derived',
    insight: { grade: 'neutral', verdict: '已完成', reason: '', facts: [], source: 'observer_summary' },
    runtime: { node_type: 'tool_call', source_id: id, observed_at: observedAt, raw_label: id },
    ...overrides,
  }
}

const edge = (source: string, target: string, relation_kind: GraphEdgeDetail['relation_kind'] = 'dependency'): GraphEdgeDetail => ({
  source, target, relation_kind, label: null, rationale: '', provenance: 'database',
})

function rectsDoNotOverlap(nodes: GraphStage[], positions: Record<string, { x: number; y: number }>, measurements: CardMeasurements) {
  const rectangles = nodes.map((item) => {
    const size = measurements[item.id] ?? { width: 280, height: 156 }
    const position = positions[item.id]
    return { left: position.x, top: position.y, right: position.x + (size.width ?? 280), bottom: position.y + (size.height ?? 156) }
  })
  for (let left = 0; left < rectangles.length; left += 1) for (let right = left + 1; right < rectangles.length; right += 1) {
    const a = rectangles[left]
    const b = rectangles[right]
    const overlap = a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
    expect(overlap, `${nodes[left].id} overlaps ${nodes[right].id}`).toBe(false)
  }
}

describe('autoCardLayout', () => {
  it('uses measured widths and preserves left-to-right causal order', () => {
    const nodes = [node('downstream', '2026-09-04T00:00:01Z'), node('upstream', '2026-09-04T00:00:10Z')]
    const positions = autoCardLayout(nodes, [edge('upstream', 'downstream')], {
      upstream: { width: 420, height: 180 }, downstream: { width: 180, height: 156 },
    })
    expect(positions.downstream.x).toBeGreaterThan(positions.upstream.x)
    expect(positions.downstream.x).toBeGreaterThanOrEqual(positions.upstream.x + 420 + 35)
  })

  it('wraps 6, 12, and 50 expanded members without overlap and lets the tail advance', () => {
    for (const count of [6, 12, 50]) {
      const members = Array.from({ length: count }, (_, index) => node(`member-${index}`, `2026-09-04T00:00:${String(index).padStart(2, '0')}Z`))
      const group = node(`batch-${count}`, '2026-09-04T00:00:00Z', { runtime: { node_type: 'batch_group', source_id: `batch-${count}`, observed_at: '2026-09-04T00:00:00Z', expanded: true, child_ids: members.map((item) => item.id) } })
      const tail = node(`tail-${count}`, '2026-09-04T01:00:00Z')
      const all = [group, ...members, tail]
      const measurements = Object.fromEntries(all.map((item) => [item.id, { width: item.id.startsWith('member') && Number(item.id.slice(7)) % 5 === 0 ? 330 : 280, height: 156 }]))
      const positions = autoCardLayout(all, [], measurements, { availableWidth: 1610, expandedGroups: new Set([group.id]) })
      expect(new Set(members.map((item) => positions[item.id].y)).size).toBeGreaterThan(1)
      expect(positions[tail.id].x).toBeGreaterThan(Math.max(...members.map((item) => positions[item.id].x)))
      rectsDoNotOverlap(all, positions, measurements)
    }
  })

  it('reserves folded stack extents and stays deterministic for shuffled input', () => {
    const first = node('first-batch', '2026-09-04T00:00:01Z', { runtime: { node_type: 'batch_group', source_id: 'first', observed_at: '2026-09-04T00:00:01Z', expanded: false, child_ids: ['a'] } })
    const second = node('second-batch', '2026-09-04T00:00:02Z', { runtime: { node_type: 'batch_group', source_id: 'second', observed_at: '2026-09-04T00:00:02Z', expanded: false, child_ids: ['b'] } })
    const measurements = { 'first-batch': { width: 280, height: 214 }, 'second-batch': { width: 280, height: 214 } }
    const firstLayout = autoCardLayout([first, second], [], measurements)
    const secondLayout = autoCardLayout([second, first], [], measurements)
    expect(secondLayout).toEqual(firstLayout)
    expect(firstLayout['second-batch'].x).toBeGreaterThanOrEqual(firstLayout['first-batch'].x + 280 + 24 + 35)
  })

  it('terminates on dependency cycles and remains stable with structure heights', () => {
    const nodes = [node('a', '2026-09-04T00:00:01Z'), node('b', '2026-09-04T00:00:02Z'), node('structure', null, { kind: 'structure', runtime: { node_type: 'structure_evidence', source_id: 'structure', observed_at: null, has_viewer: true } })]
    const positions = autoCardLayout(nodes, [edge('a', 'b'), edge('b', 'a')], {
      a: { width: 280, height: 156 }, b: { width: 280, height: 156 }, structure: { width: 280, height: 420 },
    })
    expect(Object.keys(positions)).toEqual(expect.arrayContaining(['a', 'b', 'structure']))
    rectsDoNotOverlap(nodes, positions, { a: { width: 280, height: 156 }, b: { width: 280, height: 156 }, structure: { width: 280, height: 420 } })
  })
})

