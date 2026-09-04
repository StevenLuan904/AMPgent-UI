import { describe, expect, it } from 'vitest'
import { selectReadableRuntimeNodeIds } from './runtimeViewport'
import type { GraphStage, RuntimeNodeMeta } from './types'

const node = (id: string, node_type: RuntimeNodeMeta['node_type'], observed_at: string, status: GraphStage['status'] = 'completed', expanded = false) => ({
  id,
  status,
  runtime: { node_type, observed_at, expanded },
})

describe('readable runtime viewport selection', () => {
  it('keeps a small graph complete', () => {
    const nodes = [node('event-1', 'event_group', '2026-09-04T00:00:01Z'), node('tool-1', 'tool_group', '2026-09-04T00:00:02Z'), node('generation-1', 'generation', '2026-09-04T00:00:03Z')]
    expect(selectReadableRuntimeNodeIds(nodes)).toEqual(['event-1', 'tool-1', 'generation-1'])
  })

  it('preserves context across dense multi-lane runs without growing the fit set', () => {
    const nodes = [
      ...Array.from({ length: 8 }, (_, index) => node(`event-${index + 1}`, 'event_group', `2026-09-04T00:00:${String(index + 1).padStart(2, '0')}Z`)),
      ...Array.from({ length: 12 }, (_, index) => node(`tool-${index + 1}`, 'batch_group', `2026-09-04T00:01:${String(index + 1).padStart(2, '0')}Z`)),
      ...Array.from({ length: 4 }, (_, index) => node(`generation-${index + 1}`, 'generation', `2026-09-04T00:02:${String(index + 1).padStart(2, '0')}Z`)),
    ]
    const selected = selectReadableRuntimeNodeIds(nodes)
    expect(selected).toHaveLength(9)
    expect(selected.filter((id) => id.startsWith('event-'))).toHaveLength(3)
    expect(selected.filter((id) => id.startsWith('tool-'))).toHaveLength(4)
    expect(selected.filter((id) => id.startsWith('generation-'))).toHaveLength(2)
    expect(selected).toEqual(expect.arrayContaining(['event-1', 'event-2', 'event-3', 'tool-1', 'tool-2', 'tool-3', 'tool-4', 'generation-1', 'generation-2']))
  })

  it('keeps an expanded aggregate and nearby members in the readable window', () => {
    const nodes = [
      node('event-1', 'event_group', '2026-09-04T00:00:01Z'),
      node('batch-1', 'batch_group', '2026-09-04T00:00:02Z', 'running', true),
      node('member-1', 'tool_call', '2026-09-04T00:00:03Z'),
      node('member-2', 'tool_call', '2026-09-04T00:00:04Z'),
      node('generation-1', 'generation', '2026-09-04T00:00:05Z'),
    ]
    const selected = selectReadableRuntimeNodeIds(nodes, 6)
    expect(selected).toEqual(expect.arrayContaining(['batch-1', 'member-1', 'member-2', 'event-1', 'generation-1']))
  })

  it('prefers a spatially contiguous lane prefix over a late candidate record', () => {
    const nodes = [
      ...Array.from({ length: 3 }, (_, index) => node(`event-${index + 1}`, 'event_group', `2026-09-04T00:00:0${index + 1}Z`)),
      ...Array.from({ length: 4 }, (_, index) => node(`tool-${index + 1}`, 'batch_group', `2026-09-04T00:00:1${index + 1}Z`)),
      node('candidate-early', 'generation', '', 'completed'),
      node('generation-summary', 'generation', '', 'completed'),
      node('candidate-late', 'generation', '', 'completed'),
    ]
    const positions = {
      'event-1': { x: 155, y: 150 }, 'event-2': { x: 470, y: 150 }, 'event-3': { x: 785, y: 150 },
      'tool-1': { x: 155, y: 400 }, 'tool-2': { x: 470, y: 400 }, 'tool-3': { x: 785, y: 400 }, 'tool-4': { x: 1100, y: 400 },
      'candidate-early': { x: 155, y: 720 },
      'generation-summary': { x: 470, y: 720 },
      'candidate-late': { x: 1730, y: 720 },
    }
    const selected = selectReadableRuntimeNodeIds(nodes, positions, 6)
    expect(selected).toEqual(expect.arrayContaining(['candidate-early', 'generation-summary']))
    expect(selected).not.toContain('candidate-late')
  })
})
