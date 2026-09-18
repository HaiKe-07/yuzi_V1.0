<script setup>
/**
 * Live2D 形象组件（T3-01）
 *
 * 在 canvas 中渲染 Live2D 模型，支持待机动画。
 * 无模型或库未加载时，显示 CSS 占位形象。
 *
 * 布局：左侧形象区 + 右侧对话区（由 App.vue 控制）
 */
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { getLive2DManager } from '../live2d/manager.js'
import { useCompanionStore } from '../stores/companion'

const store = useCompanionStore()
const manager = getLive2DManager()

const canvasRef = ref(null)
const status = ref({ ready: false, error: '' })
const showPlaceholder = ref(true)

// Live2D 模型路径（可从 config 读取，默认用占位模型目录）
const MODEL_PATH = './live2d/default/default.model3.json'

onMounted(async () => {
  // 等 companion store 初始化完成（获取后端状态）
  await store.init()
  await initLive2D()
  // 监听 AI 情绪变化 → 表情联动（T3-02 预留）
  watch(() => store.status?.ai_emotion, (newEmo) => {
    if (newEmo && status.value.ready) {
      manager.setExpression(emotionLabelToName(newEmo))
    }
  })
})

async function initLive2D() {
  if (!canvasRef.value) return
  manager.onReady = () => {
    status.value.ready = true
    showPlaceholder.value = false
  }
  manager.onError = (msg) => {
    status.value.error = msg
    showPlaceholder.value = true
  }
  const ok = await manager.init(canvasRef.value, MODEL_PATH)
  if (!ok) {
    showPlaceholder.value = true
  }
}

function emotionLabelToName(label) {
  const map = {
    '开心': 'happy', '难过': 'sad', '生气': 'angry',
    '中性': 'neutral', '期待': 'excited', '平静': 'neutral',
  }
  return map[label] || 'neutral'
}

function handleWake() {
  // 唤醒按钮（测试用，实际由 T2-08 唤醒词触发）
  if (status.value.ready) {
    manager.onWake()
  }
}

// 窗口大小变化时重新适配
function onResize() {
  if (status.value.ready) manager.resize()
}

onUnmounted(() => {
  manager.destroy()
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <div class="live2d-view">
    <!-- Live2D canvas（模型加载后显示） -->
    <canvas
      ref="canvasRef"
      class="live2d-canvas"
      :class="{ hidden: showPlaceholder }"
    ></canvas>

    <!-- 占位形象（无模型时显示，CSS 动画） -->
    <div v-if="showPlaceholder" class="placeholder">
      <div class="avatar">
        <div class="avatar-face">
          <div class="eye left" :class="{ blink: !store.loading }"></div>
          <div class="eye right" :class="{ blink: !store.loading }"></div>
          <div class="mouth" :class="{ talking: store.loading }"></div>
        </div>
        <div class="hair"></div>
      </div>
      <p class="hint">
        {{ status.error || 'Live2D 形象占位' }}
      </p>
      <p class="sub-hint">
        放置模型到 ui/live2d/default/ 目录启用形象
      </p>
    </div>

    <!-- 测试按钮 -->
    <button class="wake-btn" @click="handleWake" title="模拟唤醒">
      {{ status.ready ? '唤醒形象' : '' }}
    </button>
  </div>
</template>

<style scoped>
.live2d-view {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  overflow: hidden;
}

.live2d-canvas {
  width: 100%;
  height: 100%;
}
.live2d-canvas.hidden {
  display: none;
}

/* 占位形象 */
.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.avatar {
  position: relative;
  width: 180px;
  height: 200px;
}

.avatar-face {
  position: absolute;
  bottom: 0;
  width: 160px;
  height: 180px;
  left: 10px;
  background: #f0d0c0;
  border-radius: 50% 50% 45% 45% / 55% 55% 45% 45%;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
}

.hair {
  position: absolute;
  top: 0;
  left: 0;
  width: 180px;
  height: 120px;
  background: #3a2a1a;
  border-radius: 50% 50% 30% 30% / 60% 60% 40% 40%;
  z-index: -1;
}

.eye {
  position: absolute;
  top: 70px;
  width: 18px;
  height: 24px;
  background: #2a2a3a;
  border-radius: 50%;
  animation: blink 4s infinite;
}
.eye.left { left: 50px; }
.eye.right { right: 50px; }

.eye.blink {
  animation: blink 4s infinite;
}

@keyframes blink {
  0%, 90%, 100% { transform: scaleY(1); }
  95% { transform: scaleY(0.1); }
}

.mouth {
  position: absolute;
  bottom: 40px;
  left: 50%;
  transform: translateX(-50%);
  width: 20px;
  height: 6px;
  background: #c08080;
  border-radius: 0 0 10px 10px;
}
.mouth.talking {
  animation: talk 0.3s infinite;
}
@keyframes talk {
  0%, 100% { height: 4px; }
  50% { height: 12px; border-radius: 50%; }
}

.hint {
  color: var(--text-secondary, #a0a0b0);
  font-size: 13px;
}
.sub-hint {
  color: var(--text-secondary, #606070);
  font-size: 11px;
  opacity: 0.7;
}

.wake-btn {
  position: absolute;
  bottom: 10px;
  right: 10px;
  padding: 4px 10px;
  font-size: 11px;
  border: 1px solid var(--border, #2a2a4a);
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary, #a0a0b0);
  cursor: pointer;
  opacity: 0.5;
  transition: opacity 0.2s;
}
.wake-btn:hover {
  opacity: 1;
}
.wake-btn:empty {
  display: none;
}
</style>
