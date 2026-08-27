import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, DatabaseZap, FlaskConical, RefreshCw, ShieldCheck } from 'lucide-react'
import { loadAnalysisSnapshot, type AnalysisSnapshot } from './dataKernel'
import './analysis-dashboard.css'

const metricLabels: Record<string, { label: string; help: string; method: string }> = {
  amp_read_log10_mic_um: { label: 'AMP read 抑菌浓度', help: 'AMP read 用于交叉复核候选短肽的抗菌活性预测。', method: 'AMP read' },
  llamp_log10_mic_um: { label: 'LLAMP 抑菌浓度', help: 'LLAMP 提供按物种条件化的抑菌浓度软估计。', method: 'LLAMP' },
  macrel_amp_probability: { label: 'Macrel 抗菌概率', help: 'Macrel 是短肽抗菌活性与溶血风险分类模型。', method: 'Macrel' },
  macrel_hemolysis_probability: { label: 'Macrel 溶血概率', help: 'Macrel 是短肽抗菌活性与溶血风险分类模型。', method: 'Macrel' },
  toxinpred3_hybrid_score: { label: 'ToxinPred3 毒性评分', help: 'ToxinPred3 用于预测短肽毒性风险。', method: 'ToxinPred3' },
  net_charge_ph7_4: { label: '生理酸碱度净电荷', help: '按生理酸碱度估算的序列净电荷。', method: '序列理化计算' },
  hydrophobic_moment_eisenberg: { label: '疏水矩', help: '衡量短肽两亲性空间分布的序列描述符。', method: '序列理化计算' },
  hydrophobic_ratio_modlamp: { label: '疏水残基比例', help: '序列中疏水残基所占比例。', method: '序列理化计算' },
  maximum_hydrophobic_run: { label: '最长连续疏水片段', help: '序列中连续疏水残基的最大长度。', method: '序列理化计算' },
  macrel_hemolysis_label: { label: 'Macrel 溶血分类', help: 'Macrel 输出的溶血风险分类结果。', method: 'Macrel' },
  toxinpred3_label: { label: 'ToxinPred3 毒性分类', help: 'ToxinPred3 输出的毒性分类结果。', method: 'ToxinPred3' },
}

function methodValue(method: Record<string, unknown> | undefined, key: string) {
  const value = method?.[key]
  return typeof value === 'string' && value.length ? value : '未记录'
}

function methodVersion(method: Record<string, unknown> | undefined) {
  const value = methodValue(method, 'toolVersion')
  if (value === '未记录') return value
  const date = value.match(/\d{4}\.\d{2}\.\d{2}/)?.[0]
  if (date) return `版本 ${date}`
  const semantic = value.match(/v?\d+\.\d+(?:\.\d+)?/)?.[0]?.replace(/^v/, '')
  return semantic ? `版本 ${semantic}` : '版本已记录'
}

function warningLabel(warning: string) {
  if (warning.includes('Source run status is cancelled')) return '源轮次已取消；序列生成、评分与候选决策在下游取消前已经完成。'
  if (warning.includes('Structure and final portfolio stages are incomplete')) return '结构阶段与最终候选组合尚未完成，不能从本快照推断最终科学结论。'
  if (warning.includes('frozen release snapshot')) return '当前数据是发布冻结快照，不会自动反映 PostgreSQL 中之后写入的新运行。'
  return warning
}

