<template>
  <div class="chat-container">
    <div class="progress-section">
      <div class="progress-bar">
        <div class="progress-fill" :style="{ width: `${progress * 100}%` }"></div>
      </div>
      <div class="progress-info">
        <span class="progress-stage">{{ stageLabel }}</span>
        <span class="progress-percent">{{ Math.round(progress * 100) }}%</span>
      </div>
    </div>

    <div class="runtime-layout">
      <div class="messages-list" ref="messagesContainer">
        <div
          v-for="item in displayMessages"
          :key="item.id"
          class="message"
          :style="{ borderColor: item.borderColor }"
        >
          <div class="message-icon">{{ item.icon }}</div>
          <div class="message-content">
            <div class="message-meta">
              <span class="message-agent">{{ item.agentLabel }}</span>
              <span v-if="item.section" class="message-section">{{ item.section }}</span>
              <span v-if="item.progress !== null" class="message-progress">{{ item.progress }}%</span>
            </div>
            <pre v-if="item.isCode" class="message-code">{{ item.text }}</pre>
            <div v-else class="message-text">{{ item.text }}</div>
          </div>
        </div>

        <div v-if="result" class="result-card" :class="{ blocked: isGuidanceBlocked }">
          <div class="result-icon">{{ resultIcon }}</div>
          <h3>{{ resultTitle }}</h3>
          <p v-if="isGuidanceBlocked" class="result-audit">
            审计未通过：参数来源或数值追踪仍需修复，请先查看方案与审计报告。
          </p>
          <div v-if="isGuidanceResult" class="verification-layers">
            <div
              v-for="layer in verificationLayers"
              :key="layer.key"
              class="verification-layer"
              :class="layer.tone"
            >
              <span class="verification-layer-label">{{ layer.label }}</span>
              <strong>{{ layer.statusLabel }}</strong>
            </div>
          </div>
          <section v-if="capabilityCoverage.length" class="capability-coverage">
            <h4>逐子问题能力覆盖</h4>
            <article
              v-for="item in capabilityCoverage"
              :key="item.subproblemId"
              class="capability-item"
              :class="item.tone"
            >
              <div class="capability-heading">
                <strong>{{ item.subproblemId }}</strong>
                <span>{{ item.statusLabel }}</span>
              </div>
              <p v-if="item.modelFamilies.length">模型族：{{ item.modelFamilies.join(' / ') }}</p>
              <p v-if="item.missingOperators.length" class="capability-missing">
                缺失算子：{{ item.missingOperators.join(', ') }}
              </p>
              <ul v-if="item.blockingReasons.length">
                <li v-for="reason in item.blockingReasons" :key="reason">原因：{{ reason }}</li>
              </ul>
              <ul v-if="item.recoveryActions.length" class="capability-recovery">
                <li v-for="action in item.recoveryActions" :key="action">恢复：{{ action }}</li>
              </ul>
            </article>
          </section>
          <div class="result-files">
            <a
              v-if="primaryMarkdownPath"
              :href="guidanceDownloadUrl"
              download="guidance.md"
              class="file-link"
            >
              📄 下载 MD
            </a>
            <a v-if="showDocxLink" :href="getFileUrl(result.docx_path)" target="_blank" class="file-link">
              🧾 下载 DOCX
            </a>
            <a v-if="result.notebook_path" :href="getFileUrl(result.notebook_path)" target="_blank" class="file-link">
              📓 查看 Notebook
            </a>
            <a
              v-if="result.work_dir"
              :href="workspaceDownloadUrl"
              download
              class="file-link"
            >
              🗜️ 下载工作区 ZIP
            </a>
            <span v-if="result && result.work_dir" class="file-path">结果文件已保存到项目工作区</span>
          </div>
          <div v-if="result.error_message" class="result-error">❌ 错误: {{ result.error_message }}</div>
        </div>

        <div v-if="isRunning && !result" class="typing-indicator">
          <span></span><span></span><span></span>
        </div>
      </div>

      <aside class="monitor-panel">
        <div class="monitor-title">项目透视</div>

        <div class="monitor-current">
          <div class="monitor-row">
            <span class="label">当前 Agent</span>
            <span class="value">{{ stageLabel }}</span>
          </div>
          <div class="monitor-row">
            <span class="label">当前任务</span>
            <span class="value">{{ currentSubtask }}</span>
          </div>
          <div class="monitor-row">
            <span class="label">任务类型</span>
            <span class="value">{{ taskType === 'polish' ? '论文润色' : '方案生成' }}</span>
          </div>
        </div>

        <div class="agent-overview">
          <button
            v-for="agentCard in agentCards"
            :key="agentCard.id"
            class="agent-card"
            :class="{ active: selectedAgent === agentCard.id }"
            @click="selectedAgent = agentCard.id"
          >
            <span class="agent-card-title">{{ agentCard.icon }} {{ agentCard.label }}</span>
            <span class="agent-card-count">{{ agentCard.count }} 条</span>
          </button>
        </div>

        <div class="timeline-title">{{ selectedAgentTitle }}</div>
        <div class="timeline-list">
          <div v-for="item in filteredAgentMessages" :key="`${item.id}-${selectedAgent}`" class="timeline-item">
            <div class="timeline-head">
              <span class="timeline-agent">{{ item.icon }} {{ item.agentLabel }}</span>
              <span v-if="item.section" class="timeline-section">{{ item.section }}</span>
            </div>
            <pre v-if="item.isCode" class="timeline-code">{{ item.text }}</pre>
            <div v-else class="timeline-text">{{ item.text }}</div>
          </div>
          <div v-if="filteredAgentMessages.length === 0" class="timeline-empty">当前视图还没有内容。</div>
        </div>
      </aside>
    </div>

    <div class="actions-bar">
      <button class="action-btn secondary" @click="$emit('back')">← 返回</button>
      <button v-if="isRunning" class="action-btn danger" :disabled="stopping" @click="$emit('stop', props.taskId)">
        {{ stopping ? '停止中...' : '停止任务' }}
      </button>
      <button v-if="result" class="action-btn primary" @click="$emit('newTask')">🔄 新建任务</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  guidanceResultTitle,
  normalizeCapabilityCoverage,
  normalizeVerificationLayers,
} from '../verificationLayers'

