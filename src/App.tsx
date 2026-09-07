import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  useNodesState,
  type Edge,
  type EdgeMouseHandler,
  type NodeMouseHandler,
  type ReactFlowInstance,
} from '@xyflow/react'
import {
  Activity,
  ArrowLeft,
  Atom,
  Box,
  BrainCircuit,
  ChartNoAxesCombined,
  ChevronRight,
  Clock3,
  CircleDot,
  Database,
  Ellipsis,
  FileJson2,
  Fingerprint,
  FlaskConical,
  GitBranch,
  Layers3,
  Route,
  RefreshCw,
  ScanSearch,
  Settings2,
  ShieldCheck,
  X,
} from 'lucide-react'
import { AnalysisDashboard } from './analysis/AnalysisDashboard'
import { loadAnalysisSnapshot, type AnalysisSnapshot } from './analysis/dataKernel'
import { EvidenceDashboard } from './analysis/EvidenceDashboard'
import { MoleculeViewer } from './MoleculeViewer'
import { candidateGenerationLabel, formatGenerationPopulation } from './generationPopulation'
import { formatQualityGateRule, qualityGateCountSteps, qualityGateStatusLabel } from './generationQualityGate'
import {
  distributionForStage,
  ResultDistribution,
  type ResultDistributionData,
} from './ResultDistribution'
import { LaneLabel, WorkflowNode, type LaneNode, type StageNode } from './WorkflowNode'
import { assertMatchingRunIdentity, preserveSelectedRunOnListRefresh, type RunIdentity } from './runIdentity'
import { formatRunTitle } from './runPresentation'
import { buildRuntimeGraph, candidatePreviewCountLabel, candidatePreviewDenominator, displayObservedEventName, displayToolName, nextExpandedRuntimeGroups, runtimeEventStatus, type RuntimeGraphModel } from './runtimeGraph'
import { loadObserverEventHistory, mergeObserverDetailEventHistory, observerEventPageMax, shouldFetchOlderObserverEvents } from './observerEvents'
import { mergeNodeDetailCalls, nodeCallsWindowLabel, observerCallPageLimit, observerNodeCallsUrl } from './observerCalls'
import { compactReadableRuntimePositions, expandedClusterLayoutRevision, selectReadableRuntimeNodeIds, shouldRefocusExpandedCluster } from './runtimeViewport'
import { nodeDetailCacheTtlMs, observerDetailFailureMessage, observerDetailRequestAction, observerIdlePrefetchDelayMs, observerInitialPrefetchCount, observerInitialPrefetchStages, observerListTimeoutMs, observerInFlightStageIds, observerMergePrefetchQueue, observerNextPrefetchStage, observerNodeDetailCacheKey, observerNodeDetailTimeoutMs, observerPendingPrefetchCount, observerPollingIntervalMs, observerPrefetchQueueMatches, observerPrefetchInFlightKey, observerPrefetchRefreshExpired, observerPrefetchStageOrder, observerRequeuePrefetchStage, observerResponseIsStale, observerRunDetailCacheKey, observerRunDetailTimeoutMs, observerRunListCacheKey, observerSnapshotCacheMaxBytes, observerSnapshotCacheTtlMs, observerSnapshotCacheVersion, observerVisibilityRefreshNeeded, type ObserverPrefetchQueue } from './observerPolling'

const readableViewportMinZoom = 0.68
// A focused cluster may legitimately be wider than the readable spine. Keep
// this lower bound local to the explicit focus action so the default view
// remains readable while every revealed member and its frame can be seen.
const expandedClusterMinZoom = 0.9

type RuntimeClusterFrame = { id: string; label: string; left: number; top: number; width: number; height: number }

function runtimeChildNodeIds(nodes: GraphStage[], group: GraphStage) {
  const nodeIds = new Set(nodes.map((node) => node.id))
  const rawIds = [...(group.runtime?.child_ids ?? []), ...(group.runtime?.event_ids ?? [])]
  const resolved = rawIds.flatMap((rawId) => {
    const candidates = rawId.startsWith('event:') || rawId.startsWith('call:') || rawId.startsWith('candidate:') || rawId.startsWith('tool-summary:')
      ? [rawId]
      : [`call:${rawId}`, `event:${rawId}`, `candidate:${rawId}`, `tool-summary:${rawId}`, `tool-summary:${encodeURIComponent(rawId)}`, rawId]
    const match = candidates.find((candidate) => nodeIds.has(candidate))
    return match ? [match] : []
  })
  return [...new Set([group.id, ...resolved])]
}
import { schedulerHealthDescription, schedulerHealthPresentation } from './schedulerHealth'
import type {
  CandidatePreview,
  GenerationQualityGate,
  GraphEdgeDetail,
  GraphStage,
  MetricSummary,
  NodeDetail,
  RunDetail,
  RunListItem,
  RunListResponse,
  TimelineEvent,
  ToolAttempt,
  ViewerArtifact,
} from './types'

const nodeTypes = Object.freeze({ stage: WorkflowNode, lane: LaneLabel })
const runtimeExpandableNodeTypes = new Set(['tool_group', 'event_group', 'batch_group', 'tool_summary_group', 'candidate_group'])
const connectionStorageKey = 'ampgent.data-service.base.v1'
const selectedRunStorageKey = 'ampgent.observer.selected-run.v1'
const defaultApiBase = import.meta.env.VITE_API_BASE ?? ''

function normalizeApiBase(value: string) {
  return value.trim().replace(/\/+$/, '')
}

function readApiBase() {
  const stored = window.localStorage.getItem(connectionStorageKey)
  return stored === null ? defaultApiBase : stored
}

function getConfiguredApiBase() {
  return readApiBase()
}
const statusText: Record<string, string> = {
  created: '已创建', submitted: '已提交', running: '运行中', succeeded: '已完成', failed: '运行异常终止', cancelled: '已取消',
  completed: '已完成', stopped: '已停止', pending: '待写入',
}

function readableEventType(type: string, payload?: unknown) {
  return displayObservedEventName(type, payload)
}

const metricLabels: Record<string, string> = {
  llamp_log10_mic_um: '最小抑菌浓度预测',
  amp_read_log10_mic_um: '交叉模型最小抑菌浓度预测',
  macrel_amp_probability: '抗菌概率',
  macrel_hemolysis_label: '溶血风险类别',
  macrel_hemolysis_probability: '溶血概率',
  toxinpred3_hybrid_score: '毒性综合评分',
  toxinpred3_label: '毒性预测类别',
  hydrophobic_moment_eisenberg: '疏水矩',
  hydrophobic_ratio_modlamp: '疏水残基比例',
  maximum_hydrophobic_run: '最大连续疏水残基数',
  net_charge_ph7_4: '酸碱度7.4下净电荷',
}

const professionalTermHelp: Record<string, string> = {
  amp_designer: 'AMP Designer用于基于模型生成抗菌短肽候选序列。',
  ampgan: 'AMPGAN v2是用于生成抗菌肽候选的对抗生成模型。',
  hydramp: 'HydrAMP用于生成并优化抗菌肽候选序列。',
  amp_read: 'AMP read用于交叉复核候选短肽的抗菌活性预测。',
  'v38-metric-mic_potency': 'MIC活性预测用于估计最小抑菌浓度。',
  'v38-metric-mic_potency_amp_read': 'AMP read用于交叉复核最小抑菌浓度。',
  'v38-metric-hemolysis_risk': '溶血风险评估用于观察红细胞相容性信号。',
  boltz: 'Boltz 2用于预测蛋白质与短肽复合物的三维构象。',
  rosetta: 'Rosetta用于采样并评估蛋白质与短肽的界面构象。',
}

function formatTime(value: string | null) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function readableDataError(cause: unknown, fallback: string) {
  if (cause instanceof Error && /^(轮次|数据库|无法|数据服务)/.test(cause.message)) return cause.message
  return fallback
}

const nodeDetailCache = new Map<string, { detail: NodeDetail; fetchedAt: number }>()

type ObserverFetchResult<T> = { payload: T; cacheState: string | null }

async function fetchJsonWithTimeout<T>(url: string, timeoutMs: number): Promise<ObserverFetchResult<T>> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(url, { signal: controller.signal })
    if (!response.ok) throw new Error(`数据服务响应 ${response.status}`)
    return { payload: await response.json() as T, cacheState: response.headers.get('x-ampgent-cache') }
  } catch (cause) {
    if (controller.signal.aborted) throw new Error(`数据服务超时（${Math.round(timeoutMs / 1000)} 秒）`)
    throw cause
  } finally {
    window.clearTimeout(timeout)
  }
}

type ObserverCacheEnvelope<T> = { version: number; apiBase: string; fetchedAt: number; payload: T }

function readObserverCache<T>(key: string, apiBase: string): { payload: T; fetchedAt: number } | null {
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return null
    const cached = JSON.parse(raw) as Partial<ObserverCacheEnvelope<T>>
    if (cached.version !== observerSnapshotCacheVersion || cached.apiBase !== normalizeApiBase(apiBase) || typeof cached.fetchedAt !== 'number' || cached.payload === undefined) return null
    if (Date.now() - cached.fetchedAt < 0 || Date.now() - cached.fetchedAt > observerSnapshotCacheTtlMs) return null
    return { payload: cached.payload as T, fetchedAt: cached.fetchedAt }
  } catch {
    return null
  }
}

function writeObserverCache<T>(key: string, apiBase: string, payload: T) {
  const envelope: ObserverCacheEnvelope<T> = { version: observerSnapshotCacheVersion, apiBase: normalizeApiBase(apiBase), fetchedAt: Date.now(), payload }
  try {
    const encoded = JSON.stringify(envelope)
    if (encoded.length > observerSnapshotCacheMaxBytes) return
    window.localStorage.setItem(key, encoded)
  } catch {
    // Cache is an optimization only; private mode or quota limits must not
    // affect the authoritative request path.
  }
}

