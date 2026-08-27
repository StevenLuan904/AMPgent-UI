import type { AnalysisSnapshot } from './analysis/dataKernel'
import type { RunDetail } from './types'

export interface ResultDistributionData {
  label: string
  unit: string
  values: number[]
  source: string
  direction?: 'higher' | 'lower' | 'neutral'
}

const metricByStage: Record<string, { key: string; label: string; unit: string; transform?: (value: number) => number; direction: 'higher' | 'lower' | 'neutral' }> = {
  mic: { key: 'llamp_log10_mic_um', label: '预测最小抑菌浓度', unit: '微摩尔', transform: (value) => 10 ** value, direction: 'lower' },
  amp_read: { key: 'amp_read_log10_mic_um', label: '交叉预测最小抑菌浓度', unit: '微摩尔', transform: (value) => 10 ** value, direction: 'lower' },
  hemolysis: { key: 'macrel_hemolysis_probability', label: '溶血概率', unit: '%', transform: (value) => value * 100, direction: 'lower' },
  toxicity: { key: 'toxinpred3_hybrid_score', label: '毒性风险分值', unit: '%', transform: (value) => value * 100, direction: 'lower' },
  developability: { key: 'net_charge_ph7_4', label: '净电荷', unit: '基本电荷', direction: 'neutral' },
}

const generatorByStage: Record<string, string> = {
  amp_designer: 'amp_designer',
  ampgan: 'ampgan_v2',
  hydramp: 'hydramp',
}

function finite(values: Array<number | null | undefined>) {
  return values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
}

export function distributionForStage(snapshot: AnalysisSnapshot | null, detail: RunDetail, stageId: string): ResultDistributionData | null {
  if (stageId === 'target_data' || stageId === 'targets') {
    return { label: '靶点序列长度', unit: '残基', values: detail.branches.map((branch) => branch.sequence_length), source: `${detail.branches.length} 个数据库靶点`, direction: 'neutral' }
  }
  if (stageId === 'boltz') {
    const counts = detail.structure_counts.boltz_pose ?? {}
    return { label: '构象通道产量', unit: '构象', values: Object.values(counts), source: `${Object.keys(counts).length} 个结构通道`, direction: 'neutral' }
  }
  if (stageId === 'rosetta') {
    const counts = detail.structure_counts.rosetta_decoy ?? {}
    return { label: '精修通道产量', unit: '样本', values: Object.values(counts), source: `${Object.keys(counts).length} 个结构通道`, direction: 'neutral' }
  }
  const runSnapshot = snapshot?.run.id === detail.run.id ? snapshot : null
  if (!runSnapshot) return null

  const metric = metricByStage[stageId]
  if (metric) {
    const values = finite(runSnapshot.candidates.map((candidate) => candidate.metrics[metric.key]?.value))
      .map((value) => metric.transform ? metric.transform(value) : value)
    return { label: metric.label, unit: metric.unit, values, source: `${values.length.toLocaleString()} 条唯一候选`, direction: metric.direction }
  }

  const generator = generatorByStage[stageId]
  if (generator) {
    const values = runSnapshot.candidates
      .filter((candidate) => candidate.originSet.includes(generator))
      .map((candidate) => candidate.sequence.length)
    return { label: '生成序列长度', unit: '残基', values, source: `${values.length.toLocaleString()} 条来源候选`, direction: 'neutral' }
  }

  if (stageId === 'candidate_pool') {
    return { label: '唯一候选长度', unit: '残基', values: runSnapshot.candidates.map((candidate) => candidate.sequence.length), source: `${runSnapshot.candidates.length.toLocaleString()} 条唯一候选`, direction: 'neutral' }
  }
  if (stageId === 'admission') {
    const values = finite(runSnapshot.candidates.filter((candidate) => candidate.admission.status === 'mature_core' || candidate.admission.status === 'promising_uncertain').map((candidate) => candidate.admission.paretoFront))
    return { label: '入选候选前沿层级', unit: '层', values, source: `${values.length} 条入选候选`, direction: 'lower' }
  }
  if (stageId === 'portfolio') {
    const values = runSnapshot.candidates.filter((candidate) => candidate.admission.structureEligible).map((candidate) => candidate.sequence.length)
    return { label: '结构资格候选长度', unit: '残基', values, source: `${values.length} 条结构资格候选`, direction: 'neutral' }
  }
  return null
}

function quantile(sorted: number[], probability: number) {
  if (!sorted.length) return 0
  const index = (sorted.length - 1) * probability
  const lower = Math.floor(index)
  const fraction = index - lower
  return sorted[lower] + ((sorted[lower + 1] ?? sorted[lower]) - sorted[lower]) * fraction
}

function precision(value: number) {
  const absolute = Math.abs(value)
  if (absolute >= 100) return value.toFixed(0)
  if (absolute >= 10) return value.toFixed(1)
  return value.toFixed(2)
}