type TaskType = 'guidance' | 'writing' | 'polish'

interface RuntimeMessage {
  type?: string
  agent?: string
  content?: string
  message?: string
  section?: string
  data?: {
    message?: string
    stage?: string
    progress?: number
    current_subtask?: string
    primary_artifact_type?: string
    markdown_path?: string
    guidance_path?: string
    paper_path?: string
    docx_path?: string
    notebook_path?: string
    work_dir?: string
    error_message?: string
    audit_status?: string
    audit_summary?: string
    audit_blocks?: unknown
  }
}

interface NormalizedMessage {
  id: string
  agent: string
  agentLabel: string
  icon: string
  borderColor: string
  kind: string
  text: string
  section: string
  progress: number | null
  isCode: boolean
}

interface AgentMeta {
  id: string
  label: string
  icon: string
  color: string
}

const props = defineProps<{
  taskId: string
  messages: RuntimeMessage[]
  progress: number
  stage: string
  result: Record<string, any> | null
  taskType: TaskType
  isRevision?: boolean
  stopping?: boolean
}>()

defineEmits<{
  back: []
  newTask: []
  viewPaper: [taskId: string]
  stop: [taskId: string]
}>()

const messagesContainer = ref<HTMLElement>()
const selectedAgent = ref('all')

const guidanceAgentMeta: AgentMeta[] = [
    { id: 'breakdown', label: '题目拆解', icon: '🧭', color: 'rgba(15, 118, 110, 0.22)' },
    { id: 'modeling', label: '假设与建模', icon: '🧮', color: 'rgba(245, 158, 11, 0.22)' },
    { id: 'review', label: '模型审查', icon: '🔎', color: 'rgba(220, 38, 38, 0.18)' },
    { id: 'solve', label: '算法求解', icon: '💻', color: 'rgba(36, 78, 168, 0.22)' },
    { id: 'analysis', label: '结果验证', icon: '📈', color: 'rgba(5, 150, 105, 0.22)' },
    { id: 'charts', label: '图表一致性', icon: '📊', color: 'rgba(124, 58, 237, 0.18)' },
    { id: 'writing', label: '方案组织', icon: '📝', color: 'rgba(22, 163, 74, 0.18)' },
    { id: 'final_audit', label: '最终审查', icon: '🛡️', color: 'rgba(99, 102, 241, 0.18)' },
]

