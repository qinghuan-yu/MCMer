<template>
  <div ref="pageRef" class="page" :class="pageClass">
    <div class="page-decor" aria-hidden="true">
      <div class="bg-line line-v-left decor-layer"></div>
      <div class="bg-line line-v-right decor-layer"></div>
      <div class="bg-line line-h decor-layer"></div>
      <div class="bg-line line-diagonal decor-layer"></div>
      <div class="circle-left decor-layer"></div>
      <div class="dot-grid dot-grid-left decor-layer"></div>
      <div class="dot-grid dot-grid-bottom decor-layer"></div>
      <div class="blue-block left-block decor-layer"></div>
      <div class="blue-block right-block decor-layer"></div>
      <div class="arc-right decor-layer"></div>
    </div>

    <!-- Header -->
    <header class="header">
      <div class="brand">
        <div class="brand-title">MCMer</div>
      </div>
      <nav class="nav">
        <a class="nav-item" :class="{ active: currentView === 'new' }" @click="switchView('new')">首页</a>
        <a class="nav-item" :class="{ active: currentView === 'history' || currentView === 'paper' || currentView === 'running' }" @click="switchView('history')">项目</a>
        <a class="nav-item" :class="{ active: currentView === 'settings' }" @click="switchView('settings')">设置</a>
      </nav>
      <div class="header-right">
        <span class="status-badge" :class="taskStatus">{{ statusText }}</span>
      </div>
    </header>

    <main class="view-shell">
      <Transition name="view-fade" mode="out-in">
        <section :key="currentView" class="view-stage">
          <template v-if="currentView === 'new'">
            <section class="hero">
              <div class="hero-left">
                <h1 class="title">
                  {{ heroTitleLine1 }}<br /><span>{{ heroTitleLine2 }}</span>
                </h1>
                <p class="subtitle">{{ heroSubtitle }}</p>
                <div class="entry-switcher">
                  <button
                    v-for="entry in entryOptions"
                    :key="entry.id"
                    class="entry-card"
                    :class="{ active: selectedTaskType === entry.id }"
                    @click="selectedTaskType = entry.id"
                  >
                    <span class="entry-kicker">{{ entry.kicker }}</span>
                    <strong>{{ entry.title }}</strong>
                    <small>{{ entry.description }}</small>
                  </button>
                </div>
                <div class="hero-geometry">
                  <div class="geo-circle"></div>
                  <div class="geo-line"></div>
                </div>
              </div>
              <div class="panel" data-layout-anchor="new-panel">
                <TaskInput
                  :mode="selectedTaskType"
                  @submit="handleSubmit"
                  :loading="isRunning"
                />
              </div>
            </section>

            <section class="steps">
              <template v-for="(step, index) in currentEntrySteps" :key="step.id">
                <div class="step" :class="{ 'active-step': currentStage === step.id || (!currentStage && index === 0) }">
                  <span>{{ String(index + 1).padStart(2, '0') }}</span>
                  <strong>{{ step.label }}</strong>
                </div>
                <div v-if="index < currentEntrySteps.length - 1" class="divider"></div>
              </template>
            </section>
          </template>

          <section v-else-if="currentView === 'history'" class="content-area" data-layout-anchor="history-content">
            <HistoryView
              @open-project="handleOpenProject"
              @view-paper="handleViewPaper"
              @revise="handleStartRevise"
              @new-task="switchView('new')"
            />
          </section>

          <section v-else-if="currentView === 'paper'" class="content-area" data-layout-anchor="paper-content">
            <PaperView
              :task-id="selectedTaskId"
              @back="switchView('history')"
              @revise="handleStartRevise"
              @new-task="switchView('new')"
            />
          </section>

          <section v-else-if="currentView === 'running'" class="content-area" data-layout-anchor="running-content">
            <ChatView
              :task-id="currentTaskId"
              :messages="messages"
              :progress="progress"
              :stage="currentStage"
              :result="taskResult"
              :task-type="currentTaskType"
              :is-revision="isRevision"
              :stopping="isStoppingTask"
              @back="handleRunningBack"
              @new-task="handleStartFreshTask"
              @view-paper="handleViewPaperFromResult"
              @stop="handleStopTask"
            />
          </section>

          <section v-else class="content-area">
            <SettingsPage @new-task="switchView('new')" />
          </section>
        </section>
      </Transition>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import TaskInput from './components/TaskInput.vue'
