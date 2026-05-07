<template>
  <div class="paper-container">
    <!-- 顶部导航 -->
    <div class="paper-header">
      <button class="back-btn" @click="$emit('back')">← 返回历史</button>
      <div class="paper-info">
        <span class="paper-title">项目论文</span>
        <span v-if="paperVersion > 1" class="version-badge">
          v{{ paperVersion }}
        </span>
      </div>
      <div class="header-actions">
        <button class="btn-revise" @click="showRevisePanel = !showRevisePanel">
          ✏️ 修订论文
        </button>
      </div>
    </div>

    <!-- 加载 -->
    <div v-if="loading" class="loading-state">
      <div class="spinner"></div>
      <span>加载论文...</span>
    </div>

    <!-- 错误 -->
    <div v-else-if="error" class="error-state">
      <div class="error-icon">❌</div>
      <p>{{ error }}</p>
      <button @click="$emit('back')">返回</button>
    </div>

    <!-- 论文内容 -->
    <div v-else class="paper-layout">
      <!-- 左侧：论文预览 -->
      <div class="paper-preview">
        <div class="paper-toolbar">
          <span class="section-label">📄 论文预览</span>
          <div class="version-selector" v-if="versions.length > 1">
            <label>版本:</label>
            <select v-model="selectedVersion" @change="loadVersion">
              <option v-for="v in versions" :key="v" :value="v">
                v{{ v }}{{ v === latestVersion ? ' (最新)' : '' }}
              </option>
            </select>
          </div>
        </div>
        <div class="paper-content markdown-body" v-html="renderedPaper"></div>
      </div>

      <!-- 右侧：修订面板 -->
      <div v-if="showRevisePanel" class="revise-panel">
        <div class="revise-panel-header">
          <h3>✏️ 修订建议</h3>
          <button class="close-btn" @click="showRevisePanel = false">✕</button>
        </div>

        <div class="original-question" v-if="question">
          <strong>原始题目:</strong>
          <p>{{ question.slice(0, 200) }}{{ question.length > 200 ? '...' : '' }}</p>
        </div>

        <label class="input-label">你的修改建议</label>
        <textarea
          v-model="feedback"
          placeholder="请描述你希望如何修改论文..."
          rows="10"
          class="revise-textarea"
        ></textarea>

        <div class="revise-hints">
          <p>💡 建议示例：</p>
          <ul>
            <li>在模型假设中增加正态分布假设</li>
            <li>修改第三章算法为遗传算法</li>
            <li>增加灵敏度分析章节</li>
            <li>修正符号说明中的变量</li>
          </ul>
        </div>

        <label class="checkbox-label">
          <input type="checkbox" v-model="reviseWithCode" />
          同时让代码手重新执行相关代码
        </label>

        <label class="input-label">修订模式</label>
        <select v-model="workflowMode" class="mode-select">
          <option value="fast">fast：快速模式</option>
          <option value="standard">standard：标准模式</option>
          <option value="strict">strict：严格模式</option>
        </select>

        <button
          class="submit-revise-btn"
          :disabled="!feedback.trim() || submitting"
          @click="submitRevise"
        >
          <span v-if="submitting" class="spinner-small"></span>
          {{ submitting ? '提交中...' : '🚀 提交修订' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { Marked } from 'marked'
import markedKatex from 'marked-katex-extension'
import 'katex/dist/katex.min.css'

const props = defineProps<{
  taskId: string
}>()

const emit = defineEmits<{
  back: []
  revise: [taskId: string, feedback: string, reviseCode: boolean, workflowMode: 'fast' | 'standard' | 'strict']
  newTask: []
}>()

const loading = ref(true)
const error = ref('')
const paperContent = ref('')
const question = ref('')
const paperVersion = ref(1)
const latestVersion = ref(1)

const showRevisePanel = ref(false)
const feedback = ref('')
const reviseWithCode = ref(false)
const workflowMode = ref<'fast' | 'standard' | 'strict'>('standard')
const submitting = ref(false)

// 版本选择
const versions = computed(() => {
  const v = [1]
  for (let i = 2; i <= latestVersion.value; i++) v.push(i)
  return v
})
const selectedVersion = ref(1)
const markdownRenderer = new Marked(
  markedKatex({
    nonStandard: true,
    throwOnError: false,
  })
)

// 渲染 Markdown
const renderedPaper = computed(() => {
  if (!paperContent.value) return '<p><em>暂无内容</em></p>'
  try {
    return markdownRenderer.parse(paperContent.value) as string
  } catch {
    return `<pre>${escapeHtml(paperContent.value)}</pre>`
  }
})

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

async function loadPaper(version?: number) {
  loading.value = true
  error.value = ''

  try {
    // 加载上下文
    const ctxRes = await fetch(`/api/history/${props.taskId}`)
    if (!ctxRes.ok) throw new Error('任务不存在')
    const ctx = await ctxRes.json()
    question.value = ctx.question || ''
    paperVersion.value = ctx.paper_version || 1
    latestVersion.value = ctx.paper_version || 1
    paperContent.value = ctx.latest_paper || ctx.paper || ''

    // 如果需要加载特定版本
    const ver = version || 1
    if (ver > 1 || version !== undefined) {
      const paperRes = await fetch(`/api/history/${props.taskId}/paper?version=${ver}`)
      if (paperRes.ok) {
        const data = await paperRes.json()
        paperContent.value = data.content || ''
      }
    }

    selectedVersion.value = ver
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function loadVersion() {
  loadPaper(selectedVersion.value)
}

function submitRevise() {
  if (!feedback.value.trim()) return
  submitting.value = true
  emit('revise', props.taskId, feedback.value.trim(), reviseWithCode.value, workflowMode.value)
  // 不在这里重置 submitting，让父组件在 WebSocket 完成后处理
}

watch(() => props.taskId, (newId) => {
  if (newId) loadPaper()
})

onMounted(() => {
  if (props.taskId) loadPaper()
})
</script>

<style scoped>
.paper-container {
  max-width: 1400px;
  width: 100%;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 140px);
}

.paper-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0 16px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 16px;
}

.back-btn {
  background: none;
  border: none;
  color: var(--primary);
  cursor: pointer;
  font-size: 0.9rem;
}

.back-btn:hover { text-decoration: underline; }

.paper-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.paper-title {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text);
}

.version-badge {
  background: rgba(99, 102, 241, 0.15);
  color: var(--primary);
  padding: 1px 10px;
  border-radius: 10px;
  font-size: 0.8rem;
  font-weight: 600;
}

.btn-revise {
  padding: 8px 20px;
  background: linear-gradient(135deg, var(--primary), #a855f7);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-revise:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 15px rgba(99, 102, 241, 0.3);
}

/* 加载 & 错误 */
.loading-state, .error-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-secondary);
}

