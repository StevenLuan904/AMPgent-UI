import {
  AnalysisCardRejectedError,
  type AnalysisCardQueryState,
  type CardGridSize,
  type CardPresentationMode,
  type CardPresentationPlan,
  type CardVisualElement,
  type PivotChartType,
} from './analysisDataContracts'

const MINIMUM_SIZE: Record<PivotChartType, CardGridSize> = {
  kpi: { width: 220, height: 140 },
  funnel: { width: 300, height: 210 },
  bar: { width: 300, height: 210 },
  stacked_bar: { width: 340, height: 230 },
  boxplot: { width: 340, height: 230 },
  violin: { width: 440, height: 280 },
  histogram: { width: 320, height: 220 },
  ecdf: { width: 340, height: 230 },
  scatter: { width: 360, height: 240 },
  parallel: { width: 560, height: 320 },
  heatmap: { width: 380, height: 250 },
  sankey: { width: 460, height: 280 },
  table: { width: 520, height: 260 },
}

const ALL_ELEMENTS: CardVisualElement[] = [
  'title', 'primary_value', 'primary_chart', 'axes', 'legend', 'filters', 'summary',
  'details', 'warnings', 'provenance', 'actions',
]

function modeFor(size: CardGridSize): CardPresentationMode {
  if (size.width < 420 || size.height < 250) return 'compact'
  if (size.width < 760 || size.height < 430) return 'standard'
  return 'expanded'
}

function compactChart(chart: PivotChartType): PivotChartType {
  if (chart === 'violin') return 'boxplot'
  if (chart === 'sankey') return 'funnel'
  if (chart === 'heatmap' || chart === 'stacked_bar') return 'bar'
  if (chart === 'parallel' || chart === 'table') return 'kpi'
  return chart
}

function visibleFor(mode: CardPresentationMode, chart: PivotChartType): CardVisualElement[] {
  if (mode === 'compact') {
    return chart === 'kpi'
      ? ['title', 'primary_value', 'warnings', 'actions']
      : ['title', 'primary_value', 'primary_chart', 'warnings', 'actions']
  }
  if (mode === 'standard') {
    return ['title', 'primary_value', 'primary_chart', 'axes', 'legend', 'filters', 'summary', 'warnings', 'actions']
  }
  return [...ALL_ELEMENTS]
}

export function planCardPresentation(
  card: AnalysisCardQueryState,
  size: CardGridSize,
): CardPresentationPlan {
  if (!Number.isFinite(size.width) || !Number.isFinite(size.height) || size.width < 180 || size.height < 120) {
    throw new AnalysisCardRejectedError([{
      code: 'card_too_small', path: 'size',
      message: `Card size ${size.width}×${size.height} is below the absolute readable minimum of 180×120.`,
    }])
  }
  const mode = modeFor(size)
  const effectiveChart = mode === 'compact' ? compactChart(card.chart) : card.chart
  const minimumSize = MINIMUM_SIZE[effectiveChart]
  const visible = visibleFor(mode, effectiveChart)
  const hidden = ALL_ELEMENTS
    .filter((element) => !visible.includes(element))
    .map((element) => ({
      element,
      reason: mode === 'compact'
        ? 'Hidden at compact size to preserve the primary signal.'
        : 'Available when the card is expanded.',
    }))
  return {
    mode,
    effectiveChart,
    visible,
    hidden,
    minimumSize,
    density: mode === 'compact' ? 'tight' : mode === 'standard' ? 'comfortable' : 'spacious',
    showLabels: mode === 'compact' ? 'key_only' : mode === 'standard' ? 'selected' : 'all',
  }
}

export function recommendedGridMinimum(chart: PivotChartType): CardGridSize {
  return { ...MINIMUM_SIZE[chart] }
}