import ChatView from './components/ChatView.vue'
import HistoryView from './components/HistoryView.vue'
import PaperView from './components/PaperView.vue'
import SettingsPage from './components/SettingsPage.vue'

type TaskType = 'writing' | 'polish'

interface RuntimeMessage {
  type: string
  agent?: string
  content?: string
  section?: string
  message?: string
  data?: {
    task_id: string
    status: string
    stage: string
    progress: number
    message: string
    task_type?: TaskType
    current_subtask?: string
    paper_path?: string
    notebook_path?: string
    work_dir?: string
    error_message?: string
  }
}

interface HistoryTask {
  task_id: string
  question: string
  status: string
  task_type: TaskType
  created_at: string
  has_paper: boolean
  has_notebook: boolean
  revision_count: number
  is_revision: boolean
  parent_task_id: string
}

interface TaskDetail {
  task_id: string
  status: string
  task_type: TaskType
  work_dir: string
}

interface TaskResultPayload {
  task_id: string
  status: string
  task_type: TaskType
  paper_path: string
  notebook_path: string
  work_dir: string
  error_message?: string
}

interface TaskDraftPayload {
  taskType: TaskType
  question: string
  sourceQuestion: string
  paperContent: string
  polishingRequirements: string
  files: File[]
}

// 视图状态
type ViewState = 'new' | 'history' | 'paper' | 'running' | 'settings'
const currentView = ref<ViewState>('new')

// 任务状态
const currentTaskId = ref('')
const isRunning = ref(false)
const isRevision = ref(false)
const messages = ref<RuntimeMessage[]>([])
const progress = ref(0)
const currentStage = ref('')
const taskStatus = ref('idle')
const taskResult = ref<TaskResultPayload | null>(null)
const selectedTaskId = ref('')
const runtimeSourceView = ref<'new' | 'history'>('new')
const reconnectAttempts = ref(0)
const isStoppingTask = ref(false)
const selectedTaskType = ref<TaskType>('writing')
const currentTaskType = ref<TaskType>('writing')

let activeSocket: WebSocket | null = null
let messagePoller: number | null = null

const statusText = computed(() => {
  const map: Record<string, string> = {
    idle: '就绪',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已停止',
  }
  return map[taskStatus.value] || '就绪'
})

const pageClass = computed(() => `view-${currentView.value}`)
const entryOptions = [
  {
    id: 'writing' as TaskType,
    kicker: '7 AGENTS',
    title: '写作功能',
    description: '题目拆解、建模、审查、求解、验证、图表、成文。',
  },
  {
    id: 'polish' as TaskType,
    kicker: '5 AGENTS',
    title: '论文润色',
    description: '一致性审查、数据复核、图文核对与竞赛化措辞修订。',
  },
]
const taskTypeSteps: Record<TaskType, { id: string; label: string }[]> = {
  writing: [
    { id: 'breakdown', label: '拆解' },
    { id: 'modeling', label: '建模' },
    { id: 'review', label: '审查' },
    { id: 'solve', label: '求解' },
    { id: 'analysis', label: '验证' },
    { id: 'charts', label: '图表' },
    { id: 'writing', label: '成文' },
  ],
  polish: [
    { id: 'breakdown', label: '拆解' },
    { id: 'consistency', label: '一致性' },
    { id: 'recalculation', label: '复核' },
    { id: 'chart_consistency', label: '图文' },
    { id: 'wording', label: '润色' },
  ],
}
const currentEntrySteps = computed(() => taskTypeSteps[selectedTaskType.value])
const heroTitleLine1 = computed(() => selectedTaskType.value === 'writing' ? 'Modeling to' : 'Polish to')
const heroTitleLine2 = computed(() => selectedTaskType.value === 'writing' ? 'Submission' : 'Competition Style')
const heroSubtitle = computed(() => selectedTaskType.value === 'writing'
  ? '从题目拆解到算法求解、图表生成与论文成文的完整链路。'
  : '对已有论文执行结构审查、数值复核、图文校验与措辞修订。')

function switchView(view: ViewState) {
  currentView.value = view
}

function resetRuntimeState() {
  currentTaskId.value = ''
  messages.value = []
  progress.value = 0
  currentStage.value = ''
  taskResult.value = null
  taskStatus.value = 'idle'
  isRunning.value = false
  isRevision.value = false
  currentTaskType.value = selectedTaskType.value
  reconnectAttempts.value = 0
  isStoppingTask.value = false
}

