// 对话状态管理（Pinia store）。
//
// 把对 window.companion 的调用集中在此，组件只消费 store。

import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useCompanionStore = defineStore('companion', () => {
  const ready = ref(false)
  const status = ref({})
  const history = ref([])
  const loading = ref(false)
  const error = ref('')

  function api() {
    if (!window.companion) {
      throw new Error('window.companion 未注入（需在 Electron 环境运行）')
    }
    return window.companion
  }

  async function init() {
    try {
      const s = await api().status()
      status.value = s
      const h = await api().history(50)
      history.value = h
      ready.value = true
    } catch (e) {
      // 非 Electron 环境（如纯浏览器调试）会失败，给一个可见错误
      error.value = e.message
      ready.value = false
    }
  }

  async function sendText(text) {
    if (!text || !text.trim()) return
    loading.value = true
    error.value = ''
    try {
      history.value.push({
        role: 'user', content: text, timestamp: new Date().toISOString(),
      })
      const { reply } = await api().chat(text)
      history.value.push({
        role: 'assistant', content: reply,
        timestamp: new Date().toISOString(),
      })
      return reply
    } catch (e) {
      error.value = e.message
      throw e
    } finally {
      loading.value = false
    }
  }

  async function speak(text) {
    loading.value = true
    try {
      return await api().speak(text)
    } finally {
      loading.value = false
    }
  }

  async function getSettings() {
    return await api().getSettings()
  }

  async function updateSettings(updates) {
    return await api().updateSettings(updates)
  }

  return {
    ready, status, history, loading, error,
    init, sendText, speak, getSettings, updateSettings,
  }
})
