import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, ChevronLeft, ChevronRight, FlaskConical, RefreshCw, ShieldCheck, Star } from 'lucide-react'
import { loadAnalysisSnapshot, type AnalysisSnapshot, type SnapshotCandidate } from './dataKernel'
import { bestPeptideMetricKeys, compareBestPeptides, displayMetric, maturityLabel } from './bestPeptideRules'
import './analysis-dashboard.css'

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

  const candidates = useMemo(() => snapshot ? [...snapshot.candidates].sort(compareBestPeptides) : [], [snapshot])
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
            <header><div><CheckCircle2 /><span><b>候选短肽列表</b><small>排序依据：成熟度 · Pareto · 结构资格 · 提案排名</small></span></div><span>只读</span></header>
            <div className="best-peptide-table-scroll"><table><thead><tr><th>序列</th><th>来源</th><th>Macrel 抗菌概率</th><th>Macrel 溶血风险</th><th>毒性风险</th><th>净电荷</th><th>结构资格</th><th>成熟度</th></tr></thead><tbody>
              {visibleCandidates.map((candidate) => (
                <tr key={candidate.id}>
                  <td><code>{candidate.sequence}</code><small>{candidate.proposalRank === null ? '无提案排名' : `提案 #${candidate.proposalRank}`}</small></td>
                  <td>{sourceLabel(candidate)}</td>
                  <td>{displayMetric(candidate, bestPeptideMetricKeys.activity)}</td>
                  <td>{displayMetric(candidate, bestPeptideMetricKeys.hemolysis)}</td>
                  <td>{displayMetric(candidate, bestPeptideMetricKeys.toxicity)}</td>
                  <td>{displayMetric(candidate, bestPeptideMetricKeys.netCharge, 2)}</td>
                  <td><span className={candidate.admission.structureEligible ? 'best-peptide-yes' : 'best-peptide-no'}>{candidate.admission.structureEligible ? '合格' : '未合格'}</span></td>
                  <td><b>{maturityLabel(candidate.admission.status)}</b>{candidate.admission.paretoFront !== null && <small>Pareto #{candidate.admission.paretoFront}</small>}</td>
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
