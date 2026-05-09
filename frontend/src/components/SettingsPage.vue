<template>
  <section class="settings-page">
    <div class="settings-shell" data-layout-anchor="settings-shell">
      <header class="page-header">
        <div>
          <p class="page-kicker">Settings</p>
          <h1>设置中心</h1>
        </div>
        <button class="page-action" @click="$emit('new-task')">返回首页</button>
      </header>

      <div class="page-grid">
        <ApiKeySettings />

        <section class="model-card">
          <div class="card-head model-head">
            <div>
              <h2>默认模型选择</h2>
            </div>
          </div>

          <div v-if="saveMsg" class="save-toast" :class="saveType">{{ saveMsg }}</div>

          <div class="model-stack">
            <div class="model-panel">
              <div class="field-group">
                <label>默认模型</label>
                <BaseSelect v-model="form.default_model" :options="defaultModelOptions" placeholder="使用 .env 配置" />
              </div>
            </div>

            <div class="model-panel">
              <div class="field-group">
                <label>写作手模型</label>
                <BaseSelect v-model="form.writer_model" :options="writerModelOptions" placeholder="使用默认模型" />
              </div>

              <p class="model-hint">建议为长文写作单独选择更擅长长上下文的模型。</p>
            </div>
          </div>

          <div class="model-actions">
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
import { computed, onMounted, reactive, ref } from 'vue'
import ApiKeySettings from './settings/ApiKeySettings.vue'
import BaseSelect from './ui/BaseSelect.vue'

defineEmits<{ 'new-task': [] }>()

const models = ref<Array<{ id: string; name: string; provider: string }>>([])
const form = reactive({
  default_model: '',
  writer_model: '',
})
const saving = ref(false)
const saveMsg = ref('')
const saveType = ref<'success' | 'error'>('success')

const defaultModelOptions = computed(() => [
  { value: '', label: '使用 .env 配置', meta: '跟随后端环境变量中的默认模型。' },
  ...models.value.map((model) => ({
    value: model.id,
    label: `${model.name} (${model.provider})`,
  })),
])

const writerModelOptions = computed(() => [
  { value: '', label: '使用默认模型', meta: '不单独指定写作模型。' },
  ...models.value.map((model) => ({
    value: model.id,
    label: `${model.name} (${model.provider})`,
  })),
])

function inferProvider(modelId: string): string {
  const raw = (modelId || '').split('/', 1)[0]?.toLowerCase() || 'custom'
  const labelMap: Record<string, string> = {
    openai: 'OpenAI',
    anthropic: 'Anthropic',
    deepseek: 'DeepSeek',
    mimo: 'MiMo',
    gemini: 'Google',
    ollama: 'Ollama',
    custom: 'Custom',
  }
  return labelMap[raw] || raw
}

function ensureSavedModelVisible(modelId: string) {
  if (!modelId || models.value.some((item) => item.id === modelId)) {
    return
  }

  const fallbackName = modelId.includes('/') ? modelId.split('/').slice(1).join('/') : modelId
  models.value = [
    ...models.value,
    {
      id: modelId,
      name: `${fallbackName}（当前已保存）`,
      provider: inferProvider(modelId),
    },
  ]
}

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
  ensureSavedModelVisible(form.default_model)
  ensureSavedModelVisible(form.writer_model)
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
  min-width: 0;
}

.settings-shell {
  display: flex;
  flex-direction: column;
  gap: 22px;
  width: 100%;
  min-width: 0;
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
  grid-template-columns: minmax(0, 1.55fr) minmax(340px, 0.95fr);
  gap: 20px;
  align-items: start;
  min-width: 0;
}

.model-card {
  display: flex;
  flex-direction: column;
  gap: 22px;
  min-width: 0;
  padding: 22px 20px 20px;
  border: 1px solid rgba(20, 28, 45, 0.09);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 14px 36px rgba(18, 31, 58, 0.06);
}

.card-head {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.model-head h2 {
  font-size: 22px;
  letter-spacing: -0.03em;
}

.model-note {
  max-width: 320px;
  margin: 0;
  font-size: 13px;
  line-height: 1.65;
}

.model-stack {
  display: grid;
  gap: 14px;
}

.model-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid rgba(20, 28, 45, 0.09);
  border-radius: 14px;
  background: #fbfcfe;
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
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}

.model-actions {
  display: flex;
  justify-content: stretch;
}

.btn-primary {
  border: none;
  background: linear-gradient(135deg, var(--blue), var(--blue-dark));
  color: white;
  width: 100%;
  min-height: 44px;
  border-radius: 12px;
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

  .model-note {
    max-width: none;
  }

  .page-action,
  .btn-primary {
    width: 100%;
  }

  .model-card {
    padding: 20px 16px 16px;
  }

  .model-panel {
    padding: 14px;
  }
}
</style>