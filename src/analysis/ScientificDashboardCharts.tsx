import { useMemo, useRef, useState, type PointerEvent } from 'react'
import type { ConstraintIntersectionAnalysis } from './peptideFamilyAnalysis'

export interface ParetoPoint3D {
  sequence: string
  generator: string
  activity: number
  hemolysis: number
  toxicity: number
  paretoRank: number | null
  structureEligible: boolean
  color: string
}

interface ProjectedPoint extends ParetoPoint3D {
  screenX: number
  screenY: number
  depth: number
  depthScale: number
  floorX: number
  floorY: number
}

const cubeCorners: Array<[number, number, number]> = [
  [0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0],
  [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1],
]

const cubeEdges = [
  [0, 1], [0, 2], [1, 3], [2, 3], [4, 5], [4, 6], [5, 7], [6, 7],
  [0, 4], [1, 5], [2, 6], [3, 7],
]

function projectedConvexHull(points: ProjectedPoint[]) {
  if (points.length < 4) return points
  const sorted = [...points].sort((left, right) => left.screenX - right.screenX || left.screenY - right.screenY)
  const cross = (origin: ProjectedPoint, left: ProjectedPoint, right: ProjectedPoint) =>
    (left.screenX - origin.screenX) * (right.screenY - origin.screenY) - (left.screenY - origin.screenY) * (right.screenX - origin.screenX)
  const lower: ProjectedPoint[] = []
  for (const point of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) lower.pop()
    lower.push(point)
  }
  const upper: ProjectedPoint[] = []
  for (const point of [...sorted].reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) upper.pop()
    upper.push(point)
  }
  return [...lower.slice(0, -1), ...upper.slice(0, -1)]
}

