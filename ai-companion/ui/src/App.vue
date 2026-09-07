<script setup>
import { onMounted, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import TitleBar from './components/TitleBar.vue'
import { useCompanionStore } from './stores/companion'

const store = useCompanionStore()
const activeTab = ref('chat') // chat | settings

onMounted(async () => {
  await store.init()
})
</script>

<template>
  <div class="app-shell">
    <TitleBar />
    <nav class="tab-bar">
      <button
        :class="{ active: activeTab === 'chat' }"
        @click="activeTab = 'chat'"
      >对话</button>
      <button
        :class="{ active: activeTab === 'settings' }"
        @click="activeTab = 'settings'"
      >设置</button>
    </nav>
    <main class="content">
      <ChatPanel v-show="activeTab === 'chat'" />
      <SettingsPanel v-show="activeTab === 'settings'" />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-primary);
  color: var(--text-primary);
}
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border);
  background: var(--bg-secondary);
}
.tab-bar button {
  flex: 1;
  padding: 10px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 14px;
  transition: all 0.15s;
}
.tab-bar button.active {
  color: var(--accent);
  border-bottom: 2px solid var(--accent);
}
.content {
  flex: 1;
  overflow: hidden;
}
</style>