export function EvidenceDashboard({ runId }: { runId?: string }) {
  const [snapshot, setSnapshot] = useState<AnalysisSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [page, setPage] = useState(0)
  const pageSize = 6

  useEffect(() => {
    let cancelled = false
    setError(null)
    const liveAnalyticsEnabled = import.meta.env.VITE_ANALYTICS_API_ENABLED === 'true'
    void loadAnalysisSnapshot({ runId: liveAnalyticsEnabled ? runId : undefined }).then((value) => {
      if (!cancelled) setSnapshot(value)
    }).catch(() => {
      if (!cancelled) setError('证据快照校验失败')
    })
    return () => { cancelled = true }
  }, [revision, runId])

  useEffect(() => { setPage(0) }, [snapshot?.snapshotId])
  const metricEntries = snapshot ? Object.entries(snapshot.metricMethods) : []
  const pageCount = Math.max(1, Math.ceil(metricEntries.length / pageSize))
  const safePage = Math.min(page, pageCount - 1)
  const visibleMetrics = metricEntries.slice(safePage * pageSize, (safePage + 1) * pageSize)

  return (
    <section className="analysis-page evidence-page">
      <header className="analysis-page-header">
        <div className="analysis-heading">
          <div className="analysis-eyebrow"><DatabaseZap /> 只读证据索引 <span>可追溯</span></div>
          <h1>证据库</h1>
          <p>{snapshot ? `${snapshot.summary.observedEvaluations.toLocaleString()} 项评分证据 · ${Object.keys(snapshot.metricMethods).length} 种指标 · ${snapshot.summary.uniqueCandidates.toLocaleString()} 条候选` : error ?? '正在校验证据快照…'}</p>
        </div>
        <div className="analysis-header-actions">
          <span className={`fixture-badge ${snapshot ? 'verified' : ''}`}><ShieldCheck />{snapshot ? '完整性已校验' : '正在校验'}</span>
          <button onClick={() => setRevision((value) => value + 1)}><RefreshCw />重新读取</button>
        </div>
      </header>
      {!snapshot ? (
        <div className="snapshot-state-panel"><FlaskConical /><b>{error ?? '正在读取只读数据'}</b><span>{error ? '请重新读取已校验的发布快照。' : '校验记录数量、覆盖率与传输完整性。'}</span>{error && <button onClick={() => setRevision((value) => value + 1)}>重试</button>}</div>
      ) : (
        <>
          <div className="evidence-summary-grid">
            <div><span>评分证据</span><strong>{snapshot.summary.observedEvaluations.toLocaleString()}</strong><small>覆盖 {snapshot.coverage.observed.toLocaleString()} / {snapshot.coverage.expected.toLocaleString()}</small></div>
            <div><span>评分指标</span><strong>{Object.keys(snapshot.metricMethods).length}</strong><small>数值与分类结果</small></div>
            <div><span>唯一候选</span><strong>{snapshot.summary.uniqueCandidates.toLocaleString()}</strong><small>来自 {new Set(snapshot.occurrences.map((item) => item.generator)).size} 个生成模型</small></div>
            <div><span>分布外证据</span><strong>{snapshot.coverage.outOfDomain.toLocaleString()}</strong><small>适用域审查结果</small></div>
          </div>
          <div className="evidence-trace-grid">
            <aside className="evidence-warning-card wide">
              <header><AlertTriangle /><span><b>可信边界</b><small>当前数据可支持的结论范围</small></span></header>
              <ul>{snapshot.warnings.map((warning) => <li key={warning}>{warningLabel(warning)}</li>)}</ul>
            </aside>
          </div>
          <div className="evidence-table-card">
            <header><div><CheckCircle2 /><span><b>模型与指标记录</b><small>工具版本、覆盖率与读取状态</small></span></div><span>只读</span></header>
            <table><thead><tr><th>指标</th><th>方法身份</th><th>证据数量</th><th>候选覆盖</th><th>状态</th></tr></thead><tbody>
              {visibleMetrics.map(([key, methods]) => {
                const descriptor = metricLabels[key] ?? { label: key, help: '数据库中记录的科学评分指标。', method: '数据库记录' }
                const count = snapshot.candidates.filter((candidate) => candidate.metrics[key]?.status === 'succeeded').length
                const method = methods[0]
                return <tr key={key}>
                  <td><b title={descriptor.help}>{descriptor.label}</b><small>{methods.length} 个方法记录</small></td>
                  <td><div className="evidence-method"><b title={descriptor.help}>{descriptor.method}</b><span>{methodVersion(method)}</span></div></td>
                  <td>{count.toLocaleString()} 项</td><td>{((count / snapshot.summary.uniqueCandidates) * 100).toFixed(1)}%</td><td><span className="evidence-status-ok"><i />已记录</span></td>
                </tr>
              })}
            </tbody></table>
            <div className="evidence-table-pagination"><span>第 {safePage + 1} / {pageCount} 页 · 共 {metricEntries.length} 种指标</span><div className="table-pagination"><button aria-label="上一页指标" disabled={safePage === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft /></button><button aria-label="下一页指标" disabled={safePage >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}><ChevronRight /></button></div></div>
          </div>
          <footer className="analysis-provenance-bar">
            <div><DatabaseZap /><span><b>来源</b> PostgreSQL 只读导出</span><span><b>轮次状态</b> 已取消</span><span><b>生成时间</b> {new Date(snapshot.generatedAt).toLocaleString('zh-CN')}</span></div>
            <p>完成范围：序列生成、模型评分、候选决策</p>
          </footer>
        </>
      )}
    </section>
  )
}
