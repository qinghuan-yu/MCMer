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

        <div v-if="result" class="result-card">
          <div class="result-icon">{{ taskType === 'polish' ? '✨' : '🎉' }}</div>
          <h3>{{ taskType === 'polish' ? '润色完成' : '写作完成' }}</h3>
          <div class="result-files">
            <a v-if="result.paper_path" :href="getFileUrl(result.paper_path)" target="_blank" class="file-link">
              📄 查看论文
            </a>
            <a v-if="result.notebook_path" :href="getFileUrl(result.notebook_path)" target="_blank" class="file-link">
              📓 查看 Notebook
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
            <span class="value">{{ taskType === 'polish' ? '论文润色' : '论文写作' }}</span>
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
            <span class="agent-card-text">{{ agentCard.preview }}</span>
          </button>
        </div>

        <div class="agent-tabs">
          <button
            v-for="tab in agentTabs"
            :key="tab.id"
            class="agent-tab"
            :class="{ active: selectedAgent === tab.id }"
            @click="selectedAgent = tab.id"
          >
            {{ tab.icon }} {{ tab.label }}
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

type TaskType = 'writing' | 'polish'

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
    paper_path?: string
    notebook_path?: string
    work_dir?: string
    error_message?: string
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

const agentMetaByTaskType: Record<TaskType, AgentMeta[]> = {
  writing: [
    { id: 'breakdown', label: '题目拆解', icon: '🧭', color: 'rgba(15, 118, 110, 0.22)' },
    { id: 'modeling', label: '假设与建模', icon: '🧮', color: 'rgba(245, 158, 11, 0.22)' },
    { id: 'review', label: '模型审查', icon: '🔎', color: 'rgba(220, 38, 38, 0.18)' },
    { id: 'solve', label: '算法求解', icon: '💻', color: 'rgba(36, 78, 168, 0.22)' },
    { id: 'analysis', label: '结果验证', icon: '📈', color: 'rgba(5, 150, 105, 0.22)' },
    { id: 'charts', label: '图表一致性', icon: '📊', color: 'rgba(124, 58, 237, 0.18)' },
    { id: 'writing', label: '论文组织', icon: '📝', color: 'rgba(22, 163, 74, 0.18)' },
  ],
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

const normalizedMessages = computed<NormalizedMessage[]>(() => {
  return props.messages.map((msg, index) => normalizeMessage(msg, index))
})

const displayMessages = computed(() => normalizedMessages.value.slice(-80))

const filteredAgentMessages = computed(() => {
  const list = selectedAgent.value === 'all'
    ? normalizedMessages.value
    : normalizedMessages.value.filter((item) => item.agent === selectedAgent.value)
  return list.slice(-24).reverse()
})

const agentCards = computed(() => {
  return agentTabs.value
    .filter((tab) => tab.id !== 'all')
    .map((tab) => {
      const list = normalizedMessages.value.filter((item) => item.agent === tab.id)
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

function normalizeMessage(msg: RuntimeMessage, index: number): NormalizedMessage {
  const kind = msg.type || 'info'
  const stage = msg.data?.stage || ''
  const agent = inferAgent(msg, stage)
  const meta = agentMetaMap.value[agent] || { id: agent, label: '系统', icon: '🔄', color: 'rgba(20, 28, 45, 0.12)' }
  const progress = typeof msg.data?.progress === 'number' ? Math.round(msg.data.progress * 100) : null
  const section = msg.section || msg.data?.current_subtask || ''

  let text = msg.content || msg.data?.message || msg.message || ''
  if (kind === 'result' && msg.data?.paper_path) {
    text = props.taskType === 'polish' ? '润色结果已生成，可查看论文与 Notebook。' : '项目结果已生成，可查看论文与 Notebook。'
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

function inferAgent(msg: RuntimeMessage, stage: string): string {
  if (msg.agent === 'coder') {
    return props.taskType === 'polish' ? 'recalculation' : 'solve'
  }
  if (msg.agent === 'writer') {
    return props.taskType === 'polish' ? 'wording' : 'writing'
  }
  if (msg.agent === 'coordinator') return 'breakdown'
  if (msg.agent === 'modeler') return 'modeling'
  if (msg.agent && agentMetaMap.value[msg.agent]) return msg.agent
  if (stage && agentMetaMap.value[stage]) return stage
  if (msg.type === 'code' || (msg.type === 'result' && typeof msg.content === 'string')) {
    return props.taskType === 'polish' ? 'recalculation' : 'solve'
  }
  return 'all'
}

function stageLabelFromStage(stage: string) {
  const active = agentMetaMap.value[stage]
  if (stage === 'done') return '✅ 完成'
  return active ? `${active.icon} ${active.label}` : '处理中...'
}

function getFileUrl(path: string) {
  const parts = path.replace(/\\/g, '/').split('/project/work_dir/')
  const relative = parts.length > 1 ? parts[1] : path
  return `/output/${relative}`
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
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-card);
  display: flex;
  flex-direction: column;
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

.agent-tabs {
  padding: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  border-bottom: 1px solid var(--border);
}

.agent-tab {
  border: 1px solid var(--border);
  background: transparent;
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.2s;
}

.agent-tab.active {
  background: rgba(36, 78, 168, 0.1);
  border-color: rgba(36, 78, 168, 0.32);
  color: var(--blue);
}

.timeline-list {
  padding: 10px 12px;
  overflow-y: auto;
  min-height: 0;
  flex: 1;
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

.result-icon {
  font-size: 3rem;
  margin-bottom: 8px;
}

.result-card h3 {
  margin-bottom: 16px;
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
</style>