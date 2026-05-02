<template>
  <div class="settings-overlay" @click.self="$emit('close')">
    <div class="settings-modal">
      <div class="modal-header">
        <h2>API Key 配置</h2>
        <button class="close-btn" @click="$emit('close')">关闭</button>
      </div>
      <div class="modal-body">
        <ApiKeySettings />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import ApiKeySettings from './settings/ApiKeySettings.vue'

defineEmits<{ close: [] }>()
</script>

<style scoped>
.settings-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(14, 18, 32, 0.56);
  z-index: 200;
}

.settings-modal {
  width: min(1100px, 100%);
  max-height: calc(100vh - 48px);
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 22px;
  background: rgba(245, 247, 251, 0.94);
  box-shadow: 0 30px 80px rgba(16, 24, 40, 0.22);
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 22px 24px;
  border-bottom: 1px solid rgba(20, 28, 45, 0.08);
}

.modal-header h2 {
  margin: 0;
  font-size: 22px;
  letter-spacing: -0.03em;
}

.close-btn {
  min-height: 42px;
  padding: 0 16px;
  border: 1px solid rgba(20, 28, 45, 0.12);
  border-radius: 12px;
  background: white;
  color: var(--text);
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
}

.modal-body {
  max-height: calc(100vh - 132px);
  overflow: auto;
  padding: 24px;
}

@media (max-width: 640px) {
  .settings-overlay {
    padding: 12px;
  }

  .modal-header,
  .modal-body {
    padding: 18px;
  }
}
</style>
