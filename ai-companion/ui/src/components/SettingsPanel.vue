<script setup>
import { ref, onMounted } from 'vue'
import { useCompanionStore } from '../stores/companion'

const store = useCompanionStore()
const settings = ref({})
const saving = ref(false)
const message = ref('')

onMounted(async () => {
  try {
    settings.value = await store.getSettings()
  } catch (e) {
    message.value = e.message
  }
})

async function save() {
  saving.value = true
  message.value = ''
  try {
    await store.updateSettings({
      user_name: settings.value.user_name,
      companion_name: settings.value.companion_name,
      llm_provider: settings.value.llm?.provider,
      llm_model: settings.value.llm?.model,
      asr_provider: settings.value.asr?.provider,
      tts_provider: settings.value.tts?.provider,
      tts_voice: settings.value.tts?.voice,
    })
    message.value = '已保存（部分配置重启后生效）'
  } catch (e) {
    message.value = e.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="settings-panel">
    <h2>基础设置</h2>

    <section>
      <h3>身份</h3>
      <label>
        <span>用户称呼</span>
        <input v-model="settings.user_name" placeholder="例如：阿明" />
      </label>
      <label>
        <span>陪伴者称呼</span>
        <input v-model="settings.companion_name" placeholder="例如：小七" />
      </label>
    </section>

    <section>
      <h3>大模型</h3>
      <label>
        <span>Provider</span>
        <select v-model="settings.llm.provider">
          <option value="deepseek">deepseek</option>
        </select>
      </label>
      <label>
        <span>模型</span>
        <input v-model="settings.llm.model" placeholder="deepseek-chat" />
      </label>
    </section>

    <section>
      <h3>语音</h3>
      <label>
        <span>ASR Provider</span>
        <select v-model="settings.asr.provider">
          <option value="cloud_openai_whisper">cloud_openai_whisper</option>
          <option value="cloud_xfyun">cloud_xfyun</option>
        </select>
      </label>
      <label>
        <span>TTS Provider</span>
        <select v-model="settings.tts.provider">
          <option value="cloud_openai_tts">cloud_openai_tts</option>
          <option value="cloud_volc">cloud_volc</option>
        </select>
      </label>
      <label>
        <span>TTS 音色</span>
        <input v-model="settings.tts.voice" placeholder="nova" />
      </label>
    </section>

    <div class="actions">
      <button @click="save" :disabled="saving">
        {{ saving ? '保存中...' : '保存' }}
      </button>
      <span v-if="message" class="msg">{{ message }}</span>
    </div>
  </div>
</template>

<style scoped>
.settings-panel {
  padding: 20px;
  overflow-y: auto;
  height: 100%;
}
h2 {
  margin-bottom: 16px;
  color: var(--text-primary);
}
section {
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
h3 {
  margin-bottom: 12px;
  color: var(--accent);
}
label {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 8px 0;
}
label span {
  width: 120px;
  color: var(--text-secondary);
}
input, select {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  user-select: text;
}
input:focus, select:focus {
  outline: none;
  border-color: var(--accent);
}
.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.actions button {
  padding: 8px 20px;
  border: none;
  border-radius: 6px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
}
.actions button:disabled {
  opacity: 0.5;
}
.msg {
  color: var(--success);
  font-size: 12px;
}
</style>
