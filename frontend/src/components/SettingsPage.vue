<template>
  <section class="settings-page">
    <div class="settings-shell" data-layout-anchor="settings-shell">
      <header class="page-header">
        <div>
          <p class="page-kicker">Settings</p>
          <h1>设置中心</h1>
          <p class="page-desc">
            先保存 API Key，再选择默认模型。设置页已经从原弹窗拆成独立子页，避免遮挡主页操作。
          </p>
        </div>
        <button class="page-action" @click="$emit('new-task')">返回首页</button>
      </header>

      <div class="page-grid">
        <ApiKeySettings />

        <section class="model-card">
          <div class="model-head">
            <div>
              <p class="section-kicker">Models</p>
              <h2>默认模型选择</h2>
            </div>
            <p class="model-note">如果不设置，后端将继续使用环境变量中的默认模型。</p>
          </div>

          <div v-if="saveMsg" class="save-toast" :class="saveType">{{ saveMsg }}</div>

          <div class="field-group">
            <label>默认模型</label>
            <select v-model="form.default_model">
              <option value="">使用 .env 配置</option>
              <option v-for="model in models" :key="model.id" :value="model.id">
                {{ model.name }} ({{ model.provider }})
              </option>
            </select>
          </div>

          <div class="field-group">
            <label>写作手模型</label>
            <select v-model="form.writer_model">
              <option value="">使用默认模型</option>
              <option v-for="model in models" :key="`writer-${model.id}`" :value="model.id">
                {{ model.name }} ({{ model.provider }})
              </option>
            </select>
          </div>

          <div class="model-actions">
            <span class="model-hint">建议为长文写作单独选择更擅长长上下文的模型。</span>
            <button class="btn-primary" @click="saveModels" :disabled="saving">
              <span v-if="saving" class="spinner"></span>
              {{ saving ? '保存中...' : '保存模型设置' }}
            </button>
          </div>
        </section>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import ApiKeySettings from './settings/ApiKeySettings.vue'

defineEmits<{ 'new-task': [] }>()

const models = ref<Array<{ id: string; name: string; provider: string }>>([])
const form = reactive({
  default_model: '',
  writer_model: '',
})
const saving = ref(false)
const saveMsg = ref('')
const saveType = ref<'success' | 'error'>('success')

function hintForBackendMismatch(status: number, detail: string): string {
  if (status === 404) {
    return '后端接口不存在，请确认已启动 MCMer backend。'
  }
  if (status === 0) {
    return '无法连接后端，请检查 backend 是否运行。'
  }
  if (detail) {
    return detail
  }
  return `请求失败 (HTTP ${status})`
}

async function fetchJsonOrThrow(url: string, init?: RequestInit) {
  const res = await fetch(url, init)
  const text = await res.text()
  let parsed: any = null
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = null
    }
  }

  if (!res.ok) {
    const detail = parsed?.detail || parsed?.message || text || ''
    throw new Error(hintForBackendMismatch(res.status, String(detail)))
  }

  return parsed ?? {}
}

async function loadSettings() {
  const [modelData, keyData] = await Promise.all([
    fetchJsonOrThrow('/api/config/models'),
    fetchJsonOrThrow('/api/config/keys'),
  ])

  models.value = modelData.models || []
  form.default_model = keyData.DEFAULT_MODEL || ''
  form.writer_model = keyData.WRITER_MODEL || ''
}

onMounted(async () => {
  try {
    await loadSettings()
  } catch (error) {
    const message = error instanceof Error ? error.message : '加载设置失败'
    saveMsg.value = message
    saveType.value = 'error'
    console.error('加载模型设置失败:', error)
  }
})

async function saveModels() {
  saving.value = true
  saveMsg.value = ''

  const payload: Record<string, string> = {}
  if (form.default_model) payload.DEFAULT_MODEL = form.default_model
  if (form.writer_model) payload.WRITER_MODEL = form.writer_model

  try {
    if (Object.keys(payload).length === 0) {
      saveMsg.value = '没有可保存的模型设置。'
      saveType.value = 'error'
      return
    }

    await fetchJsonOrThrow('/api/config/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })

    saveMsg.value = '模型设置已保存。'
    saveType.value = 'success'
  } catch (error) {
    const message = error instanceof Error ? error.message : '保存失败，请重试'
    saveMsg.value = message
    saveType.value = 'error'
  } finally {
    saving.value = false
    window.setTimeout(() => {
      saveMsg.value = ''
    }, 3000)
  }
}
</script>

<style scoped>
.settings-page {
  position: relative;
  z-index: 3;
  width: 100%;
}

.settings-shell {
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
}

.page-kicker,
.section-kicker {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--blue);
}

.page-header h1,
.model-head h2 {
  margin: 0;
  font-size: 38px;
  letter-spacing: -0.05em;
}

.page-desc,
.model-note,
.model-hint {
  margin: 10px 0 0;
  color: var(--muted);
  line-height: 1.7;
}

.page-action,
.btn-primary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 22px;
  border-radius: 14px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
}

.page-action {
  border: 1px solid rgba(20, 28, 45, 0.12);
  background: rgba(255, 255, 255, 0.82);
  color: var(--text);
}

.page-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(320px, 0.9fr);
  gap: 22px;
  align-items: start;
}

.model-card {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 28px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.78);
  box-shadow: 0 24px 60px rgba(18, 31, 58, 0.05);
  backdrop-filter: blur(14px);
}

.save-toast {
  padding: 12px 16px;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 600;
}

.save-toast.success {
  background: rgba(22, 163, 74, 0.08);
  color: var(--success);
}

.save-toast.error {
  background: rgba(220, 38, 38, 0.08);
  color: var(--error);
}

.field-group label {
  display: block;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
}

.field-group select {
  width: 100%;
  min-height: 46px;
  padding: 11px 14px;
  border: 1px solid rgba(20, 28, 45, 0.12);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.98);
  color: var(--text);
  font-size: 14px;
}

.field-group select:focus {
  outline: none;
  border-color: rgba(36, 78, 168, 0.45);
  box-shadow: 0 0 0 3px rgba(36, 78, 168, 0.08);
}

.model-actions {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.btn-primary {
  border: none;
  background: linear-gradient(135deg, var(--blue), var(--blue-dark));
  color: white;
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1080px) {
  .page-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .page-header {
    flex-direction: column;
  }

  .page-header h1,
  .model-head h2 {
    font-size: 30px;
  }

  .page-action,
  .btn-primary {
    width: 100%;
  }

  .model-card {
    padding: 22px 18px;
  }
}
</style>