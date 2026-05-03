<template>
  <div class="history-container">
    <div class="history-header">
      <h2>历史项目</h2>
      <span class="task-count">共 {{ tasks.length }} 个项目</span>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="loading-state">
      <div class="spinner"></div>
      <span>加载中...</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="tasks.length === 0" class="empty-state">
      <h3>还没有历史项目</h3>
      <p>完成一次数学建模任务后，项目将出现在这里</p>
    </div>

    <!-- 任务列表 -->
    <div v-else class="task-list">
      <div
        v-for="task in tasks"
        :key="task.task_id"
        class="task-card"
        :class="{
          'has-paper': task.has_paper,
          'is-revision': task.is_revision,
        }"
        tabindex="0"
        role="button"
        @click="openTask(task)"
        @keydown.enter.prevent="openTask(task)"
        @keydown.space.prevent="openTask(task)"
      >
        <div class="task-card-header">
          <div class="task-title-block">
            <span v-if="task.is_revision" class="revision-badge">修订</span>
            <span class="task-title">{{ getProjectTitle(task) }}</span>
          </div>
          <span class="task-status" :class="task.status">
            {{ statusMap[task.status] || task.status }}
          </span>
        </div>

        <div class="task-question" v-if="task.question">
          {{ task.question }}
        </div>
        <div class="task-question" v-else>
          <em>无题目信息</em>
        </div>

        <div class="task-meta">
          <span v-if="task.created_at" class="meta-item">
            🕐 {{ formatDate(task.created_at) }}
          </span>
          <span v-if="task.has_paper" class="meta-item success">📄 有论文</span>
          <span v-if="task.has_notebook" class="meta-item info">📓 有代码</span>
          <span v-if="task.revision_count > 0" class="meta-item warning">
            🔄 {{ task.revision_count }} 次修订
          </span>
        </div>

        <div class="task-actions">
          <button
            class="btn-open"
            @click.stop="openTask(task)"
          >
            打开项目
          </button>
          <button
            v-if="task.has_paper"
            class="btn-view"
            @click.stop="$emit('viewPaper', task.task_id)"
          >
            📄 查看论文
          </button>
          <button
            v-if="task.has_paper"
            class="btn-revise"
            @click.stop="startRevise(task)"
          >
            ✏️ 修订论文
          </button>
          <button
            v-if="task.status !== 'running' && task.status !== 'pending'"
            class="btn-delete"
            :disabled="deletingTaskId === task.task_id"
            @click.stop="deleteTask(task)"
          >
            {{ deletingTaskId === task.task_id ? '删除中...' : '🗑️ 删除项目' }}
          </button>
          <span v-if="!task.has_paper && task.status === 'failed'" class="hint-text">
            任务未完成，无可用论文
          </span>
        </div>
      </div>
    </div>

    <!-- 修订对话框 -->
    <div v-if="showReviseDialog" class="dialog-overlay" @click.self="closeReviseDialog">
      <div class="dialog-card">
        <h3>✏️ 修订论文</h3>
        <p class="dialog-subtitle">
          对当前项目的论文提出修改建议
        </p>
        <textarea
          v-model="reviseFeedback"
          placeholder="请描述你希望如何修改论文，例如：
- 在模型假设部分增加正态分布假设
- 修改第三章的算法，使用遗传算法替代贪心算法
- 增加灵敏度分析章节
- 修正符号说明中的变量定义
- 重新做数据可视化图表"
          rows="8"
          class="revise-input"
        ></textarea>
        <label class="checkbox-label">
          <input type="checkbox" v-model="reviseWithCode" />
          同时让代码手重新执行相关代码
        </label>
        <div class="dialog-actions">
          <button class="btn-cancel" @click="closeReviseDialog">取消</button>
          <button
            class="btn-submit"
            :disabled="!reviseFeedback.trim()"
            @click="submitRevise"
          >
            🚀 提交修订
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface HistoryTask {
  task_id: string
  question: string
  status: string
  task_type: 'writing' | 'polish'
  created_at: string
  has_paper: boolean
  has_notebook: boolean
  revision_count: number
  is_revision: boolean
  parent_task_id: string
}

const emit = defineEmits<{
  openProject: [task: HistoryTask]
  viewPaper: [taskId: string]
  revise: [taskId: string, feedback: string, reviseCode: boolean]
  newTask: []
}>()

const tasks = ref<HistoryTask[]>([])
const loading = ref(true)
const deletingTaskId = ref('')

// 修订对话框
const showReviseDialog = ref(false)
const reviseTargetId = ref('')
const reviseFeedback = ref('')
const reviseWithCode = ref(false)

const statusMap: Record<string, string> = {
  pending: '等待中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function formatDate(isoStr: string): string {
  if (!isoStr) return ''
  try {
    const d = new Date(isoStr)
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return isoStr
  }
}

function startRevise(task: HistoryTask) {
  reviseTargetId.value = task.task_id
  reviseFeedback.value = ''
  reviseWithCode.value = false
  showReviseDialog.value = true
}

function getProjectTitle(task: HistoryTask) {
  if (task.task_type === 'polish') {
    return task.is_revision ? '润色修订项目' : '论文润色项目'
  }
  return task.is_revision ? '写作修订项目' : '数学建模项目'
}

function openTask(task: HistoryTask) {
  emit('openProject', task)
}

function closeReviseDialog() {
  showReviseDialog.value = false
}

function submitRevise() {
  if (!reviseFeedback.value.trim()) return
  emit('revise', reviseTargetId.value, reviseFeedback.value.trim(), reviseWithCode.value)
  showReviseDialog.value = false
}

