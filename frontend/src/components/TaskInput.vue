<template>
  <div class="task-form" :class="`mode-${mode}`">
    <div class="form-body">
      <div class="field compact-field">
        <label>工作流模式</label>
        <BaseSelect v-model="workflowMode" :options="workflowModeOptions" :disabled="loading" />
      </div>

      <template v-if="mode === 'writing'">
        <div class="field field-grow">
          <label>输入题目</label>
          <textarea
            data-layout-anchor="task-input-textarea"
            v-model="question"
            placeholder="在此输入完整的数学建模题目..."
            :disabled="loading"
          ></textarea>
        </div>

        <div class="field compact-field">
          <label>上传数据</label>
          <div
            data-layout-anchor="task-upload-area"
            class="upload"
            :class="{ dragging: isDragging, 'has-files': uploadedFiles.length > 0 }"
            @dragover.prevent="isDragging = true"
            @dragleave.prevent="isDragging = false"
            @drop.prevent="handleDrop"
            @click="triggerFileInput"
          >
            <input
              ref="fileInput"
              type="file"
              multiple
              accept=".csv,.xlsx,.xls,.json,.txt,.dat,.tsv,.md,.pdf,.docx,.png,.jpg,.jpeg,.svg,.webp"
              class="file-input-hidden"
              @change="handleFileSelect"
            />
            <div v-if="uploadedFiles.length === 0">
              <p>拖拽原题文档或数据文件到此处，或点击上传</p>
              <span>支持 PDF / DOCX / CSV / Excel / JSON / TXT / Markdown</span>
            </div>
            <div v-else class="file-list">
              <div v-for="(f, i) in uploadedFiles" :key="i" class="file-item">
                <span class="file-name">{{ f.name }}</span>
                <span class="file-size">{{ formatSize(f.size) }}</span>
                <button class="file-remove" @click.stop="removeFile(i)" :disabled="loading">✕</button>
              </div>
              <div class="add-more" @click.stop="triggerFileInput">+ 添加更多文件</div>
            </div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="field field-slim">
          <label>原始题目</label>
          <textarea
            v-model="sourceQuestion"
            class="short-area"
            placeholder="可选。输入原始赛题或论文所回答的问题。"
            :disabled="loading"
          ></textarea>
        </div>

        <div class="field field-slim compact-field">
          <label>润色要求</label>
          <textarea
            ref="polishingRequirementsInput"
            v-model="polishingRequirements"
            class="short-area adaptive-area"
            placeholder="例如：收紧措辞、检查图文一致性、复核关键数值、改成竞赛论文风格。"
            :disabled="loading"
            @input="handlePolishingRequirementsInput"
          ></textarea>
        </div>

        <div class="field compact-field">
          <label>上传数据</label>
          <div
            class="upload"
            :class="{ dragging: isDragging, 'has-files': uploadedFiles.length > 0 }"
            @dragover.prevent="isDragging = true"
            @dragleave.prevent="isDragging = false"
            @drop.prevent="handleDrop"
            @click="triggerFileInput"
          >
            <input
              ref="fileInput"
              type="file"
              multiple
              accept=".zip,.md,.pdf,.docx,.csv,.xlsx,.xls,.json,.txt,.dat,.tsv,.png,.jpg,.jpeg,.svg,.webp"
              class="file-input-hidden"
              @change="handleFileSelect"
            />
            <div v-if="uploadedFiles.length === 0">
              <p>上传论文包、Markdown、原文档或补充数据</p>
              <span>支持 ZIP / Markdown / PDF / DOCX / CSV / Excel / JSON</span>
            </div>
            <div v-else class="file-list">
              <div v-for="(f, i) in uploadedFiles" :key="i" class="file-item">
                <span class="file-name">{{ f.name }}</span>
                <span class="file-size">{{ formatSize(f.size) }}</span>
                <button class="file-remove" @click.stop="removeFile(i)" :disabled="loading">✕</button>
              </div>
              <div class="add-more" @click.stop="triggerFileInput">+ 添加更多文件</div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <button class="start-btn" :disabled="!canSubmit || loading" @click="handleSubmit">
      <span v-if="loading" class="spinner"></span>
      {{ loading ? loadingLabel : submitLabel }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import BaseSelect from './ui/BaseSelect.vue'

type TaskMode = 'writing' | 'polish'

interface TaskDraftPayload {
  taskType: TaskMode
  workflowMode: 'fast' | 'standard' | 'strict'
  question: string
  sourceQuestion: string
  paperContent: string
  polishingRequirements: string
  files: File[]
}

const props = defineProps<{
  loading: boolean
  mode: TaskMode
}>()

const emit = defineEmits<{
  submit: [payload: TaskDraftPayload]
}>()

const question = ref('')
const sourceQuestion = ref('')
const paperContent = ref('')
const polishingRequirements = ref('')
const workflowMode = ref<'fast' | 'standard' | 'strict'>('standard')
const uploadedFiles = ref<File[]>([])
const isDragging = ref(false)
const fileInput = ref<HTMLInputElement>()
const polishingRequirementsInput = ref<HTMLTextAreaElement>()

const workflowModeOptions = [
  { value: 'fast', label: 'fast：快速模式', meta: '更快出稿，适合先看结构和方向。' },
  { value: 'standard', label: 'standard：标准模式', meta: '默认平衡模式，适合大多数写作与润色任务。' },
  { value: 'strict', label: 'strict：严格模式', meta: '增加复核和约束，适合正式交付前检查。' },
] as const

const writingSourceExtensions = ['.pdf', '.docx', '.txt', '.md']
const polishSourceExtensions = ['.zip', '.md', '.docx', '.pdf']
const commonDataExtensions = ['.csv', '.xlsx', '.xls', '.json', '.txt', '.dat', '.tsv', '.md', '.png', '.jpg', '.jpeg', '.svg', '.webp']

const canSubmit = computed(() => {
  if (props.mode === 'writing') {
    return Boolean(question.value.trim()) || uploadedFiles.value.some(isWritingSourceFile)
  }
  return Boolean(paperContent.value.trim()) || uploadedFiles.value.some(isPolishSourceFile)
})

const submitLabel = computed(() => props.mode === 'writing' ? '开始写作' : '开始润色')
const loadingLabel = computed(() => props.mode === 'writing' ? '正在创建写作任务...' : '正在创建润色任务...')

function resizePolishingRequirements() {
  const el = polishingRequirementsInput.value
  if (!el) return
  el.style.height = 'auto'
  const maxHeight = window.innerWidth <= 720 ? 180 : 240
  el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`
  el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
}

function handlePolishingRequirementsInput() {
  resizePolishingRequirements()
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function triggerFileInput() {
  if (!props.loading) fileInput.value?.click()
}

function handleDrop(e: DragEvent) {
  isDragging.value = false
  const files = e.dataTransfer?.files
  if (files) addFiles(Array.from(files))
}

function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) addFiles(Array.from(input.files))
  input.value = ''
}

function addFiles(newFiles: File[]) {
  for (const f of newFiles) {
    const ext = '.' + (f.name.split('.').pop()?.toLowerCase() || '')
    const allowed = props.mode === 'writing'
      ? Array.from(new Set([...commonDataExtensions, ...writingSourceExtensions]))
      : Array.from(new Set([...commonDataExtensions, ...polishSourceExtensions]))
    if (allowed.includes(ext) && !uploadedFiles.value.some(uf => uf.name === f.name)) {
      uploadedFiles.value.push(f)
    }
  }
}

function isWritingSourceFile(file: File) {
  const ext = '.' + (file.name.split('.').pop()?.toLowerCase() || '')
  return writingSourceExtensions.includes(ext)
}

function isPolishSourceFile(file: File) {
  const ext = '.' + (file.name.split('.').pop()?.toLowerCase() || '')
  return polishSourceExtensions.includes(ext)
}

function removeFile(index: number) {
  if (!props.loading) uploadedFiles.value.splice(index, 1)
}

function handleSubmit() {
  if (!canSubmit.value) return

  emit('submit', {
    taskType: props.mode,
    workflowMode: workflowMode.value,
    question: question.value.trim(),
    sourceQuestion: sourceQuestion.value.trim(),
    paperContent: paperContent.value.trim(),
    polishingRequirements: polishingRequirements.value.trim(),
    files: [...uploadedFiles.value],
  })
}

watch(polishingRequirements, () => {
  nextTick(resizePolishingRequirements)
})

watch(() => props.mode, () => {
  nextTick(resizePolishingRequirements)
})

onMounted(() => {
  resizePolishingRequirements()
})
</script>

<style scoped>
.task-form {
  display: flex;
  flex-direction: column;
  gap: 0;
  height: 100%;
  min-height: 0;
  overflow: visible;
}

.form-body {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  overflow: visible;
}

.mode-polish .form-body {
  overflow-y: auto;
  padding-right: 4px;
}

.field + .field { margin-top: 18px; }

.field-slim + .field,
.compact-field {
  margin-top: 12px;
}

label {
  display: block;
  margin-bottom: 10px;
  font-size: 15px;
  font-weight: 800;
  letter-spacing: -0.02em;
}

textarea {
  display: block;
  width: 100%;
  height: 166px;
  resize: none;
  padding: 16px 18px;
  border: 1px solid rgba(20, 28, 45, 0.15);
  border-radius: 6px;
  background: #ffffff;
  color: #111;
  font-size: 14px;
  line-height: 1.55;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.short-area {
  height: 78px;
}

.field-grow {
  display: flex;
  min-height: 0;
  flex: 1 1 0;
  flex-direction: column;
}

.mode-writing textarea {
  height: auto;
  min-height: 140px;
}

.mode-writing .field-grow textarea {
  height: 100%;
  flex: 1;
  min-height: 0;
}

.mode-writing .short-area {
  height: 78px;
}

.mode-polish textarea {
  height: auto;
  min-height: 84px;
}

.mode-polish .field-grow textarea {
  height: 100%;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.mode-polish .short-area {
  height: 64px;
}

.adaptive-area {
  min-height: 88px !important;
  max-height: min(30vh, 240px);
  resize: vertical;
}

.mode-polish .field + .field {
  margin-top: 14px;
}

.mode-polish .compact-field {
  margin-top: 10px;
}

.mode-polish .field:last-of-type {
  padding-bottom: 6px;
}

textarea::placeholder { color: #a4a9b3; }

textarea:focus {
  border-color: rgba(36, 78, 168, 0.45);
  box-shadow: 0 0 0 4px rgba(36, 78, 168, 0.07);
}

textarea:disabled { opacity: 0.6; cursor: not-allowed; }

.upload {
  min-height: 92px;
  display: grid;
  place-items: center;
  text-align: center;
  border: 1px dashed rgba(20, 28, 45, 0.22);
  border-radius: 6px;
  background: #ffffff;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}

.upload:hover,
.upload.dragging {
  border-color: rgba(36, 78, 168, 0.4);
  background: rgba(36, 78, 168, 0.04);
}

.upload.has-files {
  min-height: 70px;
  border-style: solid;
  border-color: rgba(36, 78, 168, 0.25);
}

.mode-polish .upload {
  min-height: 78px;
}

.mode-polish .upload.has-files {
  min-height: 62px;
}

.upload p {
  margin: 0 0 8px;
  font-size: 14px;
  color: #8a909b;
}

.upload span {
  font-size: 12px;
  color: #9da2ac;
}

.file-input-hidden { display: none; }

.file-list {
  width: 100%;
  padding: 10px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 0;
  font-size: 13px;
  border-bottom: 1px solid rgba(20, 28, 45, 0.06);
}

.file-item:last-child { border-bottom: none; }

.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.file-size {
  color: #9da2ac;
  font-size: 13px;
}

.file-remove {
  background: none;
  border: none;
  color: #9da2ac;
  cursor: pointer;
  font-size: 14px;
  padding: 2px 6px;
  border-radius: 4px;
  transition: color 0.2s;
}

.file-remove:hover { color: var(--error); }

.add-more {
  font-size: 12px;
  color: var(--blue);
  padding: 4px 0 0;
  text-align: center;
  cursor: pointer;
}

.start-btn {
  width: 100%;
  height: 58px;
  margin-top: 18px;
  border: none;
  border-radius: 7px;
  background: linear-gradient(135deg, var(--blue), var(--blue-dark));
  color: #fff;
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 0.03em;
  cursor: pointer;
  box-shadow: 0 16px 34px rgba(36, 78, 168, 0.24);
  transition: transform 0.15s, box-shadow 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  font-family: inherit;
  flex-shrink: 0;
}

.start-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 20px 40px rgba(36, 78, 168, 0.3);
}

.start-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}


.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }
</style>