import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, ChevronLeft, ChevronRight, FlaskConical, RefreshCw, ShieldCheck, Star } from 'lucide-react'
import { loadAnalysisSnapshot, type AnalysisSnapshot, type SnapshotCandidate } from './dataKernel'
import './analysis-dashboard.css'

const activityKeys = ['macrel_amp_probability', 'amp_read_log10_mic_um', 'llamp_log10_mic_um', 'activity']
const safetyKeys = ['macrel_hemolysis_probability', 'toxinpred3_hybrid_score', 'safety']

function value(candidate: SnapshotCandidate, keys: string[]) {
  for (const key of keys) {
    const metric = candidate.metrics[key]
    if (metric?.value !== null && metric?.value !== undefined && metric.status === 'succeeded') return metric.value
  }
  return null
}

function displayValue(candidate: SnapshotCandidate, keys: string[], digits = 3) {
  const metric = keys.map((key) => candidate.metrics[key]).find((item) => item?.value !== null && item?.value !== undefined && item.status === 'succeeded')
  if (!metric) return '未评估'
  return `${Number(metric.value).toFixed(digits)}${metric.unit ? ` ${metric.unit}` : ''}`
}

function maturityRank(status: string) {
  return status === 'mature_core' ? 0 : status === 'candidate_pool' ? 1 : status === 'safety_pass' ? 2 : status === 'rejected' ? 4 : 3
}

function compareCandidates(left: SnapshotCandidate, right: SnapshotCandidate) {
  const leftActivity = value(left, activityKeys)
  const rightActivity = value(right, activityKeys)
  const activityKey = activityKeys.find((key) => left.metrics[key]?.value !== null || right.metrics[key]?.value !== null)
  const activityOrder = activityKey === 'macrel_amp_probability'
    ? (rightActivity === null ? 1 : leftActivity === null ? -1 : rightActivity - leftActivity)
    : (leftActivity === null ? 1 : rightActivity === null ? -1 : leftActivity - rightActivity)
  const leftSafety = value(left, safetyKeys)
  const rightSafety = value(right, safetyKeys)
  return maturityRank(left.admission.status) - maturityRank(right.admission.status)
    || (left.admission.paretoFront ?? Number.MAX_SAFE_INTEGER) - (right.admission.paretoFront ?? Number.MAX_SAFE_INTEGER)
    || activityOrder
    || (leftSafety === null ? 1 : rightSafety === null ? -1 : leftSafety - rightSafety)
    || Number(right.admission.structureEligible) - Number(left.admission.structureEligible)
    || (left.proposalRank === null ? 1 : right.proposalRank === null ? -1 : left.proposalRank - right.proposalRank)
    || left.id.localeCompare(right.id)
}

function sourceLabel(candidate: SnapshotCandidate) {
  return candidate.originSet.length ? candidate.originSet.join('、') : '未评估'
}

export function BestPeptideDashboard({ runId }: { runId?: string }) {
  const [snapshot, setSnapshot] = useState<AnalysisSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [page, setPage] = useState(0)
  const pageSize = 8

  useEffect(() => {
    let cancelled = false
    setSnapshot(null)
    setError(null)
    const liveAnalyticsEnabled = import.meta.env.VITE_ANALYTICS_API_ENABLED === 'true'
    void loadAnalysisSnapshot({ runId: liveAnalyticsEnabled ? runId : undefined }).then((value) => {
      if (!cancelled) setSnapshot(value)
    }).catch(() => {
      if (!cancelled) setError('最佳短肽快照校验失败')
    })
    return () => { cancelled = true }
  }, [revision, runId])

  const candidates = useMemo(() => snapshot ? [...snapshot.candidates].sort(compareCandidates) : [], [snapshot])
  const pageCount = Math.max(1, Math.ceil(candidates.length / pageSize))
  const safePage = Math.min(page, pageCount - 1)
  const visibleCandidates = candidates.slice(safePage * pageSize, (safePage + 1) * pageSize)

  return (
    <section className="analysis-page best-peptide-page">
      <header className="analysis-page-header">
        <div className="analysis-heading">
          <div className="analysis-eyebrow"><Star /> 候选组合 <span>只读数据</span></div>
          <h1>最佳短肽</h1>
          <p>{snapshot ? `按成熟度与已有科学字段排序 · ${candidates.length.toLocaleString()} 条可展示候选` : error ?? '正在校验最佳短肽快照…'}</p>
        </div>
        <div className="analysis-header-actions">
          <span className={`fixture-badge ${snapshot ? 'verified' : ''}`}><ShieldCheck />{snapshot ? '真实数据已校验' : '正在校验'}</span>
          <button onClick={() => setRevision((value) => value + 1)}><RefreshCw />重新读取</button>
        </div>
      </header>
      {!snapshot ? (
        <div className="snapshot-state-panel"><FlaskConical /><b>{error ?? '正在读取只读数据'}</b><span>{error ? '未显示任何候选数据，请重新校验发布快照。' : '校验候选记录、评分覆盖与结构资格。'}</span>{error && <button onClick={() => setRevision((value) => value + 1)}>重试</button>}</div>
      ) : (
        <>
          <div className="best-peptide-table-card">
            <header><div><CheckCircle2 /><span><b>候选短肽列表</b><small>排序依据：成熟度 · Pareto · 活性 · 安全性 · 结构资格</small></span></div><span>只读</span></header>
            <div className="best-peptide-table-scroll"><table><thead><tr><th>序列</th><th>来源</th><th>活性</th><th>安全性</th><th>净电荷</th><th>结构资格</th><th>成熟度</th></tr></thead><tbody>
              {visibleCandidates.map((candidate) => (
                <tr key={candidate.id}>
                  <td><code>{candidate.sequence}</code><small>{candidate.proposalRank === null ? '无提案排名' : `提案 #${candidate.proposalRank}`}</small></td>
                  <td>{sourceLabel(candidate)}</td>
                  <td>{displayValue(candidate, activityKeys)}</td>
                  <td>{displayValue(candidate, safetyKeys)}</td>
                  <td>{displayValue(candidate, ['net_charge_ph7_4'], 2)}</td>
                  <td><span className={candidate.admission.structureEligible ? 'best-peptide-yes' : 'best-peptide-no'}>{candidate.admission.structureEligible ? '合格' : '未合格'}</span></td>
                  <td><b>{candidate.admission.status}</b>{candidate.admission.paretoFront !== null && <small>Pareto #{candidate.admission.paretoFront}</small>}</td>
                </tr>
              ))}
            </tbody></table></div>
            <div className="evidence-table-pagination"><span>第 {safePage + 1} / {pageCount} 页 · 共 {candidates.length} 条候选</span><div className="table-pagination"><button aria-label="上一页最佳短肽" disabled={safePage === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft /></button><button aria-label="下一页最佳短肽" disabled={safePage >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}><ChevronRight /></button></div></div>
          </div>
          <footer className="analysis-provenance-bar"><div><CheckCircle2 /><span><b>来源</b> {snapshot.source === 'analytics_api' ? '分析接口只读' : '冻结分析快照'}</span><span><b>候选</b> 已返回列表</span></div><p>按快照已有字段排序 · 未评估字段显示“未评估”</p></footer>
        </>
      )}
    </section>
  )
}
