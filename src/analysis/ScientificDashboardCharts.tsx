import { useMemo, useRef, useState, type PointerEvent } from 'react'

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
}

const cubeCorners: Array<[number, number, number]> = [
  [0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0],
  [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1],
]

const cubeEdges = [
  [0, 1], [0, 2], [1, 3], [2, 3], [4, 5], [4, 6], [5, 7], [6, 7],
  [0, 4], [1, 5], [2, 6], [3, 7],
]

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
    return {
      screenX: 400 + horizontal * 500,
      screenY: 184 - (dz * Math.cos(pitch) - receding * Math.sin(pitch)) * 278,
      depth: dz * Math.sin(pitch) + receding * Math.cos(pitch),
    }
  }

  const projected = useMemo(() => points.map((point) => ({
    ...point,
    ...project(point.activity, 1 - point.hemolysis, 1 - point.toxicity),
  })).sort((left, right) => left.depth - right.depth), [points, yaw, pitch])

  const frontier = projected.filter((point) => point.paretoRank === 1)
  const frontierPolygon = useMemo(() => {
    if (frontier.length < 3) return ''
    const centerX = frontier.reduce((sum, point) => sum + point.screenX, 0) / frontier.length
    const centerY = frontier.reduce((sum, point) => sum + point.screenY, 0) / frontier.length
    return [...frontier]
      .sort((left, right) => Math.atan2(left.screenY - centerY, left.screenX - centerX) - Math.atan2(right.screenY - centerY, right.screenX - centerX))
      .map((point) => `${point.screenX.toFixed(1)},${point.screenY.toFixed(1)}`)
      .join(' ')
  }, [frontier])

  const corners = cubeCorners.map(([x, y, z]) => project(x, y, z))
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
          <filter id="frontier-glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3" /></filter>
        </defs>
        <g className="pareto-cube">
          {cubeEdges.map(([start, end], index) => <line key={index} x1={corners[start].screenX} y1={corners[start].screenY} x2={corners[end].screenX} y2={corners[end].screenY} />)}
        </g>
        {frontierPolygon && <>
          <polygon className="pareto-surface-glow" points={frontierPolygon} />
          <polygon className="pareto-surface" points={frontierPolygon} />
        </>}
        <g className="pareto-points">
          {projected.map((point, index) => <circle
            key={`${point.sequence}-${index}`}
            cx={point.screenX}
            cy={point.screenY}
            r={point.paretoRank === 1 ? 5.2 : point.structureEligible ? 3.8 : 2.45}
            fill={point.color}
            fillOpacity={point.paretoRank === 1 ? .96 : point.structureEligible ? .7 : .24}
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
        </g>
      </svg>
      {hovered && <div className="pareto-tooltip">
        <b>{hovered.sequence}</b><span>{hovered.generator} · {hovered.paretoRank === 1 ? '前沿第一层' : hovered.paretoRank ? `前沿第 ${hovered.paretoRank} 层` : '未分配前沿层级'}</span>
        <div><i>抗菌</i><strong>{hovered.activity.toFixed(3)}</strong><i>溶血</i><strong>{hovered.hemolysis.toFixed(3)}</strong><i>毒性</i><strong>{hovered.toxicity.toFixed(3)}</strong></div>
      </div>}
    </div>
  )
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