const agentMetaByTaskType: Record<TaskType, AgentMeta[]> = {
  guidance: guidanceAgentMeta,
  writing: guidanceAgentMeta,
  polish: [
    { id: 'breakdown', label: '题目拆解', icon: '🧭', color: 'rgba(15, 118, 110, 0.22)' },
    { id: 'consistency', label: '模型一致性', icon: '🧩', color: 'rgba(245, 158, 11, 0.22)' },
    { id: 'recalculation', label: '数据复核', icon: '🧪', color: 'rgba(36, 78, 168, 0.22)' },
    { id: 'chart_consistency', label: '图文一致性', icon: '📊', color: 'rgba(124, 58, 237, 0.18)' },
    { id: 'wording', label: '措辞修订', icon: '✍️', color: 'rgba(22, 163, 74, 0.18)' },
  ],
}

const agentTabs = computed(() => [
  { id: 'all', label: '总览', icon: '◌', color: 'rgba(20, 28, 45, 0.12)' },
  ...agentMetaByTaskType[props.taskType],
])

const agentMetaMap = computed(() => Object.fromEntries(agentTabs.value.map((tab) => [tab.id, tab])))

const isRunning = computed(() => !props.result && props.progress < 1)
const primaryMarkdownPath = computed(() => props.result?.markdown_path || props.result?.guidance_path || props.result?.paper_path || '')
const isGuidanceResult = computed(() => props.taskType !== 'polish' || props.result?.primary_artifact_type === 'guidance')
const guidanceDownloadUrl = computed(() => {
  if (isGuidanceResult.value && props.taskId) {
    return `/api/tasks/${encodeURIComponent(props.taskId)}/guidance.md`
  }
  return getFileUrl(primaryMarkdownPath.value)
})
const workspaceDownloadUrl = computed(() => `/api/tasks/${encodeURIComponent(props.taskId)}/workspace.zip`)
const isGuidanceBlocked = computed(() => isGuidanceResult.value && String(props.result?.audit_status || '').toUpperCase() === 'BLOCK')
const verificationLayers = computed(() => normalizeVerificationLayers(props.result?.verification_layers))
const capabilityCoverage = computed(() => normalizeCapabilityCoverage(props.result?.capability_coverage))
const resultIcon = computed(() => {
  if (isGuidanceBlocked.value) return '!'
  return props.taskType === 'polish' ? 'OK' : 'MD'
})
const resultTitle = computed(() => {
  if (isGuidanceBlocked.value) return '方案需返工'
  if (props.taskType === 'polish') return '润色完成'
  return guidanceResultTitle(props.result?.verification_layers)
})
const showDocxLink = computed(() => Boolean(props.taskType === 'polish' && props.result?.docx_path && props.result?.primary_artifact_type !== 'guidance'))

const normalizedMessages = computed<NormalizedMessage[]>(() => {
  let activeAgent = normalizeAgentKey(props.stage, '', props.taskType)
  return props.messages.map((msg, index) => {
    const stage = msg.data?.stage || ''
    const section = msg.section || msg.data?.current_subtask || ''
    const inferredAgent = inferAgent(msg, stage, section, activeAgent)

    if (inferredAgent !== 'all') {
      activeAgent = inferredAgent
    }

    return normalizeMessage(msg, index, inferredAgent)
  })
})

const displayMessages = computed(() => normalizedMessages.value.slice(-80))