export function ParetoFront3D({ points }: { points: ParetoPoint3D[] }) {
  const [yaw, setYaw] = useState(-0.72)
  const [pitch, setPitch] = useState(0.57)
  const [hovered, setHovered] = useState<ProjectedPoint | null>(null)
  const drag = useRef<{ x: number; y: number; yaw: number; pitch: number } | null>(null)

  const project = (x: number, y: number, z: number) => {
    const dx = x - 0.5
    const dy = y - 0.5
    const dz = z - 0.5
    const horizontal = dx * Math.cos(yaw) - dy * Math.sin(yaw)
    const receding = dx * Math.sin(yaw) + dy * Math.cos(yaw)
    const depth = dz * Math.sin(pitch) + receding * Math.cos(pitch)
    const depthScale = Math.max(.78, Math.min(1.16, .98 + depth * .22))
    return {
      screenX: 400 + horizontal * 500 * depthScale,
      screenY: 184 - (dz * Math.cos(pitch) - receding * Math.sin(pitch)) * 278 * depthScale,
      depth,
      depthScale,
    }
  }

  const projected = useMemo(() => points.map((point) => {
    const position = project(point.activity, 1 - point.hemolysis, 1 - point.toxicity)
    const floor = project(point.activity, 1 - point.hemolysis, 0)
    return { ...point, ...position, floorX: floor.screenX, floorY: floor.screenY }
  }).sort((left, right) => left.depth - right.depth), [points, yaw, pitch])

  const frontier = projected.filter((point) => point.paretoRank === 1)
  const frontierPolygon = useMemo(() => {
    if (frontier.length < 3) return ''
    return projectedConvexHull(frontier)
      .map((point) => `${point.screenX.toFixed(1)},${point.screenY.toFixed(1)}`)
      .join(' ')
  }, [frontier])

  const corners = cubeCorners.map(([x, y, z]) => project(x, y, z))
  const floorFace = [corners[0], corners[1], corners[3], corners[2]].map((point) => `${point.screenX},${point.screenY}`).join(' ')
  const rearFace = [corners[2], corners[3], corners[7], corners[6]].map((point) => `${point.screenX},${point.screenY}`).join(' ')
  const gridFractions = [.25, .5, .75]
  const handlePointerDown = (event: PointerEvent<SVGSVGElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { x: event.clientX, y: event.clientY, yaw, pitch }
  }
  const handlePointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (!drag.current) return
    setYaw(drag.current.yaw + (event.clientX - drag.current.x) * 0.006)
    setPitch(Math.max(0.18, Math.min(1.05, drag.current.pitch - (event.clientY - drag.current.y) * 0.004)))
  }
  const handlePointerUp = (event: PointerEvent<SVGSVGElement>) => {
    drag.current = null
    event.currentTarget.releasePointerCapture(event.pointerId)
  }

  const legend = [...new Map(points.map((point) => [point.generator, point.color])).entries()]
  const origin = project(0, 0, 0)
  const activityAxis = project(1, 0, 0)
  const hemolysisAxis = project(0, 1, 0)
  const toxicityAxis = project(0, 0, 1)

  return (
    <div className="pareto-3d" data-point-count={points.length}>
      <svg viewBox="0 0 800 360" role="img" aria-label={`三维多目标前沿，共 ${points.length} 条候选`} onPointerDown={handlePointerDown} onPointerMove={handlePointerMove} onPointerUp={handlePointerUp} onPointerCancel={() => { drag.current = null }}>
        <defs>
          <linearGradient id="frontier-surface" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#678ff0" stopOpacity=".32" />
            <stop offset="1" stopColor="#64c8bd" stopOpacity=".12" />
          </linearGradient>
          <linearGradient id="pareto-floor" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#dce8fb" stopOpacity=".18" /><stop offset="1" stopColor="#b9cceb" stopOpacity=".48" /></linearGradient>
          <linearGradient id="pareto-rear" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stopColor="#f5f8fd" stopOpacity=".18" /><stop offset="1" stopColor="#dce7f8" stopOpacity=".42" /></linearGradient>
          <filter id="frontier-glow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="5" /></filter>
          <filter id="pareto-shadow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.4" /></filter>
        </defs>
        <polygon className="pareto-rear-face" points={rearFace} fill="url(#pareto-rear)" />
        <polygon className="pareto-floor-face" points={floorFace} fill="url(#pareto-floor)" />
        <g className="pareto-floor-grid">
          {gridFractions.map((fraction) => {
            const start = project(fraction, 0, 0)
            const end = project(fraction, 1, 0)
            return <line key={`x-${fraction}`} x1={start.screenX} y1={start.screenY} x2={end.screenX} y2={end.screenY} />
          })}
          {gridFractions.map((fraction) => {
            const start = project(0, fraction, 0)
            const end = project(1, fraction, 0)
            return <line key={`y-${fraction}`} x1={start.screenX} y1={start.screenY} x2={end.screenX} y2={end.screenY} />
          })}
        </g>
        <g className="pareto-cube">
          {cubeEdges.map(([start, end], index) => <line key={index} x1={corners[start].screenX} y1={corners[start].screenY} x2={corners[end].screenX} y2={corners[end].screenY} />)}
        </g>
        {frontierPolygon && <>
          <polygon className="pareto-surface-shadow" points={frontierPolygon} transform="translate(8 10)" />
          <polygon className="pareto-surface-glow" points={frontierPolygon} />
          <polygon className="pareto-surface" points={frontierPolygon} />
        </>}
        <g className="pareto-depth-guides">
          {projected.filter((point) => point.paretoRank === 1 || point.structureEligible).map((point, index) => <line key={`${point.sequence}-guide-${index}`} x1={point.screenX} y1={point.screenY} x2={point.floorX} y2={point.floorY} />)}
        </g>
        <g className="pareto-floor-shadows">
          {projected.map((point, index) => <ellipse key={`${point.sequence}-shadow-${index}`} cx={point.floorX + 3} cy={point.floorY + 3} rx={Math.max(1.8, 3.8 * point.depthScale)} ry={Math.max(.8, 1.7 * point.depthScale)} opacity={point.paretoRank === 1 ? .28 : point.structureEligible ? .18 : .07} />)}
        </g>
        <g className="pareto-points">
          {projected.map((point, index) => <circle
            key={`${point.sequence}-${index}`}
            cx={point.screenX}
            cy={point.screenY}
            r={(point.paretoRank === 1 ? 5.4 : point.structureEligible ? 3.9 : 2.45) * point.depthScale}
            fill={point.color}
            fillOpacity={Math.min(1, (point.paretoRank === 1 ? .96 : point.structureEligible ? .72 : .24) * point.depthScale)}
            stroke={point.paretoRank === 1 || point.structureEligible ? '#fff' : 'none'}
            strokeWidth={point.paretoRank === 1 ? 1.8 : 1.1}
            onPointerEnter={() => setHovered(point)}
            onPointerLeave={() => setHovered(null)}
          />)}
        </g>
        <g className="pareto-axes">
          <line x1={origin.screenX} y1={origin.screenY} x2={activityAxis.screenX} y2={activityAxis.screenY} />
          <line x1={origin.screenX} y1={origin.screenY} x2={hemolysisAxis.screenX} y2={hemolysisAxis.screenY} />
          <line x1={origin.screenX} y1={origin.screenY} x2={toxicityAxis.screenX} y2={toxicityAxis.screenY} />
          <text x={activityAxis.screenX + 8} y={activityAxis.screenY + 12}>抗菌活性 ↑</text>
          <text x={hemolysisAxis.screenX - 8} y={hemolysisAxis.screenY + 16} textAnchor="end">低溶血倾向 ↑</text>
          <text x={toxicityAxis.screenX} y={toxicityAxis.screenY - 10} textAnchor="middle">低毒性倾向 ↑</text>
        </g>
        <g className="pareto-legend">
          {legend.map(([label, color], index) => <g key={label} transform={`translate(${18 + index * 128}, 18)`}><circle r="4" fill={color} /><text x="9" y="4">{label}</text></g>)}
          <g transform="translate(18, 40)"><rect width="16" height="9" rx="2" fill="url(#frontier-surface)" stroke="#7195e7" strokeOpacity=".5" /><text x="23" y="9">非支配前沿面</text></g>
          <g transform="translate(155, 40)"><line x1="0" y1="5" x2="31" y2="5" stroke="#b7c5d9" strokeWidth="5" /><circle cx="5" cy="5" r="2.5" fill="#607ba5" /><circle cx="27" cy="5" r="4.5" fill="#607ba5" /><text x="39" y="9">远 → 近</text></g>
        </g>
      </svg>
      {hovered && <div className="pareto-tooltip">
        <b>{hovered.sequence}</b><span>{hovered.generator} · {hovered.paretoRank === 1 ? '前沿第一层' : hovered.paretoRank ? `前沿第 ${hovered.paretoRank} 层` : '未分配前沿层级'}</span>
        <div><i>抗菌</i><strong>{hovered.activity.toFixed(3)}</strong><i>溶血</i><strong>{hovered.hemolysis.toFixed(3)}</strong><i>毒性</i><strong>{hovered.toxicity.toFixed(3)}</strong></div>
      </div>}
    </div>
  )
}