.spinner {
  width: 32px; height: 32px;
  border: 3px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.error-icon { font-size: 2rem; }

/* 论文布局 */
.paper-layout {
  flex: 1;
  display: flex;
  gap: 16px;
  overflow: hidden;
}

.paper-preview {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.paper-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  margin-bottom: 8px;
}

.section-label {
  font-weight: 600;
}

.version-selector {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.version-selector select {
  background: var(--bg-input);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 8px;
}

.paper-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  line-height: 1.8;
}

/* Markdown 渲染样式 */
.markdown-body :deep(h1) { font-size: 1.6rem; margin: 24px 0 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
.markdown-body :deep(h2) { font-size: 1.3rem; margin: 20px 0 10px; }
.markdown-body :deep(h3) { font-size: 1.1rem; margin: 16px 0 8px; }
.markdown-body :deep(p) { margin: 10px 0; }
.markdown-body :deep(code) { background: var(--bg-input); padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
.markdown-body :deep(pre) { background: var(--bg-input); padding: 16px; border-radius: 8px; overflow-x: auto; }
.markdown-body :deep(pre code) { background: none; padding: 0; }
.markdown-body :deep(blockquote) { border-left: 3px solid var(--primary); padding-left: 16px; margin: 12px 0; color: var(--text-secondary); }
.markdown-body :deep(table) { border-collapse: collapse; width: 100%; margin: 12px 0; }
.markdown-body :deep(th), .markdown-body :deep(td) { border: 1px solid var(--border); padding: 8px 12px; text-align: left; }
.markdown-body :deep(th) { background: var(--bg-input); }
.markdown-body :deep(img) { max-width: 100%; border-radius: 8px; margin: 10px 0; }
.markdown-body :deep(.katex-display) { overflow-x: auto; overflow-y: hidden; padding: 8px 0; }
.markdown-body :deep(.katex) { font-size: 1.05em; }

/* 修订面板 */
.revise-panel {
  width: 380px;
  flex-shrink: 0;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.revise-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.revise-panel-header h3 {
  margin: 0;
  font-size: 1.1rem;
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 1.2rem;
  cursor: pointer;
}

.original-question {
  margin-bottom: 16px;
  font-size: 0.85rem;
  color: var(--text-secondary);
  background: var(--bg-input);
  padding: 10px;
  border-radius: 6px;
}

.original-question strong {
  display: block;
  margin-bottom: 4px;
  color: var(--text);
}

.original-question p {
  margin: 0;
  line-height: 1.4;
}

.input-label {
  display: block;
  font-weight: 600;
  margin-bottom: 8px;
}

.mode-select {
  width: 100%;
  min-height: 44px;
  margin-top: 8px;
  border-radius: 12px;
  border: 1px solid rgba(20, 28, 45, 0.12);
  background: rgba(255, 255, 255, 0.92);
  padding: 0 12px;
}

.revise-textarea {
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

.revise-textarea:focus {
  outline: none;
  border-color: var(--primary);
}

.revise-hints {
  margin-top: 12px;
  font-size: 0.8rem;
  color: var(--text-secondary);
  background: rgba(99, 102, 241, 0.05);
  border-radius: 8px;
  padding: 10px 12px;
}

.revise-hints p { margin: 0 0 4px; font-weight: 500; }
.revise-hints ul { margin: 0; padding-left: 18px; }
.revise-hints li { margin: 2px 0; }

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  font-size: 0.85rem;
  color: var(--text-secondary);
  cursor: pointer;
}

.submit-revise-btn {
  margin-top: 16px;
  padding: 12px;
  background: linear-gradient(135deg, var(--primary), #a855f7);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.submit-revise-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.submit-revise-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 15px rgba(99, 102, 241, 0.3);
}

.spinner-small {
  width: 16px; height: 16px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
</style>