const filteredAgentMessages = computed(() => {
  const list = selectedAgent.value === 'all'
    ? normalizedMessages.value
    : normalizedMessages.value.filter((item) => item.agent === selectedAgent.value)
  return list.slice(-24).reverse()
})

const agentCards = computed(() => {
  return agentTabs.value.map((tab) => {
      const list = tab.id === 'all'
        ? normalizedMessages.value
        : normalizedMessages.value.filter((item) => item.agent === tab.id)
      const last = list[list.length - 1]
      return {
        ...tab,
        count: list.length,
        preview: last?.text || '暂无更新',
      }
    })
})

const selectedAgentTitle = computed(() => {
  const active = agentTabs.value.find((tab) => tab.id === selectedAgent.value)
  return active ? `${active.icon} ${active.label} 视图` : 'Agent 视图'
})

const currentSubtask = computed(() => {
  for (let i = normalizedMessages.value.length - 1; i >= 0; i -= 1) {
    const item = normalizedMessages.value[i]
    if (item.section) return item.section
  }
  return '等待任务分配...'
})

const stageLabel = computed(() => stageLabelFromStage(props.stage))

watch(() => props.taskType, () => {
  selectedAgent.value = 'all'
})

function normalizeMessage(msg: RuntimeMessage, index: number, inferredAgent?: string): NormalizedMessage {
  const kind = msg.type || 'info'
  const stage = msg.data?.stage || ''
  const agent = inferredAgent || inferAgent(msg, stage, msg.section || msg.data?.current_subtask || '', 'all')
  const meta = agentMetaMap.value[agent] || { id: agent, label: '系统', icon: '🔄', color: 'rgba(20, 28, 45, 0.12)' }
  const progress = typeof msg.data?.progress === 'number' ? Math.round(msg.data.progress * 100) : null
  const section = msg.section || msg.data?.current_subtask || ''

  let text = msg.content || msg.data?.message || msg.message || ''
  if (kind === 'result' && msg.data?.paper_path) {
    text = props.taskType === 'polish' ? '润色结果已生成，可查看论文与 Notebook。' : '建模指导方案已生成，可查看方案与 Notebook。'
  }
  if (kind === 'error' && msg.data?.error_message) {
    text = msg.data.error_message
  }
  if (!text) {
    text = section || '无消息内容'
  }

  return {
    id: `${agent}-${kind}-${index}`,
    agent,
    agentLabel: meta.label,
    icon: meta.icon,
    borderColor: meta.color,
    kind,
    text,
    section,
    progress,
    isCode: kind === 'code',
  }
}

function normalizeAgentKey(stage: string, section: string, taskType: TaskType): string {
  const stageKey = (stage || '').trim().toLowerCase()
  const sectionText = (section || '').trim()

  if (taskType !== 'polish') {
    if (stageKey === 'breakdown' || sectionText.includes('题目拆解')) return 'breakdown'
    if (stageKey === 'modeling' || sectionText.includes('模型建立') || sectionText.includes('假设与建模') || sectionText.includes('终审回退建模')) return 'modeling'
    if (stageKey === 'review' || sectionText.includes('模型审查')) return 'review'
    if (stageKey === 'solve' || sectionText.includes('算法求解') || sectionText.includes('算法与编程求解') || sectionText.includes('终审回退算法求解')) return 'solve'
    if (stageKey === 'verification' || stageKey === 'analysis' || sectionText.includes('数值复核') || sectionText.includes('结果分析与验证') || sectionText.includes('终审回退数值复核')) return 'analysis'
    if (stageKey === 'charts' || sectionText.includes('图表与一致性')) return 'charts'
    if (stageKey === 'writing' || sectionText.includes('方案组织') || sectionText.includes('论文组织与润色') || sectionText.includes('论文撰写') || sectionText.includes('审查后修订')) return 'writing'
    if (stageKey === 'delivery_audit' || stageKey === 'final_audit' || sectionText.includes('可交付终审复核') || sectionText.includes('最终审查')) return 'final_audit'
    return 'all'
  }

  if (stageKey === 'breakdown' || sectionText.includes('题目拆解')) return 'breakdown'
  if (stageKey === 'consistency' || sectionText.includes('一致性')) return 'consistency'
  if (stageKey === 'recalculation' || sectionText.includes('复核')) return 'recalculation'
  if (stageKey === 'chart_consistency' || sectionText.includes('图文一致性') || sectionText.includes('图表')) return 'chart_consistency'
  if (stageKey === 'wording' || sectionText.includes('措辞') || sectionText.includes('修订')) return 'wording'
  return 'all'
}

