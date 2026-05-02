<template>
  <section class="api-card">
    <div class="card-head">
      <div>
        <h2>服务商密钥配置</h2>
        <p class="section-note">
          密钥仅保存在后端；留空不会覆盖已保存的值。
        </p>
      </div>
    </div>

    <div v-if="saveMsg" class="save-toast" :class="saveType">{{ saveMsg }}</div>

    <div class="provider-grid">
      <article class="provider-section">
        <h3>OpenAI</h3>
        <div class="provider-fields">
          <div class="field-group">
            <label>API Key</label>
            <div class="input-row">
              <input
                :type="showKeys.openai ? 'text' : 'password'"
                v-model="form.openai_key"
                :placeholder="savedState.openai ? '已保存（留空则不改动）' : 'sk-...'"
              />
              <button type="button" class="toggle-btn" @click="showKeys.openai = !showKeys.openai">
                {{ showKeys.openai ? '隐藏' : '显示' }}
              </button>
            </div>
            <div v-if="savedState.openai" class="saved-hint">已检测到已保存的 OpenAI Key</div>
          </div>
          <div class="field-group">
            <label>Base URL</label>
            <input v-model="form.openai_base_url" placeholder="https://api.openai.com/v1" />
          </div>
        </div>
      </article>

      <article class="provider-section">
        <h3>Anthropic</h3>
        <div class="provider-fields">
          <div class="field-group">
            <label>API Key</label>
            <div class="input-row">
              <input
                :type="showKeys.anthropic ? 'text' : 'password'"
                v-model="form.anthropic_key"
                :placeholder="savedState.anthropic ? '已保存（留空则不改动）' : 'sk-ant-...'"
              />
              <button type="button" class="toggle-btn" @click="showKeys.anthropic = !showKeys.anthropic">
                {{ showKeys.anthropic ? '隐藏' : '显示' }}
              </button>
            </div>
            <div v-if="savedState.anthropic" class="saved-hint">已检测到已保存的 Anthropic Key</div>
          </div>
        </div>
      </article>

      <article class="provider-section">
        <h3>DeepSeek</h3>
        <div class="provider-fields">
          <div class="field-group">
            <label>API Key</label>
            <div class="input-row">
              <input
                :type="showKeys.deepseek ? 'text' : 'password'"
                v-model="form.deepseek_key"
                :placeholder="savedState.deepseek ? '已保存（留空则不改动）' : 'sk-...'"
              />
              <button type="button" class="toggle-btn" @click="showKeys.deepseek = !showKeys.deepseek">
                {{ showKeys.deepseek ? '隐藏' : '显示' }}
              </button>
            </div>
            <div v-if="savedState.deepseek" class="saved-hint">已检测到已保存的 DeepSeek Key</div>
          </div>
        </div>
      </article>

      <article class="provider-section provider-section--wide">
        <h3>MiMo</h3>
        <div class="provider-fields provider-fields--wide">
          <div class="field-group">
            <label>API Key</label>
            <div class="input-row">
              <input
                :type="showKeys.mimo ? 'text' : 'password'"
                v-model="form.mimo_key"
                :placeholder="savedState.mimo ? '已保存（留空则不改动）' : 'sk-... 或 tp-...'"
              />
              <button type="button" class="toggle-btn" @click="showKeys.mimo = !showKeys.mimo">
                {{ showKeys.mimo ? '隐藏' : '显示' }}
              </button>
            </div>
            <div v-if="savedState.mimo" class="saved-hint">已检测到已保存的 MiMo Key</div>
          </div>
          <div class="field-group">
            <label>Base URL</label>
            <select v-model="form.mimo_base_url">
              <option value="https://api.xiaomimimo.com/v1">默认 API（无套餐）</option>
              <option value="https://token-plan-cn.xiaomimimo.com/v1">Token Plan - 中国区</option>
              <option value="https://token-plan-sgp.xiaomimimo.com/v1">Token Plan - 新加坡区</option>
              <option value="https://token-plan-ams.xiaomimimo.com/v1">Token Plan - 欧洲区</option>
            </select>
          </div>
        </div>
      </article>
    </div>

    <div class="actions">
      <span v-if="saveMsg" class="footer-msg" :class="saveType">{{ saveMsg }}</span>
      <button class="btn-primary" @click="saveKeys" :disabled="saving">
        <span v-if="saving" class="spinner"></span>
        {{ saving ? '保存中...' : '保存 API Key' }}
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