async function loadHistory() {
  const res = await fetch('/api/history')
  const text = await res.text()
  let data: any = {}
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = {}
    }
  }

  if (!res.ok) {
    throw new Error(data.detail || data.message || text || `加载历史失败 (HTTP ${res.status})`)
  }

  tasks.value = data.tasks || []
}

async function deleteTask(task: HistoryTask) {
  const confirmed = window.confirm('删除后将移除该项目的工作目录、论文和消息记录，是否继续？')
  if (!confirmed) return

  deletingTaskId.value = task.task_id
  try {
    const res = await fetch(`/api/history/${task.task_id}`, { method: 'DELETE' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail || '删除失败')
    }
    tasks.value = tasks.value.filter((item) => item.task_id !== task.task_id)
  } catch (error) {
    console.error('删除历史项目失败:', error)
    window.alert(error instanceof Error ? error.message : '删除失败，请重试')
  } finally {
    deletingTaskId.value = ''
  }
}

onMounted(async () => {
  try {
    await loadHistory()
  } catch (e) {
    console.error('加载历史失败:', e)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.history-container {
  max-width: 900px;
  width: 100%;
  margin: 0 auto;
  padding: 16px 0;
}

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.history-header h2 {
  font-size: 1.3rem;
}

.task-count {
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 60px;
  color: var(--text-secondary);
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.empty-state {
  text-align: center;
  padding: 60px 20px;
  background: var(--bg-card);
  border-radius: 12px;
  border: 1px solid var(--border);
}

.empty-icon { font-size: 3rem; margin-bottom: 12px; }
.empty-state h3 { margin-bottom: 8px; }
.empty-state p { color: var(--text-secondary); margin-bottom: 20px; }

.task-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.task-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 20px;
  cursor: pointer;
  transition: border-color 0.2s, transform 0.2s, box-shadow 0.2s;
}

.task-card:hover,
.task-card:focus-visible {
  transform: translateY(-2px);
  border-color: rgba(36, 78, 168, 0.3);
  box-shadow: 0 14px 30px rgba(20, 28, 45, 0.08);
  outline: none;
}

.task-card.has-paper {
  border-left: 3px solid var(--success);
}

.task-card.is-revision {
  border-left: 3px solid var(--warning);
}

.task-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.task-title-block {
  display: flex;
  align-items: center;
  gap: 8px;
}

.task-title {
  font-size: 0.94rem;
  font-weight: 700;
  color: var(--text);
}

.revision-badge {
  background: rgba(245, 158, 11, 0.2);
  color: var(--warning);
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 0.75rem;
  font-weight: 600;
}

.task-status {
  font-size: 0.8rem;
  padding: 2px 10px;
  border-radius: 10px;
  background: var(--bg-input);
}

.task-status.completed {
  background: rgba(34, 197, 94, 0.15);
  color: var(--success);
}

.task-status.failed {
  background: rgba(239, 68, 68, 0.15);
  color: var(--error);
}

.task-status.running {
  background: rgba(59, 130, 246, 0.15);
  color: var(--info);
}

.task-question {
  font-size: 0.9rem;
  color: var(--text);
  margin-bottom: 10px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.task-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 12px;
}

.meta-item {
  font-size: 0.8rem;
  color: var(--text-secondary);
}

.meta-item.success { color: var(--success); }
.meta-item.info { color: var(--info); }
.meta-item.warning { color: var(--warning); }
.meta-item.muted { color: var(--text-secondary); opacity: 0.6; }

.task-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.btn-open,
.btn-view,
.btn-revise {
  padding: 6px 16px;
  border: none;
  border-radius: 6px;
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-open {
  background: rgba(36, 78, 168, 0.1);
  color: var(--blue);
}

.btn-open:hover {
  background: rgba(36, 78, 168, 0.16);
}

.btn-view {
  background: var(--bg-input);
  color: var(--text);
}

.btn-view:hover {
  background: var(--border);
}

.btn-revise {
  background: linear-gradient(135deg, var(--primary), #a855f7);
  color: white;
}

.btn-revise:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 15px rgba(99, 102, 241, 0.3);
}

.btn-delete {
  background: rgba(254, 242, 242, 0.96);
  color: #991b1b;
  border: 1px solid rgba(185, 28, 28, 0.16);
}

.btn-delete:disabled {
  cursor: not-allowed;
  opacity: 0.62;
}

.hint-text {
  font-size: 0.8rem;
  color: var(--text-secondary);
  font-style: italic;
}

/* 对话框 */
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.dialog-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 28px;
  width: 90%;
  max-width: 600px;
}

.dialog-card h3 {
  margin-bottom: 4px;
}

.dialog-subtitle {
  color: var(--text-secondary);
  font-size: 0.85rem;
  margin-bottom: 16px;
}

.revise-input {
  width: 100%;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  padding: 12px;
  font-size: 0.9rem;
  font-family: inherit;
  line-height: 1.5;
  resize: vertical;
}

.revise-input:focus {
  outline: none;
  border-color: var(--primary);
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 0.85rem;
  color: var(--text-secondary);
  cursor: pointer;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
}

.btn-cancel, .btn-submit {
  padding: 8px 24px;
  border: none;
  border-radius: 8px;
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-cancel {
  background: var(--bg-input);
  color: var(--text);
}

.btn-submit {
  background: linear-gradient(135deg, var(--primary), #a855f7);
  color: white;
}

.btn-submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-submit:hover:not(:disabled) {
  transform: translateY(-1px);
}

.action-btn {
  padding: 12px 32px;
  border: none;
  border-radius: 8px;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.action-btn.primary {
  background: linear-gradient(135deg, var(--primary), #a855f7);
  color: white;
}

.action-btn.primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 20px rgba(99, 102, 241, 0.4);
}
</style>
