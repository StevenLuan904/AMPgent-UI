import { describe, expect, it } from 'vitest'
import { candidateGenerationLabel, formatGenerationPopulation, formatRunGenerationPopulation } from './generationPopulation'

describe('候选代际文案', () => {
  it('不会把第零代基线描述为新生子代', () => {
    expect(formatGenerationPopulation({
      baseline_candidate_count: 768,
      descendant_candidate_count: 0,
      max_generation: 0,
    })).toBe('768 条基线候选 · 尚无新生子代')
  })

  it('同时呈现基线、子代与最高代际', () => {
    expect(formatGenerationPopulation({
      baseline_candidate_count: 768,
      descendant_candidate_count: 4,
      max_generation: 3,
    })).toBe('768 条基线 · 4 条新生子代 · 最高第 3 代')
  })

  it('区分候选的基线与子代身份', () => {
    expect(candidateGenerationLabel(0)).toBe('基线候选')
    expect(candidateGenerationLabel(5)).toBe('第 5 代子代')
  })

  it('零候选轮次显示可读状态', () => {
    expect(formatRunGenerationPopulation({
      baseline_candidate_count: 0,
      descendant_candidate_count: 0,
      max_generation: 0,
    })).toBe('尚无可展示候选')
  })
})
