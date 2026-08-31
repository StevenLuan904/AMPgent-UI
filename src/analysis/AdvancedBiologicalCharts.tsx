import { useMemo, useState } from 'react'
import type { ResidueEnrichmentAnalysis, TernaryCompositionPoint } from './advancedBiologicalAnalysis'

export interface AlluvialRecord {
  sequence: string
  source: string
  family: string
  outcome: string
  color: string
}

interface BandNode {
  key: string
  count: number
  top: number
  bottom: number
  color: string
}

function buildBands(records: AlluvialRecord[], field: 'source' | 'family' | 'outcome', top: number, bottom: number) {
  const groups = new Map<string, number[]>()
  records.forEach((record, index) => {
    const key = record[field]
    const indices = groups.get(key) ?? []
    indices.push(index)
    groups.set(key, indices)
  })
  const ordered = [...groups.entries()].sort((left, right) => right[1].length - left[1].length || left[0].localeCompare(right[0]))
  const gap = Math.min(7, Math.max(2, (bottom - top) * .12 / Math.max(1, ordered.length - 1)))
  const scale = (bottom - top - gap * Math.max(0, ordered.length - 1)) / Math.max(1, records.length)
  const positions = Array(records.length).fill(0) as number[]
  let cursor = top
  const nodes: BandNode[] = ordered.map(([key, indices]) => {
    const height = Math.max(3, indices.length * scale)
    indices.forEach((recordIndex, memberIndex) => { positions[recordIndex] = cursor + (memberIndex + .5) * height / indices.length })
    const node = { key, count: indices.length, top: cursor, bottom: cursor + height, color: records[indices[0]]?.color ?? '#7c91b1' }
    cursor += height + gap
    return node
  })
  return { nodes, positions }
}

export function SequenceAlluvialPlot({ records }: { records: AlluvialRecord[] }) {
  const [hovered, setHovered] = useState<number | null>(null)
  const layout = useMemo(() => ({
    source: buildBands(records, 'source', 43, 330),
    family: buildBands(records, 'family', 43, 330),
    outcome: buildBands(records, 'outcome', 43, 330),
  }), [records])
  const active = hovered == null ? null : records[hovered]
  if (!records.length) return <div className="energy-empty">暂无候选流向</div>
  const columns = [
    { field: 'source' as const, x: 220, label: '生成来源', layout: layout.source },
    { field: 'family' as const, x: 950, label: '序列家族', layout: layout.family },
    { field: 'outcome' as const, x: 1680, label: '候选结局', layout: layout.outcome },
  ]
  return <div className="sequence-alluvial">
    <svg viewBox="0 0 1900 360" role="img" aria-label={`候选冲积流图，共 ${records.length} 条唯一候选`}>
      <defs><filter id="alluvial-focus" x="-10%" y="-20%" width="120%" height="140%"><feGaussianBlur stdDeviation="1.2" /></filter></defs>
      <g className="alluvial-links">
        {records.map((record, index) => {
          const sourceY = layout.source.positions[index]
          const familyY = layout.family.positions[index]
          const outcomeY = layout.outcome.positions[index]
          const activeLine = hovered === index
          const sourcePath = `M232 ${sourceY} C485 ${sourceY}, 690 ${familyY}, 944 ${familyY}`
          const outcomePath = `M962 ${familyY} C1215 ${familyY}, 1420 ${outcomeY}, 1674 ${outcomeY}`
          return <g key={`${record.sequence}-${index}`} onPointerEnter={() => setHovered(index)} onPointerLeave={() => setHovered(null)}>
            <path className="alluvial-hit" d={sourcePath} />
            <path className={activeLine ? 'active' : ''} d={sourcePath} stroke={record.color} />
            <path className="alluvial-hit" d={outcomePath} />
            <path className={activeLine ? 'active' : ''} d={outcomePath} stroke={record.color} />
          </g>
        })}
      </g>
      {columns.map((column, columnIndex) => <g key={column.field} className="alluvial-column">
        <text className="alluvial-column-title" x={column.x} y="20" textAnchor="middle">{column.label}</text>
        {column.layout.nodes.map((node) => <g key={node.key}>
          <rect x={column.x - 6} y={node.top} width="12" height={Math.max(3, node.bottom - node.top)} rx="3" fill={columnIndex === 1 ? node.color : columnIndex === 2 ? '#5e7fae' : '#8aa0c0'} />
          <text className="alluvial-node-label" x={columnIndex === 2 ? column.x + 11 : column.x - 11} y={(node.top + node.bottom) / 2 + 3} textAnchor={columnIndex === 2 ? 'start' : 'end'}>{node.key}</text>
          <text className="alluvial-node-count" x={columnIndex === 2 ? column.x + 11 : column.x - 11} y={(node.top + node.bottom) / 2 + 14} textAnchor={columnIndex === 2 ? 'start' : 'end'}>{node.count}</text>
        </g>)}
      </g>)}
    </svg>
    {active && <div className="alluvial-tooltip"><b>{active.sequence}</b><span>{active.source} → {active.family} → {active.outcome}</span></div>}
  </div>
}