function inferAgent(msg: RuntimeMessage, stage: string, section: string, fallbackAgent: string): string {
  const normalizedKey = normalizeAgentKey(stage, section, props.taskType)
  if (normalizedKey !== 'all') {
    return normalizedKey
  }

  if (msg.agent === 'writer') {
    return props.taskType === 'polish' ? 'wording' : 'writing'
  }
  if (msg.agent === 'coordinator') return 'breakdown'
  if (msg.agent === 'modeler') return 'modeling'
  if (msg.agent && agentMetaMap.value[msg.agent]) return msg.agent
  if (msg.agent === 'coder' && fallbackAgent !== 'all') {
    return fallbackAgent
  }
  if (msg.type === 'code' || (msg.type === 'result' && typeof msg.content === 'string')) {
    return fallbackAgent !== 'all' ? fallbackAgent : (props.taskType === 'polish' ? 'recalculation' : 'solve')
  }
  return 'all'
}

function stageLabelFromStage(stage: string) {
  const activeKey = normalizeAgentKey(stage, '', props.taskType)
  const active = agentMetaMap.value[activeKey]
  if (stage === 'done') return '✅ 完成'
  return active ? `${active.icon} ${active.label}` : '处理中...'
}

function getFileUrl(path: string) {
  const normalized = String(path || '').replace(/\\/g, '/')
  if (!normalized) return '#'
  if (normalized.startsWith('/output/')) return normalized

  const match = normalized.match(/(?:^|\/)(?:project\/)?work_dir\/(.+)$/)
  const relative = match?.[1] || normalized.replace(/^\/+/, '')
  const encoded = relative
    .split('/')
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join('/')
  return `/output/${encoded}`
}

watch(
  () => props.messages.length,
  async () => {
    await nextTick()
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  }
)
</script>

<style scoped>
.chat-container {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 140px);
  max-width: 1220px;
  width: 100%;
  margin: 0 auto;
}

.runtime-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: 14px;
}

.progress-section {
  margin-bottom: 16px;
}

.progress-bar {
  height: 6px;
  background: var(--bg-input);
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 8px;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--primary), #244ea8);
  border-radius: 3px;
  transition: width 0.5s ease;
}