function stopMessagePolling() {
  if (messagePoller !== null) {
    window.clearInterval(messagePoller)
    messagePoller = null
  }
}

function closeRuntimeSocket() {
  if (activeSocket) {
    activeSocket.close()
    activeSocket = null
  }
}

function stopRuntimeTracking() {
  stopMessagePolling()
  closeRuntimeSocket()
}

function prepareRuntimeView(
  taskId: string,
  isRev: boolean,
  source: 'new' | 'history',
  status: string = 'running',
  taskType: TaskType = 'writing'
) {
  stopRuntimeTracking()
  runtimeSourceView.value = source
  currentTaskId.value = taskId
  isRevision.value = isRev
  currentTaskType.value = taskType
  taskStatus.value = status
  isRunning.value = status === 'running' || status === 'pending'
  currentView.value = 'running'
  messages.value = []
  progress.value = status === 'completed' ? 1 : 0
  currentStage.value = status === 'completed' ? 'done' : ''
  taskResult.value = null
  reconnectAttempts.value = 0
}

async function fetchActiveTaskIds(): Promise<string[]> {
  const res = await fetch('/api/tasks')
  if (!res.ok) return []

  const data = await res.json()
  return Array.isArray(data.tasks)
    ? data.tasks.map((task: { task_id: string }) => task.task_id)
    : []
}

async function recoverRunningTask(
  taskId: string,
  isRev: boolean,
  source: 'new' | 'history'
) {
  if (reconnectAttempts.value >= 2 || currentTaskId.value !== taskId) {
    stopMessagePolling()
    return
  }

  reconnectAttempts.value += 1
  const activeTaskIds = await fetchActiveTaskIds()

  if (activeTaskIds.includes(taskId)) {
    startMessagePolling(taskId)
    return
  }

  startWebSocket(taskId, isRev, source, true)
}

async function fetchTaskDetail(taskId: string): Promise<TaskDetail | null> {
  const res = await fetch(`/api/tasks/${taskId}`)
  if (!res.ok) return null
  return res.json()
}

function hydrateRuntimeState(runtimeMessages: RuntimeMessage[]) {
  const progressMessages = runtimeMessages.filter(
    (msg) => msg.type === 'progress' && msg.data
  )
  const lastProgress = progressMessages[progressMessages.length - 1]
  if (lastProgress?.data) {
    progress.value = lastProgress.data.progress ?? progress.value
    currentStage.value = lastProgress.data.stage || currentStage.value
  }

  const terminalMessage = [...runtimeMessages].reverse().find(
    (msg) => msg.type === 'result' || msg.type === 'error' || msg.type === 'cancelled'
  )
  if (terminalMessage?.data) {
    if (terminalMessage.data.task_type) {
      currentTaskType.value = terminalMessage.data.task_type
    }
    if (terminalMessage.type === 'result') {
      taskResult.value = terminalMessage.data as TaskResultPayload
      taskStatus.value = 'completed'
      progress.value = 1
      currentStage.value = 'done'
    } else if (terminalMessage.type === 'error') {
      taskResult.value = terminalMessage.data as TaskResultPayload
      taskStatus.value = 'failed'
    } else {
      taskResult.value = null
      taskStatus.value = 'cancelled'
      progress.value = 1
      currentStage.value = 'done'
      isRunning.value = false
    }
  }
}

async function syncTaskMessages(taskId: string) {
  const res = await fetch(`/api/tasks/${taskId}/messages`)
  if (!res.ok) {
    if (currentTaskId.value === taskId && taskStatus.value === 'running' && !activeSocket) {
      void recoverRunningTask(taskId, isRevision.value, runtimeSourceView.value)
    } else {
      stopMessagePolling()
    }
    return
  }

  const data = await res.json()
  messages.value = Array.isArray(data.messages) ? data.messages : []
  hydrateRuntimeState(messages.value)
}

function startMessagePolling(taskId: string) {
  stopMessagePolling()
  void syncTaskMessages(taskId)
  messagePoller = window.setInterval(() => {
    if (currentTaskId.value === taskId) {
      void syncTaskMessages(taskId)
    }
  }, 1200)
}

