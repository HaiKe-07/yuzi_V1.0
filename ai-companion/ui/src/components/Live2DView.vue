<script setup>
/**
 * Live2D 形象组件（T3-01/T3-02）
 *
 * 在 canvas 中渲染 Live2D 模型，支持待机动画。
 * 无模型或库未加载时，显示 CSS 占位形象。
 * T3-02: 启动情绪联动，AI 情绪变化自动切换表情。
 *
 * 布局：左侧形象区 + 右侧对话区（由 App.vue 控制）
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { getLive2DManager } from '../live2d/manager.js'
import { useCompanionStore } from '../stores/companion'

const store = useCompanionStore()
const manager = getLive2DManager()

const canvasRef = ref(null)
const status = ref({ ready: false, error: '', expression: 'neutral' })
const showPlaceholder = ref(true)

// Live2D 模型路径（可从 config 读取，默认用占位模型目录）
const MODEL_PATH = './live2d/default/default.model3.json'

// 后端 API 地址：优先取 Electron preload 暴露的真实地址，避免端口漂移
function resolveApiBase() {
  try {
    if (window.companion && window.companion.getBackendBase) {
      return window.companion.getBackendBase()
    }
  } catch (e) {
    // 纯浏览器调试，用默认
  }
  return 'http://localhost:18731'
}

onMounted(async () => {
  // 等 companion store 初始化完成（获取后端状态）
  await store.init()
  await initLive2D()
  // T3-02: 无论模型是否加载，都启动情绪联动
  // 模型加载成功 → 切换 Live2D 表情（后端 expression 驱动）
  // 模型未加载 → 占位形象也会根据情绪变色（气泡显示情绪名）
  manager.startEmotionSync(resolveApiBase())
  manager.onExpressionChange = (name) => {
    status.value.expression = name
  }
  // T3-03: 进阶交互反馈（点击反应气泡 / 拖拽提示）
  manager.onBodyTap = (part) => {
    status.value.tapHint = tapMessage(part)
    indicateTap()
  }
  manager.onDragEnd = () => {
    status.value.tapHint = '👋 回来啦'
    indicateTap()
  }
  // 占位形象：用 store 的情绪标签显示中文气泡（模型驱动时 expression 为后端英文名）
  if (window.companion) {
    initPlaceholderEmotion()
  }
})

// T3-03: 点击/拖拽瞬时提示气泡（1.2s 后淡出）
let _tapHintTimer = null
function indicateTap() {
  if (_tapHintTimer) clearTimeout(_tapHintTimer)
  _tapHintTimer = setTimeout(() => { status.value.tapHint = '' }, 1200)
}
function tapMessage(part) {
  const map = {
    Body: '嘿嘿，别戳～', Head: '诶，头不可以乱摸！',
    TapLeft: '哎哟～', TapRight: '哎哟～',
    TapLeftEar: '耳朵痒痒的…', TapRightEar: '耳朵痒痒的…',
  }
  return map[part] || '呀！'
}

// 占位形象情绪：轮询 live2d/status 拿中文情绪标签（模型未加载时驱动占位气泡）
function initPlaceholderEmotion() {
  const update = async () => {
    try {
      const data = await window.companion.live2dStatus()
      if (data && data.ai_emotion) {
        store.status = { ...store.status, ai_emotion: data.ai_emotion }
      }
    } catch (e) {
      // 后端未就绪，静默
    }
  }
  update()
  // 每 3 秒刷新占位情绪（仅占位模式使用）
  window._placeholderEmotionTimer = setInterval(update, 3000)
}

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

function handlePlaceholderTap() {
  // 模型已就绪 → 唤醒动画（实际唤醒由 T2-08 触发）；否则占位形象点击反馈
  if (status.value.ready) {
    manager.onWake()
  } else {
    status.value.tapHint = '呀！'
    indicateTap()
  }
}

// 窗口大小变化时重新适配
function onResize() {
  if (status.value.ready) manager.resize()
}

// 展示用的情绪文本：占位模式用 store 的中文标签，模型模式用后端 expression
const displayEmotionalText = computed(() => {
  return store.status?.ai_emotion || status.value.expression
})

onUnmounted(() => {
  manager.destroy()
  if (window._placeholderEmotionTimer) {
    clearInterval(window._placeholderEmotionTimer)
    window._placeholderEmotionTimer = null
  }
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <div class="live2d-view" :data-expression="status.expression">
    <!-- Live2D canvas（模型加载后显示） -->
    <canvas
      ref="canvasRef"
      class="live2d-canvas"
      :class="{ hidden: showPlaceholder }"
    ></canvas>

    <!-- 占位形象（无模型时显示，CSS 动画 + 情绪颜色） -->
    <div v-if="showPlaceholder" class="placeholder">
      <div class="avatar" :class="'emo-' + status.expression">
        <div class="avatar-face">
          <div class="eye left"></div>
          <div class="eye right"></div>
          <div class="mouth" :class="status.expression"></div>
        </div>
        <div class="hair"></div>
        <!-- 情绪气泡：占位模式显示中文标签，模型模式显示后端 expression -->
        <div class="emo-bubble" v-if="displayEmotionalText && displayEmotionalText !== 'neutral'">
          {{ displayEmotionalText }}
        </div>
      </div>
      <p class="hint">
        {{ status.error || 'Live2D 形象占位' }}
      </p>
      <p class="sub-hint">
        放置模型到 ui/live2d/default/ 目录启用形象
      </p>
    </div>

    <!-- T3-03: 点击/拖拽瞬时反馈气泡 -->
    <transition name="pop">
      <div v-if="status.tapHint" class="tap-hint">{{ status.tapHint }}</div>
    </transition>

    <!-- T3-03: 无模型时点占位形象也有反馈 -->
    <div class="wake-btn" @click="handlePlaceholderTap" title="模拟点击反应">
      {{ status.ready ? '唤醒形象' : '戳我' }}
    </div>
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
  transition: background 0.5s;
}

/* T3-02: 根据情绪调整背景色调 */
.live2d-view[data-expression="happy"] {
  background: linear-gradient(135deg, #1a2e1a 0%, #163e2e 100%);
}
.live2d-view[data-expression="sad"] {
  background: linear-gradient(135deg, #1a1a2e 0%, #0e1a3e 100%);
}
.live2d-view[data-expression="angry"] {
  background: linear-gradient(135deg, #2e1a1a 0%, #3e1616 100%);
}
.live2d-view[data-expression="excited"] {
  background: linear-gradient(135deg, #2e2a1a 0%, #3e3616 100%);
}

.live2d-canvas {
  width: 100%;
  height: 100%;
  cursor: grab;
  touch-action: none;
}
.live2d-canvas.hidden {
  display: none;
}

/* T3-03: 点击/拖拽瞬时反馈气泡 */
.tap-hint {
  position: absolute;
  top: 8%;
  left: 50%;
  transform: translateX(-50%);
  padding: 6px 14px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: #fff;
  font-size: 13px;
  white-space: nowrap;
  pointer-events: none;
  z-index: 10;
}
.pop-enter-active,
.pop-leave-active {
  transition: all 0.2s ease;
}
.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-6px);
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
  transition: background 0.5s;
}

/* 情绪影响肤色 */
.avatar.emo-happy .avatar-face { background: #f5d8c8; }
.avatar.emo-sad .avatar-face { background: #e0c8b8; }
.avatar.emo-angry .avatar-face { background: #f0c0b0; }

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
  transition: all 0.3s;
}

/* 情绪影响嘴型 */
.mouth.happy { border-radius: 0 0 30px 30px; height: 10px; }
.mouth.sad { border-radius: 30px 30px 0 0; bottom: 35px; }
.mouth.angry { border-radius: 0 0 10px 10px; height: 8px; }
.mouth.excited { border-radius: 50%; height: 14px; width: 14px; }
.mouth.neutral { border-radius: 0 0 10px 10px; }

.emo-bubble {
  position: absolute;
  top: -10px;
  right: -60px;
  padding: 4px 10px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 12px;
  font-size: 11px;
  color: #a0c0f0;
  white-space: nowrap;
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
