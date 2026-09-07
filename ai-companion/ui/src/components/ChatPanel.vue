<script setup>
import { ref, nextTick, watch } from 'vue'
import { useCompanionStore } from '../stores/companion'

const store = useCompanionStore()
const inputText = ref('')
const scrollRef = ref(null)

async function send() {
  const text = inputText.value.trim()
  if (!text || store.loading) return
  inputText.value = ''
  await store.sendText(text)
  await nextTick()
  scrollToBottom()
}

function onKeydown(e) {
  // Enter 发送，Shift+Enter 换行
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function scrollToBottom() {
  if (scrollRef.value) {
    scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  }
}

watch(() => store.history.length, scrollToBottom)
</script>

<template>
  <div class="chat-panel">
    <div class="messages" ref="scrollRef">
      <div v-if="!store.ready && !store.error" class="hint">
        正在连接后端...
      </div>
      <div v-if="store.error" class="error">
        {{ store.error }}
      </div>
      <div
        v-for="(m, i) in store.history"
        :key="i"
        :class="['msg', m.role]"
      >
        <div class="bubble">{{ m.content }}</div>
      </div>
    </div>
    <div class="input-area">
      <textarea
        v-model="inputText"
        @keydown="onKeydown"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        rows="2"
        :disabled="store.loading"
      />
      <button @click="send" :disabled="store.loading || !inputText.trim()">
        {{ store.loading ? '...' : '发送' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}
.hint, .error {
  color: var(--text-secondary);
  text-align: center;
  padding: 20px;
}
.error {
  color: var(--danger);
}
.msg {
  margin: 8px 0;
  display: flex;
}
.msg.user {
  justify-content: flex-end;
}
.msg.assistant {
  justify-content: flex-start;
}
.bubble {
  max-width: 70%;
  padding: 10px 14px;
  border-radius: 14px;
  line-height: 1.5;
  word-break: break-word;
  white-space: pre-wrap;
}
.msg.user .bubble {
  background: var(--accent);
  color: #fff;
  border-bottom-right-radius: 4px;
}
.msg.assistant .bubble {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border-bottom-left-radius: 4px;
}
.input-area {
  display: flex;
  gap: 8px;
  padding: 12px;
  border-top: 1px solid var(--border);
  background: var(--bg-secondary);
}
textarea {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-primary);
  color: var(--text-primary);
  resize: none;
}
textarea:focus {
  outline: none;
  border-color: var(--accent);
}
.input-area button {
  padding: 0 20px;
  border: none;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
}
.input-area button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