const cohortLabels: Record<string, string> = { mature_core: '成熟核心', promising_uncertain: '潜力待核', rejected: '未入选' }
const cohortColors: Record<string, string> = { mature_core: '#36a276', promising_uncertain: '#d49a4c', rejected: '#7c94bd' }

export function TernaryCompositionPlot({ points }: { points: TernaryCompositionPoint[] }) {
  const [hovered, setHovered] = useState<TernaryCompositionPoint | null>(null)
  const left = { x: 86, y: 314 }
  const right = { x: 714, y: 314 }
  const top = { x: 400, y: 30 }
  const project = (point: Pick<TernaryCompositionPoint, 'positive' | 'hydrophobic' | 'other'>) => ({
    x: point.positive * left.x + point.other * right.x + point.hydrophobic * top.x,
    y: point.positive * left.y + point.other * right.y + point.hydrophobic * top.y,
  })
  const fractions = [.2, .4, .6, .8]
  const ordered = [...points].sort((a, b) => Number(a.structureEligible) - Number(b.structureEligible))
  return <div className="ternary-composition">
    <svg viewBox="0 0 800 350" role="img" aria-label={`序列理化组成三元图，共 ${points.length} 条候选`}>
      <polygon className="ternary-face" points={`${left.x},${left.y} ${right.x},${right.y} ${top.x},${top.y}`} />
      <g className="ternary-grid">{fractions.flatMap((fraction) => {
        const lines = [
          [project({ positive: fraction, hydrophobic: 0, other: 1 - fraction }), project({ positive: fraction, hydrophobic: 1 - fraction, other: 0 })],
          [project({ positive: 0, hydrophobic: 1 - fraction, other: fraction }), project({ positive: 1 - fraction, hydrophobic: 0, other: fraction })],
          [project({ positive: 1 - fraction, hydrophobic: fraction, other: 0 }), project({ positive: 0, hydrophobic: fraction, other: 1 - fraction })],
        ]
        return lines.map(([start, end], index) => <line key={`${fraction}-${index}`} x1={start.x} y1={start.y} x2={end.x} y2={end.y} />)
      })}</g>
      <g className="ternary-points">{ordered.map((point, index) => {
        const position = project(point)
        return <circle key={`${point.sequence}-${index}`} cx={position.x} cy={position.y} r={point.structureEligible ? 4.2 : 2.4} fill={cohortColors[point.cohort] ?? '#7c94bd'} fillOpacity={point.structureEligible ? .92 : .28} stroke={point.structureEligible ? '#fff' : 'none'} strokeWidth="1.2" onPointerEnter={() => setHovered(point)} onPointerLeave={() => setHovered(null)} />
      })}</g>
      <g className="ternary-labels"><text x={left.x - 5} y={left.y + 23} textAnchor="start">正电残基</text><text x={right.x + 5} y={right.y + 23} textAnchor="end">其它残基</text><text x={top.x} y={top.y - 11} textAnchor="middle">疏水残基</text></g>
      <g className="ternary-legend">{Object.entries(cohortLabels).map(([key, label], index) => <g key={key} transform={`translate(${25 + index * 105}, 18)`}><circle r="4" fill={cohortColors[key]} /><text x="9" y="4">{label}</text></g>)}</g>
    </svg>
    {hovered && <div className="ternary-tooltip"><b>{hovered.sequence}</b><span>{cohortLabels[hovered.cohort] ?? hovered.cohort}</span><div><i>正电</i><strong>{(hovered.positive * 100).toFixed(1)}%</strong><i>疏水</i><strong>{(hovered.hydrophobic * 100).toFixed(1)}%</strong><i>其它</i><strong>{(hovered.other * 100).toFixed(1)}%</strong></div></div>}
  </div>
}