function buildFallbackResult(task: HistoryTask, detail: TaskDetail | null) {
  if (!detail?.work_dir) return null
  if (!task.has_paper && !task.has_notebook && task.status !== 'failed' && task.status !== 'completed') {
    return null
  }

  return {
    task_id: task.task_id,
    status: detail.status || task.status,
    task_type: detail.task_type || task.task_type,
    paper_path: task.has_paper ? `${detail.work_dir}\\res.md` : '',
    notebook_path: task.has_notebook ? `${detail.work_dir}\\notebook.ipynb` : '',
    work_dir: detail.work_dir,
    error_message: task.status === 'failed' ? '任务执行失败，请查看过程日志。' : '',
  } as TaskResultPayload
}

// ============================================================
// 普通任务提交 (题目 + 数据集文件)
// ============================================================
async function handleSubmit(payload: TaskDraftPayload) {
  try {
    const createRes = await fetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: payload.question,
        task_type: payload.taskType,
        source_question: payload.sourceQuestion,
        paper_content: payload.paperContent,
        polishing_requirements: payload.polishingRequirements,
      }),
    })
    if (!createRes.ok) {
      const data = await createRes.json().catch(() => ({}))
      throw new Error(data.detail || '创建任务失败')
    }
    const task = await createRes.json()
    const taskId = task.task_id

    if (payload.files.length > 0) {
      for (const file of payload.files) {
        const formData = new FormData()
        formData.append('file', file)
        try {
          await fetch(`/api/tasks/${taskId}/upload`, {
            method: 'POST',
            body: formData,
          })
        } catch (e) {
          console.error(`上传文件 ${file.name} 失败:`, e)
        }
      }
    }

    startWebSocket(taskId, false, 'new', false, payload.taskType)
  } catch (e) {
    console.error('创建任务失败:', e)
    taskStatus.value = 'failed'
    window.alert(e instanceof Error ? e.message : '创建任务失败')
  }
}

// ============================================================
// 历史 -> 查看论文
// ============================================================
function handleViewPaper(taskId: string) {
  selectedTaskId.value = taskId
  currentView.value = 'paper'
}

async function handleOpenProject(task: HistoryTask) {
  prepareRuntimeView(task.task_id, task.is_revision, 'history', task.status, task.task_type)
  await syncTaskMessages(task.task_id)

  if (!taskResult.value) {
    const detail = await fetchTaskDetail(task.task_id)
    taskResult.value = buildFallbackResult(task, detail)
  }

  if (task.status === 'completed' && progress.value < 1) {
    progress.value = 1
    currentStage.value = currentStage.value || 'done'
    taskStatus.value = 'completed'
  }

  if (task.status === 'running' || task.status === 'pending') {
    void recoverRunningTask(task.task_id, task.is_revision, 'history')
  }
}

// ============================================================
// 开始修订
// ============================================================
async function handleStartRevise(taskId: string, feedback: string, reviseCode: boolean = false) {
  try {
    // 1. 创建修订任务
    const reviseRes = await fetch(`/api/tasks/${taskId}/revise`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task_id: taskId,
        feedback: feedback,
        revise_code: reviseCode,
      }),
    })
    const revision = await reviseRes.json()

    // 2. 启动修订任务
    startWebSocket(revision.revision_task_id, true, 'history', false, 'polish')
  } catch (e) {
    console.error('创建修订任务失败:', e)
  }
}