.progress-info {
  display: flex;
  justify-content: space-between;
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.messages-list {
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
  background: var(--bg-card);
  border-radius: 12px;
  border: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.message {
  display: flex;
  gap: 12px;
  padding: 14px;
  background: var(--bg-input);
  border-radius: 10px;
  border: 1px solid transparent;
  animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.message-icon {
  font-size: 1.15rem;
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(36, 78, 168, 0.08);
  border-radius: 8px;
}

.message-content {
  flex: 1;
  min-width: 0;
}

.message-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 0.76rem;
}

.message-agent,
.message-section,
.message-progress {
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(20, 28, 45, 0.06);
  color: var(--text-secondary);
}

.message-text,
.timeline-text {
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-code,
.timeline-code {
  margin: 0;
  padding: 12px;
  border-radius: 8px;
  background: #0f172a;
  color: #dbeafe;
  font-size: 0.82rem;
  line-height: 1.55;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.monitor-panel {
  min-height: 0;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-card);
  display: flex;
  flex-direction: column;
  scrollbar-gutter: stable;
}

.monitor-title,
.timeline-title {
  padding: 12px 14px;
  font-size: 0.92rem;
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}

.monitor-current {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.monitor-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
}

.monitor-row .label {
  color: var(--text-secondary);
  font-size: 0.82rem;
  flex-shrink: 0;
}

.monitor-row .value {
  font-size: 0.86rem;
  text-align: right;
  word-break: break-word;
}

.agent-overview {
  padding: 12px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  border-bottom: 1px solid var(--border);
}

.agent-card {
  border: 1px solid var(--border);
  background: var(--bg-input);
  border-radius: 10px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s, transform 0.2s;
}

.agent-card.active,
.agent-card:hover {
  border-color: rgba(36, 78, 168, 0.34);
  transform: translateY(-1px);
}

.agent-card-title {
  font-size: 0.82rem;
  font-weight: 700;
}

.agent-card-count {
  font-size: 0.76rem;
  color: var(--text-secondary);
}

.agent-card-text {
  font-size: 0.78rem;
  color: var(--text-secondary);
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.timeline-list {
  padding: 10px 12px;
  overflow: visible;
  min-height: 0;
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.timeline-item {
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-input);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.timeline-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
}

.timeline-agent {
  font-size: 0.78rem;
  color: var(--text-secondary);
}

.timeline-section {
  font-size: 0.74rem;
  color: var(--text-secondary);
  background: rgba(20, 28, 45, 0.06);
  padding: 2px 8px;
  border-radius: 999px;
}

.timeline-empty {
  color: var(--text-secondary);
  font-size: 0.85rem;
  padding: 6px 4px;
}

.result-card {
  background: linear-gradient(135deg, rgba(36, 78, 168, 0.08), rgba(15, 118, 110, 0.08));
  border: 1px solid rgba(36, 78, 168, 0.24);
  border-radius: 12px;
  padding: 24px;
  text-align: center;
}

.result-card.blocked {
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.12), rgba(220, 38, 38, 0.08));
  border-color: rgba(220, 38, 38, 0.28);
}

.result-icon {
  font-size: 3rem;
  margin-bottom: 8px;
}

.result-card h3 {
  margin-bottom: 16px;
}

.result-audit {
  max-width: 520px;
  margin: -4px auto 16px;
  color: #92400e;
  font-size: 0.92rem;
  line-height: 1.6;
}

.verification-layers {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  max-width: 620px;
  margin: 0 auto 18px;
  text-align: left;
}

.verification-layer {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid rgba(100, 116, 139, 0.24);
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.78);
}

.verification-layer.positive {
  border-color: rgba(5, 150, 105, 0.35);
  color: #047857;
}

.verification-layer.negative {
  border-color: rgba(220, 38, 38, 0.35);
  color: #b91c1c;
}

.verification-layer.pending {
  border-color: rgba(245, 158, 11, 0.4);
  color: #92400e;
}

.verification-layer-label {
  color: var(--text-secondary);
  font-size: 0.78rem;
}

.capability-coverage {
  max-width: 720px;
  margin: 0 auto 18px;
  text-align: left;
}

.capability-coverage h4 {
  margin: 0 0 10px;
  color: var(--text-primary);
}

.capability-item {
  margin-top: 8px;
  padding: 12px 14px;
  border: 1px solid rgba(100, 116, 139, 0.24);
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.78);
}

.capability-item.positive { border-color: rgba(5, 150, 105, 0.35); }
.capability-item.negative { border-color: rgba(220, 38, 38, 0.35); }
.capability-item.pending { border-color: rgba(245, 158, 11, 0.4); }

.capability-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.capability-item p,
.capability-item ul {
  margin: 7px 0 0;
  font-size: 0.84rem;
}

.capability-item ul {
  padding-left: 18px;
}

.capability-missing { color: #b91c1c; }
.capability-recovery { color: #047857; }

@media (max-width: 640px) {
  .verification-layers {
    grid-template-columns: 1fr;
  }
}

.result-files {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
}

.file-link {
  display: inline-block;
  padding: 8px 20px;
  background: var(--primary);
  color: white;
  text-decoration: none;
  border-radius: 6px;
  font-weight: 500;
  transition: background 0.2s;
}

.file-link:hover {
  background: var(--primary-hover);
}

.file-path {
  font-size: 0.8rem;
  color: var(--text-secondary);
  margin-top: 4px;
}

.result-error {
  margin-top: 12px;
  padding: 12px;
  background: rgba(239, 68, 68, 0.1);
  border-radius: 8px;
  color: var(--error);
}

.typing-indicator {
  display: flex;
  gap: 6px;
  padding: 12px;
  justify-content: center;
}

.typing-indicator span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--primary);
  animation: typing 1.4s infinite;
}

