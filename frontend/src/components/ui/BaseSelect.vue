<template>
  <div ref="rootRef" class="base-select" :class="{ open: isOpen, disabled }">
    <button
      type="button"
      class="select-trigger"
      :disabled="disabled"
      @click="toggleOpen"
    >
      <span class="select-value">{{ selectedLabel }}</span>
      <span class="select-caret" aria-hidden="true">▾</span>
    </button>

    <div v-if="isOpen" class="select-menu">
      <button
        v-for="option in options"
        :key="String(option.value)"
        type="button"
        class="select-option"
        :class="{ active: option.value === modelValue }"
        @click="choose(option.value)"
      >
        <span class="option-label">{{ option.label }}</span>
        <span v-if="option.meta" class="option-meta">{{ option.meta }}</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

type SelectValue = string | number

interface SelectOption {
  value: SelectValue
  label: string
  meta?: string
}

const props = defineProps<{
  modelValue: SelectValue
  options: readonly SelectOption[]
  placeholder?: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: SelectValue]
}>()

const rootRef = ref<HTMLElement | null>(null)
const isOpen = ref(false)

const selectedLabel = computed(() => {
  const matched = props.options.find((option) => option.value === props.modelValue)
  return matched?.label || props.placeholder || '请选择'
})

function toggleOpen() {
  if (props.disabled) return
  isOpen.value = !isOpen.value
}

function choose(value: SelectValue) {
  emit('update:modelValue', value)
  isOpen.value = false
}

function handlePointerDown(event: PointerEvent) {
  const target = event.target as Node | null
  if (!rootRef.value || !target) return
  if (!rootRef.value.contains(target)) {
    isOpen.value = false
  }
}

onMounted(() => {
  window.addEventListener('pointerdown', handlePointerDown)
})

onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', handlePointerDown)
})
</script>

<style scoped>
.base-select {
  position: relative;
  width: 100%;
}

.select-trigger {
  appearance: none;
  width: 100%;
  min-height: 48px;
  padding: 12px 16px;
  border-radius: 14px;
  border: 1px solid rgba(20, 28, 45, 0.12);
  background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(246,248,252,0.94));
  color: var(--text);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.9), 0 10px 24px rgba(18,31,58,0.05);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.select-trigger:hover {
  border-color: rgba(36, 78, 168, 0.26);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.92), 0 14px 28px rgba(18,31,58,0.08);
}

.open .select-trigger {
  border-color: rgba(36, 78, 168, 0.45);
  box-shadow: 0 0 0 4px rgba(36, 78, 168, 0.08), 0 16px 30px rgba(18,31,58,0.08);
}

.disabled .select-trigger {
  cursor: not-allowed;
  opacity: 0.65;
}

.select-value {
  min-width: 0;
  flex: 1;
  font-size: 14px;
  line-height: 1.45;
}

.select-caret {
  flex: 0 0 auto;
  color: var(--blue);
  font-size: 14px;
}

.select-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  right: 0;
  z-index: 40;
  max-height: 300px;
  overflow-y: auto;
  padding: 8px;
  border: 1px solid rgba(20, 28, 45, 0.12);
  border-radius: 16px;
  background: rgba(255,255,255,0.98);
  box-shadow: 0 24px 60px rgba(18,31,58,0.16);
  backdrop-filter: blur(12px);
}

.select-option {
  width: 100%;
  border: none;
  background: transparent;
  border-radius: 12px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  cursor: pointer;
  text-align: left;
  transition: background 0.16s ease, color 0.16s ease;
}

.select-option:hover,
.select-option.active {
  background: rgba(36, 78, 168, 0.09);
}

.option-label {
  font-size: 14px;
  color: var(--text);
}

.option-meta {
  font-size: 12px;
  line-height: 1.4;
  color: var(--muted);
}
</style>