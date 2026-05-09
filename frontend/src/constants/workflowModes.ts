export type WorkflowMode = 'fast' | 'standard' | 'strict'

export const workflowModeOptions = [
  { value: 'fast', label: 'fast：快速模式' },
  { value: 'standard', label: 'standard：标准模式' },
  { value: 'strict', label: 'strict：严格模式' },
] as const

export const workflowModeDetailedOptions = [
  { value: 'fast', label: 'fast：快速模式', meta: '更快出稿，适合先看结构和方向。' },
  { value: 'standard', label: 'standard：标准模式', meta: '默认平衡模式，适合大多数写作与润色任务。' },
  { value: 'strict', label: 'strict：严格模式', meta: '增加复核和约束，适合正式交付前检查。' },
] as const