function useRunData(enabled: boolean, apiBase: string) {
  const requestedRunId = new URLSearchParams(window.location.search).get('run')
  const initialSelectedId = requestedRunId ?? window.localStorage.getItem(selectedRunStorageKey)
  const initialCachedDetail = initialSelectedId ? readObserverCache<RunDetail>(observerRunDetailCacheKey(apiBase, initialSelectedId), apiBase) : null
  const [runs, setRuns] = useState<RunListItem[]>(() => readObserverCache<RunListResponse>(observerRunListCacheKey(apiBase), apiBase)?.payload.runs ?? [])
  const [selectedId, setSelectedIdState] = useState<string | null>(initialSelectedId)
  const [detail, setDetail] = useState<RunDetail | null>(initialCachedDetail?.payload ?? null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(!initialCachedDetail)
  const [refreshing, setRefreshing] = useState(false)
  const [syncingStale, setSyncingStale] = useState(Boolean(initialCachedDetail))
  const [detailSyncError, setDetailSyncError] = useState<string | null>(null)
  const [lastSuccessfulDetailAt, setLastSuccessfulDetailAt] = useState<string | null>(initialCachedDetail?.payload.updated_at ?? null)
  const [eventHistoryLoading, setEventHistoryLoading] = useState(false)
  const [nodeDetails, setNodeDetails] = useState<Record<string, NodeDetail>>({})
  const [nodeDetailFetch, setNodeDetailFetch] = useState({ requested: 0, loaded: 0, failed: 0, deferred: 0 })
  const runsInFlight = useRef(false)
  const detailInFlight = useRef(false)
  const detailInFlightRunId = useRef<string | null>(null)
  const eventHistoryInFlight = useRef(false)
  const pendingDetailRunId = useRef<string | null>(null)
  const wasDocumentHidden = useRef(document.hidden)
  const detailEpoch = useRef(0)
  const previousSelectedId = useRef(selectedId)
  const previousApiBase = useRef(apiBase)
  const selectedIdRef = useRef(selectedId)
  const detailRunIdRef = useRef<string | null>(initialCachedDetail ? initialSelectedId : null)
  const detailRef = useRef<RunDetail | null>(initialCachedDetail?.payload ?? null)
  detailRef.current = detail
  const lastSuccessfulDetailAtRef = useRef<string | null>(initialCachedDetail?.payload.updated_at ?? null)
  lastSuccessfulDetailAtRef.current = lastSuccessfulDetailAt
  const nodeFetchInFlight = useRef(new Set<string>())
  const loadedStageKeys = useRef(new Set<string>())
  const currentStageIds = useRef<string[]>([])
  const prefetchQueue = useRef<ObserverPrefetchQueue | null>(null)
  const prefetchInFlightStageIds = useRef(new Set<string>())
  const idlePrefetchTimer = useRef<number | null>(null)
  const alignedStructureCounts = useRef<Record<string, number>>({})
  const runIdentities = useRef<Record<string, RunIdentity>>({})
  const runsStale = useRef(runs.length > 0)
  const detailStale = useRef(Boolean(initialCachedDetail))

  const reportBackgroundSyncError = useCallback((cause: unknown) => {
    setError(readableDataError(cause, '同步延迟'))
    runsStale.current = true
    detailStale.current = true
    setSyncingStale(true)
    setLoading(false)
  }, [])

  const loadRuns = useCallback(async () => {
    if (runsInFlight.current) return
    runsInFlight.current = true
    try {
      const response = await fetchJsonWithTimeout<RunListResponse>(`${apiBase}/v1/observer/runs?limit=12`, observerListTimeoutMs)
      const payload = response.payload
      const staleResponse = observerResponseIsStale(response.cacheState)
      runsStale.current = staleResponse
      setSyncingStale(runsStale.current || detailStale.current)
      runIdentities.current = Object.fromEntries(payload.runs.map((run) => [run.id, {
        id: run.id,
        temporal_workflow_id: run.temporal_workflow_id,
        temporal_run_id: run.temporal_run_id,
      }]))
      setRuns(payload.runs.map((run) => ({
        ...run,
        structure_record_count: Math.max(run.structure_record_count, alignedStructureCounts.current[run.id] ?? 0),
      })))
      if (!observerResponseIsStale(response.cacheState)) writeObserverCache(observerRunListCacheKey(apiBase), apiBase, payload)
      setSelectedIdState((current) => {
        // A valid deep link may point to an older run outside the recent-list page.
        // Keep it and let the authoritative detail endpoint validate it; an invalid
        // link then fails honestly instead of silently showing another run.
        const next = preserveSelectedRunOnListRefresh(current, payload.runs.map((run) => run.id))
        if (next) window.localStorage.setItem(selectedRunStorageKey, next)
        return next
      })
    } finally {
      runsInFlight.current = false
    }
  }, [apiBase])

  const loadNodeDetail = useCallback(async (runId: string, stageId: string, epoch: number, options: { refreshExpired?: boolean } = {}) => {
    const cacheKey = observerNodeDetailCacheKey(apiBase, runId, stageId)
    const cached = nodeDetailCache.get(cacheKey)
    const cacheIsFresh = cached !== undefined && Date.now() - cached.fetchedAt < nodeDetailCacheTtlMs
    if (cached) {
      if (selectedIdRef.current === runId && epoch === detailEpoch.current) {
        setNodeDetails((current) => current[stageId] === cached.detail ? current : { ...current, [stageId]: cached.detail })
      }
      if (cacheIsFresh || options.refreshExpired === false) return true
    }
    if (nodeFetchInFlight.current.has(cacheKey)) return Boolean(cached)
    nodeFetchInFlight.current.add(cacheKey)
    try {
      const detailUrl = `${apiBase}/v1/observer/runs/${runId}/nodes/${encodeURIComponent(stageId)}`
      const response = await fetchJsonWithTimeout<NodeDetail>(observerNodeCallsUrl(detailUrl, { limit: observerCallPageLimit }), observerNodeDetailTimeoutMs)
      const nodeDetail = mergeNodeDetailCalls(cached?.detail, response.payload, 'head')
      nodeDetailCache.set(cacheKey, { detail: nodeDetail, fetchedAt: Date.now() })
      if (selectedIdRef.current === runId && epoch === detailEpoch.current) {
        setNodeDetails((current) => ({ ...current, [stageId]: nodeDetail }))
        loadedStageKeys.current.add(cacheKey)
        const loaded = currentStageIds.current.filter((id) => loadedStageKeys.current.has(observerNodeDetailCacheKey(apiBase, runId, id))).length
        setNodeDetailFetch((current) => ({ ...current, loaded: Math.min(current.requested, loaded) }))
      }
      return true
    } catch {
      if (selectedIdRef.current === runId && epoch === detailEpoch.current && !cached) {
        setNodeDetailFetch((current) => ({ ...current, failed: current.failed + 1 }))
      }
      return false
    } finally {
      nodeFetchInFlight.current.delete(cacheKey)
    }
  }, [apiBase])

  const loadOlderNodeCalls = useCallback(async (stageId: string) => {
    const runId = detailRunIdRef.current
    const epoch = detailEpoch.current
    if (!runId) return false
    const cacheKey = observerNodeDetailCacheKey(apiBase, runId, stageId)
    const cached = nodeDetailCache.get(cacheKey)
    const callsWindow = cached?.detail.calls_window
    if (!cached || !callsWindow?.has_more || !callsWindow.next_cursor || nodeFetchInFlight.current.has(cacheKey)) return false
    nodeFetchInFlight.current.add(cacheKey)
    try {
      const detailUrl = `${apiBase}/v1/observer/runs/${runId}/nodes/${encodeURIComponent(stageId)}`
      const response = await fetchJsonWithTimeout<NodeDetail>(observerNodeCallsUrl(detailUrl, { cursor: callsWindow.next_cursor, limit: callsWindow.limit ?? observerCallPageLimit }), observerNodeDetailTimeoutMs)
      const nodeDetail = mergeNodeDetailCalls(cached.detail, response.payload, 'older')
      nodeDetailCache.set(cacheKey, { detail: nodeDetail, fetchedAt: cached.fetchedAt })
      if (selectedIdRef.current === runId && epoch === detailEpoch.current) setNodeDetails((current) => ({ ...current, [stageId]: nodeDetail }))
      return true
    } catch {
      return false
    } finally {
      nodeFetchInFlight.current.delete(cacheKey)
    }
  }, [apiBase])

  const schedulePrefetchPump = useCallback((delay = observerIdlePrefetchDelayMs) => {
    if (idlePrefetchTimer.current !== null) window.clearTimeout(idlePrefetchTimer.current)
    idlePrefetchTimer.current = window.setTimeout(() => prefetchPumpRef.current(), delay)
  }, [])
  const prefetchPumpRef = useRef<() => void>(() => undefined)
  const prefetchPump = useCallback(() => {
    const queue = prefetchQueue.current
    if (!queue || queue.nextIndex >= queue.orderedStageIds.length || queue.epoch !== detailEpoch.current) {
      idlePrefetchTimer.current = null
      return
    }
    if (document.hidden) {
      schedulePrefetchPump(1_500)
      return
    }
    if (runsInFlight.current || detailInFlight.current) {
      schedulePrefetchPump(2_000)
      return
    }
    // The observer reaches PostgreSQL through a high-latency tunnel. Keep
    // background node hydration single-flight instead of stacking expensive
    // stage reads while another node still holds a database connection.
    if (nodeFetchInFlight.current.size > 0) {
      schedulePrefetchPump(2_000)
      return
    }
    const cachedStageIds = new Set(queue.orderedStageIds.filter((stageId) => nodeDetailCache.has(observerNodeDetailCacheKey(apiBase, queue.runId, stageId))))
    const next = observerNextPrefetchStage(queue, cachedStageIds, observerInFlightStageIds(queue.runId, prefetchInFlightStageIds.current))
    prefetchQueue.current = next.queue
    if (!next.stageId) {
      idlePrefetchTimer.current = null
      setNodeDetailFetch((current) => ({ ...current, deferred: 0 }))
      return
    }
    const stageId = next.stageId
    const inFlightKey = observerPrefetchInFlightKey(queue.runId, stageId)
    setNodeDetailFetch((current) => ({ ...current, deferred: Math.max(0, current.deferred - 1) }))
    prefetchInFlightStageIds.current.add(inFlightKey)
    void loadNodeDetail(queue.runId, stageId, queue.epoch, { refreshExpired: false }).then((succeeded) => {
      if (!observerPrefetchQueueMatches(prefetchQueue.current, queue.runId, queue.epoch ?? -1)) return
      if (!succeeded) prefetchQueue.current = observerRequeuePrefetchStage(prefetchQueue.current!, stageId)
    }).finally(() => {
      prefetchInFlightStageIds.current.delete(inFlightKey)
      if (observerPrefetchQueueMatches(prefetchQueue.current, queue.runId, queue.epoch ?? -1)) schedulePrefetchPump()
    })
  }, [apiBase, loadNodeDetail, schedulePrefetchPump])

  const loadOlderEvents = useCallback(async () => {
    const current = detailRef.current
    const runId = detailRunIdRef.current
    if (!current || !runId || eventHistoryInFlight.current || !shouldFetchOlderObserverEvents(current.event_window)) return
    eventHistoryInFlight.current = true
    setEventHistoryLoading(true)
    const epoch = detailEpoch.current
    try {
      const detailUrl = `${apiBase}/v1/observer/runs/${runId}`
      const history = await loadObserverEventHistory(
        detailUrl,
        { payload: current },
        (pageUrl) => fetchJsonWithTimeout<Pick<RunDetail, 'events' | 'event_window'>>(pageUrl, observerRunDetailTimeoutMs),
        () => false,
        observerEventPageMax,
      )
      if (epoch === detailEpoch.current && detailRunIdRef.current === runId && history.pagesLoaded > 1) {
        detailRef.current = history.payload
        setDetail(history.payload)
      }
    } finally {
      eventHistoryInFlight.current = false
      setEventHistoryLoading(false)
    }
  }, [apiBase])
  prefetchPumpRef.current = prefetchPump

  const loadDetail = useCallback(async (runId: string, quiet = false) => {
    if (quiet && eventHistoryInFlight.current && detailRunIdRef.current === runId) return
    const requestAction = observerDetailRequestAction(detailInFlight.current, runId, detailInFlightRunId.current)
    if (requestAction === 'skip') return
    if (requestAction === 'after-current') {
      // A selection change during an older request is the only queued case.
      // Refresh timers for the same run are discarded instead of chaining.
      pendingDetailRunId.current = runId
      return
    }
    detailInFlight.current = true
    detailInFlightRunId.current = runId
    const epoch = detailEpoch.current
    if (!quiet) setLoading(true)
    else setRefreshing(true)
    try {
      const detailUrl = `${apiBase}/v1/observer/runs/${runId}`
      const response = await fetchJsonWithTimeout<RunDetail>(detailUrl, observerRunDetailTimeoutMs)
      const history = await loadObserverEventHistory(
        detailUrl,
        response,
        (pageUrl) => fetchJsonWithTimeout(pageUrl, observerRunDetailTimeoutMs),
        (cacheState) => observerResponseIsStale(cacheState ?? null),
        quiet ? 0 : 1,
      )
      const sameRun = detailRunIdRef.current === runId
      const payload = sameRun ? mergeObserverDetailEventHistory(history.payload, detailRef.current) : history.payload
      const staleResponse = observerResponseIsStale(history.cacheState)
      assertMatchingRunIdentity(runIdentities.current[runId] ?? { id: runId }, payload.run)
      if (epoch !== detailEpoch.current) return
      const stages = payload.graph?.nodes ?? []
      detailRunIdRef.current = runId
      detailRef.current = payload
      setDetail(payload)
      setDetailSyncError(null)
      if (!staleResponse) {
        lastSuccessfulDetailAtRef.current = payload.updated_at
        setLastSuccessfulDetailAt(payload.updated_at)
      }
      if (!staleResponse) writeObserverCache(observerRunDetailCacheKey(apiBase, runId), apiBase, payload)
      // A quiet refresh updates the authoritative run summary without discarding
      // already loaded stage details. Those rows are cached and refreshed separately.
      if (!sameRun) setNodeDetails({})
      currentStageIds.current = stages.map((stage) => stage.id)
      if (!sameRun) loadedStageKeys.current.clear()
      const cachedCount = stages.filter((stage) => {
        const cacheKey = observerNodeDetailCacheKey(apiBase, runId, stage.id)
        if (nodeDetailCache.has(cacheKey)) loadedStageKeys.current.add(cacheKey)
        return loadedStageKeys.current.has(cacheKey)
      }).length
      setError(null)
      detailStale.current = staleResponse
      setSyncingStale(runsStale.current || detailStale.current)
      setLoading(false)

      const orderedStages = observerPrefetchStageOrder(stages)
      const queueBefore = prefetchQueue.current
      const queue = observerMergePrefetchQueue(runId, orderedStages, queueBefore, observerInitialPrefetchCount(orderedStages.length))
      const isNewQueue = queueBefore === null || queueBefore.runId !== runId || queueBefore.epoch !== epoch
      prefetchQueue.current = { ...queue, epoch }
      const cachedStageIds = new Set(stages.filter((stage) => nodeDetailCache.has(observerNodeDetailCacheKey(apiBase, runId, stage.id))).map((stage) => stage.id))
      setNodeDetailFetch((current) => ({
        requested: stages.length,
        loaded: Math.min(stages.length, cachedCount),
        failed: sameRun ? current.failed : 0,
        deferred: observerPendingPrefetchCount(queue, cachedStageIds),
      }))
      if (isNewQueue) {
        const initialStages = observerInitialPrefetchStages(orderedStages)
        void Promise.all(initialStages.map((stage) => loadNodeDetail(runId, stage.id, epoch, { refreshExpired: observerPrefetchRefreshExpired('initial') })))
        if (observerPendingPrefetchCount(queue, cachedStageIds) > 0) schedulePrefetchPump()
      } else if (quiet) {
        // A quiet refresh may renew only the highest-value progress rows. The
        // idle queue treats any existing cache, including an expired one, as
        // fulfilled so its tail keeps advancing instead of being re-read.
        const highValueStages = orderedStages.slice(0, observerInitialPrefetchCount(orderedStages.length))
        void Promise.all(highValueStages.map((stage) => loadNodeDetail(runId, stage.id, epoch, { refreshExpired: true })))
        if (observerPendingPrefetchCount(queue, cachedStageIds) > 0 && idlePrefetchTimer.current === null) schedulePrefetchPump()
      } else if (observerPendingPrefetchCount(queue, cachedStageIds) > 0 && idlePrefetchTimer.current === null) {
        // A completed queue can gain new stages from a later detail snapshot.
        // Wake the existing queue without replaying its initial slice.
        schedulePrefetchPump()
      }
    } catch (cause) {
      if (epoch === detailEpoch.current) {
        if (detailRunIdRef.current === runId && detailRef.current) {
          setDetailSyncError(observerDetailFailureMessage(formatTime(lastSuccessfulDetailAtRef.current ?? detailRef.current.updated_at)))
          setError(null)
          setSyncingStale(false)
        } else {
          setError(readableDataError(cause, '无法连接观察器接口'))
        }
        detailStale.current = true
        if (detailRunIdRef.current !== runId || !detailRef.current) setSyncingStale(true)
      }
    } finally {
      detailInFlight.current = false
      detailInFlightRunId.current = null
      if (epoch === detailEpoch.current) {
        setLoading(false)
        setRefreshing(false)
      }
      const pendingRunId = pendingDetailRunId.current
      pendingDetailRunId.current = null
      if (pendingRunId && pendingRunId !== runId && selectedIdRef.current === pendingRunId) void loadDetail(pendingRunId)
    }
  }, [apiBase, loadNodeDetail, schedulePrefetchPump])

  useEffect(() => {
    selectedIdRef.current = selectedId
    if (previousSelectedId.current === selectedId) return
    previousSelectedId.current = selectedId
    detailEpoch.current += 1
    detailRunIdRef.current = null
    prefetchQueue.current = null
    prefetchInFlightStageIds.current.clear()
    if (idlePrefetchTimer.current !== null) window.clearTimeout(idlePrefetchTimer.current)
    setDetail(null)
    setDetailSyncError(null)
    lastSuccessfulDetailAtRef.current = null
    setLastSuccessfulDetailAt(null)
    detailStale.current = false
    setSyncingStale(runsStale.current)
    setNodeDetails({})
    setNodeDetailFetch({ requested: 0, loaded: 0, failed: 0, deferred: 0 })
    setLoading(Boolean(selectedId))
  }, [selectedId])

  useEffect(() => {
    if (!enabled) {
      if (idlePrefetchTimer.current !== null) window.clearTimeout(idlePrefetchTimer.current)
      idlePrefetchTimer.current = null
      prefetchQueue.current = null
      prefetchInFlightStageIds.current.clear()
      setLoading(false)
      return
    }
    loadRuns().catch(reportBackgroundSyncError)
    return () => {
      if (idlePrefetchTimer.current !== null) window.clearTimeout(idlePrefetchTimer.current)
      idlePrefetchTimer.current = null
      prefetchQueue.current = null
      prefetchInFlightStageIds.current.clear()
    }
  }, [enabled, loadRuns, reportBackgroundSyncError])

  useEffect(() => {
    if (!enabled || !selectedId) return
    void loadDetail(selectedId, detailRunIdRef.current === selectedId).catch(reportBackgroundSyncError)
    return () => { pendingDetailRunId.current = null }
  }, [enabled, loadDetail, reportBackgroundSyncError, selectedId])

  useEffect(() => {
    if (!enabled) return
    const timer = window.setInterval(() => {
      if (!document.hidden && !detailInFlight.current) void loadRuns().catch(reportBackgroundSyncError)
    }, 45_000)
    return () => window.clearInterval(timer)
  }, [enabled, loadRuns, reportBackgroundSyncError])

  useEffect(() => {
    if (!enabled || !selectedId) return
    const intervalMs = observerPollingIntervalMs(detail?.run.status)
    const timer = window.setInterval(() => {
      if (!document.hidden && !runsInFlight.current) void loadDetail(selectedId, true).catch(reportBackgroundSyncError)
    }, intervalMs)
    return () => window.clearInterval(timer)
  }, [detail?.run.status, enabled, loadDetail, reportBackgroundSyncError, selectedId])

  useEffect(() => {
    if (!enabled) return
    const onVisibilityChange = () => {
      const wasHidden = wasDocumentHidden.current
      wasDocumentHidden.current = document.hidden
      if (!observerVisibilityRefreshNeeded(document.hidden, wasHidden)) return
      void loadRuns().catch(reportBackgroundSyncError)
      const runId = selectedIdRef.current
      if (runId) void loadDetail(runId, true).catch(reportBackgroundSyncError)
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [enabled, loadDetail, loadRuns, reportBackgroundSyncError])

  const retry = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      await loadRuns()
      if (selectedId) await loadDetail(selectedId)
    } catch (cause) {
      setError(readableDataError(cause, '无法连接观察器接口'))
      setLoading(false)
    }
  }, [loadDetail, loadRuns, selectedId])

  const setSelectedId = useCallback((runId: string) => {
    window.localStorage.setItem(selectedRunStorageKey, runId)
    setSelectedIdState(runId)
  }, [])

  useEffect(() => {
    const apiChanged = previousApiBase.current !== apiBase
    previousApiBase.current = apiBase
    if (apiChanged) {
      detailEpoch.current += 1
      detailRunIdRef.current = null
      prefetchQueue.current = null
      prefetchInFlightStageIds.current.clear()
      if (idlePrefetchTimer.current !== null) window.clearTimeout(idlePrefetchTimer.current)
      const cachedRuns = readObserverCache<RunListResponse>(observerRunListCacheKey(apiBase), apiBase)
      runsStale.current = Boolean(cachedRuns)
      if (cachedRuns) setRuns(cachedRuns.payload.runs)
    }
    if (!selectedId) return
    const cachedDetail = readObserverCache<RunDetail>(observerRunDetailCacheKey(apiBase, selectedId), apiBase)
    if (cachedDetail && cachedDetail.payload.run.id === selectedId) {
      detailRunIdRef.current = selectedId
      setDetail(cachedDetail.payload)
      setDetailSyncError(null)
      lastSuccessfulDetailAtRef.current = cachedDetail.payload.updated_at
      setLastSuccessfulDetailAt(cachedDetail.payload.updated_at)
      detailStale.current = true
      setSyncingStale(runsStale.current || detailStale.current)
      setLoading(false)
      return
    }
    detailRunIdRef.current = null
    setDetail(null)
    setNodeDetails({})
    setNodeDetailFetch({ requested: 0, loaded: 0, failed: 0, deferred: 0 })
    detailStale.current = false
    setSyncingStale(runsStale.current)
    setLoading(true)
  }, [apiBase, selectedId])

  const refresh = useCallback(() => {
    setDetailSyncError(null)
    return selectedId ? loadDetail(selectedId, true) : undefined
  }, [loadDetail, selectedId])

  return { runs, selectedId, setSelectedId, detail, nodeDetails, nodeDetailFetch, error, loading, refreshing, syncingStale, detailSyncError, lastSuccessfulDetailAt, eventHistoryLoading, loadOlderEvents, loadOlderNodeCalls, retry, refresh }
}