export function ConstraintIntersectionPlot({ analysis }: { analysis: ConstraintIntersectionAnalysis }) {
  const [hovered, setHovered] = useState<number | null>(null)
  if (!analysis.candidateCount || !analysis.intersections.length) return <div className="energy-empty">暂无可计算交集</div>
  const left = 246
  const right = 786
  const topBase = 120
  const matrixTop = 177
  const matrixBottom = 326
  const columnWidth = (right - left) / analysis.intersections.length
  const rowHeight = (matrixBottom - matrixTop) / analysis.sets.length
  const maxIntersection = Math.max(...analysis.intersections.map((item) => item.count), 1)
  const maxSet = Math.max(...analysis.sets.map((item) => item.total), 1)
  const activeIntersection = hovered == null ? null : analysis.intersections[hovered]
  return <div className="constraint-upset">
    <svg viewBox="0 0 800 340" role="img" aria-label={`活性与安全约束交集矩阵，共 ${analysis.candidateCount} 条候选`}>
      <text className="upset-section-label" x="20" y="20">条件覆盖</text>
      <text className="upset-section-label" x={left} y="20">独占交集</text>
      <line className="upset-baseline" x1={left} y1={topBase} x2={right} y2={topBase} />
      {analysis.intersections.map((intersection, index) => {
        const x = left + columnWidth * (index + .5)
        const barHeight = Math.max(3, intersection.count / maxIntersection * 82)
        return <g key={intersection.key} className={hovered === index ? 'upset-column active' : 'upset-column'} onPointerEnter={() => setHovered(index)} onPointerLeave={() => setHovered(null)}>
          <rect className="upset-hover-target" x={x - columnWidth * .48} y="26" width={columnWidth * .96} height="303" />
          <rect className="upset-intersection-bar" x={x - Math.min(14, columnWidth * .28)} y={topBase - barHeight} width={Math.min(28, columnWidth * .56)} height={barHeight} rx="3" />
          <text className="upset-count" x={x} y={topBase - barHeight - 6} textAnchor="middle">{intersection.count}</text>
          {intersection.active.some(Boolean) && <line className="upset-connector" x1={x} y1={matrixTop + intersection.active.indexOf(true) * rowHeight + rowHeight / 2} x2={x} y2={matrixTop + intersection.active.lastIndexOf(true) * rowHeight + rowHeight / 2} />}
          {intersection.active.map((active, rowIndex) => <circle key={rowIndex} className={active ? 'upset-dot active' : 'upset-dot'} cx={x} cy={matrixTop + rowIndex * rowHeight + rowHeight / 2} r={active ? 6 : 4} />)}
        </g>
      })}
      {analysis.sets.map((set, index) => {
        const y = matrixTop + index * rowHeight + rowHeight / 2
        const width = set.total / maxSet * 86
        return <g key={set.id}>
          <line className="upset-row-rule" x1="18" y1={matrixTop + index * rowHeight} x2={right} y2={matrixTop + index * rowHeight} />
          <rect className="upset-set-bar" x={105 - width} y={y - 5} width={width} height="10" rx="3" />
          <text className="upset-set-total" x="99" y={y + 4} textAnchor="end">{set.total}</text>
          <text className="upset-set-label" x="225" y={y + 4} textAnchor="end"><title>{set.detail}</title>{set.label}</text>
        </g>
      })}
      <line className="upset-row-rule" x1="18" y1={matrixBottom} x2={right} y2={matrixBottom} />
    </svg>
    {activeIntersection && <div className="upset-tooltip"><b>{activeIntersection.labels.length ? activeIntersection.labels.join(' + ') : '未通过所列条件'}</b><span>{activeIntersection.count} 条 · {(activeIntersection.share * 100).toFixed(1)}%</span></div>}
  </div>
}

