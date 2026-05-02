<template>
  <div class="task-form">
    <div class="field">
      <label>输入题目</label>
      <textarea
        data-layout-anchor="task-input-textarea"
        v-model="question"
        placeholder="在此输入完整的数学建模题目..."
        :disabled="loading"
      ></textarea>
    </div>

    <div class="field">
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
          accept=".csv,.xlsx,.xls,.json,.txt,.dat,.tsv"
          class="file-input-hidden"
          @change="handleFileSelect"
        />
        <div v-if="uploadedFiles.length === 0">
          <p>拖拽文件到此处，或点击上传</p>
          <span>支持 CSV / Excel / JSON / TXT</span>
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

    <button
      class="start-btn"
      :disabled="!question.trim() || loading"
      @click="handleSubmit"
    >
      <span v-if="loading" class="spinner"></span>
      {{ loading ? '正在创建任务...' : '开始建模' }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  submit: [question: string, files: File[]]
}>()

const question = ref('')
const uploadedFiles = ref<File[]>([])
const isDragging = ref(false)
const fileInput = ref<HTMLInputElement>()

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
    const allowed = ['.csv', '.xlsx', '.xls', '.json', '.txt', '.dat', '.tsv']
    if (allowed.includes(ext) && !uploadedFiles.value.some(uf => uf.name === f.name)) {
      uploadedFiles.value.push(f)
    }
  }
}

function removeFile(index: number) {
  if (!props.loading) uploadedFiles.value.splice(index, 1)
}

function handleSubmit() {
  if (question.value.trim()) {
    emit('submit', question.value.trim(), [...uploadedFiles.value])
  }
}
</script>

<style scoped>
.task-form {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.field + .field { margin-top: 34px; }

label {
  display: block;
  margin-bottom: 18px;
  font-size: 18px;
  font-weight: 800;
  letter-spacing: -0.02em;
}

textarea {
  display: block;
  width: 100%;
  height: 230px;
  resize: none;
  padding: 26px;
  border: 1px solid rgba(20, 28, 45, 0.15);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.58);
  color: #111;
  font-size: 17px;
  line-height: 1.7;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}

textarea::placeholder { color: #a4a9b3; }

textarea:focus {
  border-color: rgba(36, 78, 168, 0.45);
  box-shadow: 0 0 0 4px rgba(36, 78, 168, 0.07);
}

textarea:disabled { opacity: 0.6; cursor: not-allowed; }

.upload {
  height: 150px;
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
  height: auto;
  min-height: 80px;
  border-style: solid;
  border-color: rgba(36, 78, 168, 0.25);
}

.upload p {
  margin: 0 0 10px;
  font-size: 16px;
  color: #8a909b;
}

.upload span {
  font-size: 14px;
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
  padding: 6px 0;
  text-align: center;
  cursor: pointer;
}

.start-btn {
  width: 100%;
  height: 68px;
  margin-top: 32px;
  border: none;
  border-radius: 7px;
  background: linear-gradient(135deg, var(--blue), var(--blue-dark));
  color: #fff;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: 0.04em;
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
