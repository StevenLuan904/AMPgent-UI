import { useEffect, useMemo, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import {
  Activity,
  Atom,
  Check,
  Copy,
  Dna,
  FlaskConical,
  Layers3,
  Rotate3D,
  ShieldCheck,
  Target,
  TriangleAlert,
} from 'lucide-react'
import { MoleculeViewer } from '../MoleculeViewer'
import type { ViewerArtifact } from '../types'
import './candidate-case.css'

interface MetricRecord {
  value: number | null
  text: string | null
  unit: string | null
  outOfDomain: boolean
}

interface PocketEvidence {
  sourceType: string
  sourceAccession: string | null
  confidence: number | null
  method: string | null
  resolutionAngstrom: number | null
}

interface TargetPocket {
  name: string
  type: string
  functionalRole: string
  evidenceGrade: string
  evidenceScore: number
  conditioningPriority: string
  conditioningEnabled: boolean
  residueIndices: number[]
  limitations: string[]
  evidence: PocketEvidence[]
}

interface CaseTarget {
  order: number
  name: string
  organism: string
  accession: string
  sequence: string
  sequenceLength: number
  status: string
  pockets: TargetPocket[]
}

interface BoltzRun {
  seed: number
  target: string
  lane: string
  confidenceScore: number
  complexPlddt: number
  iptm: number
  pairIptm: number
  ptm: number
  artifact: ViewerArtifact
}

interface RosettaScore {
  dG_separated: number
  dSASA_int: number
  interface_hbonds: number
  packstat: number
  peptide_score: number
  total_score: number
}

interface RosettaRun {
  seed: number
  target: string
  lane: string
  decoyCount: number
  scores: RosettaScore[]
  artifact: ViewerArtifact
}

interface CandidateCaseSnapshot {
  schemaVersion: string
  generatedAt: string
  source: string
  transportSha256: string
  run: { id: string; status: string; updatedAt: string }
  candidate: {
    id: string
    sequence: string
    originSet: string[]
    proposalRank: number
    admission: { paretoFront: number | null; reasons: string[]; status: string; structureEligible: boolean }
    metrics: Record<string, MetricRecord>
    metricContext: Record<string, { value: number; favorablePercentile: number; cohortSize: number; direction: string }>
  }
  targets: CaseTarget[]
  structure: {
    boltzRuns: BoltzRun[]
    rosettaRuns: RosettaRun[]
    coverage: {
      plannedBoltzPoses: number
      observedBoltzPoses: number
      plannedRosettaDecoys: number
      observedRosettaDecoys: number
    }
  }
  review: {
    status: string
    reason: string
    candidateDecisionAvailable: boolean
    finalPortfolioAvailable: boolean
  }
}

const methodHelp: Record<string, string> = {
  'Boltz 2': 'Boltz 2：预测生物分子复合物构象与结构置信度。',
  Rosetta: 'Rosetta：对蛋白质—短肽界面进行构象精修与能量评估。',
  'Mol*': 'Mol*：交互审视生物大分子三维结构的可视化工具。',
  LLAMP: 'LLAMP：按物种条件估计短肽最小抑菌浓度。',
  Macrel: 'Macrel：评估短肽抗菌活性和溶血风险。',
  ToxinPred3: 'ToxinPred3：提供短肽毒性分类与风险分值。',
  HydrAMP: 'HydrAMP：面向抗菌活性优化的短肽生成模型。',
  PBP2a: 'PBP2a：介导耐β-内酰胺表型的转肽酶。',
}

const targetNames: Record<string, string> = {
  'DNA gyrase subunit A': 'DNA旋转酶A亚基',
  'PBP2a family beta-lactam-resistant peptidoglycan transpeptidase, partial': 'PBP2a耐β-内酰胺转肽酶',
}

const organismNames: Record<string, string> = {
  'Escherichia coli K-12 MG1655': '大肠杆菌 K-12 MG1655',
  'Staphylococcus epidermidis': '表皮葡萄球菌',
}

const pocketNames: Record<string, string> = {
  'LEI-800 GyrA allosteric pocket': 'LEI-800别构口袋',
  'GyrA fluoroquinolone cleavage-complex site': '氟喹诺酮切割复合位点',
  'PBP2a transpeptidase active site': 'PBP2a转肽酶活性位点',
  'PBP2a ceftaroline/muramate allosteric domain': 'PBP2a头孢洛林/胞壁酸别构域',
}

function Term({ children }: { children: string }) {
  return <span className="case-term" title={methodHelp[children]}>{children}</span>
}

function median(values: number[]) {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}

function metric(caseData: CandidateCaseSnapshot, key: string) {
  return caseData.candidate.metrics[key]
}

function formatMic(logValue: number | null) {
  return logValue === null ? '—' : `${Math.pow(10, logValue).toFixed(1)} 微摩尔`
}

function withApiBase(artifact: ViewerArtifact | null, apiBase: string) {
  if (!artifact || !apiBase || !artifact.artifact_url.startsWith('/')) return artifact
  return { ...artifact, artifact_url: `${apiBase.replace(/\/+$/, '')}${artifact.artifact_url}` }
}

function ResiduePropertyHeatmap({ sequence }: { sequence: string }) {
  const rows = [
    { label: '疏水', residues: new Set('AILMFWVY'), color: '#57b9b6' },
    { label: '芳香', residues: new Set('FWY'), color: '#9171c8' },
    { label: '正电', residues: new Set('KR'), color: '#5d83de' },
  ]
  const data = rows.flatMap((row, rowIndex) => [...sequence].map((residue, index) => ({
    value: [index, rowIndex, row.residues.has(residue) ? 1 : 0],
    itemStyle: { color: row.residues.has(residue) ? row.color : '#f2f5f8', borderColor: '#fff', borderWidth: 2 },
  })))
  return <ReactECharts className="case-chart" option={{
    animation: false,
    visualMap: { show: false, min: 0, max: 1, inRange: { color: ['#f2f5f8', '#5d83de'] } },
    grid: { left: 42, right: 8, top: 28, bottom: 18 },
    tooltip: { formatter: (params: { value: number[] }) => `${rows[params.value[1]].label} · ${sequence[params.value[0]]}${params.value[0] + 1}<br/>${params.value[2] ? '具备此性质' : '不具备此性质'}` },
    xAxis: { type: 'category', position: 'top', data: [...sequence].map((residue, index) => `${residue}\n${index + 1}`), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { interval: 0, fontFamily: 'DM Mono', fontSize: 9, lineHeight: 12, color: '#617089' } },
    yAxis: { type: 'category', inverse: true, data: rows.map((row) => row.label), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { fontSize: 9, color: '#657289' } },
    series: [{ type: 'heatmap', data, emphasis: { itemStyle: { borderColor: '#24334d', borderWidth: 1 } } }],
  }} />
}

function StructureConfidenceChart({ runs }: { runs: BoltzRun[] }) {
  return <ReactECharts className="case-chart" option={{
    animationDuration: 350,
    color: ['#537fec', '#55b8b5', '#a27ad4'],
    grid: { left: 38, right: 8, top: 28, bottom: 26 },
    legend: { top: 0, icon: 'circle', itemWidth: 7, textStyle: { fontSize: 9, color: '#6d788b' } },
    tooltip: { trigger: 'axis', valueFormatter: (value: number) => value.toFixed(3) },
    xAxis: { type: 'category', data: runs.map((run) => `种子 ${String(run.seed).slice(-2)}`), axisTick: { show: false }, axisLabel: { fontSize: 9, color: '#7a8597' } },
    yAxis: { type: 'value', min: 0, max: 1, splitNumber: 4, axisLabel: { fontSize: 8, color: '#8b95a4' }, splitLine: { lineStyle: { color: '#edf1f5' } } },
    series: [
      { name: '复合物置信度', type: 'bar', barMaxWidth: 16, data: runs.map((run) => run.confidenceScore) },
      { name: '界面置信度', type: 'bar', barMaxWidth: 16, data: runs.map((run) => run.pairIptm) },
      { name: '复合物局部置信度', type: 'bar', barMaxWidth: 16, data: runs.map((run) => run.complexPlddt) },
    ],
  }} />
}

function RosettaScoreChart({ scores }: { scores: RosettaScore[] }) {
  const ordered = [...scores].sort((left, right) => left.dG_separated - right.dG_separated)
  const center = median(ordered.map((score) => score.dG_separated))
  return <ReactECharts className="case-chart" option={{
    animationDuration: 350,
    grid: { left: 45, right: 12, top: 20, bottom: 28 },
    tooltip: { trigger: 'axis', formatter: (params: Array<{ data: number; dataIndex: number }>) => `精修样本 ${params[0].dataIndex + 1}<br/>界面能：${params[0].data.toFixed(1)} Rosetta能量单位` },
    xAxis: { type: 'category', data: ordered.map((_, index) => index + 1), name: '按界面能排序的样本', nameLocation: 'middle', nameGap: 20, axisTick: { show: false }, axisLabel: { interval: 7, fontSize: 8, color: '#8a94a5' } },
    yAxis: { type: 'value', name: '界面能 ↓', nameTextStyle: { fontSize: 9, color: '#718096' }, axisLabel: { fontSize: 8, color: '#8a94a5' }, splitLine: { lineStyle: { color: '#edf1f5' } } },
    series: [
      { type: 'line', symbol: 'circle', symbolSize: 5, lineStyle: { width: 1.5, color: '#5c82e8' }, itemStyle: { color: '#5c82e8' }, data: ordered.map((score) => score.dG_separated) },
      { type: 'line', symbol: 'none', silent: true, lineStyle: { type: 'dashed', color: '#d28a55', width: 1 }, data: ordered.map(() => center) },
    ],
  }} />
}

function TargetCard({ target, computed }: { target: CaseTarget; computed: boolean }) {
  const primaryPocket = target.pockets.find((pocket) => pocket.conditioningPriority === 'primary') ?? target.pockets[0]
  const evidence = primaryPocket?.evidence[0]
  return (
    <article className="case-target-card">
      <header>
        <span className="target-order">靶点 {target.order}</span>
        <span className={`target-compute-state ${computed ? 'computed' : ''}`}>{computed ? '已有结构证据' : '已分派 · 待计算'}</span>
      </header>
      <h3 title={target.name.startsWith('PBP2a') ? methodHelp.PBP2a : 'GyrA：细菌DNA旋转酶A亚基，是拓扑异构酶抑制剂的经典靶点。'}>{targetNames[target.name] ?? target.name}</h3>
      <p>{organismNames[target.organism] ?? target.organism} · {target.accession} · {target.sequenceLength} 个残基</p>
      <div className="target-sequence-preview"><code>{target.sequence.slice(0, 54)}</code><span>…</span></div>
      <div className="pocket-evidence-row">
        <span><b>主口袋</b>{pocketNames[primaryPocket?.name] ?? primaryPocket?.name}</span>
        <span><b>证据</b>{primaryPocket?.evidenceGrade} 级 · {(primaryPocket?.evidenceScore * 100).toFixed(0)}%</span>
        <span><b>残基</b>{primaryPocket?.residueIndices.length} 个</span>
        <span><b>结构</b>{evidence?.sourceAccession ?? '—'}{evidence?.resolutionAngstrom ? ` · ${evidence.resolutionAngstrom.toFixed(2)} 埃` : ''}</span>
      </div>
      <div className="pocket-residues"><b>口袋残基</b><span>{primaryPocket?.residueIndices.join(' · ')}</span></div>
      <details><summary>完整靶点序列</summary><code>{target.sequence}</code></details>
    </article>
  )
}

export function CandidateCaseWorkbench({ apiBase = '' }: { apiBase?: string }) {
  const [caseData, setCaseData] = useState<CandidateCaseSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<'static' | 'interactive'>('static')
  const [structureSource, setStructureSource] = useState<'boltz' | 'rosetta'>('rosetta')
  const [seedIndex, setSeedIndex] = useState(0)
  const [representation, setRepresentation] = useState<'cartoon' | 'atomic' | 'surface'>('cartoon')
  const [colorTheme, setColorTheme] = useState<'chain-id' | 'hydrophobicity' | 'element-symbol'>('chain-id')
  const [autoRotate, setAutoRotate] = useState(false)
  const [showResidues, setShowResidues] = useState(true)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch('/data/candidate-case.snapshot.json', { headers: { Accept: 'application/json' } })
      .then((response) => {
        if (!response.ok) throw new Error(`案例快照读取失败：${response.status}`)
        return response.json() as Promise<CandidateCaseSnapshot>
      })
      .then((payload) => { if (!cancelled) setCaseData(payload) })
      .catch(() => { if (!cancelled) setError('候选案例数据暂时不可用') })
    return () => { cancelled = true }
  }, [])

  const scores = useMemo(() => caseData?.structure.rosettaRuns.flatMap((run) => run.scores) ?? [], [caseData])
  if (error) return <div className="case-state"><TriangleAlert /><b>{error}</b></div>
  if (!caseData) return <div className="case-state"><FlaskConical /><b>正在读取候选案例…</b></div>

  const selectedRun = structureSource === 'boltz'
    ? caseData.structure.boltzRuns[Math.min(seedIndex, caseData.structure.boltzRuns.length - 1)]
    : caseData.structure.rosettaRuns[Math.min(seedIndex, caseData.structure.rosettaRuns.length - 1)]
  const viewerArtifact = withApiBase(selectedRun?.artifact ?? null, apiBase)
  const m = caseData.candidate.metrics
  const primaryPocket = caseData.targets[0]?.pockets.find((pocket) => pocket.conditioningPriority === 'primary')
  const coverage = caseData.structure.coverage
  const coveragePercent = coverage.plannedBoltzPoses ? coverage.observedBoltzPoses / coverage.plannedBoltzPoses * 100 : 0
  const dGValues = scores.map((score) => score.dG_separated)
  const sasaValues = scores.map((score) => score.dSASA_int)
  const hbondValues = scores.map((score) => score.interface_hbonds)

  const copySequence = async () => {
    await navigator.clipboard.writeText(caseData.candidate.sequence)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }

  return (
    <div className="candidate-case-workbench">
      <section className="case-identity-strip">
        <div className="case-sequence-block">
          <div className="case-kicker"><span>结构实证案例</span><b>成熟核心 · 非支配前沿第 1 层</b></div>
          <div className="case-sequence-line"><code>{caseData.candidate.sequence}</code><button aria-label="复制候选序列" onClick={copySequence}>{copied ? <Check /> : <Copy />}</button></div>
          <div className="case-identity-meta"><span>22 个残基</span><span>来源 <Term>HydrAMP</Term></span><span>提案排序 {caseData.candidate.proposalRank}</span><span>结构资格已通过</span></div>
        </div>
        <div className="case-decision-summary">
          <span>当前结论</span>
          <strong>进入结构复核</strong>
          <p>活性与安全性进入非加权前沿；界面置信度仍属探索性证据。</p>
        </div>
        <div className="case-metric-grid">
          <div><span><Term>LLAMP</Term> 抑菌浓度</span><strong>{formatMic(metric(caseData, 'llamp_log10_mic_um')?.value ?? null)}</strong><small>大肠杆菌条件预测</small></div>
          <div><span><Term>Macrel</Term> 抗菌概率</span><strong>{((m.macrel_amp_probability?.value ?? 0) * 100).toFixed(1)}%</strong><small>通用分类器</small></div>
          <div><span>溶血概率</span><strong>{((m.macrel_hemolysis_probability?.value ?? 0) * 100).toFixed(1)}%</strong><small>低风险分类</small></div>
          <div><span><Term>ToxinPred3</Term> 毒性</span><strong>{((m.toxinpred3_hybrid_score?.value ?? 0) * 100).toFixed(1)}%</strong><small>预测无毒</small></div>
          <div><span>净电荷</span><strong>{(m.net_charge_ph7_4?.value ?? 0).toFixed(2)}</strong><small>酸碱度 7.4</small></div>
          <div><span>最大疏水连续段</span><strong>{m.maximum_hydrophobic_run?.value ?? '—'} 个残基</strong><small>疏水比例 {((m.hydrophobic_ratio_modlamp?.value ?? 0) * 100).toFixed(1)}%</small></div>
        </div>
      </section>

      <div className="case-main-grid">
        <article className="case-panel case-structure-panel">
          <header className="case-panel-header">
            <span className="case-panel-icon"><Atom /></span>
            <span><h2 title="GyrA：细菌DNA旋转酶A亚基，是拓扑异构酶抑制剂的经典靶点。">GyrA 原位复合物</h2><p>真实结构文件 · 875 残基靶蛋白与 22 残基短肽</p></span>
            <div className="case-view-tabs"><button className={viewMode === 'static' ? 'active' : ''} onClick={() => setViewMode('static')}>静态渲染</button><button className={viewMode === 'interactive' ? 'active' : ''} onClick={() => setViewMode('interactive')}><Term>Mol*</Term> 交互</button></div>
          </header>
          <div className="case-structure-controls">
            <label><span>结构来源</span><div><button className={structureSource === 'boltz' ? 'active' : ''} onClick={() => setStructureSource('boltz')}><Term>Boltz 2</Term></button><button className={structureSource === 'rosetta' ? 'active' : ''} onClick={() => setStructureSource('rosetta')}><Term>Rosetta</Term></button></div></label>
            <label><span>表示方式</span><div><button className={representation === 'cartoon' ? 'active' : ''} onClick={() => setRepresentation('cartoon')}>卡通</button><button className={representation === 'atomic' ? 'active' : ''} onClick={() => setRepresentation('atomic')}>原子</button><button className={representation === 'surface' ? 'active' : ''} onClick={() => setRepresentation('surface')}>表面</button></div></label>
            <label><span>着色</span><div><button className={colorTheme === 'chain-id' ? 'active' : ''} onClick={() => setColorTheme('chain-id')}>分子链</button><button className={colorTheme === 'hydrophobicity' ? 'active' : ''} onClick={() => setColorTheme('hydrophobicity')}>疏水性</button><button className={colorTheme === 'element-symbol' ? 'active' : ''} onClick={() => setColorTheme('element-symbol')}>元素</button></div></label>
            <label><span>视图</span><div><button className={autoRotate ? 'active' : ''} disabled={viewMode === 'static'} onClick={() => setAutoRotate((value) => !value)}><Rotate3D />慢速旋转</button><button className={showResidues ? 'active' : ''} onClick={() => setShowResidues((value) => !value)}><Layers3 />口袋残基</button></div></label>
          </div>
          <div className="case-structure-stage">
            {viewMode === 'static' ? (
              <img src="/data/candidate-case-gyrase.png" alt="GyrA 与候选短肽复合物的静态三维渲染" />
            ) : (
              <MoleculeViewer artifact={viewerArtifact} autoRotate={autoRotate} representation={representation} colorTheme={colorTheme} />
            )}
            {showResidues && primaryPocket && <div className="case-pocket-overlay"><b>{pocketNames[primaryPocket.name] ?? primaryPocket.name}</b><span>{primaryPocket.residueIndices.join(' · ')}</span></div>}
            <div className="case-structure-source"><span>{structureSource === 'boltz' ? '预测构象' : '精修样本'}</span><b>随机种子 {selectedRun?.seed}</b><div>{(structureSource === 'boltz' ? caseData.structure.boltzRuns : caseData.structure.rosettaRuns).map((run, index) => <button key={run.seed} className={seedIndex === index ? 'active' : ''} onClick={() => setSeedIndex(index)}>{index + 1}</button>)}</div></div>
          </div>
          <footer className="case-structure-evidence">
            <div><span><Term>Boltz 2</Term> 完成</span><strong>{coverage.observedBoltzPoses} / {coverage.plannedBoltzPoses}</strong><small>本候选计划构象</small></div>
            <div><span>界面置信度</span><strong>{median(caseData.structure.boltzRuns.map((run) => run.pairIptm)).toFixed(3)}</strong><small>两组随机种子中位数</small></div>
            <div><span><Term>Rosetta</Term> 精修</span><strong>{coverage.observedRosettaDecoys}</strong><small>原位界面样本</small></div>
            <div><span>界面能中位数</span><strong>{median(dGValues).toFixed(1)}</strong><small>Rosetta 能量单位</small></div>
            <div><span>界面埋藏面积</span><strong>{median(sasaValues).toFixed(0)} 平方埃</strong><small>32 个精修样本</small></div>
            <div><span>界面氢键</span><strong>{median(hbondValues).toFixed(1)}</strong><small>中位数</small></div>
          </footer>
        </article>

        <aside className="case-side-stack">
          <article className="case-panel case-review-card">
            <header><ShieldCheck /><span><h2>证据判读</h2><p>决策、结构覆盖与评审状态</p></span></header>
            <div className="review-status-row"><span className="status-complete"><Check /></span><div><b>候选决策完成</b><small>成熟核心 · 非加权前沿第 1 层</small></div></div>
            <div className="review-status-row"><span className="status-partial"><Activity /></span><div><b>结构证据部分完成</b><small>GyrA 原位通道 · 覆盖 {coveragePercent.toFixed(1)}%</small></div></div>
            <div className="review-status-row"><span className="status-pending"><TriangleAlert /></span><div><b>科学评审待续</b><small>最终候选组合尚未形成</small></div></div>
            <p className="review-conclusion">保留为结构复核候选；下一步补齐 PBP2a 与错误口袋对照。</p>
          </article>
          <article className="case-panel case-matrix-card">
            <header><Target /><span><h2>靶点—证据矩阵</h2><p>分派计划与实际计算覆盖</p></span></header>
            <table><thead><tr><th>靶点</th><th>分派</th><th>构象</th><th>精修</th></tr></thead><tbody>{caseData.targets.map((target, index) => <tr key={target.accession}><td>{targetNames[target.name] ?? target.name}<small>{target.accession}</small></td><td><span className="matrix-assigned">已分派</span></td><td>{index === 0 ? '2 / 6' : '0 / 6'}</td><td>{index === 0 ? '32 / 96' : '0 / 96'}</td></tr>)}</tbody></table>
          </article>
          <article className="case-panel case-sequence-chart">
            <header><Dna /><span><h2>残基性质图</h2><p>逐位点疏水、芳香与正电特征</p></span></header>
            <ResiduePropertyHeatmap sequence={caseData.candidate.sequence} />
          </article>
        </aside>
      </div>

      <div className="case-chart-grid">
        <article className="case-panel"><header><Activity /><span><h2>结构置信度</h2><p>两组原位 <Term>Boltz 2</Term> 构象</p></span></header><StructureConfidenceChart runs={caseData.structure.boltzRuns} /><footer><span>界面置信度 {Math.min(...caseData.structure.boltzRuns.map((run) => run.pairIptm)).toFixed(3)}–{Math.max(...caseData.structure.boltzRuns.map((run) => run.pairIptm)).toFixed(3)}</span><b>探索性结构证据</b></footer></article>
        <article className="case-panel"><header><Activity /><span><h2>界面能排序</h2><p>32 个 <Term>Rosetta</Term> 精修样本</p></span></header><RosettaScoreChart scores={scores} /><footer><span>中位数 {median(dGValues).toFixed(1)} · 最低 {Math.min(...dGValues).toFixed(1)}</span><b>同一靶点与原位通道内比较</b></footer></article>
        <article className="case-panel case-rank-context"><header><Activity /><span><h2>队列相对位置</h2><p>在 773 条唯一候选中的有利百分位</p></span></header><div className="rank-context-list"><div><span>抗菌概率</span><b>{caseData.candidate.metricContext.macrel_amp_probability.favorablePercentile}%</b><small>数值越高越有利</small></div><div><span>预测抑菌浓度</span><b>{caseData.candidate.metricContext.llamp_log10_mic_um.favorablePercentile}%</b><small>数值越低越有利</small></div><div><span>溶血风险</span><b>{caseData.candidate.metricContext.macrel_hemolysis_probability.favorablePercentile}%</b><small>数值越低越有利</small></div><div><span>毒性风险</span><b>{caseData.candidate.metricContext.toxinpred3_hybrid_score.favorablePercentile}%</b><small>数值越低越有利</small></div></div></article>
      </div>

      <section className="case-target-section">
        <header><div><Target /><span><h2>双靶点分派</h2><p>真实靶点序列、口袋残基与结构证据等级</p></span></div><span className="case-source-badge">数据库只读导出 · 结构证据已冻结</span></header>
        <div>{caseData.targets.map((target, index) => <TargetCard key={target.accession} target={target} computed={index === 0} />)}</div>
      </section>
    </div>
  )
}