function RunList({ runs, selectedId, graphObservedCalls, onSelect }: { runs: RunListItem[]; selectedId: string | null; graphObservedCalls: number | null; onSelect: (id: string) => void }) {
  if (!runs.length) {
    return <div className="run-list"><div className="run-list-empty"><Database /><span><b>暂无可用运行数据</b><small>观察器接口未返回可展示的 PostgreSQL 运行记录。</small></span></div></div>
  }
  return (
    <div className="run-list">
      {!runs.some((run) => run.id === selectedId) && selectedId && <div className="run-list-missing"><b>当前运行不在最近列表</b><small>仍以 URL 指定的 PostgreSQL run id 读取详情，不会高亮其他运行。</small></div>}
      {runs.map((run) => (
        <button key={run.id} className={`run-row ${run.id === selectedId ? 'active' : ''}`} onClick={() => onSelect(run.id)}>
          <span className={`run-status-dot status-${run.status}`} />
          <span className="run-row-copy">
                <strong>{formatRunTitle(run)}</strong>
            <small title="列表统计来自运行记录；是否已映射到运行图以当前详情为准.">{formatTime(run.created_at)} · {run.tool_call_count} 条工具记录{run.id === selectedId && run.tool_call_count > 0 && graphObservedCalls === 0 ? ' · 尚未映射到运行图' : ''}</small>
          </span>
          <ChevronRight />
        </button>
      ))}
    </div>
  )
}

function Sidebar({
  runs,
  selectedId,
  graphObservedCalls,
  structureRun,
  activeView,
  onView,
  onSelect,
  onOpenStructureEvidence,
}: {
  runs: RunListItem[]
  selectedId: string | null
  graphObservedCalls: number | null
  structureRun: RunListItem | null
  activeView: 'overview' | 'analysis' | 'evidence'
  onView: (view: 'overview' | 'analysis' | 'evidence') => void
  onSelect: (id: string) => void
  onOpenStructureEvidence: () => void
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar-label">概览与数据</div>
      <nav className="primary-nav">
        <button className={activeView === 'overview' ? 'active' : ''} onClick={() => onView('overview')}><Layers3 />概览</button>
        <button className={activeView === 'analysis' ? 'active' : ''} onClick={() => onView('analysis')}><ChartNoAxesCombined />分析</button>
        <button className={activeView === 'evidence' ? 'active' : ''} onClick={() => onView('evidence')}><Database />证据库</button>
      </nav>
      <div className="sidebar-label runs-label">轮次 · 科学运行</div>
      <RunList runs={runs} selectedId={selectedId} graphObservedCalls={graphObservedCalls} onSelect={onSelect} />
      <div className="sidebar-sections">
        <button><span><SparkIcon icon="sequence" /></span><b>序列设计</b><small>生成模型与十一项指标</small></button>
        <button><span><GitBranch /></span><b>多靶点</b><small>原位与错误口袋对照</small></button>
        <button
          className={`structure-evidence-link${structureRun?.id === selectedId && activeView === 'overview' ? ' active' : ''}`}
          title="Boltz 2预测复合物构象；Rosetta进行界面精修与评分。"
          disabled={!structureRun}
          onClick={onOpenStructureEvidence}
        >
          <span><Atom /></span><b>结构证据</b>
          <small>{structureRun ? `最近轮次 · ${structureRun.structure_record_count.toLocaleString()} 条记录` : '数据库中尚无结构记录'}</small>
        </button>
        <button><span><ShieldCheck /></span><b>科学评审</b><small>证据来源追踪</small></button>
      </div>
    </aside>
  )
}

function SparkIcon({ icon }: { icon: string }) {
  return icon === 'sequence' ? <Activity /> : <CircleDot />
}

function CanvasHeader({ detail, refreshing, syncingStale, detailSyncError, selectionMode, selectedCount, onRefresh, onToggleSelection }: {
  detail: RunDetail
  refreshing: boolean
  syncingStale: boolean
  detailSyncError: string | null
  selectionMode: boolean
  selectedCount: number
  onRefresh: () => void
  onToggleSelection: () => void
}) {
  const isStructureReview = (detail.counts.boltz_poses ?? 0) > 0 || (detail.counts.rosetta_decoys ?? 0) > 0
  const displayCandidateCount = detail.display_population?.candidate_count ?? detail.counts.candidates
  const previewTotal = candidatePreviewDenominator(detail)
  const excludedCandidateCount = detail.display_population?.excluded_candidate_count ?? detail.counts.excluded_candidates ?? detail.candidate_exclusions?.length ?? 0
  const generationSummary = detail.generation_population
    ? formatGenerationPopulation(detail.generation_population)
    : `${displayCandidateCount.toLocaleString()} 个候选`
  const scientificStatus = detail.run.scientific_run_status?.status ?? detail.run.status
  const temporalObservability = detail.run.temporal_observability
  const schedulerHealth = temporalObservability ? schedulerHealthPresentation(temporalObservability) : null
  const schedulerHealthTitle = temporalObservability
    ? `${schedulerHealthDescription(temporalObservability)}${temporalObservability.observed_at ? ` · ${formatTime(temporalObservability.observed_at)} 观测` : ''}`
    : ''
  const isAcceptanceFixture = detail.source !== 'postgresql'
  return (
    <header className="canvas-header">
      <div className="canvas-title-block">
        <div className="eyebrow"><span>{detailSyncError ? '数据同步中断' : isAcceptanceFixture ? '验收数据' : syncingStale ? '正在同步' : '科学运行'}</span></div>
        <h1>{isStructureReview ? '短肽结构证据复核' : '序列优先的短肽设计'}</h1>
        <div className="round-meta">
          <span>{formatTime(detail.run.created_at)} 创建</span><i />
          <span>{generationSummary}</span><i />
          <span>候选预览 {candidatePreviewCountLabel(detail.candidates.length, previewTotal)}</span><i />
          {excludedCandidateCount > 0 && <><span title="历史运行中已存在的生成子代，仅保留审计记录。">{excludedCandidateCount.toLocaleString()} 个历史重放已排除</span><i /></>}
          {detail.counts.admitted > 0 && <><span>{detail.counts.admitted.toLocaleString()} 个进入结构阶段</span><i /></>}
          {detail.branches.length > 0 && <><span>{detail.branches.length} 个靶点</span><i /></>}
          {(detail.counts.boltz_poses > 0 || detail.counts.rosetta_decoys > 0) && <span>{detail.counts.boltz_poses.toLocaleString()} 个复合物构象 · {detail.counts.rosetta_decoys.toLocaleString()} 个界面精修样本</span>}
        </div>
      </div>
      <div className="header-actions">
        <button className={`analysis-select-button ${selectionMode ? 'active' : ''}`} onClick={onToggleSelection}>
          <ChartNoAxesCombined /><span>{selectionMode ? `已选 ${selectedCount} 个节点` : '组合分析'}</span>
        </button>
        <span className={`run-pill status-${scientificStatus}`} title="科学运行状态来自权威数据库。"><i />{statusText[scientificStatus] ?? scientificStatus}</span>
        {schedulerHealth && <span className={`observability-pill tone-${schedulerHealth.tone}`} title={schedulerHealthTitle}>{schedulerHealth.label}</span>}
        <button className={`icon-button ${detailSyncError ? 'retry-detail-button' : ''}`} onClick={onRefresh} title={detailSyncError ? '重试详情读取' : '立即刷新'} aria-label={detailSyncError ? '重试详情' : '立即刷新'}>
          <RefreshCw className={refreshing ? 'spin' : ''} />
          {detailSyncError && <span>重试详情</span>}
        </button>
        <button className="icon-button"><Ellipsis /></button>
      </div>
    </header>
  )
}