const residueNames: Record<string, string> = {
  A: '丙氨酸', C: '半胱氨酸', D: '天冬氨酸', E: '谷氨酸', F: '苯丙氨酸', G: '甘氨酸', H: '组氨酸', I: '异亮氨酸', K: '赖氨酸', L: '亮氨酸', M: '甲硫氨酸', N: '天冬酰胺', P: '脯氨酸', Q: '谷氨酰胺', R: '精氨酸', S: '丝氨酸', T: '苏氨酸', V: '缬氨酸', W: '色氨酸', Y: '酪氨酸',
}

function residueColor(residue: string) {
  if ('KRH'.includes(residue)) return '#4f7df3'
  if ('DE'.includes(residue)) return '#df7b74'
  if ('AILMFWVY'.includes(residue)) return '#9b7bd3'
  if ('STNQ'.includes(residue)) return '#55bfc3'
  return '#8ca06f'
}

export function ResidueEnrichmentForest({ analysis }: { analysis: ResidueEnrichmentAnalysis }) {
  const [hovered, setHovered] = useState<number | null>(null)
  const maxAbs = Math.max(1, Math.ceil(Math.max(...analysis.rows.flatMap((row) => [Math.abs(row.lower), Math.abs(row.upper)])) * 2) / 2)
  const left = 400
  const right = 1700
  const top = 38
  const bottom = 374
  const x = (value: number) => left + (value + maxAbs) / (2 * maxAbs) * (right - left)
  const rowHeight = (bottom - top) / analysis.rows.length
  const ticks = [-maxAbs, -maxAbs / 2, 0, maxAbs / 2, maxAbs]
  const active = hovered == null ? null : analysis.rows[hovered]
  return <div className="residue-forest">
    <svg viewBox="0 0 1800 405" role="img" aria-label={`成熟核心与未入选候选的残基富集森林图，比较 ${analysis.selectedCount} 与 ${analysis.referenceCount} 条候选`}>
      <g className="forest-grid">{ticks.map((tick) => <g key={tick}><line x1={x(tick)} y1={top - 6} x2={x(tick)} y2={bottom + 2} /><text x={x(tick)} y="395" textAnchor="middle">{tick.toFixed(tick === 0 ? 0 : 1)}</text></g>)}</g>
      <line className="forest-zero" x1={x(0)} y1={top - 7} x2={x(0)} y2={bottom + 3} />
      <text className="forest-direction" x={left} y="18">未入选富集 ←</text><text className="forest-direction" x={right} y="18" textAnchor="end">→ 成熟核心富集</text>
      {analysis.rows.map((row, index) => {
        const y = top + rowHeight * (index + .5)
        return <g key={row.residue} className={hovered === index ? 'forest-row active' : 'forest-row'} onPointerEnter={() => setHovered(index)} onPointerLeave={() => setHovered(null)}>
          <rect className="forest-hover-target" x="0" y={y - rowHeight / 2} width="1800" height={rowHeight} />
          <text className="forest-residue-label" x="388" y={y + 4} textAnchor="end">{row.residue} · {residueNames[row.residue]}</text>
          <line className="forest-interval" x1={x(row.lower)} y1={y} x2={x(row.upper)} y2={y} />
          <circle className="forest-point" cx={x(row.log2OddsRatio)} cy={y} r="4.6" fill={residueColor(row.residue)} />
        </g>
      })}
    </svg>
    {active && <div className="forest-tooltip"><b>{active.residue} · {residueNames[active.residue]}</b><span>对数优势比 {active.log2OddsRatio.toFixed(2)} · 95%区间 {active.lower.toFixed(2)} 至 {active.upper.toFixed(2)}</span><small>成熟核心 {(active.selectedFraction * 100).toFixed(1)}% · 未入选 {(active.referenceFraction * 100).toFixed(1)}%</small></div>}
  </div>
}
