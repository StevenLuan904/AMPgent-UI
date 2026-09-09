import type { RunListItem } from './types'

export interface RunIdentity {
  id: string
  temporal_workflow_id?: string | null
  temporal_run_id?: string | null
  workflow_id?: string | null
}

export interface AuthoritativeRunRound {
  key: string
  rootRunId: string
  runs: RunListItem[]
  basis: 'root_generation_run_id' | 'member_run_ids' | 'source_run_id'
  displayRound: string | null
}

function nonEmptyId(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function explicitMemberSet(run: RunListItem) {
  const members = run.member_run_ids?.filter(nonEmptyId) ?? []
  return members.length > 1 ? [...new Set([run.id, ...members])].sort() : null
}

/**
 * Groups sidebar runs only when the Observer returns an authoritative identity.
 * A run title, tool name, timestamp, candidate sequence, or evidence stage is
 * deliberately never a grouping key.
 */
export function groupRunsByAuthoritativeRound(runs: RunListItem[]): Array<RunListItem | AuthoritativeRunRound> {
  const explicitSets = runs
    .map((run) => explicitMemberSet(run))
    .filter((ids): ids is string[] => ids !== null)
  const memberSetByRunId = new Map<string, string>()
  const groupOwnerByKey = new Map<string, string>()
  const rootKeyByRunId = new Map<string, string>()
  for (const run of runs) {
    if (nonEmptyId(run.root_generation_run_id)) rootKeyByRunId.set(run.id, `root:${run.root_generation_run_id}`)
  }
  for (const ids of explicitSets) {
    const key = `members:${ids.join(',')}`
    const owner = runs.find((run) => explicitMemberSet(run)?.join(',') === ids.join(','))
    const groupKey = owner && rootKeyByRunId.get(owner.id) ? rootKeyByRunId.get(owner.id)! : key
    if (owner) groupOwnerByKey.set(groupKey, owner.id)
    for (const id of ids) memberSetByRunId.set(id, groupKey)
  }

  const groupForRun = (run: RunListItem) => {
    if (nonEmptyId(run.root_generation_run_id)) {
      return { key: `root:${run.root_generation_run_id}`, rootRunId: run.root_generation_run_id, basis: 'root_generation_run_id' as const }
    }
    const memberKey = memberSetByRunId.get(run.id)
    if (memberKey) {
      return { key: memberKey, rootRunId: memberKey.startsWith('root:') ? memberKey.slice('root:'.length) : groupOwnerByKey.get(memberKey) ?? run.id, basis: 'member_run_ids' as const }
    }
    if (run.run_role === 'evidence' && nonEmptyId(run.source_run_id)) {
      const sourceKey = rootKeyByRunId.get(run.source_run_id) ?? memberSetByRunId.get(run.source_run_id) ?? `source:${run.source_run_id}`
      return { key: sourceKey, rootRunId: rootKeyByRunId.get(run.source_run_id)?.slice('root:'.length) ?? groupOwnerByKey.get(sourceKey) ?? run.source_run_id, basis: 'source_run_id' as const }
    }
    return null
  }

  const grouped = new Map<string, { firstIndex: number; runs: RunListItem[]; rootRunId: string; basis: AuthoritativeRunRound['basis'] }>()
  runs.forEach((run, index) => {
    const identity = groupForRun(run)
    if (!identity) return
    const current = grouped.get(identity.key) ?? { firstIndex: index, runs: [], rootRunId: identity.rootRunId, basis: identity.basis }
    current.runs.push(run)
    grouped.set(identity.key, current)
  })

  const emitted = new Set<string>()
  const output: Array<RunListItem | AuthoritativeRunRound> = []
  runs.forEach((run, index) => {
    const identity = groupForRun(run)
    if (!identity || emitted.has(identity.key)) {
      if (!identity) output.push(run)
      return
    }
    const current = grouped.get(identity.key)
    if (!current || current.runs.length < 2 || current.firstIndex !== index) {
      if (current?.runs.length === 1) {
        output.push(run)
        emitted.add(identity.key)
      }
      return
    }
    const orderedRuns = [...current.runs].sort((left, right) => {
      if (left.id === current.rootRunId) return -1
      if (right.id === current.rootRunId) return 1
      return 0
    })
    output.push({
      key: identity.key,
      rootRunId: current.rootRunId,
      runs: orderedRuns,
      basis: current.basis,
      displayRound: orderedRuns.find((item) => nonEmptyId(item.display_round))?.display_round ?? null,
    })
    emitted.add(identity.key)
  })
  return output
}

/** Keep an explicit user selection stable across list refreshes. */
export function preserveSelectedRunOnListRefresh(currentId: string | null, listedRunIds: string[]) {
  return currentId ?? listedRunIds[0] ?? null
}

export function assertMatchingRunIdentity(expected: RunIdentity, actual: RunIdentity) {
  if (actual.id !== expected.id) {
    throw new Error('运行身份校验失败：PostgreSQL 运行编号不一致')
  }
  if (
    actual.temporal_workflow_id
    && actual.workflow_id
    && actual.temporal_workflow_id !== actual.workflow_id
  ) {
    throw new Error('运行身份校验失败：Temporal 工作流编号不一致')
  }
  for (const field of ['temporal_workflow_id', 'temporal_run_id'] as const) {
    if (expected[field] !== undefined && expected[field] !== actual[field]) {
      throw new Error(`运行身份校验失败：${field === 'temporal_workflow_id' ? 'Temporal 工作流编号' : 'Temporal 运行编号'}不一致`)
    }
  }
}