function GraphView({
  detail,
  nodeDetails,
  runtimeGraph,
  analysisSnapshot,
  persistedDistributions,
  selectedStage,
  selectedEdge,
  selectionMode,
  analysisSelection,
  onSelect,
  onToggleAnalysis,
  onSelectEdge,
  onToggleGroup,
  onAvailableWidthChange,
  onLoadOlderEvents,
  eventHistoryLoading,
}: {
  detail: RunDetail
  nodeDetails: Record<string, NodeDetail>
  runtimeGraph: RuntimeGraphModel
  analysisSnapshot: AnalysisSnapshot | null
  persistedDistributions: Record<string, ResultDistributionData>
  selectedStage: string | null
  selectedEdge: GraphEdgeDetail | null
  selectionMode: boolean
  analysisSelection: string[]
  onSelect: (id: string) => void
  onToggleAnalysis: (id: string) => void
  onSelectEdge: (edge: GraphEdgeDetail) => void
  onToggleGroup: (id: string) => void
  onAvailableWidthChange: (width: number) => void
  onLoadOlderEvents: () => void
  eventHistoryLoading: boolean
}) {
  const flowInstance = useRef<ReactFlowInstance<LaneNode | StageNode, Edge> | null>(null)
  const currentFitRunId = useRef(detail.run.id)
  const initialFitAttempts = useRef(0)
  const initialFitInFlight = useRef(false)
  const initialFitPending = useRef(false)
  const initialFitTimer = useRef<number | null>(null)
  const lastFittedLayoutSignature = useRef<string | null>(null)
  const layoutSignatureRef = useRef('')
  const scheduleInitialFitRef = useRef<() => void>(() => undefined)
  const pendingClusterFocus = useRef<string | null | undefined>(undefined)
  const clusterFocusRequestId = useRef(0)
  const clusterFocusTimer = useRef<number | null>(null)
  const expandedClusterFocusTimer = useRef<number | null>(null)
  const expandedClusterFocusInFlight = useRef(false)
  const expandedClusterFocusedRevision = useRef<string | null>(null)
  const expandedClusterLayoutRevisionRef = useRef('')
  const expandedClusterActive = useRef(false)
  const clusterFocusUserMoved = useRef(false)
  const clusterResizeTimer = useRef<number | null>(null)
  const previousGraphViewportSize = useRef({ width: 0, height: 0 })
  const expandedFrameRaf = useRef<number | null>(null)
  const userInteracted = useRef(false)
  const programmaticFit = useRef(false)
  const graphAreaRef = useRef<HTMLDivElement>(null)
  const [graphViewportSize, setGraphViewportSize] = useState({ width: 0, height: 0 })
  const [expandedClusterFrames, setExpandedClusterFrames] = useState<RuntimeClusterFrame[]>([])
  useEffect(() => {
    const element = graphAreaRef.current
    if (!element) return
    const reportWidth = () => {
      if (element.clientWidth > 0) onAvailableWidthChange(element.clientWidth)
      if (element.clientWidth > 0 && element.clientHeight > 0) {
        setGraphViewportSize((previous) => previous.width === element.clientWidth && previous.height === element.clientHeight
          ? previous
          : { width: element.clientWidth, height: element.clientHeight })
      }
    }
    reportWidth()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(reportWidth)
    observer.observe(element)
    return () => observer.disconnect()
  }, [onAvailableWidthChange])
  // The aggregate tool chain is a first-class scientific loop summary. Its
  // per-tool children stay folded until requested.
  const readableRuntimeNodeIds = useMemo(() => {
    const smallExpandedCandidateGroup = runtimeGraph.nodes.some((node) => node.runtime?.node_type === 'candidate_group'
      && node.runtime.expanded
      && (node.runtime.child_ids?.length ?? 0) <= 3)
    const hasObservedActivityRetry = runtimeGraph.nodes.some((node) => (node.runtime?.activity_retry_count ?? 0) > 0)
    const expandedSummaryGroup = runtimeGraph.nodes.find((node) => node.runtime?.node_type === 'tool_summary_group' && node.runtime.expanded)
    const readableLimit = expandedSummaryGroup
      ? Math.min(14, (expandedSummaryGroup.runtime?.child_ids?.length ?? 0) + 4)
      : smallExpandedCandidateGroup ? 8 : graphViewportSize.width > 2100 ? 7 : hasObservedActivityRetry ? 6 : 5
    const selected = selectReadableRuntimeNodeIds(runtimeGraph.nodes, runtimeGraph.positions, readableLimit)
    const expandedClusterIds = runtimeGraph.nodes
      .filter((node) => node.runtime?.expanded && ['tool_group', 'event_group', 'batch_group', 'tool_summary_group', 'candidate_group'].includes(node.runtime.node_type))
      .flatMap((group) => runtimeChildNodeIds(runtimeGraph.nodes, group))
    const visibleIds = [...new Set([...selected, ...expandedClusterIds])]
    return visibleIds.filter((id) => {
      const node = runtimeGraph.nodes.find((candidate) => candidate.id === id)
      if (expandedSummaryGroup && ['candidate_group', 'candidate_preview', 'generation', 'population_summary'].includes(node?.runtime?.node_type ?? '')) return false
      if (node?.runtime?.node_type !== 'tool_summary') return true
      return Boolean(expandedSummaryGroup?.runtime?.child_ids?.includes(id))
    })
  }, [graphViewportSize.width, runtimeGraph.nodes, runtimeGraph.positions])
  const readableRuntimePositions = useMemo(() => {
    const compact = compactReadableRuntimePositions(readableRuntimeNodeIds, runtimeGraph.positions)
    const expandedSummary = runtimeGraph.nodes.find((node) => node.runtime?.node_type === 'tool_summary_group' && node.runtime.expanded)
    if (!expandedSummary || !readableRuntimeNodeIds.includes(expandedSummary.id)) return compact
    const childIds = (expandedSummary.runtime?.child_ids ?? []).filter((id) => readableRuntimeNodeIds.includes(id))
    const leadingIds = readableRuntimeNodeIds
      .filter((id) => id !== expandedSummary.id && !childIds.includes(id) && runtimeGraph.nodes.find((node) => node.id === id)?.runtime?.node_type !== 'population_summary')
      .sort((left, right) => (runtimeGraph.positions[left]?.x ?? 0) - (runtimeGraph.positions[right]?.x ?? 0))
    const groupX = 190 + leadingIds.length * 330
    leadingIds.forEach((id, index) => { compact[id] = { x: 190 + index * 330, y: 300 } })
    compact[expandedSummary.id] = { x: groupX, y: 300 }
    childIds.forEach((id, index) => {
      compact[id] = { x: groupX + 330 + (index % 3) * 330, y: 110 + Math.floor(index / 3) * 190 }
    })
    const populationId = readableRuntimeNodeIds.find((id) => runtimeGraph.nodes.find((node) => node.id === id)?.runtime?.node_type === 'population_summary')
    if (populationId) compact[populationId] = { x: groupX + 4 * 330, y: 300 }
    return compact
  }, [readableRuntimeNodeIds, runtimeGraph.nodes, runtimeGraph.positions])
  const readableLayoutSignature = useMemo(() => [
    `${graphViewportSize.width}x${graphViewportSize.height}`,
    ...readableRuntimeNodeIds.map((id) => {
      const position = readableRuntimePositions[id]
      return `${id}:${position ? `${Math.round(position.x)},${Math.round(position.y)}` : 'unplaced'}`
    }),
  ].join('|'), [graphViewportSize.height, graphViewportSize.width, readableRuntimeNodeIds, readableRuntimePositions])
  const readableRuntimeNodeIdSet = useMemo(() => new Set(readableRuntimeNodeIds), [readableRuntimeNodeIds])
  layoutSignatureRef.current = readableLayoutSignature
  const markUserInteracted = useCallback(() => {
    if (!programmaticFit.current) userInteracted.current = true
  }, [])
  const handleToggleGroup = useCallback((id: string) => {
    const group = runtimeGraph.nodes.find((node) => node.id === id)
    const isExpanded = Boolean(group?.runtime?.expanded)
    // Group toggles are explicit user navigation. Expansion focuses the
    // revealed cluster; collapse returns to the bounded scientific spine.
    // Neither action should be mistaken for a fresh run or let hydration
    // reclaim the viewport after the user has chosen a reading surface.
    userInteracted.current = true
    clusterFocusUserMoved.current = false
    expandedClusterFocusedRevision.current = null
    clusterFocusRequestId.current += 1
    pendingClusterFocus.current = isExpanded ? null : id
    if (clusterFocusTimer.current !== null) window.clearTimeout(clusterFocusTimer.current)
    if (initialFitTimer.current !== null) window.clearTimeout(initialFitTimer.current)
    initialFitPending.current = false
    onToggleGroup(id)
  }, [onToggleGroup, runtimeGraph.nodes])
  const fitReadableViewport = useCallback(async () => {
    const instance = flowInstance.current
    if (!instance || expandedClusterActive.current) return false
    const readableIds = new Set(readableRuntimeNodeIds)
    if (!instance.getNodes().some((node) => readableIds.has(node.id))) return false
    programmaticFit.current = true
    try {
      // Wait for one layout frame so React Flow has measured the cards, then
      // use the current instance nodes directly. Calling fitView here would
      // let its internal animation overwrite the deterministic viewport.
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve())))
      const readableNodes = instance.getNodes().filter((node) => readableIds.has(node.id))
      if (!readableNodes.length) return false
      const currentZoom = Math.max(0.01, instance.getViewport().zoom)
      const domNodes = new Map(
        [...document.querySelectorAll<HTMLElement>('.react-flow__node')]
          .map((element) => [element.getAttribute('data-id'), element] as const),
      )
      const measuredNodes = readableNodes.map((node) => {
        const element = domNodes.get(node.id)
        const rect = element?.getBoundingClientRect()
        const domWidth = rect && rect.width > 0 ? rect.width / currentZoom : 0
        const domHeight = rect && rect.height > 0 ? rect.height / currentZoom : 0
        return {
          node,
          width: (node.measured?.width ?? 0) > 0 ? node.measured!.width! : domWidth,
          height: (node.measured?.height ?? 0) > 0 ? node.measured!.height! : domHeight,
        }
      })
      if (!measuredNodes.every(({ width, height }) => width > 0 && height > 0)) return false
      // React Flow may measure the first render before the cards are mounted,
      // leaving a large viewport at the minimum zoom. Re-center from the
      // selected node bounds after fitView so the result is deterministic on
      // both 1920 and 2560 screens. The top summary and bottom controls keep
      // a reserved band; later records remain available by panning.
      const graphRect = graphAreaRef.current?.getBoundingClientRect()
      if (graphRect) {
        const measuredBounds = measuredNodes.map(({ node, width, height }) => ({
          left: node.position.x,
          top: node.position.y,
          right: node.position.x + width,
          bottom: node.position.y + height,
        }))
        const minimumNodeX = Math.min(...measuredBounds.map((node) => node.left))
        const minimumNodeY = Math.min(...measuredBounds.map((node) => node.top))
        const maximumNodeX = Math.max(...measuredBounds.map((node) => node.right))
        const maximumNodeY = Math.max(...measuredBounds.map((node) => node.bottom))
        const boundsWidth = Math.max(1, maximumNodeX - minimumNodeX)
        const boundsHeight = Math.max(1, maximumNodeY - minimumNodeY)
        // Fit the selected scientific spine inside the full canvas. Historical
        // continuation remains available by panning, but is never teased as a
        // clipped half-card at the right edge.
        const horizontalSafety = graphRect.width > 2000 ? 56 : 40
        const usableWidth = Math.max(1, graphRect.width - horizontalSafety * 2)
        const usableHeight = Math.max(1, graphRect.height - 122)
        const zoom = Math.min(1, Math.max(readableViewportMinZoom, Math.min(usableWidth / boundsWidth, usableHeight / boundsHeight)))
        const topSafety = 128
        const bottomSafety = graphRect.height - 12
        const centeredY = topSafety + Math.min(140, Math.max(0, usableHeight - boundsHeight * zoom) * 0.2) - minimumNodeY * zoom
        const topAlignedY = topSafety - minimumNodeY * zoom
        const bottomAlignedY = bottomSafety - maximumNodeY * zoom
        const viewport = {
          x: horizontalSafety + (usableWidth - boundsWidth * zoom) / 2 - minimumNodeX * zoom,
          y: Math.min(bottomAlignedY, Math.max(topAlignedY, centeredY)),
          zoom,
        }
        await instance.setViewport(viewport, { duration: 120 })
        // The measured card height is authoritative for the final screen check.
        // Stacked pseudo-cards can extend beyond React Flow's node box, so make
        // one bounded correction against the actual canvas and fixed summary.
        await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()))
        const currentViewport = instance.getViewport()
        const currentGraphRect = graphAreaRef.current?.getBoundingClientRect()
        const summaryRect = document.querySelector<HTMLElement>('.runtime-history-status')?.getBoundingClientRect()
        const screenRects = readableNodes
          .map((node) => domNodes.get(node.id)?.getBoundingClientRect())
          .filter((rect): rect is DOMRect => Boolean(rect && rect.width > 0 && rect.height > 0))
        if (currentGraphRect && screenRects.length) {
          const topBoundary = Math.max(currentGraphRect.top + 12, (summaryRect?.bottom ?? currentGraphRect.top) + 10)
          const bottomBoundary = currentGraphRect.bottom - 12
          const minimumScreenTop = Math.min(...screenRects.map((rect) => rect.top))
          const maximumScreenBottom = Math.max(...screenRects.map((rect) => rect.bottom))
          const topOverflow = Math.max(0, topBoundary - minimumScreenTop)
          const bottomOverflow = Math.max(0, maximumScreenBottom - bottomBoundary)
          if (topOverflow > 0 && bottomOverflow > 0) {
            const availableHeight = Math.max(1, bottomBoundary - topBoundary)
            const occupiedHeight = Math.max(1, maximumScreenBottom - minimumScreenTop)
              const correctedZoom = Math.max(readableViewportMinZoom, currentViewport.zoom * Math.min(0.98, availableHeight / occupiedHeight))
            if (correctedZoom < currentViewport.zoom - 0.005) {
              const minimumWorldY = (minimumScreenTop - currentGraphRect.top - currentViewport.y) / currentViewport.zoom
              await instance.setViewport({
                x: currentViewport.x,
                y: topBoundary - currentGraphRect.top - minimumWorldY * correctedZoom,
                zoom: correctedZoom,
              }, { duration: 0 })
            }
          } else if (topOverflow > 0 || bottomOverflow > 0) {
            await instance.setViewport({
              ...currentViewport,
              y: currentViewport.y + (topOverflow > 0 ? topOverflow : -bottomOverflow),
            }, { duration: 0 })
          }
          const horizontalViewport = instance.getViewport()
          const horizontalRects = readableNodes
            .map((node) => domNodes.get(node.id)?.getBoundingClientRect())
            .filter((rect): rect is DOMRect => Boolean(rect && rect.width > 0 && rect.height > 0))
          if (horizontalRects.length) {
            const edgeSafety = currentGraphRect.width > 2000 ? 56 : 40
            const leftBoundary = currentGraphRect.left + edgeSafety
            const rightBoundary = currentGraphRect.right - edgeSafety
            const minimumScreenLeft = Math.min(...horizontalRects.map((rect) => rect.left))
            const maximumScreenRight = Math.max(...horizontalRects.map((rect) => rect.right))
            const leftOverflow = Math.max(0, leftBoundary - minimumScreenLeft)
            const rightOverflow = Math.max(0, maximumScreenRight - rightBoundary)
            if (leftOverflow > 0 && rightOverflow > 0) {
              const availableWidth = Math.max(1, rightBoundary - leftBoundary)
              const occupiedWidth = Math.max(1, maximumScreenRight - minimumScreenLeft)
              const correctedZoom = Math.max(readableViewportMinZoom, horizontalViewport.zoom * Math.min(0.98, availableWidth / occupiedWidth))
              const minimumWorldX = (minimumScreenLeft - currentGraphRect.left - horizontalViewport.x) / horizontalViewport.zoom
              await instance.setViewport({
                x: leftBoundary - currentGraphRect.left - minimumWorldX * correctedZoom,
                y: horizontalViewport.y,
                zoom: correctedZoom,
              }, { duration: 0 })
            } else if (leftOverflow > 0 || rightOverflow > 0) {
              await instance.setViewport({
                ...horizontalViewport,
                x: horizontalViewport.x + (leftOverflow > 0 ? leftOverflow : -rightOverflow),
              }, { duration: 0 })
            }
          }
        }
      }
      return true
    } finally {
      programmaticFit.current = false
    }
  }, [readableRuntimeNodeIds])
  const focusExpandedCluster = useCallback(async (groupId: string) => {
    const instance = flowInstance.current
    if (!instance) return false
    const group = runtimeGraph.nodes.find((node) => node.id === groupId)
    if (!group) return false
    const clusterIds = new Set(runtimeChildNodeIds(runtimeGraph.nodes, group))
    programmaticFit.current = true
    try {
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve())))
      const visibleStageNodes = instance.getNodes().filter((node) => !node.hidden && node.type === 'stage')
      const clusterNodes = visibleStageNodes.filter((node) => clusterIds.has(node.id))
      if (!clusterNodes.length) return false
      // Fit only the revealed cluster. Adjacent mainline cards remain as
      // lightweight reading context but must not force the scientific detail
      // cards down to an unreadable zoom.
      const focusNodes = clusterNodes
      const graphRect = graphAreaRef.current?.getBoundingClientRect()
      if (graphRect) {
        const domNodes = new Map(
          [...document.querySelectorAll<HTMLElement>('.react-flow__node')]
            .map((element) => [element.getAttribute('data-id'), element] as const),
        )
        const rects = focusNodes
          .map((node) => domNodes.get(node.id)?.getBoundingClientRect())
          .filter((rect): rect is DOMRect => Boolean(rect && rect.width > 0 && rect.height > 0))
        if (rects.length) {
          const currentViewport = instance.getViewport()
          const currentZoom = Math.max(0.01, currentViewport.zoom)
          const measuredBounds = focusNodes.map((node) => {
            const element = domNodes.get(node.id)
            const rect = element?.getBoundingClientRect()
            const width = rect && rect.width > 0 ? rect.width / currentZoom : node.measured?.width ?? 280
            const height = rect && rect.height > 0 ? rect.height / currentZoom : node.measured?.height ?? 156
            return {
              left: node.position.x,
              top: node.position.y,
              right: node.position.x + width,
              bottom: node.position.y + height,
            }
          })
          const worldPadding = 28 / currentZoom
          const left = Math.min(...measuredBounds.map((rect) => rect.left)) - worldPadding
          const top = Math.min(...measuredBounds.map((rect) => rect.top)) - worldPadding
          const right = Math.max(...measuredBounds.map((rect) => rect.right)) + worldPadding
          const bottom = Math.max(...measuredBounds.map((rect) => rect.bottom)) + worldPadding
          const summaryRect = document.querySelector<HTMLElement>('.runtime-history-status')?.getBoundingClientRect()
          const leftBoundary = graphRect.left + 12
          const topBoundary = Math.max(graphRect.top + 12, (summaryRect?.bottom ?? graphRect.top) + 10)
          const rightBoundary = graphRect.right - 12
          const bottomBoundary = graphRect.bottom - 12
          const occupiedWidth = Math.max(1, right - left)
          const occupiedHeight = Math.max(1, bottom - top)
          const availableWidth = Math.max(1, rightBoundary - leftBoundary)
          const availableHeight = Math.max(1, bottomBoundary - topBoundary)
          const zoom = Math.min(1.35, Math.max(expandedClusterMinZoom, Math.min(availableWidth / occupiedWidth, availableHeight / occupiedHeight)))
          await instance.setViewport({
            x: leftBoundary - graphRect.left + (availableWidth - occupiedWidth * zoom) / 2 - left * zoom,
            y: topBoundary - graphRect.top + (availableHeight - occupiedHeight * zoom) / 2 - top * zoom,
            zoom,
          }, { duration: 240 })
          // React Flow can finish its own measurement one frame after the
          // explicit bounds pass. A bounded correction keeps the dashed frame
          // inside the real canvas without another full-graph fit.
          await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()))
        }
      }
      return true
    } finally {
      programmaticFit.current = false
    }
  }, [runtimeGraph.nodes])
  const fitReadableViewportRef = useRef(fitReadableViewport)
  fitReadableViewportRef.current = fitReadableViewport
  const focusExpandedClusterRef = useRef(focusExpandedCluster)
  focusExpandedClusterRef.current = focusExpandedCluster
  const scheduleInitialFit = useCallback(() => {
    const signature = layoutSignatureRef.current
    if (!signature || expandedClusterActive.current || userInteracted.current || initialFitInFlight.current || initialFitAttempts.current >= 8 || lastFittedLayoutSignature.current === signature) return
    if (!flowInstance.current) {
      initialFitPending.current = true
      return
    }
    if (initialFitTimer.current !== null) window.clearTimeout(initialFitTimer.current)
    initialFitPending.current = false
    initialFitTimer.current = window.setTimeout(() => {
      initialFitTimer.current = null
      if (userInteracted.current || initialFitInFlight.current) return
      const runId = currentFitRunId.current
      const signatureAtStart = layoutSignatureRef.current
      initialFitAttempts.current += 1
      initialFitInFlight.current = true
      void fitReadableViewportRef.current().then((succeeded) => {
        initialFitInFlight.current = false
        const layoutStillMatches = layoutSignatureRef.current === signatureAtStart
        if (succeeded && currentFitRunId.current === runId && !userInteracted.current && layoutStillMatches) {
          lastFittedLayoutSignature.current = signatureAtStart
        } else if (currentFitRunId.current === runId && !userInteracted.current) {
          scheduleInitialFitRef.current()
        }
      })
    }, 140)
  }, [])
  scheduleInitialFitRef.current = scheduleInitialFit
  useEffect(() => () => {
    if (initialFitTimer.current !== null) window.clearTimeout(initialFitTimer.current)
    if (clusterFocusTimer.current !== null) window.clearTimeout(clusterFocusTimer.current)
    if (expandedClusterFocusTimer.current !== null) window.clearTimeout(expandedClusterFocusTimer.current)
    if (clusterResizeTimer.current !== null) window.clearTimeout(clusterResizeTimer.current)
    if (expandedFrameRaf.current !== null) window.cancelAnimationFrame(expandedFrameRaf.current)
  }, [])
  useEffect(() => {
    const applyChineseControlLabels = () => {
      const root = document.querySelector('.graph-area')
      if (!root) return
      const labels: Array<[string, string]> = [
        ['.react-flow__controls-zoomin', '放大画布'],
        ['.react-flow__controls-zoomout', '缩小画布'],
      ]
      labels.forEach(([selector, label]) => {
        const control = root.querySelector<HTMLElement>(selector)
        control?.setAttribute('aria-label', label)
        control?.setAttribute('title', label)
      })
    }
    const frame = window.requestAnimationFrame(applyChineseControlLabels)
    return () => window.cancelAnimationFrame(frame)
  }, [detail.run.id, runtimeGraph.nodes.length])
  useEffect(() => {
    // Establish one readable initial window per run. Selecting a node, loading
    // more details, resizing the inspector, or expanding a group must not
    // reset the scientist's pan/zoom.
    currentFitRunId.current = detail.run.id
    lastFittedLayoutSignature.current = null
    initialFitAttempts.current = 0
    initialFitInFlight.current = false
    initialFitPending.current = true
    if (initialFitTimer.current !== null) window.clearTimeout(initialFitTimer.current)
    userInteracted.current = false
    clusterFocusUserMoved.current = false
    expandedClusterFocusedRevision.current = null
    if (expandedClusterFocusTimer.current !== null) window.clearTimeout(expandedClusterFocusTimer.current)
    expandedClusterFocusTimer.current = null
    scheduleInitialFit()
  }, [detail.run.id, scheduleInitialFit])
  useEffect(() => {
    scheduleInitialFit()
  }, [readableLayoutSignature, scheduleInitialFit])
  const expandedClusterSignature = useMemo(() => runtimeGraph.nodes
    .filter((node) => node.runtime?.expanded)
    .map((node) => node.id)
    .sort()
    .join('|'), [runtimeGraph.nodes])
  expandedClusterActive.current = Boolean(expandedClusterSignature)
  const expandedClusterNodeIdSet = useMemo(() => {
    const group = runtimeGraph.nodes.find((node) => node.runtime?.expanded)
    return group ? new Set(runtimeChildNodeIds(runtimeGraph.nodes, group)) : null
  }, [expandedClusterSignature, runtimeGraph.nodes])
  const measureExpandedClusterFrames = useCallback(() => {
    expandedFrameRaf.current = null
    const graphRect = graphAreaRef.current?.getBoundingClientRect()
    if (!graphRect) return
    const domNodes = new Map(
      [...document.querySelectorAll<HTMLElement>('.react-flow__node')]
        .map((element) => [element.getAttribute('data-id'), element] as const),
    )
    const nextFrames = runtimeGraph.nodes
      .filter((node) => node.runtime?.expanded)
      .flatMap<RuntimeClusterFrame>((group) => {
        const rects = runtimeChildNodeIds(runtimeGraph.nodes, group)
          .map((id) => domNodes.get(id)?.getBoundingClientRect())
          .filter((rect): rect is DOMRect => Boolean(rect && rect.width > 0 && rect.height > 0))
        if (!rects.length) return []
        const padding = 20
        const left = Math.min(...rects.map((rect) => rect.left)) - padding
        const top = Math.min(...rects.map((rect) => rect.top)) - padding
        const right = Math.max(...rects.map((rect) => rect.right)) + padding
        const bottom = Math.max(...rects.map((rect) => rect.bottom)) + padding
        return [{
          id: group.id,
          label: group.label,
          left: left - graphRect.left,
          top: top - graphRect.top,
          width: right - left,
          height: bottom - top,
        }]
      })
    setExpandedClusterFrames((previous) => {
      const same = previous.length === nextFrames.length
        && previous.every((frame, index) => {
          const next = nextFrames[index]
          return next && frame.id === next.id
            && Math.abs(frame.left - next.left) < 0.5
            && Math.abs(frame.top - next.top) < 0.5
            && Math.abs(frame.width - next.width) < 0.5
            && Math.abs(frame.height - next.height) < 0.5
        })
      return same ? previous : nextFrames
    })
  }, [runtimeGraph.nodes])
  const scheduleExpandedClusterMeasure = useCallback(() => {
    if (expandedFrameRaf.current !== null) return
    expandedFrameRaf.current = window.requestAnimationFrame(measureExpandedClusterFrames)
  }, [measureExpandedClusterFrames])
  useEffect(() => {
    const previous = previousGraphViewportSize.current
    previousGraphViewportSize.current = graphViewportSize
    if (!expandedClusterSignature || graphViewportSize.width <= 0 || graphViewportSize.height <= 0) return
    if (previous.width === 0 || (previous.width === graphViewportSize.width && previous.height === graphViewportSize.height)) return
    const groupId = expandedClusterSignature.split('|')[0]
    if (!groupId) return
    if (clusterResizeTimer.current !== null) window.clearTimeout(clusterResizeTimer.current)
    if (clusterFocusUserMoved.current) return
    clusterResizeTimer.current = window.setTimeout(() => {
      clusterResizeTimer.current = null
      void focusExpandedClusterRef.current(groupId).finally(scheduleExpandedClusterMeasure)
    }, 180)
    return () => {
      if (clusterResizeTimer.current !== null) window.clearTimeout(clusterResizeTimer.current)
      clusterResizeTimer.current = null
    }
  }, [expandedClusterSignature, graphViewportSize.height, graphViewportSize.width, scheduleExpandedClusterMeasure])
  useEffect(() => {
    scheduleExpandedClusterMeasure()
    return () => {
      if (expandedFrameRaf.current !== null) window.cancelAnimationFrame(expandedFrameRaf.current)
      expandedFrameRaf.current = null
    }
  }, [expandedClusterSignature, graphViewportSize.height, graphViewportSize.width, readableLayoutSignature, scheduleExpandedClusterMeasure])
  useEffect(() => {
    const requested = pendingClusterFocus.current
    if (requested === undefined) return
    pendingClusterFocus.current = undefined
    const requestId = clusterFocusRequestId.current
    if (clusterFocusTimer.current !== null) window.clearTimeout(clusterFocusTimer.current)
    const retryFocus = (attempt: number) => {
      if (requestId !== clusterFocusRequestId.current) return
      clusterFocusTimer.current = null
      const focus = requested === null ? fitReadableViewportRef.current() : focusExpandedClusterRef.current(requested)
      const revisionAtStart = expandedClusterLayoutRevisionRef.current
      void focus.then((succeeded) => {
        if (succeeded && requested !== null && revisionAtStart === expandedClusterLayoutRevisionRef.current && !clusterFocusUserMoved.current) {
          expandedClusterFocusedRevision.current = revisionAtStart
        }
        if (!succeeded && attempt < 4 && requestId === clusterFocusRequestId.current) {
          clusterFocusTimer.current = window.setTimeout(() => retryFocus(attempt + 1), 120)
        }
      }).finally(scheduleExpandedClusterMeasure)
    }
    clusterFocusTimer.current = window.setTimeout(() => retryFocus(0), 140)
    return () => {
      if (clusterFocusTimer.current !== null) window.clearTimeout(clusterFocusTimer.current)
      clusterFocusTimer.current = null
    }
  }, [expandedClusterSignature, scheduleExpandedClusterMeasure])
  const computedNodes = useMemo<Array<StageNode | LaneNode>>(() => {
    const readablePositions = readableRuntimeNodeIds.map((id) => readableRuntimePositions[id]).filter(Boolean)
    const mainY = Math.min(...readablePositions.map((position) => position.y), 220)
    const firstSpineX = Math.min(...readablePositions.map((position) => position.x), 190)
    const laneNodes: LaneNode[] = [
      { id: 'lane:main', type: 'lane', position: { x: Math.max(0, firstSpineX - 150), y: mainY }, initialWidth: 132, initialHeight: 47, data: { index: '01', label: '运行主线' }, draggable: false, selectable: false },
    ]
    return [
      ...laneNodes,
      ...runtimeGraph.nodes.map((stage): StageNode => {
        const basePosition = readableRuntimePositions[stage.id] ?? runtimeGraph.positions[stage.id] ?? { x: 0, y: 0 }
        const viewerKey = stage.runtime?.viewer_key
        const runtimeViewer = viewerKey
          ? detail.viewers?.[viewerKey] ?? Object.values(nodeDetails).find((source) => source.node_id === viewerKey)?.viewer ?? Object.values(nodeDetails).find((source) => source.viewers?.[viewerKey])?.viewers?.[viewerKey] ?? null
          : null
        const distributionKey = stage.runtime?.distribution_key ?? stage.runtime?.evidence_key
        const runtimeDistribution = distributionKey
          ? persistedDistributions[distributionKey] ?? distributionForStage(analysisSnapshot, detail, distributionKey)
          : undefined
        return ({
      id: stage.id,
      type: 'stage',
          position: basePosition,
      initialWidth: 280,
      initialHeight: stage.kind === 'structure' || stage.runtime?.has_viewer ? 250 : stage.id === 'targets' ? 224 : ['tool_group', 'event_group', 'batch_group', 'tool_summary_group', 'candidate_group'].includes(stage.runtime?.node_type ?? '') ? 214 : 156,
      hidden: !readableRuntimeNodeIdSet.has(stage.id) || Boolean(expandedClusterNodeIdSet && !expandedClusterNodeIdSet.has(stage.id)),
      data: {
        stage,
        branches: detail.branches,
        viewer: detail.viewers?.[stage.id] ?? runtimeViewer ?? (stage.kind === 'structure' ? detail.viewer : null),
        distribution: persistedDistributions[stage.id]
          ?? distributionForStage(analysisSnapshot, detail, stage.id)
          ?? runtimeDistribution
          ?? { label: '节点结果', unit: '条', values: [], source: '尚无数值结果', direction: 'neutral' },
        selected: selectionMode ? analysisSelection.includes(stage.id) : selectedStage === stage.id,
        onToggleGroup: handleToggleGroup,
      },
      draggable: false,
        })
      }),
    ]
  }, [analysisSelection, analysisSnapshot, detail, expandedClusterNodeIdSet, graphViewportSize.height, graphViewportSize.width, handleToggleGroup, nodeDetails, persistedDistributions, readableRuntimeNodeIds, readableRuntimeNodeIdSet, readableRuntimePositions, runtimeGraph, selectedStage, selectionMode])
  const [nodes, setNodes, onNodesChange] = useNodesState<StageNode | LaneNode>(computedNodes)
  useEffect(() => {
    setNodes((current) => {
      const measuredById = new Map(current.map((node) => [node.id, node.measured] as const))
      return computedNodes.map((node) => ({ ...node, measured: measuredById.get(node.id) ?? node.measured }))
    })
  }, [computedNodes, setNodes])
  const expandedClusterLayoutRevisionValue = useMemo(() => {
    const expandedGroups = runtimeGraph.nodes.filter((node) => node.runtime?.expanded)
    return expandedGroups.map((group) => {
      const members = runtimeChildNodeIds(runtimeGraph.nodes, group).map((id) => {
        const flowNode = nodes.find((node) => node.id === id)
        return {
          id,
          position: readableRuntimePositions[id] ?? runtimeGraph.positions[id],
          width: flowNode?.measured?.width,
          height: flowNode?.measured?.height,
        }
      })
      return expandedClusterLayoutRevision(group.id, members)
    }).sort().join('||')
  }, [nodes, readableRuntimePositions, runtimeGraph.nodes, runtimeGraph.positions])
  expandedClusterLayoutRevisionRef.current = expandedClusterLayoutRevisionValue
  useEffect(() => {
    const revision = expandedClusterLayoutRevisionValue
    if (!expandedClusterSignature || !revision) {
      expandedClusterFocusedRevision.current = null
      if (expandedClusterFocusTimer.current !== null) window.clearTimeout(expandedClusterFocusTimer.current)
      expandedClusterFocusTimer.current = null
      return
    }
    if (!shouldRefocusExpandedCluster(expandedClusterFocusedRevision.current, revision, clusterFocusUserMoved.current)) return
    const groupId = expandedClusterSignature.split('|')[0]
    if (!groupId) return
    if (expandedClusterFocusTimer.current !== null) window.clearTimeout(expandedClusterFocusTimer.current)
    let attempts = 0
    const runFocus = () => {
      expandedClusterFocusTimer.current = null
      if (clusterFocusUserMoved.current || expandedClusterFocusedRevision.current === revision) return
      if (expandedClusterLayoutRevisionRef.current !== revision) return
      if (expandedClusterFocusInFlight.current) {
        if (attempts < 6) expandedClusterFocusTimer.current = window.setTimeout(runFocus, 120)
        return
      }
      attempts += 1
      expandedClusterFocusInFlight.current = true
      void focusExpandedClusterRef.current(groupId).then((succeeded) => {
        expandedClusterFocusInFlight.current = false
        const latestRevision = expandedClusterLayoutRevisionRef.current
        if (succeeded && latestRevision === revision && !clusterFocusUserMoved.current) {
          expandedClusterFocusedRevision.current = revision
          return
        }
        if (!clusterFocusUserMoved.current && attempts < 6) {
          // Hydration can add a member while the first focus animation is in
          // flight. Re-run against the current measured cluster, but stop
          // after a bounded number of settling passes.
          expandedClusterFocusTimer.current = window.setTimeout(runFocus, 160)
        }
      }).finally(scheduleExpandedClusterMeasure)
    }
    expandedClusterFocusTimer.current = window.setTimeout(runFocus, 220)
    return () => {
      if (expandedClusterFocusTimer.current !== null) window.clearTimeout(expandedClusterFocusTimer.current)
      expandedClusterFocusTimer.current = null
    }
  }, [expandedClusterLayoutRevisionValue, expandedClusterSignature, scheduleExpandedClusterMeasure])
  const stageById = useMemo(() => Object.fromEntries(runtimeGraph.nodes.map((node) => [node.id, node])), [runtimeGraph.nodes])
  const readablePresentationEdges = useMemo<GraphEdgeDetail[]>(() => {
    const ordered = readableRuntimeNodeIds
      .map((id) => ({ id, position: readableRuntimePositions[id] }))
      .filter((item): item is { id: string; position: { x: number; y: number } } => Boolean(item.position))
      .filter((item) => runtimeGraph.nodes.find((node) => node.id === item.id)?.runtime?.node_type !== 'tool_summary')
      .sort((left, right) => left.position.x - right.position.x || left.position.y - right.position.y)
    return ordered.slice(1).flatMap((target, index) => {
      const source = ordered[index]
      if (!source || source.position.x === target.position.x) return []
      const explicit = runtimeGraph.edges.some((edge) => edge.source === source.id && edge.target === target.id)
      if (explicit) return []
      return [{
        source: source.id,
        target: target.id,
        label: null,
        rationale: '按当前运行中持久化观测的时间与画布顺序连接首屏阅读路径；只表示阅读顺序，不表示执行依赖。',
        provenance: 'derived' as const,
        relation_kind: 'sequence' as const,
      }]
    })
  }, [readableRuntimeNodeIds, readableRuntimePositions, runtimeGraph.edges, runtimeGraph.nodes])
  const expandedClusterBoundaryEdge = useMemo(() => {
    if (!expandedClusterNodeIdSet) return null
    return runtimeGraph.edges.find((edge) => {
      const sourceInCluster = expandedClusterNodeIdSet.has(edge.source)
      const targetInCluster = expandedClusterNodeIdSet.has(edge.target)
      return sourceInCluster !== targetInCluster && (sourceInCluster || targetInCluster)
    }) ?? null
  }, [expandedClusterNodeIdSet, runtimeGraph.edges])
  const visibleGraphEdges = useMemo(() => {
    const visibleRuntimeEdges = runtimeGraph.edges.filter((edge) => {
      if (!expandedClusterNodeIdSet) return readableRuntimeNodeIdSet.has(edge.source) && readableRuntimeNodeIdSet.has(edge.target)
      const sourceInCluster = expandedClusterNodeIdSet.has(edge.source)
      const targetInCluster = expandedClusterNodeIdSet.has(edge.target)
      return (sourceInCluster && targetInCluster) || edge === expandedClusterBoundaryEdge
    })
    const visibleReadingEdges = readablePresentationEdges.filter((edge) => {
      if (!expandedClusterNodeIdSet) return true
      return expandedClusterNodeIdSet.has(edge.source) && expandedClusterNodeIdSet.has(edge.target)
    })
    return [...visibleRuntimeEdges, ...visibleReadingEdges]
  }, [expandedClusterBoundaryEdge, expandedClusterNodeIdSet, readablePresentationEdges, readableRuntimeNodeIdSet, runtimeGraph.edges])
  const edges = useMemo<Edge[]>(() => visibleGraphEdges.map((edge, index) => {
    const source = stageById[edge.source] as GraphStage | undefined
    const active = source?.status === 'completed' || source?.status === 'running'
    const isSelected = selectedEdge?.source === edge.source && selectedEdge?.target === edge.target
    const isDependency = edge.relation_kind === 'dependency'
    const isRetry = edge.relation_kind === 'retry'
    const isFallback = edge.relation_kind === 'fallback'
    const isParallel = edge.relation_kind === 'parallel'
    const isSequence = edge.relation_kind === 'sequence'
    const isAssociation = edge.relation_kind === 'association' || edge.relation_kind === 'lineage' || edge.relation_kind === 'grouping'
    const stroke = isSelected ? '#2257ee' : isDependency ? '#64748b' : isRetry ? '#a8752d' : isFallback ? '#7862a5' : isParallel ? '#7c8ba1' : isSequence ? '#6f7f98' : isAssociation ? '#b6c0cf' : active ? '#8290a5' : '#9aa7b8'
    const isCausal = isDependency || isRetry || isFallback
    return {
      id: `${edge.source}-${edge.target}-${index}`,
      source: edge.source,
      target: edge.target,
      type: 'smoothstep',
      pathOptions: { offset: 22, stepPosition: 0.55 },
      animated: source?.status === 'running' && isCausal,
      label: edge.provenance === 'derived' && !isParallel && !isSequence ? undefined : edge.label ?? undefined,
      labelStyle: { fill: '#536176', fontSize: 11, fontWeight: 600 },
      labelBgStyle: { fill: '#ffffff', fillOpacity: 0.98 },
      labelBgPadding: [6, 4],
      labelBgBorderRadius: 5,
      data: { detail: edge },
      // Sequence edges are a reading aid only. Without a backend relation
      // field they must not resemble a causal dependency arrow.
      markerEnd: isCausal ? { type: MarkerType.ArrowClosed, width: 11, height: 11, color: stroke } : undefined,
      style: { stroke, strokeWidth: isSelected ? 2.6 : isCausal ? 2.2 : isSequence ? 1.4 : isAssociation ? 1.2 : 1.7, strokeDasharray: isParallel ? '3 5' : isSequence ? '7 7' : isAssociation || (edge.provenance === 'derived' && !isSequence) ? '4 5' : undefined },
    }
  }), [selectedEdge, stageById, visibleGraphEdges])
  const graphRenderKey = `${detail.run.id}:${runtimeGraph.nodes.filter((node) => node.runtime?.expanded).map((node) => node.id).sort().join(',')}`
  const handleNodeClick: NodeMouseHandler = (_, node) => {
    if (node.type !== 'stage') return
    markUserInteracted()
    const nodeType = node.type === 'stage' ? (node.data as StageNode['data']).stage.runtime?.node_type : undefined
    if (selectionMode) onToggleAnalysis(node.id)
    else if (nodeType && runtimeExpandableNodeTypes.has(nodeType)) {
      handleToggleGroup(node.id)
    } else onSelect(node.id)
  }
  const handleEdgeClick: EdgeMouseHandler = (_, edge) => {
    markUserInteracted()
    const edgeDetail = (edge.data as { detail?: GraphEdgeDetail } | undefined)?.detail
    if (edgeDetail) onSelectEdge(edgeDetail)
  }

  return (
    <div className="graph-area" ref={graphAreaRef}>
      <ReactFlow
        key={graphRenderKey}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        onEdgeClick={handleEdgeClick}
        onNodesChange={onNodesChange}
        onInit={(instance) => { flowInstance.current = instance; scheduleInitialFit() }}
        onMoveStart={() => {
          if (!programmaticFit.current) {
            userInteracted.current = true
            if (expandedClusterSignature) clusterFocusUserMoved.current = true
          }
        }}
        onMove={() => { scheduleExpandedClusterMeasure() }}
        fitViewOptions={{ padding: 0.12, minZoom: readableViewportMinZoom, maxZoom: 1 }}
        defaultViewport={{ x: 22, y: 68, zoom: 0.9 }}
        minZoom={0.2}
        maxZoom={1.35}
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
        elementsSelectable
      >
        <Background variant={BackgroundVariant.Dots} gap={26} size={1} color="#e8ebf1" />
        <Controls showInteractive={false} showFitView={false} position="bottom-left" />
      </ReactFlow>
      {expandedClusterFrames.map((frame) => (
        <div
          key={frame.id}
          className="runtime-expanded-cluster-frame"
          data-cluster-id={frame.id}
          style={{ left: frame.left, top: frame.top, width: frame.width, height: frame.height }}
          aria-hidden="true"
        >
          <span>{frame.label}</span>
        </div>
      ))}
      {runtimeGraph.eventWindow.mayBeTruncated && (
        <div className="runtime-history-status" role="status">
          <span>{runtimeGraph.eventWindow.remaining !== undefined
            ? `已加载 ${runtimeGraph.eventWindow.returned} 条 · 仍有至少 ${runtimeGraph.eventWindow.remaining} 条更早记录`
            : `已加载 ${runtimeGraph.eventWindow.returned} 条 · 已达最近 ${runtimeGraph.eventWindow.limit} 条窗口上限`}</span>
          {shouldFetchOlderObserverEvents(detail.event_window) && <button type="button" onClick={onLoadOlderEvents} disabled={eventHistoryLoading}>{eventHistoryLoading ? '正在加载…' : '加载更早事件'}</button>}
        </div>
      )}
      <button className="runtime-fit-button" aria-label="回到可读视图" title="回到可读视图" onClick={() => { void fitReadableViewport() }}>可读视图</button>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string | number | null | undefined }) {
  return <div className="fact"><span>{label}</span><strong>{value ?? '—'}</strong></div>
}