export interface EnergyGroup {
  target: string
  values: number[]
  color: string
}

function kernelDensity(values: number[], min: number, max: number, samples = 48) {
  if (!values.length) return []
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / Math.max(1, values.length - 1)
  const bandwidth = Math.max(4, 1.06 * Math.sqrt(variance) * Math.pow(values.length, -0.2))
  return Array.from({ length: samples }, (_, index) => {
    const value = min + (max - min) * index / (samples - 1)
    const density = values.reduce((sum, sample) => {
      const distance = (value - sample) / bandwidth
      return sum + Math.exp(-0.5 * distance * distance)
    }, 0) / (values.length * bandwidth * Math.sqrt(2 * Math.PI))
    return { value, density }
  })
}

export function RosettaEnergyViolin({ groups }: { groups: EnergyGroup[] }) {
  const threshold = -50
  const allValues = groups.flatMap((group) => group.values).filter(Number.isFinite)
  if (!allValues.length) return <div className="energy-empty">暂无界面能样本</div>
  const sorted = [...allValues].sort((left, right) => left - right)
  const quantile = (fraction: number) => sorted[Math.min(sorted.length - 1, Math.round((sorted.length - 1) * fraction))]
  const q1 = quantile(.25)
  const q3 = quantile(.75)
  const upperFence = q3 + 2.5 * Math.max(1, q3 - q1)
  const observedMin = Math.min(...allValues, threshold)
  const observedMax = Math.max(...allValues, threshold)
  const min = Math.floor((observedMin - 8) / 10) * 10
  const max = Math.ceil((Math.min(observedMax, upperFence) + 8) / 10) * 10
  const highOutliers = allValues.filter((value) => value > max)
  const top = 28
  const bottom = 264
  const y = (value: number) => top + (max - value) / (max - min) * (bottom - top)
  const ticks = Array.from({ length: 6 }, (_, index) => max - (max - min) * index / 5)
  const width = 800
  const slot = (width - 92) / groups.length
  return <div className="energy-violin" data-sample-count={allValues.length}>
    <svg viewBox="0 0 800 300" role="img" aria-label={`Rosetta 界面能小提琴图，共 ${allValues.length} 个精修样本`}>
      <g className="energy-grid">
        {ticks.map((tick) => <g key={tick}><line x1="60" y1={y(tick)} x2="784" y2={y(tick)} /><text x="51" y={y(tick) + 4} textAnchor="end">{Math.round(tick)}</text></g>)}
      </g>
      <line className="energy-threshold" x1="60" y1={y(threshold)} x2="784" y2={y(threshold)} />
      <text className="energy-threshold-label" x="778" y={y(threshold) - 7} textAnchor="end">稳定结合阈值 −50</text>
      {groups.map((group, groupIndex) => {
        const center = 76 + slot * (groupIndex + .5)
        const inRangeValues = group.values.filter((value) => value <= max)
        const density = kernelDensity(inRangeValues, min, max)
        const maxDensity = Math.max(...density.map((sample) => sample.density), .0001)
        const halfWidth = Math.min(76, slot * .34)
        const right = density.map((sample) => `${center + sample.density / maxDensity * halfWidth},${y(sample.value)}`)
        const left = [...density].reverse().map((sample) => `${center - sample.density / maxDensity * halfWidth},${y(sample.value)}`)
        const stableRate = group.values.filter((value) => value <= threshold).length / group.values.length
        return <g key={group.target}>
          <polygon points={[...right, ...left].join(' ')} fill={group.color} fillOpacity=".2" stroke={group.color} strokeWidth="1.8" />
          {group.values.map((value, valueIndex) => {
            const jitter = (((valueIndex * 37) % 29) / 28 - .5) * Math.min(42, slot * .2)
            const clipped = value > max
            return <circle key={`${value}-${valueIndex}`} cx={center + jitter} cy={clipped ? top + 3 : y(value)} r={clipped ? 4 : 2.8} fill={clipped ? '#db725e' : group.color} fillOpacity={clipped ? .95 : .65} stroke={clipped ? '#fff' : 'none'} strokeWidth="1"><title>{`${group.target} · 界面能 ${value.toFixed(1)}${clipped ? ' · 高能异常构象' : ''}`}</title></circle>
          })}
          <text className="energy-group-label" x={center} y="282" textAnchor="middle">{group.target}</text>
          <text className="energy-group-meta" x={center} y="296" textAnchor="middle">{group.values.length} 个样本 · 稳定 {Math.round(stableRate * 100)}%</text>
        </g>
      })}
      {!!highOutliers.length && <text className="energy-outlier-label" x="778" y="18" textAnchor="end">高能异常构象 {highOutliers.length} 个</text>}
      <text className="energy-axis-label" transform="translate(14 180) rotate(-90)" textAnchor="middle">界面能（越低越稳定）</text>
    </svg>
  </div>
}
