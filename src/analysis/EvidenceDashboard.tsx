import { useEffect, useState } from 'react'
import { CheckCircle2, DatabaseZap, FlaskConical, RefreshCw, ShieldCheck } from 'lucide-react'
import { loadAnalysisSnapshot, type AnalysisSnapshot } from './dataKernel'
import './analysis-dashboard.css'

const metricLabels: Record<string, { label: string; help: string }> = {
  amp_read_log10_mic_um: { label: 'AMP read 抑菌浓度', help: 'AMP read 用于交叉复核候选短肽的抗菌活性预测。' },
  llamp_log10_mic_um: { label: 'LLAMP 抑菌浓度', help: 'LLAMP 提供按物种条件化的抑菌浓度软估计。' },
  macrel_amp_probability: { label: 'Macrel 抗菌概率', help: 'Macrel 是短肽抗菌活性与溶血风险分类模型。' },
  macrel_hemolysis_probability: { label: 'Macrel 溶血概率', help: 'Macrel 是短肽抗菌活性与溶血风险分类模型。' },
  toxinpred3_hybrid_score: { label: 'ToxinPred3 毒性评分', help: 'ToxinPred3 用于预测短肽毒性风险。' },
  net_charge_ph7_4: { label: '生理酸碱度净电荷', help: '按生理酸碱度估算的序列净电荷。' },
  hydrophobic_moment_eisenberg: { label: '疏水矩', help: '衡量短肽两亲性空间分布的序列描述符。' },
  hydrophobic_ratio_modlamp: { label: '疏水残基比例', help: '序列中疏水残基所占比例。' },
  maximum_hydrophobic_run: { label: '最长连续疏水片段', help: '序列中连续疏水残基的最大长度。' },
  macrel_hemolysis_label: { label: 'Macrel 溶血分类', help: 'Macrel 输出的溶血风险分类结果。' },
  toxinpred3_label: { label: 'ToxinPred3 毒性分类', help: 'ToxinPred3 输出的毒性分类结果。' },
}

export function EvidenceDashboard({ runId }: { runId?: string }) {
  const [snapshot, setSnapshot] = useState<AnalysisSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)

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
          <div className="evidence-table-card">
            <header><div><CheckCircle2 /><span><b>模型与指标记录</b><small>名称、证据数量与读取状态</small></span></div><span>只读</span></header>
            <table><thead><tr><th>指标</th><th>证据数量</th><th>候选覆盖</th><th>状态</th></tr></thead><tbody>
              {Object.entries(snapshot.metricMethods).map(([key, methods]) => {
                const descriptor = metricLabels[key] ?? { label: key, help: '数据库中记录的科学评分指标。' }
                const count = snapshot.candidates.filter((candidate) => candidate.metrics[key]?.status === 'succeeded').length
                return <tr key={key}><td><b title={descriptor.help}>{descriptor.label}</b><small>{methods.length} 个方法记录</small></td><td>{count.toLocaleString()} 项</td><td>{((count / snapshot.summary.uniqueCandidates) * 100).toFixed(1)}%</td><td><span className="evidence-status-ok"><i />已记录</span></td></tr>
              })}
            </tbody></table>
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