function CandidateCard({ candidate }: { candidate: CandidatePreview }) {
  const visibleMetrics = candidate.metrics.filter((metric) => metric.value !== null).slice(0, 3)
  const cohort = candidate.cohort === 'mature_core' ? '成熟核心' : candidate.cohort === 'exploration' ? '探索组' : '候选组'
  return (
    <div className="candidate-card">
      <div><span className="cohort-chip">候选预览 · {cohort}</span>{candidate.generation !== undefined && <small>{candidateGenerationLabel(candidate.generation)}</small>}<small>{candidate.length} 个氨基酸</small></div>
      <code>{candidate.sequence}</code>
      <div className="candidate-metrics">
        {visibleMetrics.map((metric) => <span key={metric.name}><b>{metric.value?.toFixed(2)}</b>{metricLabels[metric.name] ?? '计算指标'}</span>)}
      </div>
    </div>
  )
}

function formatMetricValue(value: number | null) {
  if (value === null) return '—'
  const magnitude = Math.abs(value)
  if (magnitude > 0 && (magnitude < 0.001 || magnitude >= 10000)) return value.toExponential(2)
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(value)
}

function MetricTable({ metrics }: { metrics: Record<string, MetricSummary> }) {
  const rows = Object.entries(metrics)
  if (!rows.length) return <div className="empty-evidence">数值评估记录：0</div>
  return (
    <div className="metric-table-wrap">
      <table className="metric-table">
        <thead><tr><th>指标</th><th>代表值</th><th>范围</th><th>样本 / 超出适用域</th></tr></thead>
        <tbody>
          {rows.map(([name, metric]) => {
            const isLogMic = name.includes('log10_mic') && metric.mean !== null
            const mean = isLogMic ? 10 ** metric.mean! : metric.mean
            const minimum = isLogMic && metric.min !== null ? 10 ** metric.min : metric.min
            const maximum = isLogMic && metric.max !== null ? 10 ** metric.max : metric.max
            return (
              <tr key={name}>
                <td><b>{metricLabels[name] ?? '计算指标'}</b><small>{isLogMic ? '微摩尔 · 对数预测值反变换' : metric.unit ?? '无单位'}</small></td>
                <td>{formatMetricValue(mean)}</td>
                <td>{formatMetricValue(minimum)} — {formatMetricValue(maximum)}</td>
                <td>{metric.count.toLocaleString()} / {metric.out_of_domain}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function recordValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function omitAuditHashes(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(omitAuditHashes)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !key.toLowerCase().includes('sha256'))
      .map(([key, nested]) => [key, omitAuditHashes(nested)]))
  }
  return value
}

const decisionStatusLabels: Record<string, string> = {
  mature_core: '成熟核心',
  promising_uncertain: '有潜力但不确定',
  rejected: '淘汰',
}

const decisionReasonLabels: Record<string, string> = {
  rank_instability: '排名不稳定',
  'label_gate_failed:macrel_hemolysis_label': '溶血标签门槛未通过 · MACREL',
  'label_gate_failed:toxinpred3_label': '毒性标签门槛未通过 · ToxinPred3',
  outside_frozen_structure_budget: '超出冻结的结构计算预算',
  selected_by_deterministic_nonweighted_pareto_front: '由确定性非加权 Pareto 前沿选中',
  selected_within_fixed_exploration_budget: '固定探索预算内入选',
}

const toolRelationLabels: Record<string, string> = { dependency: '依赖', retry: '重试/恢复', fallback: '回退', parallel: '并行观测组', association: '关联' }

function ToolAttemptDisclosure({ call }: { call: ToolAttempt }) {
  const context = call.structure_context?.[0]
  const inputs = recordValue(call.inputs)
  const parameters = recordValue(call.parameters)
  const lane = context?.lane === 'wrong_pocket' ? '错误口袋对照' : context?.lane === 'native' ? '原位' : context?.lane
  const facts = [
    ['靶点', context?.target],
    ['通道', lane],
    ['候选序列', context?.candidate_sequence],
    ['结果', context ? `${context.records} 条结构记录` : undefined],
    ['评分函数', parameters.score_function ?? inputs.score_function],
    ['构象数量', parameters.nstruct ?? inputs.nstruct],
    ['并行精修样本', parameters.parallel_decoys ?? inputs.parallel_decoys],
    ['证据等级', parameters.evidence_grade],
    ['短肽链', inputs.peptide_chain],
    ['插件', inputs.plugin],
    ['阶段', inputs.stage],
    ['候选数', parameters.score_all_candidate_count],
    ['随机种子', call.random_seed],
    ['模型', context ? undefined : call.model_uri],
  ].filter((item): item is [string, unknown] => item[1] !== null && item[1] !== undefined)
  return (
    <details className="tool-attempt">
      <summary>
        <span className={`attempt-state ${call.status}`} aria-hidden="true" />
        <span className="attempt-name">
          <b title={`${professionalTermHelp[call.tool_name] ?? '工具调用事实'} 原始键：${call.tool_name}`}>{displayToolName(call.tool_name)}</b>
          <small title={context ? `${context.target} · ${lane}` : call.tool_version}>
            {context ? `${context.target} · ${lane}` : inputs.plugin ? String(inputs.plugin) : '持久化运行'} <i /> 第 {call.attempt} 次尝试
          </small>
        </span>
        <span className="attempt-duration"><Clock3 />{call.duration_seconds === null ? '—' : call.duration_seconds < 0.1 ? '小于0.1秒' : `${call.duration_seconds.toFixed(1)}秒`}</span>
        <ChevronRight />
      </summary>
      <div className="attempt-body">
        {!!call.relations?.length && <div className="call-relation-list"><span>数据库显式关系</span>{call.relations.map((relation) => <b key={`${relation.direction}:${relation.related_call_id}:${relation.relation_type}`} title={`原始关系类型：${relation.relation_type}`}>{relation.direction === 'upstream' ? '上游' : '下游'} · {toolRelationLabels[relation.relation_type] ?? '依赖'} · {relation.related_call_id}</b>)}</div>}
        <div className="science-fact-grid">
          {facts.map(([label, value]) => <div key={label}><span>{label}</span><b title={String(value)}>{String(value).replaceAll('_', ' ')}</b></div>)}
        </div>
        <details className="json-disclosure"><summary><FileJson2 />完整输入与参数</summary><pre>{JSON.stringify(omitAuditHashes({ inputs: call.inputs, parameters: call.parameters }), null, 2)}</pre></details>
        {!!call.artifacts.length && (
          <details className="artifact-disclosure">
            <summary><FileJson2 /><span>证据文件</span><b>{call.artifacts.length}</b><ChevronRight /></summary>
            <div className="artifact-list">
              {call.artifacts.map((artifact) => (
                <a key={`${artifact.sha256}-${artifact.role}`} href={`${getConfiguredApiBase()}${artifact.url}`} target="_blank" rel="noreferrer">
                  <FileJson2 />
                  <span><b title={artifact.role}>{artifact.role}</b><small>{artifact.media_type} · {(artifact.size_bytes / 1024).toFixed(1)} KB</small></span>
                </a>
              ))}
            </div>
          </details>
        )}
      </div>
    </details>
  )
}

function ReasoningPanel({ nodeDetail }: { nodeDetail: NodeDetail }) {
  const reasons = Object.entries(nodeDetail.reasoning.reason_counts)
  const maximum = Math.max(1, ...reasons.map(([, count]) => count))
  const showAdmissionTrace = nodeDetail.node_id === 'admission' || nodeDetail.node_id === 'portfolio'
  return (
    <div className="reasoning-panel">
      <div className="analysis-kicker"><BrainCircuit />持久化分析</div>
      {nodeDetail.narrative.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
      {showAdmissionTrace && !!Object.keys(nodeDetail.reasoning.status_counts).length && (
        <div className="decision-stats">
          {Object.entries(nodeDetail.reasoning.status_counts).map(([status, count]) => <div key={status}><b>{count}</b><span>{decisionStatusLabels[status] ?? status.replaceAll('_', ' ')}</span></div>)}
        </div>
      )}
      {showAdmissionTrace && !!reasons.length && (
        <div className="reason-bars">
          {reasons.slice(0, 10).map(([reason, count]) => (
            <div key={reason}><span><b>{decisionReasonLabels[reason] ?? reason.replaceAll('_', ' ').replaceAll(':', ' · ')}</b><i>{count}</i></span><em><i style={{ width: `${(count / maximum) * 100}%` }} /></em></div>
          ))}
        </div>
      )}
      {nodeDetail.reasoning.decisions.map((decision, index) => (
        <details className="policy-disclosure" key={index}>
          <summary><Fingerprint />决策依据 <ChevronRight /></summary>
          <div className="decision-provenance">
            <Fact label="智能体" value={String(decision.agent_name ?? '—')} />
            <Fact label="决策类型" value={String(decision.type ?? '—')} />
          </div>
          <pre>{JSON.stringify(decision.policy ?? {}, null, 2)}</pre>
        </details>
      ))}
    </div>
  )
}

function QualityGatePanel({ gate }: { gate: GenerationQualityGate }) {
  const steps = qualityGateCountSteps(gate)
  return (
    <section className={`quality-gate-panel state-${gate.status}`}>
      <header>
        <span><ShieldCheck /><span><h3>新生序列质量门</h3><small title="规则序列生成器第二版：在生成与评估前执行确定性序列质量预筛。">规则序列生成器 · 第二版</small></span></span>
        <b>{qualityGateStatusLabel(gate)}</b>
      </header>
      <div className="quality-gate-counts" aria-label="新生序列质量门分层计数">
        {steps.map((step) => <div key={step.label}><span>{step.label}</span><strong>{step.value.toLocaleString()}</strong></div>)}
      </div>
      <div className="quality-gate-rules">
        {gate.rules.map((rule) => {
          const formatted = formatQualityGateRule(rule)
          return <div key={rule.metric_key}><span>{formatted.label}</span><strong>{formatted.value}</strong></div>
        })}
      </div>
      <footer><span>{gate.status === 'applied' ? '当前运行的持久化记录' : '当前运行尚无第二版预筛记录'}</span><b>数据库直读</b></footer>
    </section>
  )
}

function Inspector({ detail, stageId, analysisSnapshot, distributionOverride, onClose }: { detail: RunDetail; stageId: string; analysisSnapshot: AnalysisSnapshot | null; distributionOverride?: ResultDistributionData; onClose: () => void }) {
  const stage = detail.graph.nodes.find((item) => item.id === stageId) ?? detail.graph.nodes[0]
  const groupLabel = { inputs: '输入', design: '设计', evaluation: '评估', decision: '决策', structure: '结构', review: '评审', observed: '运行观测' }[stage.group]
  const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const qualityGate = nodeDetail?.generation_quality_gate ?? stage.generation_quality_gate ?? detail.generation_quality_gate
  const distribution = distributionOverride
    ?? distributionForStage(analysisSnapshot, detail, stageId)
    ?? { label: '节点结果', unit: '条', values: [], source: '尚无数值结果', direction: 'neutral' }

  useEffect(() => {
    const controller = new AbortController()
    const load = async () => {
      try {
        const cacheKey = observerNodeDetailCacheKey(getConfiguredApiBase(), detail.run.id, stageId)
        const cached = nodeDetailCache.get(cacheKey)
        if (cached && Date.now() - cached.fetchedAt < nodeDetailCacheTtlMs) {
          setNodeDetail(cached.detail)
          setDetailError(null)
          return
        }
        const response = await fetchJsonWithTimeout<NodeDetail>(`${getConfiguredApiBase()}/v1/observer/runs/${detail.run.id}/nodes/${encodeURIComponent(stageId)}`, observerNodeDetailTimeoutMs)
        const nodeDetail = response.payload
        nodeDetailCache.set(cacheKey, { detail: nodeDetail, fetchedAt: Date.now() })
        setNodeDetail(nodeDetail)
        setDetailError(null)
      } catch (cause) {
        if (!controller.signal.aborted) setDetailError(cause instanceof Error ? cause.message : '节点证据读取失败')
      }
    }
    setNodeDetail(null)
    void load()
    return () => { controller.abort() }
  }, [detail.run.id, stageId])

  return (
    <aside className="inspector expanded-inspector">
      <div className="inspector-header">
        <div><small>节点详情 · {groupLabel}</small><h2 title={professionalTermHelp[stage.id]}>{stage.label}</h2></div>
        <button className="icon-button" onClick={onClose}><X /></button>
      </div>
      {(stageId === 'boltz' || stageId === 'rosetta') && (() => {
        const structureViewer = detail.viewers?.[stageId] ?? detail.viewer
        return (
        <section className="viewer-section">
          <MoleculeViewer artifact={structureViewer} />
          {structureViewer && <div className="structure-caption"><code>{structureViewer.sequence}</code><span title="标准靶点名称保留数据库原始命名，以保证可追溯性。">{structureViewer.target_name} · {structureViewer.lane === 'native' ? '原位' : '错误口袋对照'} · 随机种子 {structureViewer.seed}</span></div>}
        </section>
        )
      })()}
      <section className={`scientific-summary grade-${stage.insight.grade}`}>
        <div>
          <small>{stage.insight.source === 'persisted_decision' ? '持久化智能体决策' : '数据库结果'}</small>
          <strong><i />{stage.insight.verdict}</strong>
        </div>
        <p>{stage.insight.reason}</p>
        <span>{stage.insight.facts.map((fact) => `${fact.label} ${fact.value}`).join(' · ')}</span>
      </section>
      {stageId === 'candidate_pool' && qualityGate && <QualityGatePanel gate={qualityGate} />}
      {distribution && <section className="inspector-distribution-section"><ResultDistribution data={distribution} /></section>}
      <section className="inspector-section evidence-overview">
        <div className="section-title"><h3>数据概览</h3><span className={`stage-badge ${stage.status}`}>{statusText[stage.status] ?? stage.status}</span></div>
        <div className="fact-grid four-up">
          <Fact label="持久化记录" value={stage.current.toLocaleString()} />
          <Fact label="计划总量" value={stage.total.toLocaleString()} />
          <Fact label="数据来源" value={stage.provenance === 'database' ? '数据库直读' : stage.provenance === 'derived' ? '证据推导' : '等待写入'} />
          <Fact label="更新时间" value={formatTime(detail.updated_at)} />
        </div>
        {stage.provenance === 'derived' && <p className="inference-note"><ScanSearch />进度来源：持久化证据与流程拓扑</p>}
      </section>
      {detailError && <div className="detail-loading error">{detailError}</div>}
      {!nodeDetail && !detailError && <div className="detail-loading"><RefreshCw className="spin" />正在组合节点证据…</div>}
      {nodeDetail && (
        <>
          <section className="inspector-section"><ReasoningPanel nodeDetail={nodeDetail} /></section>
          {!!nodeDetail.structure_results.length && (
            <details className="detail-disclosure" open>
              <summary><Route />结构结果分布 <span>{nodeDetail.structure_results.reduce((sum, row) => sum + row.records, 0)} 条</span><ChevronRight /></summary>
              <div className="structure-result-grid">{nodeDetail.structure_results.map((row) => <div key={`${row.target}-${row.lane}`}><b title={row.target}>{row.target}</b><span>{row.lane === 'native' ? '原位' : '错误口袋对照'}</span><strong>{row.records.toLocaleString()}</strong><small>{row.seeds} 个随机种子 · {row.kind === 'boltz_pose' ? '复合物构象' : '界面精修样本'}</small></div>)}</div>
            </details>
          )}
          {!!Object.keys(nodeDetail.metrics).length && <details className="detail-disclosure" open>
            <summary><Activity />计算指标 <span>{Object.keys(nodeDetail.metrics).length} 项</span><ChevronRight /></summary>
            <MetricTable metrics={nodeDetail.metrics} />
          </details>}
          {stageId === 'targets' && <details className="detail-disclosure" open><summary><Route />冻结靶点面板 <span>{detail.branches.length} 个靶点</span><ChevronRight /></summary><div className="detail-content">{detail.branches.map((branch) => <div className="branch-card target-branch-card" key={branch.key}><TargetGlyph /><div><b title="标准靶点名称保留数据库原始命名，以保证可追溯性。">{branch.target_name}</b><span title="生物学物种名称保留数据库原始命名，以保证可追溯性。">{branch.organism}</span><small>{branch.sequence_length} 个氨基酸 · 合格靶点 · {branch.accession}</small><details className="target-sequence"><summary>查看完整氨基酸序列 <ChevronRight /></summary><code>{branch.sequence}</code></details></div></div>)}</div></details>}
          {stageId === 'admission' && <details className="detail-disclosure"><summary><Layers3 />入选候选示例 <span>{detail.counts.admitted}</span><ChevronRight /></summary><div className="candidate-list detail-content">{detail.candidates.slice(0, 8).map((candidate) => <CandidateCard key={candidate.id} candidate={candidate} />)}</div></details>}
          {!!nodeDetail.calls.length && <details className="detail-disclosure" open>
            <summary><FileJson2 />工具运行记录 <span>{nodeDetail.calls.length}</span><ChevronRight /></summary>
            <div className="tool-attempt-list">
              {nodeDetail.calls.length ? nodeDetail.calls.slice(0, nodeDetail.calls.length > 8 ? 3 : 6).map((call) => <ToolAttemptDisclosure key={call.id} call={call} />) : <div className="empty-evidence">持久化工具调用：0</div>}
              {nodeDetail.calls.length > (nodeDetail.calls.length > 8 ? 3 : 6) && (
                <details className="remaining-attempts">
                  <summary>查看其余 {nodeDetail.calls.length - (nodeDetail.calls.length > 8 ? 3 : 6)} 次运行 <ChevronRight /></summary>
                  <div>{nodeDetail.calls.slice(nodeDetail.calls.length > 8 ? 3 : 6).map((call) => <ToolAttemptDisclosure key={call.id} call={call} />)}</div>
                </details>
              )}
            </div>
          </details>}
        </>
      )}
      <details className="detail-disclosure timeline-disclosure">
        <summary><Clock3 />数据库事件 <span>{detail.events.length}</span><ChevronRight /></summary>
        <div className="event-list detail-content">{detail.events.slice(0, 12).map((event) => <div className="event-row" key={event.sequence_no}><i /><div><b>{readableEventType(event.type, event.payload)}</b><span>{event.type} · {event.actor} · {formatTime(event.occurred_at)}</span></div></div>)}</div>
      </details>
    </aside>
  )
}

const relationKindLabels: Record<NonNullable<GraphEdgeDetail['relation_kind']>, string> = {
  dependency: '依赖', retry: '重试', fallback: '回退', parallel: '并行观测组', sequence: '观测先后', association: '关联', lineage: '父子谱系', grouping: '代际分组',
}

function EdgeInspector({ graph, edge, onClose }: { graph: RuntimeGraphModel; edge: GraphEdgeDetail; onClose: () => void }) {
  const source = graph.nodes.find((node) => node.id === edge.source)
  const target = graph.nodes.find((node) => node.id === edge.target)
  const relationLabel = edge.relation_kind ? relationKindLabels[edge.relation_kind] : '运行关系'
  const provenanceLabel = edge.relation_kind === 'sequence'
    ? '按持久化时间与事件序号排列；不代表依赖、重试或触发'
    : edge.relation_kind === 'parallel' && edge.provenance === 'derived'
    ? '基于观测时间区间重叠；不代表调度依赖'
    : edge.provenance === 'database' ? '关系来自显式数据库字段' : '关系来自图上观测或候选代际分组'
  return (
    <aside className="inspector edge-inspector">
      <div className="inspector-header"><div><small>运行关系 · {relationLabel} · {edge.provenance === 'database' ? '数据库显式字段' : '图上观测字段'}</small><h2>{edge.label ?? relationLabel}</h2></div><button className="icon-button" aria-label="关闭运行关系详情" onClick={onClose}><X /></button></div>
      <section className="edge-route"><div><span>{source?.label ?? edge.source}</span><small>{source ? `${source.current.toLocaleString()} 条记录` : '节点未返回'}</small></div><i><Route /></i><div><span>{target?.label ?? edge.target}</span><small>{target ? statusText[target.status] : '节点未返回'}</small></div></section>
      <section className="inspector-section">
        <div className="analysis-kicker"><BrainCircuit />决策上下文</div>
        <p className="edge-rationale">{edge.rationale}</p>
        <div className="runtime-provenance-chip">{provenanceLabel}</div>
      </section>
    </aside>
  )
}

function RuntimeInspector({ detail, nodeDetails, graph, nodeId, distribution, onClose, onToggleGroup, onLoadOlderCalls }: { detail: RunDetail; nodeDetails: Record<string, NodeDetail>; graph: RuntimeGraphModel; nodeId: string; distribution?: ResultDistributionData | null; onClose: () => void; onToggleGroup: (id: string) => void; onLoadOlderCalls: (stageId: string) => Promise<boolean> }) {
  const [loadingCallStageId, setLoadingCallStageId] = useState<string | null>(null)
  const node = graph.nodes.find((item) => item.id === nodeId)
  if (!node) return null
  const call = nodeId.startsWith('call:') ? graph.calls[nodeId.slice(5)] : undefined
  const event = nodeId.startsWith('event:') ? graph.events[nodeId.slice(6)] : undefined
  const candidate = nodeId.startsWith('candidate:') ? detail.candidates.find((item) => item.id === nodeId.slice(10)) : undefined
  const generation = nodeId.startsWith('generation:') ? nodeId.slice(11) : undefined
  const isPopulationSummary = node.runtime?.node_type === 'population_summary'
  const isStructureEvidence = node.runtime?.node_type === 'structure_evidence'
  const runtimeViewerKey = node.runtime?.viewer_key
  const runtimeViewer = runtimeViewerKey
    ? detail.viewers?.[runtimeViewerKey]
      ?? Object.values(nodeDetails).find((source) => source.node_id === runtimeViewerKey)?.viewer
      ?? Object.values(nodeDetails).find((source) => source.viewers?.[runtimeViewerKey])?.viewers?.[runtimeViewerKey]
      ?? (runtimeViewerKey === '__default__' ? detail.viewer : null)
    : null
  const isRuntimeGroup = Boolean(node.runtime?.node_type && runtimeExpandableNodeTypes.has(node.runtime.node_type))
  const isToolSummary = node.runtime?.node_type === 'tool_summary'
  const groupCallIds = isRuntimeGroup ? node.runtime?.child_ids ?? [] : []
  const groupEventIds = isRuntimeGroup ? node.runtime?.event_ids ?? [] : []
  const groupCandidateIds = node.runtime?.node_type === 'candidate_group' ? node.runtime?.child_ids ?? [] : []
  const groupCalls = groupCallIds.map((id) => graph.calls[id]).filter((item): item is ToolAttempt => Boolean(item))
  const groupEvents = groupEventIds.map((id) => graph.events[id]).filter((item): item is TimelineEvent => Boolean(item))
  const groupCandidates = groupCandidateIds.map((id) => detail.candidates.find((candidate) => candidate.id === id)).filter((item): item is CandidatePreview => Boolean(item))
  const summaryTools = node.runtime?.summary_tools ?? []
  const groupExpanded = [...groupCallIds.map((id) => `call:${id}`), ...groupEventIds, ...groupCandidateIds.map((id) => `candidate:${id}`), ...summaryTools.map((item) => `tool-summary:${encodeURIComponent(item.tool_name)}`)].some((id) => graph.nodes.some((item) => item.id === id))
  const callWindowSources = Object.entries(nodeDetails)
    .filter(([, source]) => Boolean(source.calls_window) || source.calls.length >= observerCallPageLimit)
    .map(([stageId, source]) => ({ stageId, source }))
  return (
    <aside className="inspector expanded-inspector runtime-inspector">
      <div className="inspector-header">
        <div><small>{isStructureEvidence ? '运行节点 · 结构证据' : isRuntimeGroup ? '运行节点 · 可追溯聚合' : isPopulationSummary ? '运行节点 · 种群口径' : '运行节点 · 数据库直读'}</small><h2 title={node.label}>{node.label}</h2></div>
        <button className="icon-button" aria-label="关闭运行节点详情" onClick={onClose}><X /></button>
      </div>
      <section className={`scientific-summary grade-${node.insight.grade}`}>
        <div><small>{node.runtime?.node_type === 'tool_call' ? '工具调用事实' : isRuntimeGroup ? '运行观测聚合事实' : isPopulationSummary ? '权威种群汇总事实' : node.runtime?.node_type === 'lifecycle_event' ? '生命周期事件事实' : '候选数据事实'}</small><strong><i />{node.insight.verdict}</strong></div>
        <p>{node.insight.reason}</p>
        <span>{node.insight.facts.map((fact) => `${fact.label} ${fact.value}`).join(' · ')}</span>
      </section>
      {distribution?.values.length ? <section className="inspector-distribution-section"><ResultDistribution data={distribution} /></section> : null}
      {isStructureEvidence && runtimeViewer && <section className="viewer-section runtime-viewer-detail">
        <MoleculeViewer artifact={runtimeViewer} />
        <div className="structure-caption"><code>{runtimeViewer.sequence}</code><span>{runtimeViewer.target_name} · {runtimeViewer.lane === 'native' ? '原位' : '错误口袋对照'} · viewer {runtimeViewerKey}</span></div>
      </section>}
      {node.runtime?.viewer_key && <p className="runtime-note runtime-viewer-note">结构证据映射：{node.runtime.viewer_mapping_basis ?? '后端 viewer 键'} · {node.runtime.viewer_key}</p>}
      {isRuntimeGroup && <section className="inspector-section runtime-group-section"><div className="section-title"><h3>{node.runtime?.node_type === 'tool_summary_group' ? '汇总工具明细' : node.runtime?.node_type === 'candidate_group' ? '代际预览明细' : '聚合明细'}</h3><button className="group-toggle" onClick={() => onToggleGroup(node.id)}>{groupExpanded ? '收起明细' : '展开明细'}</button></div><p className="runtime-note">{node.runtime?.node_type === 'tool_summary_group' ? '数据库状态汇总；逐次明细按工具与状态核对。' : node.runtime?.node_type === 'candidate_group' ? '按候选记录中明确的 generation 字段分组。' : '默认显示批次或连续观测的汇总事实；展开后可按时间查看工具调用与生命周期事件。'}</p><code className="runtime-raw-key">聚合依据：{node.runtime?.grouping_basis ?? '候选记录 generation 字段'}</code>{node.runtime?.viewer_key && <p className="runtime-note">结构证据：{node.runtime.viewer_mapping_basis ?? '后端 viewer 键'} · {node.runtime.viewer_key}</p>}<div className="runtime-group-list">{summaryTools.map((item) => <div key={item.tool_name}><span className="attempt-state pending" /><b>{item.display_name}</b><small>汇总 {item.summary_count} · 已映射 {item.materialized_count} · 尚缺 {item.missing_count}</small></div>)}{groupCalls.map((item) => <div key={item.id}><span className={`attempt-state ${item.status}`} /><b>尝试 {item.attempt}</b><small>{statusText[item.status] ?? item.status}</small></div>)}{groupEvents.map((item) => { const status = runtimeEventStatus(item); return <div key={`event:${item.sequence_no}`}><span className={`attempt-state ${status}`} /><b>事件 {item.sequence_no}</b><small>{readableEventType(item.type, item.payload)} · {statusText[status]} · {formatTime(item.occurred_at)}</small></div> })}{groupCandidates.map((item) => <div key={item.id}><span className="attempt-state pending" /><b>候选预览 {item.proposal_rank === null ? item.id.slice(0, 8) : `#${item.proposal_rank}`}</b><small>{item.length} 个氨基酸 · {item.parent_id ? '有父候选' : '未返回父候选'}</small></div>)}</div></section>}
      {isToolSummary && <section className="inspector-section runtime-group-section"><div className="section-title"><h3>汇总级工具证据</h3><span className="stage-badge pending">仅汇总</span></div><p className="runtime-note">数据库状态汇总；逐次明细未返回。</p><div className="runtime-group-list">{summaryTools.map((item) => <div key={item.tool_name}><span className="attempt-state pending" /><b>汇总数量 {item.summary_count}</b><small>已映射 {item.materialized_count} · 尚缺 {item.missing_count}</small></div>)}</div></section>}
      {isPopulationSummary && <section className="inspector-section"><div className="analysis-kicker"><Layers3 />种群口径</div><p className="runtime-note">数据库汇总计数；候选轨仅展示当前返回预览。</p><div className="fact-grid">{node.insight.facts.map((fact) => <Fact key={fact.label} label={fact.label} value={fact.value} />)}</div></section>}
      {call && <section className="inspector-section"><div className="section-title"><h3>工具调用与证据</h3><span className={`stage-badge ${node.status}`}>{statusText[call.status] ?? call.status}</span></div><ToolAttemptDisclosure call={call} /></section>}
      {callWindowSources.length > 0 && <section className="inspector-section call-window-section"><div className="section-title"><h3>工具调用窗口</h3><span className="stage-badge pending">按游标</span></div>{callWindowSources.map(({ stageId, source }) => { const label = nodeCallsWindowLabel(source); const window = source.calls_window; return <div className="call-window-row" key={stageId}><span>{stageId}</span><small>{label}</small>{window?.has_more && <button type="button" onClick={async () => { setLoadingCallStageId(stageId); await onLoadOlderCalls(stageId); setLoadingCallStageId(null) }} disabled={loadingCallStageId !== null}>{loadingCallStageId === stageId ? '读取中…' : '加载更早调用'}</button>}</div> })}</section>}
      {event && <section className="inspector-section"><div className="analysis-kicker"><Clock3 />事件 payload</div><div className="runtime-event-meta"><b>{event.actor}</b><span>序号 {event.sequence_no} · {formatTime(event.occurred_at)}</span></div><div className="runtime-raw-key">原始事件键：{event.type}</div><pre className="runtime-json">{JSON.stringify(event.payload, null, 2)}</pre></section>}
      {candidate && <section className="inspector-section"><div className="analysis-kicker"><GitBranch />候选预览记录</div><code className="runtime-sequence">{candidate.sequence}</code><div className="fact-grid"><Fact label="代际" value={candidate.generation ?? '—'} /><Fact label="父候选" value={candidate.parent_id ?? '未返回'} /><Fact label="生成调用" value={candidate.generator_call_id ?? '未返回'} /><Fact label="序列长度" value={candidate.length} /><Fact label="预览范围" value={node.runtime?.preview_index && node.runtime.preview_total !== null ? `${node.runtime.preview_index}/${node.runtime.preview_total}` : node.runtime?.preview_index ? `已返回第 ${node.runtime.preview_index} 条` : '当前返回记录'} /></div>{candidate.reasons.length > 0 && <div className="runtime-reasons"><span>后端返回原因（未用于状态推断）</span>{candidate.reasons.map((reason) => <b key={reason}>{reason}</b>)}</div>}</section>}
      {generation && <section className="inspector-section"><div className="analysis-kicker"><Layers3 />代际分组</div><p className="runtime-note">此节点由候选记录中明确的 <code>generation={generation}</code> 字段聚合而成；它不是预设阶段，也不代表执行依赖。</p></section>}
      {isToolSummary || node.runtime?.node_type === 'tool_summary_group' || isPopulationSummary ? <details className="inspector-section runtime-provenance detail-disclosure"><summary><Database />图构造契约 <span>数据缺口 {graph.gaps.length} 项</span><ChevronRight /></summary><div className="detail-content"><p>可见节点来自本次运行详情返回的真实记录与显式字段。</p><ul>{graph.gaps.slice(0, 5).map((gap) => <li key={gap}>{gap}</li>)}</ul></div></details> : <section className="inspector-section runtime-provenance"><div className="analysis-kicker"><Database />图构造契约</div><p>可见节点来自本次运行详情返回的工具调用、生命周期事件、候选记录和显式字段。未返回的依赖关系不在图中补画；关联边不表示因果。</p><ul>{graph.gaps.slice(0, 5).map((gap) => <li key={gap}>{gap}</li>)}</ul></section>}
    </aside>
  )
}

function TargetGlyph() {
  return <span className="target-glyph"><Box /></span>
}

function LoadingScreen({ error, onRetry, onOpenAnalysis }: { error: string | null; onRetry: () => void; onOpenAnalysis: () => void }) {
  return <div className="loading-screen">
    <div className="loading-mark"><FlaskConical /></div>
    <h2>{error ? '观察器接口暂时不可用' : '正在读取权威数据库…'}</h2>
    <p>{error ?? '同步实时运行记录'}</p>
    {error && <>
      <div className="service-status-grid" aria-label="实时服务状态">
        <div className="status-unavailable"><i /><b>观察器接口</b><span>不可达</span></div>
        <div className="status-unverified"><i /><b title="用于保存运行、候选与评测记录的权威数据库。">PostgreSQL 权威库</b><span>等待接口核验</span></div>
        <div className="status-unavailable"><i /><b title="用于追踪科学工作流进度的调度系统。">Temporal 可观测性</b><span>当前不可观测</span></div>
      </div>
      <small className="service-status-note">运行结论以 PostgreSQL 权威记录为准</small>
      <div className="loading-actions"><button onClick={onRetry}>重新读取</button><button className="primary" onClick={onOpenAnalysis}>查看冻结分析</button></div>
    </>}
  </div>
}

function DataConnectionDialog({ value, onClose, onSave }: { value: string; onClose: () => void; onSave: (value: string) => void }) {
  const [mode, setMode] = useState<'local' | 'custom'>(value ? 'custom' : 'local')
  const [customBase, setCustomBase] = useState(value || 'http://127.0.0.1:8081')
  const [testState, setTestState] = useState<'idle' | 'testing' | 'success' | 'error'>('idle')
  const [testMessage, setTestMessage] = useState('')
  const candidateBase = mode === 'local' ? '' : normalizeApiBase(customBase)

  const testConnection = async () => {
    setTestState('testing')
    setTestMessage('正在检查数据服务…')
    try {
      const response = await fetch(`${candidateBase}/healthz`, { headers: { Accept: 'application/json' } })
      if (!response.ok) throw new Error(`服务返回 ${response.status}`)
      const payload = await response.json() as { status?: string }
      if (payload.status !== 'ok') throw new Error('健康状态异常')
      setTestState('success')
      setTestMessage('观察器接口已响应；运行数据将在读取时核验。')
    } catch (cause) {
      setTestState('error')
      setTestMessage(cause instanceof Error && /^服务|^健康/.test(cause.message) ? cause.message : '无法连接此数据服务。')
    }
  }

  return createPortal(
    <div className="connection-dialog-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="connection-dialog" role="dialog" aria-modal="true" aria-labelledby="connection-dialog-title">
        <header><div><span className="connection-icon"><Database /></span><span><h2 id="connection-dialog-title">数据连接</h2><p>配置只读数据服务，不在浏览器保存数据库口令。</p></span></div><button aria-label="关闭数据连接设置" onClick={onClose}><X /></button></header>
        <div className="connection-options">
          <button className={mode === 'local' ? 'selected' : ''} onClick={() => { setMode('local'); setTestState('idle') }}>
            <i>{mode === 'local' && <span />}</i><span><b>本机默认</b><small>随开发命令自动启动</small><code>127.0.0.1:8081</code></span>
          </button>
          <button className={mode === 'custom' ? 'selected' : ''} onClick={() => { setMode('custom'); setTestState('idle') }}>
            <i>{mode === 'custom' && <span />}</i><span><b>自定义服务</b><small>连接其他只读数据接口</small><code>可配置地址</code></span>
          </button>
        </div>
        <label className="connection-field"><span>数据服务地址</span><input aria-label="数据服务地址" disabled={mode === 'local'} value={mode === 'local' ? 'http://127.0.0.1:8081' : customBase} onChange={(event) => { setCustomBase(event.target.value); setTestState('idle') }} /></label>
        <div className={`connection-test-state state-${testState}`}><span className="connection-status-dot" /><p>{testState === 'idle' ? '保存前可先检查服务与数据库是否可读。' : testMessage}</p></div>
        <footer><button onClick={testConnection} disabled={testState === 'testing'}><RefreshCw className={testState === 'testing' ? 'spin' : ''} />检查连接</button><span /><button onClick={onClose}>取消</button><button className="primary" disabled={mode === 'custom' && !normalizeApiBase(customBase)} onClick={() => onSave(candidateBase)}>保存并应用</button></footer>
      </section>
    </div>,
    document.body,
  )
}

export default function App() {
  const [activeView, setActiveView] = useState<'overview' | 'analysis' | 'evidence'>('overview')
  const [apiBase, setApiBase] = useState(readApiBase)
  const [connectionOpen, setConnectionOpen] = useState(false)
  const data = useRunData(true, apiBase)
  const [selectedStage, setSelectedStage] = useState<string | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<GraphEdgeDetail | null>(null)
  const [selectionMode, setSelectionMode] = useState(false)
  const [analysisSelection, setAnalysisSelection] = useState<string[]>([])
  const [analysisSnapshot, setAnalysisSnapshot] = useState<AnalysisSnapshot | null>(null)
  const [persistedDistributions, setPersistedDistributions] = useState<Record<string, ResultDistributionData>>({})
  const [expandedRuntimeGroups, setExpandedRuntimeGroups] = useState<Set<string>>(new Set())
  const [graphAvailableWidth, setGraphAvailableWidth] = useState(0)
  const toggleRuntimeGroup = useCallback((id: string) => {
    setExpandedRuntimeGroups((current) => nextExpandedRuntimeGroups(current, id))
  }, [])
  const structureRun = useMemo(() => data.runs.find((run) => run.structure_record_count > 0) ?? null, [data.runs])
  const runtimeGraph = useMemo(() => data.detail ? buildRuntimeGraph(data.detail, data.nodeDetails, { expandedGroups: expandedRuntimeGroups, availableWidth: graphAvailableWidth, sourceFetch: data.nodeDetailFetch }) : null, [data.detail, data.nodeDetails, data.nodeDetailFetch, expandedRuntimeGroups, graphAvailableWidth])
  useEffect(() => {
    setExpandedRuntimeGroups(new Set())
  }, [data.detail?.run.id])
  useEffect(() => {
    let cancelled = false
    const liveAnalyticsEnabled = import.meta.env.VITE_ANALYTICS_API_ENABLED === 'true'
    void loadAnalysisSnapshot({ runId: liveAnalyticsEnabled ? data.detail?.run.id : undefined, apiBase }).then((snapshot) => {
      if (!cancelled) setAnalysisSnapshot(snapshot)
    }).catch(() => {
      if (!cancelled) setAnalysisSnapshot(null)
    })
    return () => { cancelled = true }
  }, [apiBase, data.detail?.run.id])
  useEffect(() => {
    const detail = data.detail
    if (!detail) {
      setPersistedDistributions({})
      return
    }
    // Runtime overview nodes already carry their own observed facts. Do not
    // eagerly dereference every artifact just to fill the legacy distribution
    // widget; missing artifact storage must remain an explicit user action.
    setPersistedDistributions({})
  }, [apiBase, data.detail?.run.id])
  const selectedAnalysisNodes = useMemo(() => runtimeGraph?.nodes.filter((node) => analysisSelection.includes(node.id)) ?? [], [analysisSelection, runtimeGraph])
  const toggleAnalysisNode = (id: string) => setAnalysisSelection((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])
  return (
    <div className="app-shell">
      <div className="topbar"><button><ArrowLeft /></button><div className="brand"><span><FlaskConical /></span>AMPgent <i>科学分析</i></div><button className={`source-state ${activeView === 'overview' && (data.error || data.detailSyncError) ? 'has-error' : ''}`} onClick={() => setConnectionOpen(true)} title="查看或修改只读数据连接"><Database /><span>{activeView !== 'overview' ? '分析数据 · 只读' : data.detailSyncError ?? (data.syncingStale ? '上次读取 · 正在同步' : data.detail && data.detail.run.id === data.selectedId ? data.detail.source === 'postgresql' ? '数据库已连接' : '验收数据 · 只读夹具' : data.error ? '观察器不可用' : data.runs.length > 0 ? '轮次已读取 · 正在读取详情' : '正在连接')}</span><span className="live-dot" /><Settings2 /></button></div>
      <div className="workspace">
        <Sidebar
          runs={data.runs}
          selectedId={data.selectedId}
          graphObservedCalls={runtimeGraph?.stats.observedCalls ?? null}
          structureRun={structureRun}
          activeView={activeView}
          onView={(view) => { setActiveView(view); setSelectedStage(null); setSelectedEdge(null) }}
          onSelect={(id) => { data.setSelectedId(id); setSelectedStage(null); setSelectedEdge(null); setAnalysisSelection([]); setSelectionMode(false) }}
          onOpenStructureEvidence={() => {
            if (!structureRun) return
            data.setSelectedId(structureRun.id)
            setActiveView('overview')
            setSelectedStage('boltz')
            setSelectedEdge(null)
            setAnalysisSelection([])
            setSelectionMode(false)
          }}
        />
        {activeView === 'analysis' ? (
          <AnalysisDashboard detail={data.detail} seedNodeIds={analysisSelection} apiBase={apiBase} />
        ) : activeView === 'evidence' ? (
          <EvidenceDashboard runId={data.detail?.run.id} />
        ) : data.detail && data.detail.run.id === data.selectedId && !data.loading ? (
          <>
            <main className="main-canvas">
              <CanvasHeader
                detail={data.detail}
                refreshing={data.refreshing}
                syncingStale={data.syncingStale}
                detailSyncError={data.detailSyncError}
                selectionMode={selectionMode}
                selectedCount={analysisSelection.length}
                onRefresh={data.refresh}
                onToggleSelection={() => {
                  setSelectionMode((value) => !value)
                  setSelectedStage(null)
                  setSelectedEdge(null)
                }}
              />
              <GraphView
                detail={data.detail}
                nodeDetails={data.nodeDetails}
                runtimeGraph={runtimeGraph!}
                 analysisSnapshot={analysisSnapshot}
                 persistedDistributions={persistedDistributions}
                selectedStage={selectedStage}
                selectedEdge={selectedEdge}
                selectionMode={selectionMode}
                analysisSelection={analysisSelection}
                onSelect={(id) => { setSelectedStage(id); setSelectedEdge(null) }}
                onToggleAnalysis={toggleAnalysisNode}
                onSelectEdge={(edge) => { setSelectedEdge(edge); setSelectedStage(null) }}
                onToggleGroup={toggleRuntimeGroup}
                onAvailableWidthChange={setGraphAvailableWidth}
                onLoadOlderEvents={data.loadOlderEvents}
                eventHistoryLoading={data.eventHistoryLoading}
              />
              {selectionMode && (
                <div className="analysis-selection-bar">
                  <div className="selection-summary">
                    <ChartNoAxesCombined />
                    <span><b>组合分析</b><small>{analysisSelection.length ? '已按节点语义准备分析条件' : '选择需要联合分析的节点'}</small></span>
                  </div>
                  <div className="selection-chips">
                    {selectedAnalysisNodes.map((node) => <button key={node.id} onClick={() => toggleAnalysisNode(node.id)}>{node.label}<X /></button>)}
                    {!selectedAnalysisNodes.length && <span>可连续选择多张流程卡片</span>}
                  </div>
                  {!!analysisSelection.length && <button className="clear-selection" onClick={() => setAnalysisSelection([])}>清除</button>}
                  <button className="build-analysis" disabled={!analysisSelection.length} onClick={() => { setActiveView('analysis'); setSelectionMode(false) }}>生成分析卡片</button>
                </div>
              )}
            </main>
            {selectedStage && <RuntimeInspector
              detail={data.detail}
              nodeDetails={data.nodeDetails}
              graph={runtimeGraph!}
              nodeId={selectedStage}
              distribution={(() => {
                const selectedNode = runtimeGraph!.nodes.find((node) => node.id === selectedStage)
                const distributionKey = selectedNode?.runtime?.distribution_key ?? selectedNode?.runtime?.evidence_key ?? selectedStage
                return persistedDistributions[selectedStage]
                  ?? persistedDistributions[distributionKey]
                  ?? distributionForStage(analysisSnapshot, data.detail, selectedStage)
                  ?? distributionForStage(analysisSnapshot, data.detail, distributionKey)
              })()}
              onClose={() => setSelectedStage(null)}
              onToggleGroup={toggleRuntimeGroup}
              onLoadOlderCalls={data.loadOlderNodeCalls}
            />}
            {selectedEdge && <EdgeInspector graph={runtimeGraph!} edge={selectedEdge} onClose={() => setSelectedEdge(null)} />}
          </>
        ) : (
          <main className="main-canvas"><LoadingScreen error={data.error} onRetry={() => { void data.retry() }} onOpenAnalysis={() => setActiveView('analysis')} /></main>
        )}
      </div>
      {connectionOpen && <DataConnectionDialog value={apiBase} onClose={() => setConnectionOpen(false)} onSave={(value) => { window.localStorage.setItem(connectionStorageKey, value); setApiBase(value); setConnectionOpen(false) }} />}
    </div>
  )
}
