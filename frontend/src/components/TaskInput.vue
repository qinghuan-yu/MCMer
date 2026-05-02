<template>
  <div class="task-form">
    <template v-if="mode === 'writing'">
      <div class="field">
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
            accept=".csv,.xlsx,.xls,.json,.txt,.dat,.tsv,.md"
            class="file-input-hidden"
            @change="handleFileSelect"
          />
          <div v-if="uploadedFiles.length === 0">
            <p>拖拽文件到此处，或点击上传</p>
            <span>支持 CSV / Excel / JSON / TXT / Markdown</span>
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

      <div class="field">
        <label>待润色论文</label>
        <textarea
          v-model="paperContent"
          placeholder="粘贴需要润色的 Markdown 或论文正文..."
          :disabled="loading"
        ></textarea>
      </div>

      <div class="field field-slim compact-field">
        <label>润色要求</label>
        <textarea
          v-model="polishingRequirements"
          class="short-area"
          placeholder="例如：收紧措辞、检查图文一致性、复核关键数值、改成竞赛论文风格。"
          :disabled="loading"
        ></textarea>
      </div>

      <div class="field compact-field">
        <label>补充数据</label>
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
            accept=".csv,.xlsx,.xls,.json,.txt,.dat,.tsv,.md"
            class="file-input-hidden"
            @change="handleFileSelect"
          />
          <div v-if="uploadedFiles.length === 0">
            <p>上传表格、原始结果或补充说明</p>
            <span>支持 CSV / Excel / JSON / TXT / Markdown</span>
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

    <button class="start-btn" :disabled="!canSubmit || loading" @click="handleSubmit">
      <span v-if="loading" class="spinner"></span>
      {{ loading ? loadingLabel : submitLabel }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

type TaskMode = 'writing' | 'polish'

interface TaskDraftPayload {
  taskType: TaskMode
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
const uploadedFiles = ref<File[]>([])
const isDragging = ref(false)
const fileInput = ref<HTMLInputElement>()

const canSubmit = computed(() => {
  if (props.mode === 'writing') {
    return Boolean(question.value.trim())
  }
  return Boolean(paperContent.value.trim())
})

const submitLabel = computed(() => props.mode === 'writing' ? '开始写作' : '开始润色')
const loadingLabel = computed(() => props.mode === 'writing' ? '正在创建写作任务...' : '正在创建润色任务...')

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
    const allowed = ['.csv', '.xlsx', '.xls', '.json', '.txt', '.dat', '.tsv', '.md']
    if (allowed.includes(ext) && !uploadedFiles.value.some(uf => uf.name === f.name)) {
      uploadedFiles.value.push(f)
    }
  }
}

function removeFile(index: number) {
  if (!props.loading) uploadedFiles.value.splice(index, 1)
}

function handleSubmit() {
  if (!canSubmit.value) return

  emit('submit', {
    taskType: props.mode,
    question: question.value.trim(),
    sourceQuestion: sourceQuestion.value.trim(),
    paperContent: paperContent.value.trim(),
    polishingRequirements: polishingRequirements.value.trim(),
    files: [...uploadedFiles.value],
  })
}
</script>

<style scoped>
.task-form {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.field + .field { margin-top: 24px; }

.field-slim + .field,
.compact-field {
  margin-top: 18px;
}

label {
  display: block;
  margin-bottom: 14px;
  font-size: 16px;
  font-weight: 800;
  letter-spacing: -0.02em;
}

textarea {
  display: block;
  width: 100%;
  height: 208px;
  resize: none;
  padding: 22px;
  border: 1px solid rgba(20, 28, 45, 0.15);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.58);
  color: #111;
  font-size: 15px;
  line-height: 1.65;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.short-area {
  height: 96px;
}

textarea::placeholder { color: #a4a9b3; }

textarea:focus {
  border-color: rgba(36, 78, 168, 0.45);
  box-shadow: 0 0 0 4px rgba(36, 78, 168, 0.07);
}

textarea:disabled { opacity: 0.6; cursor: not-allowed; }

.upload {
  min-height: 108px;
  display: grid;
  place-items: center;
  text-align: center;
  border: 1px dashed rgba(20, 28, 45, 0.22);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.42);
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}

.upload:hover,
.upload.dragging {
  border-color: rgba(36, 78, 168, 0.4);
  background: rgba(36, 78, 168, 0.04);
}

.upload.has-files {
  min-height: 80px;
  border-style: solid;
  border-color: rgba(36, 78, 168, 0.25);
}

.upload p {
  margin: 0 0 8px;
  font-size: 15px;
  color: #8a909b;
}

.upload span {
  font-size: 13px;
  color: #9da2ac;
}

.file-input-hidden { display: none; }

.file-list {
  width: 100%;
  padding: 12px 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  font-size: 14px;
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
  font-size: 13px;
  color: var(--blue);
  padding: 6px 0 0;
  text-align: center;
  cursor: pointer;
}

.start-btn {
  width: 100%;
  height: 62px;
  margin-top: 28px;
  border: none;
  border-radius: 7px;
  background: linear-gradient(135deg, var(--blue), var(--blue-dark));
  color: #fff;
  font-size: 20px;
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