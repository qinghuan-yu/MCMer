export type VerificationLayerKey =
  | 'execution_verified'
  | 'evidence_supported'
  | 'model_adequate'

export interface VerificationLayerPayload {
  execution_verified?: string
  evidence_supported?: string
  model_adequate?: string
}

export interface VerificationLayerView {
  key: VerificationLayerKey
  label: string
  status: string
  statusLabel: string
  tone: 'positive' | 'negative' | 'pending'
}

export interface SubproblemCapabilityPayload {
  subproblem_id?: string
  coverage_status?: string
  model_families?: string[]
  required_operators?: string[]
  missing_operators?: string[]
  blocking_reasons?: string[]
  recovery_actions?: string[]
}

export interface SubproblemCapabilityView {
  subproblemId: string
  status: string
  statusLabel: string
  tone: 'positive' | 'negative' | 'pending'
  modelFamilies: string[]
  requiredOperators: string[]
  missingOperators: string[]
  blockingReasons: string[]
  recoveryActions: string[]
}

const layerDefinitions: Array<{ key: VerificationLayerKey; label: string }> = [
  { key: 'execution_verified', label: '执行正确性' },
  { key: 'evidence_supported', label: '证据可追溯性' },
  { key: 'model_adequate', label: '模型充分性' },
]

const statusLabels: Record<string, string> = {
  passed: '通过',
  failed: '未通过',
  blocked: '阻断',
  not_assessed: '未评估',
}

export function normalizeVerificationLayers(
  payload?: VerificationLayerPayload | null,
): VerificationLayerView[] {
  return layerDefinitions.map(({ key, label }) => {
    const status = String(payload?.[key] || 'not_assessed')
    return {
      key,
      label,
      status,
      statusLabel: statusLabels[status] || status,
      tone: status === 'passed'
        ? 'positive'
        : status === 'failed' || status === 'blocked'
          ? 'negative'
          : 'pending',
    }
  })
}

export function guidanceResultTitle(
  payload?: VerificationLayerPayload | null,
): string {
  const layers = normalizeVerificationLayers(payload)
  const execution = layers[0]
  const evidence = layers[1]
  const adequacy = layers[2]
  if (execution.status !== 'passed') {
    return execution.tone === 'negative' ? '执行正确性未通过' : '执行正确性待验证'
  }
  if (evidence.status !== 'passed') {
    return evidence.tone === 'negative' ? '证据可追溯性未通过' : '证据可追溯性待验证'
  }
  if (adequacy.status !== 'passed') {
    return adequacy.tone === 'negative' ? '模型充分性未通过' : '模型充分性待验证'
  }
  return '三层验证通过'
}

export function normalizeCapabilityCoverage(
  payload?: SubproblemCapabilityPayload[] | null,
): SubproblemCapabilityView[] {
  if (!Array.isArray(payload)) return []
  const coverageLabels: Record<string, string> = {
    complete: '已覆盖',
    partial: '部分覆盖',
    blocked: '阻断',
  }
  return payload.map((item, index) => {
    const status = String(item?.coverage_status || 'blocked')
    return {
      subproblemId: String(item?.subproblem_id || `subproblem-${index + 1}`),
      status,
      statusLabel: coverageLabels[status] || status,
      tone: status === 'complete' ? 'positive' : status === 'blocked' ? 'negative' : 'pending',
      modelFamilies: Array.isArray(item?.model_families) ? item.model_families.map(String) : [],
      requiredOperators: Array.isArray(item?.required_operators) ? item.required_operators.map(String) : [],
      missingOperators: Array.isArray(item?.missing_operators) ? item.missing_operators.map(String) : [],
      blockingReasons: Array.isArray(item?.blocking_reasons) ? item.blocking_reasons.map(String) : [],
      recoveryActions: Array.isArray(item?.recovery_actions) ? item.recovery_actions.map(String) : [],
    }
  })
}