.typing-indicator span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typing {
  0%, 60%, 100% {
    opacity: 0.3;
    transform: scale(0.8);
  }
  30% {
    opacity: 1;
    transform: scale(1);
  }
}

.actions-bar {
  display: flex;
  justify-content: space-between;
  padding: 16px 0;
  gap: 12px;
}

.action-btn {
  height: 42px;
  padding: 0 18px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: white;
  font-weight: 700;
  cursor: pointer;
}

.action-btn.primary {
  background: var(--primary);
  color: white;
  border-color: var(--primary);
}

.action-btn.danger {
  color: var(--error);
  border-color: rgba(220, 38, 38, 0.2);
  background: rgba(220, 38, 38, 0.06);
}

.action-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

@media (max-width: 1180px) {
  .chat-container {
    height: auto;
    min-height: calc(100dvh - 132px);
  }

  .runtime-layout {
    grid-template-columns: 1fr;
  }

  .messages-list {
    max-height: min(58dvh, 620px);
  }

  .monitor-panel {
    max-height: none;
    overflow: visible;
  }

  .agent-overview {
    display: flex;
    gap: 10px;
    overflow-x: auto;
    padding-bottom: 14px;
    scrollbar-gutter: auto;
  }

  .agent-card {
    flex: 0 0 168px;
  }

  .timeline-list {
    max-height: 320px;
    overflow-y: auto;
  }
}

@media (max-width: 720px) {
  .chat-container {
    min-height: calc(100dvh - 126px);
  }

  .progress-section {
    margin-bottom: 12px;
  }

  .runtime-layout {
    gap: 12px;
  }

  .messages-list {
    max-height: none;
    min-height: 360px;
    padding: 12px;
    gap: 10px;
    border-radius: 10px;
  }

  .message {
    gap: 10px;
    padding: 12px;
    border-radius: 10px;
  }

  .message-icon {
    width: 30px;
    height: 30px;
    font-size: 1rem;
  }

  .message-meta {
    gap: 6px;
  }

  .message-agent,
  .message-section,
  .message-progress {
    max-width: 100%;
    overflow-wrap: anywhere;
  }

  .monitor-title,
  .timeline-title,
  .monitor-current {
    padding-inline: 12px;
  }

  .monitor-row {
    flex-direction: column;
    gap: 3px;
  }

  .monitor-row .value {
    text-align: left;
  }

  .agent-overview {
    grid-template-columns: none;
    padding: 10px 12px 12px;
  }

  .agent-card {
    flex-basis: 146px;
    padding: 9px;
  }

  .timeline-list {
    max-height: 300px;
    padding-inline: 10px;
  }

  .timeline-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .result-card {
    padding: 18px 14px;
  }

  .result-icon {
    font-size: 2.2rem;
  }

  .file-link {
    width: 100%;
    text-align: center;
  }

  .file-path {
    overflow-wrap: anywhere;
  }

  .actions-bar {
    position: sticky;
    bottom: 0;
    z-index: 8;
    margin: 0 -16px -28px;
    padding: 12px 16px calc(12px + env(safe-area-inset-bottom));
    background: rgba(248, 248, 246, 0.94);
    backdrop-filter: blur(10px);
    flex-wrap: wrap;
  }

  .action-btn {
    flex: 1 1 140px;
    min-width: 0;
  }
}

@media (max-width: 430px) {
  .messages-list {
    min-height: 320px;
  }

  .message {
    flex-direction: column;
  }

  .message-icon {
    width: 28px;
    height: 28px;
  }

  .agent-card {
    flex-basis: 132px;
  }

  .actions-bar {
    margin-inline: -12px;
    padding-inline: 12px;
  }
}
</style>