// ============================================================
// WebSocket 连接
// ============================================================
function startWebSocket(
  taskId: string,
  isRev: boolean,
  source: 'new' | 'history',
  preserveState: boolean = false,
  taskType: TaskType = currentTaskType.value
) {
  if (!preserveState) {
    prepareRuntimeView(taskId, isRev, source, 'running', taskType)
  } else {
    runtimeSourceView.value = source
    currentTaskId.value = taskId
    isRevision.value = isRev
    currentTaskType.value = taskType
    taskStatus.value = 'running'
    isRunning.value = true
    currentView.value = 'running'
    taskResult.value = null
  }

  if (activeSocket) {
    activeSocket.close()
    activeSocket = null
  }

  startMessagePolling(taskId)

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${window.location.host}/ws/ws/${taskId}`
  const ws = new WebSocket(wsUrl)
  activeSocket = ws

  ws.onmessage = (event) => {
    const msg: RuntimeMessage = JSON.parse(event.data)
    reconnectAttempts.value = 0

    if (msg.type === 'progress' && msg.data) {
      progress.value = msg.data.progress
      currentStage.value = msg.data.stage
      if (msg.data.task_type) {
        currentTaskType.value = msg.data.task_type
      }
    } else if (msg.type === 'result') {
      taskResult.value = msg.data as TaskResultPayload
      currentTaskType.value = (msg.data?.task_type as TaskType) || currentTaskType.value
      taskStatus.value = 'completed'
      progress.value = 1
      currentStage.value = 'done'
      isRunning.value = false
      void syncTaskMessages(taskId)
      ws.close()
    } else if (msg.type === 'error') {
      taskResult.value = msg.data as TaskResultPayload
      currentTaskType.value = (msg.data?.task_type as TaskType) || currentTaskType.value
      taskStatus.value = 'failed'
      isRunning.value = false
      void syncTaskMessages(taskId)
      ws.close()
    } else if (msg.type === 'cancelled') {
      taskResult.value = null
      taskStatus.value = 'cancelled'
      progress.value = 1
      currentStage.value = 'done'
      isRunning.value = false
      void syncTaskMessages(taskId)
      ws.close()
    } else {
      void syncTaskMessages(taskId)
    }
  }

  ws.onerror = () => {
    if (taskStatus.value !== 'running') {
      taskStatus.value = 'failed'
      isRunning.value = false
    }
  }
  ws.onclose = () => {
    if (activeSocket === ws) {
      activeSocket = null
    }

    if (taskStatus.value === 'running' && currentTaskId.value === taskId) {
      void recoverRunningTask(taskId, isRev, source)
      return
    }

    if (taskStatus.value !== 'running') {
      stopMessagePolling()
    }
    isRunning.value = false
  }
}

function handleRunningBack() {
  currentView.value = runtimeSourceView.value

  if (taskStatus.value === 'running') {
    return
  }

  stopRuntimeTracking()
  resetRuntimeState()
}

function handleStartFreshTask() {
  stopRuntimeTracking()
  resetRuntimeState()
  currentView.value = 'new'
}

async function handleStopTask(taskId: string) {
  if (!taskId || isStoppingTask.value) return

  const confirmed = window.confirm('停止后当前运行中的任务会被中断，是否继续？')
  if (!confirmed) return

  isStoppingTask.value = true
  try {
    const res = await fetch(`/api/tasks/${taskId}`, { method: 'DELETE' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail || '停止任务失败')
    }

    taskStatus.value = 'cancelled'
    isRunning.value = false
    progress.value = 1
    currentStage.value = 'done'
    messages.value = [
      ...messages.value,
      {
        type: 'cancelled',
        agent: 'system',
        message: '任务已停止',
        data: {
          task_id: taskId,
          status: 'cancelled',
          stage: 'done',
          progress: 1,
          message: '任务已停止',
        },
      },
    ]
    stopRuntimeTracking()
  } catch (error) {
    console.error('停止任务失败:', error)
    window.alert(error instanceof Error ? error.message : '停止任务失败，请重试')
  } finally {
    isStoppingTask.value = false
  }
}

function handleViewPaperFromResult(taskId: string) {
  selectedTaskId.value = taskId || currentTaskId.value
  currentView.value = 'paper'
}

onBeforeUnmount(() => {
  stopRuntimeTracking()
})
</script>

<style scoped>
.page {
  position: relative;
  min-height: 100vh;
  overflow-x: hidden;
  background:
    radial-gradient(circle at 34% 20%, rgba(255,255,255,0.98), rgba(245,246,248,0.95) 52%, #f7f7f5 100%);
}

/* ---- 背景装饰 ---- */
.page-decor {
  position: fixed;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  z-index: 1;
}

.decor-layer {
  transition:
    transform 0.72s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.72s cubic-bezier(0.22, 1, 0.36, 1);
  will-change: transform, opacity;
}

.bg-line { position: absolute; z-index: 1; background: var(--line); }
.line-v-left { left: 18.5%; top: 0; width: 1px; height: 100%; }
.line-v-right { right: 5.4%; top: 0; width: 1px; height: 100%; }
.line-h { left: 0; bottom: 18.5%; width: 72%; height: 1px; }
.line-diagonal { right: -4%; bottom: -12%; width: 1px; height: 88%; transform: rotate(42deg); transform-origin: bottom; }

.circle-left {
  position: absolute; z-index: 1;
  left: 7.5%; top: 16%;
  width: 420px; height: 420px;
  border: 1px solid rgba(20, 28, 45, 0.16);
  border-radius: 50%;
}

.dot-grid {
  position: absolute; z-index: 1;
  width: 190px; height: 130px;
  background-image: radial-gradient(rgba(20, 28, 45, 0.2) 1.4px, transparent 1.4px);
  background-size: 22px 22px;
}
.dot-grid-left { left: 3.2%; top: 50%; }
.dot-grid-bottom { right: 24%; bottom: 8%; opacity: 0.45; }

.blue-block {
  position: absolute; z-index: 1;
  background: linear-gradient(135deg, var(--blue), var(--blue-dark));
}
.left-block {
  left: -3.5%; top: 64%;
  width: 255px; height: 220px;
  clip-path: polygon(0 0, 100% 0, 100% 22%, 32% 100%, 0 100%);
}
.right-block {
  right: -7%; bottom: -12%;
  width: 490px; height: 440px;
  clip-path: polygon(40% 0, 100% 0, 100% 100%, 0 100%);
}

.arc-right {
  position: absolute; z-index: 2;
  right: -5%; bottom: -13%;
  width: 390px; height: 390px;
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 50%;
}

.page.view-new .left-block { transform: translate3d(0, 0, 0) scale(1); }
.page.view-new .right-block { transform: translate3d(0, 0, 0) scale(1); }
.page.view-new .circle-left { transform: translate3d(0, 0, 0) scale(1); }

.page.view-history .left-block { transform: translate3d(-36px, -54px, 0) scale(0.92); }
.page.view-history .right-block { transform: translate3d(-48px, 26px, 0) scale(0.96); }
.page.view-history .circle-left { transform: translate3d(24px, -18px, 0) scale(0.96); opacity: 0.72; }
.page.view-history .dot-grid-bottom { transform: translate3d(-16px, -14px, 0); opacity: 0.32; }

.page.view-paper .left-block { transform: translate3d(-18px, -36px, 0) scale(0.95); }
.page.view-paper .right-block { transform: translate3d(-24px, 10px, 0) scale(0.94); }
.page.view-paper .arc-right { transform: translate3d(-26px, -8px, 0) scale(0.97); opacity: 0.76; }

.page.view-running .left-block { transform: translate3d(20px, -64px, 0) scale(1.02); }
.page.view-running .right-block { transform: translate3d(-68px, 18px, 0) scale(1.05); }
.page.view-running .circle-left { transform: translate3d(30px, -30px, 0) scale(1.04); opacity: 0.62; }
.page.view-running .dot-grid-left { transform: translate3d(10px, -24px, 0); opacity: 0.38; }

.page.view-settings .left-block { transform: translate3d(-64px, -28px, 0) scale(0.86); opacity: 0.9; }
.page.view-settings .right-block { transform: translate3d(18px, 34px, 0) scale(0.92); opacity: 0.88; }
.page.view-settings .arc-right { transform: translate3d(12px, 22px, 0) scale(0.9); opacity: 0.58; }

/* ---- Header ---- */
.header {
  position: relative; z-index: 5;
  height: 112px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 74px;
}

.view-shell {
  position: relative;
  z-index: 3;
  width: 100%;
  min-height: calc(100vh - 112px);
  isolation: isolate;
}

.view-stage {
  min-height: calc(100vh - 112px);
  width: 100%;
  flex: 1 0 auto;
  display: flex;
  flex-direction: column;
  will-change: opacity;
}

.view-fade-enter-active,
.view-fade-leave-active {
  width: 100%;
  transition: opacity 0.42s cubic-bezier(0.22, 1, 0.36, 1);
}

.view-fade-leave-active {
  position: absolute;
  inset: 0;
}

.view-fade-enter-from,
.view-fade-leave-to {
  opacity: 0;
}

.brand-title {
  font-size: 28px;
  font-weight: 800;
  letter-spacing: -0.04em;
}

.brand-subtitle {
  margin-top: 4px;
  font-size: 15px;
  color: var(--muted);
}

.nav {
  position: absolute;
  left: 50%; top: 54px;
  transform: translateX(-50%);
  display: flex;
  gap: 84px;
}

.nav-item {
  position: relative;
  font-size: 16px;
  font-weight: 700;
  color: #111;
  text-decoration: none;
  cursor: pointer;
  transition: color 0.2s;
}

.nav-item:hover { color: var(--blue); }

.nav-item.active::after {
  content: "";
  position: absolute;
  left: 50%; bottom: -14px;
  width: 36px; height: 3px;
  transform: translateX(-50%);
  background: var(--blue);
  border-radius: 99px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-badge {
  padding: 6px 18px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  background: rgba(20,28,45,0.06);
  color: var(--muted);
}
.status-badge.running { background: rgba(36,78,168,0.1); color: var(--blue); }
.status-badge.completed { background: rgba(22,163,74,0.1); color: var(--success); }
.status-badge.failed { background: rgba(220,38,38,0.1); color: var(--error); }

/* ---- Hero ---- */
.hero {
  position: relative;
  z-index: 3;
  display: grid;
  width: 100%;
  grid-template-columns: minmax(0, 1fr) minmax(0, 0.95fr);
  align-items: center;
  gap: 80px;
  min-height: calc(100vh - 270px);
  padding: 20px 110px 0 168px;
}

.hero-left {
  position: relative;
  min-width: 0;
  padding-left: 72px;
}

.title {
  margin: 0;
  font-size: clamp(64px, 6vw, 96px);
  line-height: 0.96;
  font-weight: 900;
  letter-spacing: -0.075em;
}

.title span { color: var(--blue); }

.subtitle {
  margin-top: 42px;
  font-size: 22px;
  color: var(--muted);
  letter-spacing: 0.02em;
}

.entry-switcher {
  margin-top: 34px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  max-width: 560px;
}

.entry-card {
  border: 1px solid rgba(20, 28, 45, 0.12);
  background: rgba(255, 255, 255, 0.62);
  border-radius: 10px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-align: left;
  cursor: pointer;
  transition: transform 0.18s, border-color 0.18s, box-shadow 0.18s;
}

.entry-card:hover,
.entry-card.active {
  transform: translateY(-2px);
  border-color: rgba(36, 78, 168, 0.3);
  box-shadow: 0 16px 30px rgba(36, 78, 168, 0.08);
}

.entry-kicker {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.16em;
  color: var(--blue);
}

.entry-card strong {
  font-size: 20px;
  font-weight: 800;
}

.entry-card small {
  font-size: 13px;
  line-height: 1.55;
  color: var(--muted);
}

.hero-geometry {
  position: absolute;
  left: 0; top: 320px;
  width: 260px; height: 210px;
}

.geo-circle {
  position: absolute;
  left: 96px; top: 42px;
  width: 96px; height: 96px;
  border: 1px solid rgba(20, 28, 45, 0.18);
  border-radius: 50%;
}

.geo-line {
  position: absolute;
  left: 58px; top: 108px;
  width: 220px; height: 1px;
  background: rgba(20, 28, 45, 0.55);
  transform: rotate(-47deg);
  transform-origin: left center;
}

.panel {
  position: relative;
  justify-self: end;
  width: min(100%, 650px);
  padding: 48px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel);
  backdrop-filter: blur(18px);
  box-shadow: 0 28px 80px rgba(18, 31, 58, 0.05), inset 0 1px 0 rgba(255,255,255,0.78);
}

/* ---- Content Area (非首页) ---- */
.content-area {
  position: relative;
  z-index: 3;
  flex: 1;
  height: calc(100vh - 112px);
  padding: 24px 74px;
  max-width: 1200px;
  width: 100%;
  min-width: 0;
  margin: 0 auto;
  overflow-y: auto;
  overscroll-behavior: contain;
}

/* ---- Steps ---- */
.steps {
  position: relative;
  z-index: 5;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 20px;
  width: min(100%, 1180px);
  margin-left: 124px;
  padding-bottom: 60px;
}

.step { min-width: 88px; }

.step span {
  display: block;
  margin-bottom: 16px;
  font-size: 24px;
  font-weight: 700;
  color: var(--blue);
}

.step strong {
  font-size: 24px;
  font-weight: 800;
}

.divider {
  width: 1px;
  height: 36px;
  background: var(--line);
}

.active-step { position: relative; }

.active-step::before {
  content: "";
  position: absolute;
  top: -28px; left: -8px;
  width: 96px; height: 3px;
  background: var(--blue);
}
</style>
