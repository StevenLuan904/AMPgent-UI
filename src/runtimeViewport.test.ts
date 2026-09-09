import { describe, expect, it } from 'vitest'
import { compactReadableRuntimePositions, expandedClusterLayoutRevision, expandedFocusNodeIds, selectReadableRuntimeNodeIds, shouldRefocusExpandedCluster } from './runtimeViewport'
import type { GraphStage, RuntimeNodeMeta } from './types'

const node = (id: string, node_type: RuntimeNodeMeta['node_type'], observed_at: string, status: GraphStage['status'] = 'completed', expanded = false) => ({
  id,
  status,
  runtime: { node_type, observed_at, expanded },
})

describe('readable runtime viewport selection', () => {
  it('creates a new focus revision when members are hydrated or measured differently', () => {
    const initial = expandedClusterLayoutRevision('tool-summary-group', [
      { id: 'tool-a', position: { x: 520, y: 110 }, width: 280, height: 156 },
    ])
    const hydrated = expandedClusterLayoutRevision('tool-summary-group', [
      { id: 'tool-a', position: { x: 520, y: 110 }, width: 280, height: 156 },
      { id: 'tool-b', position: { x: 850, y: 110 }, width: 280, height: 156 },
    ])
    const remeasured = expandedClusterLayoutRevision('tool-summary-group', [
      { id: 'tool-a', position: { x: 520, y: 110 }, width: 280, height: 214 },
      { id: 'tool-b', position: { x: 850, y: 110 }, width: 280, height: 156 },
    ])
    expect(hydrated).not.toBe(initial)
    expect(remeasured).not.toBe(hydrated)
    expect(shouldRefocusExpandedCluster(initial, hydrated, false)).toBe(true)
    expect(shouldRefocusExpandedCluster(hydrated, remeasured, false)).toBe(true)
  })

  it('does not reclaim the viewport after a scientist pans or zooms', () => {
    const previous = expandedClusterLayoutRevision('tool-summary-group', [
      { id: 'tool-a', position: { x: 520, y: 110 }, width: 280, height: 156 },
    ])
    const next = expandedClusterLayoutRevision('tool-summary-group', [
      { id: 'tool-a', position: { x: 520, y: 110 }, width: 280, height: 214 },
    ])
    expect(shouldRefocusExpandedCluster(previous, next, true)).toBe(false)
    expect(shouldRefocusExpandedCluster(previous, previous, false)).toBe(false)
  })

  it('removes columns occupied only by hidden records', () => {
    const compact = compactReadableRuntimePositions(
      ['decision', 'recovery', 'population'],
      {
        decision: { x: 190, y: 220 },
        hidden: { x: 505, y: 220 },
        recovery: { x: 1765, y: 220 },
        population: { x: 3340, y: 220 },
      },
    )
    expect(compact).toEqual({
      decision: { x: 190, y: 220 },
      recovery: { x: 520, y: 220 },
      population: { x: 850, y: 220 },
    })
  })

  it('preserves a shared-column batch as an evenly spaced vertical cluster', () => {
    const compact = compactReadableRuntimePositions(
      ['batch', 'member-a', 'member-b', 'member-c'],
      {
        batch: { x: 190, y: 220 },
        'member-a': { x: 505, y: 410 },
        'member-b': { x: 505, y: 600 },
        'member-c': { x: 505, y: 790 },
      },
    )
    expect(compact.batch).toEqual({ x: 190, y: 220 })
    expect(compact['member-a'].x).toBe(520)
    expect(compact['member-b'].y - compact['member-a'].y).toBe(190)
    expect(compact['member-c'].y - compact['member-b'].y).toBe(190)
  })

  it('keeps a small graph complete', () => {
    const nodes = [node('event-1', 'event_group', '2026-09-04T00:00:01Z'), node('tool-1', 'tool_group', '2026-09-04T00:00:02Z'), node('generation-1', 'generation', '2026-09-04T00:00:03Z')]
    expect(selectReadableRuntimeNodeIds(nodes)).toEqual(['event-1', 'tool-1', 'generation-1'])
  })

  it('focuses an expanded cluster with only its nearest workflow context', () => {
    const positions = {
      decision: { x: 190, y: 220 },
      group: { x: 520, y: 220 },
      childA: { x: 835, y: 110 },
      childB: { x: 835, y: 300 },
      recovery: { x: 1150, y: 220 },
      population: { x: 1465, y: 220 },
    }
    expect(expandedFocusNodeIds(
      ['decision', 'group', 'childA', 'childB', 'recovery', 'population'],
      positions,
      new Set(['group', 'childA', 'childB']),
    )).toEqual(expect.arrayContaining(['decision', 'group', 'childA', 'childB', 'recovery']))
    expect(expandedFocusNodeIds(
      ['decision', 'group', 'childA', 'childB', 'recovery', 'population'],
      positions,
      new Set(['group', 'childA', 'childB']),
    )).not.toContain('population')
  })

  it('preserves context across dense multi-lane runs without growing the fit set', () => {
    const nodes = [
      ...Array.from({ length: 8 }, (_, index) => node(`event-${index + 1}`, 'event_group', `2026-09-04T00:00:${String(index + 1).padStart(2, '0')}Z`)),
      ...Array.from({ length: 12 }, (_, index) => node(`tool-${index + 1}`, 'batch_group', `2026-09-04T00:01:${String(index + 1).padStart(2, '0')}Z`)),
      ...Array.from({ length: 4 }, (_, index) => node(`generation-${index + 1}`, 'generation', `2026-09-04T00:02:${String(index + 1).padStart(2, '0')}Z`)),
      node('summary-1', 'tool_summary_group', '', 'pending'),
    ]
    const selected = selectReadableRuntimeNodeIds(nodes)
    expect(selected).toHaveLength(7)
    expect(selected.filter((id) => id.startsWith('event-'))).toHaveLength(1)
    expect(selected.filter((id) => id.startsWith('tool-'))).toHaveLength(2)
    expect(selected.filter((id) => id.startsWith('generation-'))).toHaveLength(4)
    expect(selected).toEqual(expect.arrayContaining(['event-8', 'tool-12', 'generation-1', 'generation-2', 'generation-3', 'generation-4']))
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

  it('keeps a persisted activity retry visible in a dense recent window', () => {
    const retry = node('retry-group', 'event_group', '2026-09-04T00:00:02Z', 'stopped')
    const nodes = [
      node('decision', 'event_group', '2026-09-04T00:00:01Z'),
      { ...retry, runtime: { ...retry.runtime, activity_retry_count: 1 } },
      ...Array.from({ length: 7 }, (_, index) => node(`tool-${index}`, 'tool_group', `2026-09-04T00:00:${String(index + 3).padStart(2, '0')}Z`)),
      node('population', 'population_summary', ''),
      node('generation', 'candidate_group', ''),
    ]
    expect(selectReadableRuntimeNodeIds(nodes, 6)).toContain('retry-group')
  })

  it('keeps an operational replay bundle out of the folded scientific spine', () => {
    const nodes = [
      node('decision', 'event_group', '2026-09-04T00:00:01Z'),
      { ...node('replay', 'tool_call', '2026-09-04T00:00:02Z'), runtime: { node_type: 'tool_call' as const, observed_at: '2026-09-04T00:00:02Z', expanded: false, raw_label: 'autoresearch-replay-bundle' } },
      node('metric', 'tool_group', '2026-09-04T00:00:03Z'),
      node('population', 'population_summary', ''),
      node('generation', 'candidate_group', ''),
    ]
    expect(selectReadableRuntimeNodeIds(nodes, 5)).not.toContain('replay')
    expect(selectReadableRuntimeNodeIds(nodes, 5)).toEqual(expect.arrayContaining(['decision', 'metric', 'population', 'generation']))
  })

  it('also filters a replay-bundle aggregate without hiding metric evidence', () => {
    const nodes = [
      node('decision', 'event_group', '2026-09-04T00:00:01Z'),
      { ...node('replay-group', 'tool_group', '2026-09-04T00:00:02Z'), runtime: { node_type: 'tool_group' as const, observed_at: '2026-09-04T00:00:02Z', expanded: false, raw_label: 'autoresearch-replay-bundle', distribution_key: undefined } },
      { ...node('mic', 'tool_group', '2026-09-04T00:00:03Z'), runtime: { ...node('mic', 'tool_group', '2026-09-04T00:00:03Z').runtime, distribution_key: 'mic' } },
      node('population', 'population_summary', ''),
      node('generation', 'candidate_group', ''),
    ]
    expect(selectReadableRuntimeNodeIds(nodes, 5)).not.toContain('replay-group')
    expect(selectReadableRuntimeNodeIds(nodes, 5)).toContain('mic')
  })

  it('keeps a real metric distribution node visible without promoting the audit summary', () => {
    const nodes = [
      node('decision', 'event_group', '2026-09-04T00:00:01Z'),
      { ...node('mic', 'tool_group', '2026-09-04T00:00:02Z'), runtime: { ...node('mic', 'tool_group', '2026-09-04T00:00:02Z').runtime, distribution_key: 'mic' } },
      node('summary', 'tool_summary_group', '', 'pending'),
      node('population', 'population_summary', ''),
      node('generation', 'candidate_group', ''),
    ]
    const selected = selectReadableRuntimeNodeIds(nodes, 5)
    expect(selected).toContain('mic')
    expect(selected).not.toContain('summary')
  })

  it('keeps a population summary and a candidate preview group discoverable together', () => {
    const nodes = [
      node('population-summary', 'population_summary', ''),
      node('generation-1', 'candidate_group', ''),
      node('candidate-1', 'candidate_preview', ''),
      node('event-1', 'event_group', '2026-09-04T00:00:01Z'),
      node('tool-1', 'tool_group', '2026-09-04T00:00:02Z'),
    ]
    const selected = selectReadableRuntimeNodeIds(nodes)
    expect(selected).toEqual(expect.arrayContaining(['population-summary', 'generation-1']))
  })

  it('keeps explicit structure evidence discoverable with the result context', () => {
    const nodes = [
      node('structure-evidence:boltz', 'structure_evidence', ''),
      node('population-summary', 'population_summary', ''),
      node('generation-1', 'candidate_group', ''),
      node('event-1', 'event_group', '2026-09-04T00:00:01Z'),
      node('tool-1', 'tool_group', '2026-09-04T00:00:02Z'),
    ]
    const selected = selectReadableRuntimeNodeIds(nodes)
    expect(selected).toEqual(expect.arrayContaining(['structure-evidence:boltz', 'population-summary', 'generation-1']))
  })

  it('includes every member of a small expanded candidate group when requested', () => {
    const nodes = [
      node('event-1', 'event_group', '2026-09-04T00:00:01Z'),
      node('tool-1', 'tool_group', '2026-09-04T00:00:02Z'),
      node('population-summary', 'population_summary', ''),
      node('generation-1', 'candidate_group', '', 'completed', true),
      node('candidate-1', 'candidate_preview', ''),
      node('candidate-2', 'candidate_preview', ''),
      node('candidate-3', 'candidate_preview', ''),
    ]
    expect(selectReadableRuntimeNodeIds(nodes, 10)).toEqual(expect.arrayContaining([
      'generation-1', 'candidate-1', 'candidate-2', 'candidate-3', 'population-summary',
    ]))
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
    expect(selected).toContain('candidate-late')
    expect(selected).not.toContain('candidate-early')
  })
})
