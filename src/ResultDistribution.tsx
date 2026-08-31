import type { AnalysisSnapshot } from './analysis/dataKernel'
import type { NodeDetail, RunDetail, ToolArtifact } from './types'

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

const persistedMetricStages = Object.keys(metricByStage)
const persistedGeneratorStages = Object.keys(generatorByStage)

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function artifactUrl(apiBase: string, artifact: ToolArtifact) {
  if (!artifact.url.startsWith('/')) return artifact.url
  return `${apiBase}${artifact.url}`
}

function payloadRecords(payload: unknown) {
  const result = record(record(payload).result)
  const records = result.records
  return Array.isArray(records) ? records.map(record) : []
}

async function readArtifact(apiBase: string, artifact: ToolArtifact, signal: AbortSignal) {
  const response = await fetch(artifactUrl(apiBase, artifact), { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`结果文件读取失败：${response.status}`)
  return response.json() as Promise<unknown>
}

export async function loadPersistedRunDistributions(
  apiBase: string,
  detail: RunDetail,
  signal: AbortSignal,
): Promise<Record<string, ResultDistributionData>> {
  const nodeIds = [...persistedGeneratorStages, ...persistedMetricStages, 'admission']
  const nodeEntries = await Promise.all(nodeIds.map(async (nodeId) => {
    const response = await fetch(`${apiBase}/v1/observer/runs/${detail.run.id}/nodes/${nodeId}`, { signal })
    return [nodeId, response.ok ? await response.json() as NodeDetail : null] as const
  }))
  const nodes = Object.fromEntries(nodeEntries) as Record<string, NodeDetail | null>
  const distributions: Record<string, ResultDistributionData> = {}
  const generatedSequences: string[] = []

  await Promise.all(persistedGeneratorStages.map(async (stageId) => {
    const node = nodes[stageId]
    if (!node) return
    const artifacts = node.calls.flatMap((call) => call.artifacts)
      .filter((artifact) => artifact.role === 'v38_raw_generator_output' && artifact.media_type === 'application/json')
    const payloads = await Promise.all(artifacts.map((artifact) => readArtifact(apiBase, artifact, signal)))
    const sequences = payloads.flatMap(payloadRecords)
      .map((row) => row.sequence)
      .filter((value): value is string => typeof value === 'string' && value.length > 0)
    generatedSequences.push(...sequences)
    distributions[stageId] = {
      label: '生成序列长度', unit: '残基', values: sequences.map((sequence) => sequence.length),
      source: `${sequences.length.toLocaleString()} 条持久化提案`, direction: 'neutral',
    }
  }))

  await Promise.all(persistedMetricStages.map(async (stageId) => {
    const node = nodes[stageId]
    const metric = metricByStage[stageId]
    if (!node || !metric) return
    const artifacts = node.calls.flatMap((call) => call.artifacts)
      .filter((artifact) => artifact.role === 'v38_metric_result' && artifact.media_type === 'application/json')
    const payloads = await Promise.all(artifacts.map((artifact) => readArtifact(apiBase, artifact, signal)))
    const values = finite(payloads.flatMap(payloadRecords).flatMap((row) => {
      const observations = Array.isArray(row.observations) ? row.observations.map(record) : []
      return observations.filter((observation) => observation.metric_name === metric.key)
        .map((observation) => typeof observation.numeric_value === 'number' ? observation.numeric_value : null)
    })).map((value) => metric.transform ? metric.transform(value) : value)
    distributions[stageId] = {
      label: metric.label, unit: metric.unit, values,
      source: `${values.length.toLocaleString()} 条持久化评分`, direction: metric.direction,
    }
  }))

  const uniqueSequences = [...new Set(generatedSequences)]
  distributions.candidate_pool = {
    label: '唯一候选长度', unit: '残基', values: uniqueSequences.map((sequence) => sequence.length),
    source: `${uniqueSequences.length.toLocaleString()} 条去重序列`, direction: 'neutral',
  }
  const statusCounts = Object.values(nodes.admission?.reasoning.status_counts ?? {})
  distributions.admission = {
    label: '决策分组规模', unit: '候选', values: statusCounts,
    source: `${statusCounts.length} 个决策分组`, direction: 'neutral',
  }
  return distributions
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
  if (stageId === 'knowledge') {
    const stage = detail.graph.nodes.find((item) => item.id === stageId)
    if (!stage) return null
    return {
      label: '知识证据记录量',
      unit: '条',
      values: [stage.current],
      source: '当前知识检索节点',
      direction: 'neutral',
    }
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
  const minimum = rawMin >= 0 ? Math.max(0, rawMin - span * 0.05) : rawMin - span * 0.05
  const maximum = rawMax + span * 0.05
  const x = (value: number) => 10 + ((value - minimum) / (maximum - minimum)) * (width - 20)
  if (!density) return { sorted, rawMin, rawMax, minimum, maximum, x, points: [] as Array<[number, number]> }
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
  return { sorted, rawMin, rawMax, minimum, maximum, x, points: points.map(([px, py]) => [px, height - 15 - (py / maxDensity) * (height - 28)] as [number, number]) }
}

function DistributionStatisticMarker({
  x,
  width,
  label,
  value,
  unit,
  kind,
}: {
  x: number
  width: number
  label: string
  value: number
  unit: string
  kind: 'median' | 'mean'
}) {
  const position = (x / width) * 100
  const edge = position < 18 ? ' edge-left' : position > 82 ? ' edge-right' : ''
  const readableValue = `${precision(value)}${unit ? ` ${unit}` : ''}`
  return (
    <span
      className={`distribution-stat-marker ${kind}${edge}`}
      style={{ left: `${position}%` }}
      tabIndex={0}
      aria-label={`${label} ${readableValue}`}
    >
      <span className="distribution-stat-tooltip"><small>{label}</small><b>{readableValue}</b></span>
    </span>
  )
}

export function ResultDistribution({ data, compact = false }: { data: ResultDistributionData; compact?: boolean }) {
  const values = finite(data.values)
  if (!values.length) return compact ? (
    <div className="result-distribution compact empty" title={`${data.label} · 尚无结果`}>
      <div className="distribution-mini-label"><span>{data.label}</span><b>尚无结果</b></div>
      <svg viewBox="0 0 238 50" preserveAspectRatio="none" aria-label={`${data.label}暂无结果`}>
        <line x1="10" y1="36" x2="228" y2="36" className="distribution-axis" />
      </svg>
    </div>
  ) : (
    <figure className="result-distribution detailed empty">
      <figcaption><div><span>结果分布</span><h3>{data.label}</h3><p>{data.source}</p></div></figcaption>
      <svg viewBox="0 0 720 110" role="img" aria-label={`${data.label}暂无结果`}>
        <line x1="20" y1="72" x2="700" y2="72" className="distribution-axis" />
        <text x="360" y="56" textAnchor="middle" className="distribution-empty-label">尚无结果</text>
      </svg>
    </figure>
  )
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
  const axisTicks = [0, .25, .5, .75, 1].map((fraction) => ({
    fraction,
    x: 10 + fraction * (width - 20),
    value: geometry.minimum + fraction * (geometry.maximum - geometry.minimum),
  }))

  if (compact) return (
    <div className="result-distribution compact" title={`${data.label} · ${modeLabel} · ${data.source}`}>
      <div className="distribution-mini-label"><span>{data.label}</span><b>{values.length.toLocaleString()} 个结果</b></div>
      <div className="distribution-mini-plot">
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label={`${data.label}${modeLabel}`}>
          <line x1="10" y1={height - 14} x2={width - 10} y2={height - 14} className="distribution-axis" />
          {density ? <><path d={area} className="distribution-area" /><path d={curve} className="distribution-curve" /></> : values.map((value, index) => <circle key={`${value}-${index}`} cx={geometry.x(value)} cy={height - 18 - (index % 4) * 5} r="2.6" className="distribution-point" />)}
        </svg>
        <DistributionStatisticMarker x={geometry.x(median)} width={width} label="中位数" value={median} unit={data.unit} kind="median" />
        <DistributionStatisticMarker x={geometry.x(mean)} width={width} label="平均数" value={mean} unit={data.unit} kind="mean" />
      </div>
      <div className="distribution-mini-axis"><span>{precision(geometry.rawMin)}</span><span>{modeLabel}</span><span>{precision(geometry.rawMax)}</span></div>
    </div>
  )

  return (
    <figure className="result-distribution detailed">
      <figcaption><div><span>结果分布 · {modeLabel}</span><h3>{data.label}</h3><p>{data.source} · 单位：{data.unit}</p></div><div className="distribution-statline"><span><small>中位数</small><b>{precision(median)}</b></span><span><small>平均数</small><b>{precision(mean)}</b></span><span><small>四分位距</small><b>{precision(q1)}–{precision(q3)}</b></span><span><small>范围</small><b>{precision(geometry.rawMin)}–{precision(geometry.rawMax)}</b></span></div></figcaption>
      <div className="distribution-detailed-plot">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.label}${modeLabel}`}>
          {axisTicks.map((tick) => <line key={tick.fraction} x1={tick.x} y1="16" x2={tick.x} y2={height - 34} className="distribution-grid" />)}
          <line x1="10" y1={height - 34} x2={width - 10} y2={height - 34} className="distribution-axis" />
          {density ? <><path d={area.replaceAll(String(height - 14), String(height - 34))} className="distribution-area" /><path d={curve} className="distribution-curve" />{values.filter((_, index) => index % Math.max(1, Math.floor(values.length / 90)) === 0).map((value, index) => <line key={index} x1={geometry.x(value)} y1={height - 34} x2={geometry.x(value)} y2={height - 27} className="distribution-rug" />)}</> : values.map((value, index) => <circle key={`${value}-${index}`} cx={geometry.x(value)} cy={height - 46 - (index % 5) * 13} r="5" className="distribution-point" />)}
          <rect x={geometry.x(q1)} y={height - 26} width={Math.max(2, geometry.x(q3) - geometry.x(q1))} height="8" rx="4" className="distribution-iqr" />
          {axisTicks.map((tick) => <text key={`label-${tick.fraction}`} x={tick.x} y={height - 3} textAnchor={tick.fraction === 0 ? 'start' : tick.fraction === 1 ? 'end' : 'middle'} className="distribution-tick">{precision(tick.value)}</text>)}
          <text x={width - 10} y="14" textAnchor="end" className="distribution-axis-unit">{data.unit}</text>
        </svg>
        <DistributionStatisticMarker x={geometry.x(median)} width={width} label="中位数" value={median} unit={data.unit} kind="median" />
        <DistributionStatisticMarker x={geometry.x(mean)} width={width} label="平均数" value={mean} unit={data.unit} kind="mean" />
      </div>
      <footer><span><i className="curve-key" />{modeLabel}</span><span><i className="median-key" />中位数</span><span><i className="mean-key" />平均数</span><span><i className="iqr-key" />四分位距</span><b>{data.direction === 'lower' ? '数值越低越有利' : data.direction === 'higher' ? '数值越高越有利' : '描述性分布'}</b></footer>
    </figure>
  )
}
