<script setup>
// 无边框窗口自绘标题栏。最小化/关闭按钮通过 preload 暴露的 window.companion.window 调用。
// T3-04 阶段接入托盘时再扩展。
function minimize() {
  window.companion?.window?.minimize?.()
}
function close() {
  window.companion?.window?.close?.()
}
</script>

<template>
  <div class="title-bar">
    <span class="title">AI 陪伴助手</span>
    <div class="controls">
      <button class="btn-min" @click="minimize" title="最小化">—</button>
      <button class="btn-close" @click="close" title="关闭">×</button>
    </div>
  </div>
</template>

<style scoped>
.title-bar {
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  background: var(--bg-secondary);
  -webkit-app-region: drag;  /* 可拖动 */
}
.controls {
  -webkit-app-region: no-drag;
  display: flex;
  gap: 4px;
}
.controls button {
  width: 28px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 14px;
}
.controls button:hover {
  background: var(--bg-tertiary);
}
.btn-close:hover {
  background: var(--danger);
  color: #fff;
}
</style>