const showKeys = reactive({
  openai: false,
  anthropic: false,
  deepseek: false,
  mimo: false,
})

const form = reactive({
  openai_key: '',
  openai_base_url: '',
  anthropic_key: '',
  deepseek_key: '',
  mimo_key: '',
  mimo_base_url: 'https://api.xiaomimimo.com/v1',
})

const savedState = reactive({
  openai: false,
  anthropic: false,
  deepseek: false,
  mimo: false,
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

function validateApiKeyValue(value: string, provider: 'openai' | 'anthropic' | 'deepseek' | 'mimo'): string | null {
  const trimmed = value.trim()
  if (!trimmed) return null

  if (trimmed.includes('your-api-key') || trimmed.includes('sk-your-') || trimmed.includes('your_key')) {
    return '检测到占位符 Key，请替换为真实 API Key。'
  }

  if (provider === 'openai' && !trimmed.startsWith('sk-')) {
    return 'OpenAI Key 通常以 sk- 开头，请确认输入是否正确。'
  }
  if (provider === 'anthropic' && !trimmed.startsWith('sk-ant-')) {
    return 'Anthropic Key 通常以 sk-ant- 开头，请确认输入是否正确。'
  }
  if (provider === 'deepseek' && !trimmed.startsWith('sk-')) {
    return 'DeepSeek Key 通常以 sk- 开头，请确认输入是否正确。'
  }
  if (provider === 'mimo' && !trimmed.startsWith('sk-') && !trimmed.startsWith('tp-')) {
    return 'MiMo Key 通常以 sk- 或 tp- 开头，请确认输入是否正确。'
  }

  return null
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

async function loadKeys() {
  const keyData = await fetchJsonOrThrow('/api/config/keys')
  savedState.openai = Boolean(keyData.OPENAI_API_KEY)
  savedState.anthropic = Boolean(keyData.ANTHROPIC_API_KEY)
  savedState.deepseek = Boolean(keyData.DEEPSEEK_API_KEY)
  savedState.mimo = Boolean(keyData.MIMO_API_KEY)

  form.openai_key = ''
  form.anthropic_key = ''
  form.deepseek_key = ''
  form.mimo_key = ''
  form.openai_base_url = keyData.OPENAI_BASE_URL || ''
  form.mimo_base_url = keyData.MIMO_BASE_URL || 'https://api.xiaomimimo.com/v1'
}

onMounted(async () => {
  try {
    await loadKeys()
  } catch (error) {
    const message = error instanceof Error ? error.message : '加载配置失败'
    saveMsg.value = `加载失败: ${message}`
    saveType.value = 'error'
    console.error('加载 API 配置失败:', error)
  }
})

async function saveKeys() {
  saving.value = true
  saveMsg.value = ''

  const openaiErr = validateApiKeyValue(form.openai_key, 'openai')
  if (openaiErr) {
    saveMsg.value = openaiErr
    saveType.value = 'error'
    saving.value = false
    return
  }

  const anthropicErr = validateApiKeyValue(form.anthropic_key, 'anthropic')
  if (anthropicErr) {
    saveMsg.value = anthropicErr
    saveType.value = 'error'
    saving.value = false
    return
  }

  const deepseekErr = validateApiKeyValue(form.deepseek_key, 'deepseek')
  if (deepseekErr) {
    saveMsg.value = deepseekErr
    saveType.value = 'error'
    saving.value = false
    return
  }

  const mimoErr = validateApiKeyValue(form.mimo_key, 'mimo')
  if (mimoErr) {
    saveMsg.value = mimoErr
    saveType.value = 'error'
    saving.value = false
    return
  }

  const payload: Record<string, string> = {}
  if (form.openai_key.trim()) payload.OPENAI_API_KEY = form.openai_key.trim()
  if (form.openai_base_url.trim()) payload.OPENAI_BASE_URL = form.openai_base_url.trim()
  if (form.anthropic_key.trim()) payload.ANTHROPIC_API_KEY = form.anthropic_key.trim()
  if (form.deepseek_key.trim()) payload.DEEPSEEK_API_KEY = form.deepseek_key.trim()
  if (form.mimo_key.trim()) payload.MIMO_API_KEY = form.mimo_key.trim()
  if (form.mimo_base_url.trim()) payload.MIMO_BASE_URL = form.mimo_base_url.trim()

  try {
    if (Object.keys(payload).length === 0) {
      saveMsg.value = '没有可保存的内容。'
      saveType.value = 'error'
      return
    }

    await fetchJsonOrThrow('/api/config/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })

    await loadKeys()
    saveMsg.value = '配置已保存并生效。'
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
.api-card {
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

.card-head h2 {
  margin: 0;
  font-size: 22px;
  letter-spacing: -0.03em;
}

.section-note {
  margin: 0;
  max-width: 320px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--muted);
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

.provider-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 14px;
  min-width: 0;
}

.provider-section {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  align-items: start;
  column-gap: 16px;
  min-width: 0;
  padding: 14px 14px 12px;
  border: 1px solid rgba(20, 28, 45, 0.08);
  border-radius: 14px;
  background: #fcfdff;
  box-shadow: 0 4px 16px rgba(18, 31, 58, 0.04);
}

.provider-section h3 {
  margin: 4px 0 0;
  font-size: 16px;
  letter-spacing: -0.03em;
}

.provider-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  min-width: 0;
}

.provider-fields--wide {
  grid-template-columns: 1.35fr minmax(180px, 0.95fr);
}

.field-group {
  margin-bottom: 0;
}

.field-group label {
  display: block;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}

.field-group input,
.field-group select {
  width: 100%;
  min-width: 0;
  min-height: 42px;
  padding: 9px 12px;
  border: 1px solid rgba(20, 28, 45, 0.1);
  border-radius: 12px;
  background: #fff;
  color: var(--text);
  font-size: 13px;
}

.field-group input:focus,
.field-group select:focus {
  outline: none;
  border-color: rgba(36, 78, 168, 0.45);
  box-shadow: 0 0 0 3px rgba(36, 78, 168, 0.08);
}

.input-row {
  display: flex;
  gap: 8px;
  min-width: 0;
}

.input-row input {
  flex: 1;
  min-width: 0;
}

.toggle-btn {
  min-width: 58px;
  padding: 0 12px;
  border: 1px solid rgba(20, 28, 45, 0.1);
  border-radius: 12px;
  background: #fff;
  color: var(--text);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.saved-hint {
  margin-top: 6px;
  font-size: 11px;
  color: var(--muted);
}

.actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 16px;
}

.footer-msg {
  font-size: 13px;
  font-weight: 600;
}

.footer-msg.success {
  color: var(--success);
}

.footer-msg.error {
  color: var(--error);
}

.btn-primary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-width: 146px;
  min-height: 42px;
  padding: 0 18px;
  border: 1px solid rgba(36, 78, 168, 0.45);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.98);
  color: var(--blue);
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
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

@media (max-width: 960px) {
  .provider-section {
    grid-template-columns: 1fr;
    row-gap: 14px;
  }

  .provider-section h3 {
    margin-top: 0;
  }

  .provider-fields,
  .provider-fields--wide {
    grid-template-columns: 1fr;
  }

  .actions {
    flex-direction: column;
    align-items: stretch;
  }

  .btn-primary {
    width: 100%;
  }
}

@media (max-width: 640px) {
  .api-card {
    padding: 22px 18px;
  }

  .section-head h2 {
    font-size: 24px;
  }

  .input-row,
  .actions {
    flex-direction: column;
    align-items: stretch;
  }

  .toggle-btn,
  .btn-primary {
    width: 100%;
  }
}
</style>