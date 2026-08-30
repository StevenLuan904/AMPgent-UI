import type { GenerationPopulation } from './types'

export function formatGenerationPopulation(population: GenerationPopulation) {
  if (population.baseline_candidate_count === 0 && population.descendant_candidate_count === 0) {
    return '尚无可展示候选'
  }
  if (population.descendant_candidate_count === 0) {
    return `${population.baseline_candidate_count.toLocaleString()} 条基线候选 · 尚无新生子代`
  }
  return `${population.baseline_candidate_count.toLocaleString()} 条基线 · ${population.descendant_candidate_count.toLocaleString()} 条新生子代 · 最高第 ${population.max_generation} 代`
}

export function candidateGenerationLabel(generation: number) {
  return generation === 0 ? '基线候选' : `第 ${generation} 代子代`
}
