import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
    guidanceResultTitle,
    normalizeCapabilityCoverage,
    normalizeVerificationLayers,
} from '../src/verificationLayers.ts'


test('normalizes and labels all three mathematical verification layers', () => {
  const layers = normalizeVerificationLayers({
    execution_verified: 'passed',
    evidence_supported: 'failed',
    model_adequate: 'not_assessed',
  })

  assert.deepEqual(
    layers.map(({ key, label, status }) => ({ key, label, status })),
    [
      { key: 'execution_verified', label: '执行正确性', status: 'passed' },
      { key: 'evidence_supported', label: '证据可追溯性', status: 'failed' },
      { key: 'model_adequate', label: '模型充分性', status: 'not_assessed' },
    ],
  )
})


test('guidance result title describes the weakest layer instead of workflow completion', () => {
  assert.equal(
    guidanceResultTitle({
      execution_verified: 'passed',
      evidence_supported: 'passed',
      model_adequate: 'not_assessed',
    }),
    '模型充分性待验证',
  )
  assert.notEqual(guidanceResultTitle(undefined), '方案完成')
})


test('chat result card renders every verification layer instead of a completion badge', () => {
  const source = readFileSync(new URL('../src/components/ChatView.vue', import.meta.url), 'utf8')

  assert.match(source, /normalizeVerificationLayers/)
  assert.match(source, /v-for="layer in verificationLayers"/)
  assert.match(source, /layer\.label/)
  assert.match(source, /layer\.statusLabel/)
  assert.doesNotMatch(source, /方案完成/)
})


test('history hydration forwards verification layers to the result card', () => {
  const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

  assert.match(source, /verification_layers\?: VerificationLayerPayload/)
  assert.match(source, /verification_layers: context\?\.verification_layers/)
})


test('workflow completion labels do not imply mathematical completion', () => {
  const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  const historySource = readFileSync(
    new URL('../src/components/HistoryView.vue', import.meta.url),
    'utf8',
  )

  assert.doesNotMatch(appSource, /completed: '已完成'/)
  assert.doesNotMatch(historySource, /completed: '已完成'/)
  assert.match(appSource, /completed: '流程已结束'/)
  assert.match(historySource, /completed: '流程已结束'/)
})


test('normalizes subproblem capability coverage with missing operators and recovery actions', () => {
  const coverage = normalizeCapabilityCoverage([
    {
      subproblem_id: 'problem2',
      coverage_status: 'blocked',
      model_families: ['simulation'],
      required_operators: ['pde.finite_element'],
      missing_operators: ['pde.finite_element'],
      blocking_reasons: ['PDE operator is unavailable.'],
      recovery_actions: ['Register a validated PDE operator.'],
    },
  ])

  assert.equal(coverage[0].statusLabel, '阻断')
  assert.equal(coverage[0].tone, 'negative')
  assert.deepEqual(coverage[0].missingOperators, ['pde.finite_element'])
  assert.deepEqual(coverage[0].recoveryActions, ['Register a validated PDE operator.'])
})


test('chat and history expose subproblem capability coverage', () => {
  const chatSource = readFileSync(new URL('../src/components/ChatView.vue', import.meta.url), 'utf8')
  const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

  assert.match(chatSource, /normalizeCapabilityCoverage/)
  assert.match(chatSource, /v-for="item in capabilityCoverage"/)
  assert.match(chatSource, /item\.missingOperators/)
  assert.match(chatSource, /item\.recoveryActions/)
  assert.match(appSource, /capability_coverage\?: SubproblemCapabilityPayload\[\]/)
  assert.match(appSource, /capability_coverage: context\?\.capability_coverage/)
})