function chartGeometry(values: number[], width: number, height: number, density: boolean) {
  const sorted = [...values].sort((left, right) => left - right)
  const rawMin = sorted[0] ?? 0
  const rawMax = sorted[sorted.length - 1] ?? 1
  const span = rawMax - rawMin || Math.max(Math.abs(rawMax), 1)
  const minimum = rawMin - span * 0.05
  const maximum = rawMax + span * 0.05
  const x = (value: number) => 10 + ((value - minimum) / (maximum - minimum)) * (width - 20)
  if (!density) return { sorted, rawMin, rawMax, x, points: [] as Array<[number, number]> }
  const bandwidth = Math.max(span * Math.pow(Math.max(values.length, 2), -0.2) * 0.42, span / 120)
  const points = Array.from({ length: 52 }, (_, index) => {
    const value = minimum + (index / 51) * (maximum - minimum)
    const densityValue = values.reduce((sum, item) => {
      const z = (value - item) / bandwidth
      return sum + Math.exp(-0.5 * z * z)
    }, 0) / (values.length * bandwidth)
    return [x(value), densityValue] as [number, number]
  })
  const maxDensity = Math.max(...points.map((point) => point[1]), 1e-9)
  return { sorted, rawMin, rawMax, x, points: points.map(([px, py]) => [px, height - 15 - (py / maxDensity) * (height - 28)] as [number, number]) }
}

export function ResultDistribution({ data, compact = false }: { data: ResultDistributionData; compact?: boolean }) {
  const values = finite(data.values)
  if (!values.length) return null
  const density = values.length >= 24
  const width = compact ? 238 : 720
  const height = compact ? 50 : 210
  const geometry = chartGeometry(values, width, height, density)
  const median = quantile(geometry.sorted, 0.5)
  const q1 = quantile(geometry.sorted, 0.25)
  const q3 = quantile(geometry.sorted, 0.75)
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const curve = geometry.points.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const area = density ? `${curve} L${width - 10},${height - 14} L10,${height - 14} Z` : ''
  const modeLabel = density ? '核密度曲线' : '散点分布'

  if (compact) return (
    <div className="result-distribution compact" title={`${data.label} · ${modeLabel} · ${data.source}`}>
      <div className="distribution-mini-label"><span>{data.label}</span><b>{precision(median)} {data.unit}</b></div>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label={`${data.label}${modeLabel}`}>
        <line x1="10" y1={height - 14} x2={width - 10} y2={height - 14} className="distribution-axis" />
        {density ? <><path d={area} className="distribution-area" /><path d={curve} className="distribution-curve" /></> : values.map((value, index) => <circle key={`${value}-${index}`} cx={geometry.x(value)} cy={height - 18 - (index % 4) * 5} r="2.6" className="distribution-point" />)}
        <line x1={geometry.x(median)} y1="7" x2={geometry.x(median)} y2={height - 10} className="distribution-median" />
      </svg>
      <div className="distribution-mini-axis"><span>{precision(geometry.rawMin)}</span><span>中位数</span><span>{precision(geometry.rawMax)}</span></div>
    </div>
  )

  return (
    <figure className="result-distribution detailed">
      <figcaption><div><span>结果分布 · {modeLabel}</span><h3>{data.label}</h3><p>{data.source} · 单位：{data.unit}</p></div><div className="distribution-summary"><span><small>中位数</small><b>{precision(median)}</b></span><span><small>四分位距</small><b>{precision(q1)}–{precision(q3)}</b></span><span><small>范围</small><b>{precision(geometry.rawMin)}–{precision(geometry.rawMax)}</b></span></div></figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.label}${modeLabel}`}>
        {[0, .25, .5, .75, 1].map((fraction) => <line key={fraction} x1={10 + fraction * (width - 20)} y1="16" x2={10 + fraction * (width - 20)} y2={height - 30} className="distribution-grid" />)}
        <line x1="10" y1={height - 30} x2={width - 10} y2={height - 30} className="distribution-axis" />
        {density ? <><path d={area.replaceAll(String(height - 14), String(height - 30))} className="distribution-area" /><path d={curve} className="distribution-curve" />{values.filter((_, index) => index % Math.max(1, Math.floor(values.length / 90)) === 0).map((value, index) => <line key={index} x1={geometry.x(value)} y1={height - 30} x2={geometry.x(value)} y2={height - 24} className="distribution-rug" />)}</> : values.map((value, index) => <circle key={`${value}-${index}`} cx={geometry.x(value)} cy={height - 42 - (index % 5) * 13} r="5" className="distribution-point" />)}
        <rect x={geometry.x(q1)} y={height - 23} width={Math.max(2, geometry.x(q3) - geometry.x(q1))} height="7" rx="3.5" className="distribution-iqr" />
        <line x1={geometry.x(median)} y1="12" x2={geometry.x(median)} y2={height - 14} className="distribution-median" />
        <text x="10" y={height - 2} className="distribution-tick">{precision(geometry.rawMin)}</text>
        <text x={width / 2} y={height - 2} textAnchor="middle" className="distribution-tick">均值 {precision(mean)}</text>
        <text x={width - 10} y={height - 2} textAnchor="end" className="distribution-tick">{precision(geometry.rawMax)} {data.unit}</text>
      </svg>
      <footer><span><i className="curve-key" />{modeLabel}</span><span><i className="median-key" />中位数</span><span><i className="iqr-key" />四分位距</span><b>{data.direction === 'lower' ? '数值越低越有利' : data.direction === 'higher' ? '数值越高越有利' : '描述性分布'}</b></footer>
    </figure>
  )
}
