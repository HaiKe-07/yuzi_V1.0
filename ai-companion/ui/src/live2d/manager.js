/**
 * Live2D 管理器（T3-01/T3-02）
 *
 * 封装 pixi-live2d-display 的加载、渲染、动画控制逻辑。
 * Vue 组件通过此模块操作 Live2D，不直接碰 PIXI/Live2D API。
 *
 * 功能：
 * 1. 初始化 PIXI Application + Live2D 模型加载
 * 2. 待机动画（自动眨眼、呼吸、随机动作）
 * 3. 表情切换（setExpression）—— T3-02 情绪联动核心
 * 4. 动作播放（startMotion，如点头、挥手）
 * 5. 嘴型同步（lipSync，为 T2-06 情感 TTS 联动预留）
 * 6. 鼠标拖拽视角跟随
 * 7. 情绪轮询 / SSE 订阅（T3-02 自动切换表情）
 *
 * 依赖：
 *   npm install pixi.js@6.5.10 pixi-live2d-display@0.4.0
 *   Live2D Cubism Core SDK（live2dcubismcore.min.js，需手动放置到 public/）
 *
 * 模型文件结构（放在 modelPath 下）：
 *   default.model3.json   ← 模型定义
 *   default.moc3          ← 网格数据
 *   default.png           ← 纹理
 *   motions/              ← 动作文件
 *   expressions/          ← 表情文件
 */

// 懒加载 PIXI 和 pixi-live2d-display，避免无模型时 import 失败
let PIXI = null
let Live2DModel = null
let libsLoaded = false

async function loadLibs() {
  if (libsLoaded) return { PIXI, Live2DModel }
  try {
    // pixi-live2d-display 依赖 PIXI v6（v7 接口不兼容）
    PIXI = await import('pixi.js')
    // live2d-display 需要挂载到 window.PIXI
    window.PIXI = PIXI
    const live2dModule = await import('pixi-live2d-display/cubism4.js')
    Live2DModel = live2dModule.Live2DModel
    libsLoaded = true
  } catch (e) {
    console.warn('[Live2D] 依赖加载失败，将使用占位符', e)
    libsLoaded = false
  }
  return { PIXI, Live2DModel }
}

/**
 * Live2D 管理器类
 */
export class Live2DManager {
  constructor() {
    this.app = null
    this.model = null
    this.canvas = null
    this.modelReady = false
    this.currentExpression = 'default'
    // T3-05: 形象当前是否处于活跃（点亮）状态；false = 休眠（暗色）
    this.awake = false
    // 自动动作定时器
    this._idleTimer = null
    // 情绪轮询定时器 / SSE
    this._emotionPollTimer = null
    this._eventSource = null
    this._lastExpression = null
    // 回调
    this.onReady = null
    this.onError = null
    this.onExpressionChange = null
    // T3-05: 唤醒状态变化回调（前端据此切"点亮"视觉）
    this.onAwakeChange = null
  }

  /**
   * 初始化 PIXI Application 并加载 Live2D 模型
   * @param {HTMLCanvasElement} canvas - 目标 canvas 元素
   * @param {string} modelPath - 模型 .model3.json 路径
   * @returns {Promise<boolean>} 是否成功
   */
  async init(canvas, modelPath) {
    this.canvas = canvas
    const { PIXI: pixi, Live2DModel: Model } = await loadLibs()
    if (!pixi || !Model) {
      this._notifyError('PIXI/Live2D 库未加载')
      return false
    }

    try {
      // 创建 PIXI Application（透明背景）
      this.app = new pixi.Application({
        view: canvas,
        backgroundAlpha: 0,
        antialias: true,
        resolution: window.devicePixelRatio || 1,
        autoDensity: true,
        resizeTo: canvas.parentElement,
      })

      // 加载 Live2D 模型
      this.model = await Model.from(modelPath)
      this.app.stage.addChild(this.model)

      // 调整模型尺寸和位置
      this._fitModel()

      // 启动待机动画
      this._startIdle()

      // 鼠标拖拽视角跟随
      this._setupInteraction()

      this.modelReady = true
      if (this.onReady) this.onReady()
      console.info('[Live2D] 模型加载成功', modelPath)
      return true
    } catch (e) {
      console.error('[Live2D] 模型加载失败', e)
      this._notifyError(`模型加载失败: ${e.message}`)
      return false
    }
  }

  /**
   * 调整模型适配 canvas 尺寸
   */
  _fitModel() {
    if (!this.model || !this.canvas) return
    const canvasW = this.canvas.clientWidth
    const canvasH = this.canvas.clientHeight
    const modelW = this.model.width
    const modelH = this.model.height
    // 等比缩放，让模型高度占 canvas 高度的 90%
    const scale = (canvasH * 0.9) / modelH
    this.model.scale.set(scale)
    // 居中
    this.model.x = (canvasW - modelW * scale) / 2
    this.model.y = (canvasH - modelH * scale) / 2
  }

  /**
   * 启动待机动画：自动眨眼 + 呼吸 + 随机小动作
   */
  _startIdle() {
    if (!this.model) return
    // pixi-live2d-display 内置眨眼和呼吸
    // 自动随机动作：每 15-30 秒播一个 idle 动作
    this._idleTimer = setInterval(() => {
      if (this.model && this.modelReady) {
        this.startMotion('Idle', 2) // 优先级 2 = 低
      }
    }, 20000 + Math.random() * 10000)
  }

  /**
   * 停止待机动画
   */
  _stopIdle() {
    if (this._idleTimer) {
      clearInterval(this._idleTimer)
      this._idleTimer = null
    }
  }

  /**
   * 鼠标交互：模型视角跟随鼠标
   */
  _setupInteraction() {
    if (!this.model || !this.canvas) return
    this.canvas.addEventListener('mousemove', (e) => {
      if (!this.model || !this.modelReady) return
      const rect = this.canvas.getBoundingClientRect()
      const x = (e.clientX - rect.left) / rect.width - 0.5
      const y = (e.clientY - rect.top) / rect.height - 0.5
      // 让模型看向鼠标方向（-1~1）
      this.model.focusController?.focus(x * 2, -y * 2)
    })
  }

  /**
   * 切换表情（T3-02 核心）
   * @param {string} name - 表情名称（happy/sad/angry/neutral/excited/happy_strong 等）
   */
  setExpression(name) {
    if (!this.model || !this.modelReady) return
    if (name === this.currentExpression) return // 避免重复切换
    try {
      this.model.expression(name)
      this.currentExpression = name
      console.debug('[Live2D] 表情切换:', name)
      if (this.onExpressionChange) this.onExpressionChange(name)
    } catch (e) {
      // 模型可能没有该表情文件，静默回退到 neutral
      console.warn('[Live2D] 表情切换失败（可能缺少表情文件）:', name)
      if (name !== 'neutral') {
        try { this.model.expression('neutral') } catch {}
      }
    }
  }

  /**
   * T3-02: 启动情绪联动
   * 优先用 SSE（/api/emotion/stream），不支持时回退到轮询（/api/live2d/status）
   * @param {string} baseUrl - 后端地址，如 http://localhost:18731
   */
  startEmotionSync(baseUrl = '') {
    // 先尝试 SSE
    if (typeof EventSource !== 'undefined') {
      this._startSSE(baseUrl)
    } else {
      this._startPolling(baseUrl)
    }
  }

  /**
   * SSE 模式：订阅 /api/emotion/stream
   */
  _startSSE(baseUrl) {
    try {
      this._eventSource = new EventSource(`${baseUrl}/api/emotion/stream`)
      this._eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.expression && data.expression !== this._lastExpression) {
            this._lastExpression = data.expression
            this.setExpression(data.expression)
          }
        } catch (e) {
          // 心跳包或解析失败，忽略
        }
      }
      this._eventSource.onerror = () => {
        console.warn('[Live2D] SSE 连接失败，回退到轮询')
        this._stopSSE()
        this._startPolling(baseUrl)
      }
      console.info('[Live2D] 情绪 SSE 订阅已启动')
    } catch (e) {
      console.warn('[Live2D] SSE 启动失败，回退到轮询', e)
      this._startPolling(baseUrl)
    }
  }

  /**
   * 轮询模式：定时拉 /api/live2d/status
   */
  _startPolling(baseUrl) {
    this._emotionPollTimer = setInterval(async () => {
      try {
        const resp = await fetch(`${baseUrl}/api/live2d/status`)
        const data = await resp.json()
        if (data.expression && data.expression !== this._lastExpression) {
          this._lastExpression = data.expression
          this.setExpression(data.expression)
        }
      } catch (e) {
        // 后端未启动，静默
      }
    }, 3000) // 每 3 秒轮询
    console.info('[Live2D] 情绪轮询已启动（3秒间隔）')
  }

  /**
   * 停止情绪联动
   */
  stopEmotionSync() {
    this._stopSSE()
    if (this._emotionPollTimer) {
      clearInterval(this._emotionPollTimer)
      this._emotionPollTimer = null
    }
  }

  _stopSSE() {
    if (this._eventSource) {
      this._eventSource.close()
      this._eventSource = null
    }
  }

  /**
   * 播放动作
   * @param {string} group - 动作组名（如 'Idle' / 'TapBody' / 'FlickHead'）
   * @param {number} priority - 优先级 0=强制, 1=普通, 2=背景
   */
  startMotion(group, priority = 1) {
    if (!this.model || !this.modelReady) return
    try {
      this.model.motion(group, priority)
    } catch (e) {
      console.debug('[Live2D] 动作播放失败:', e)
    }
  }

  /**
   * 嘴型同步（为 T2-06 TTS 联动预留）
   * @param {number} value - 开口度 0~1
   */
  lipSync(value) {
    if (!this.model || !this.modelReady) return
    try {
      // 通过参数 ParamMouthOpenY 控制嘴型
      this.model.internalModel?.coreModel?.setParameterValueById?.(
        'ParamMouthOpenY', value
      )
    } catch (e) {
      // 静默失败
    }
  }

  /**
   * T3-05: 唤醒点亮。
   *
   * 从休眠态切换到活跃态：
   * 1. 置 awake=true（前端据此给形象/背景加"点亮"光晕，恢复亮度）
   * 2. 切到 excited 表情（睁眼、精神）
   * 3. 播一个唤醒动作（TapBody = 轻拍/挥手），强调注意力
   * 4. 通知 onAwakeChange(true)
   * @returns {number|null} 若进入点亮态则返回恢复休眠的延时句柄（可 clearTimeout）
   */
  wakeUp() {
    this.setAwake(true)
    this.setExpression('excited')
    this.startMotion('TapBody', 1)
    return null
  }

  /**
   * 进入休眠态：表情回 neutral，awake=false（前端调暗/去光晕）
   * 由组件在点亮动画结束后调用，或在窗口隐藏/托盘常驻时调用。
   */
  goToSleep() {
    this.setAwake(false)
    this.setExpression('neutral')
  }

  /**
   * 切换活跃（点亮）状态，并广播变化。
   * @param {boolean} val
   */
  setAwake(val) {
    if (this.awake === val) return
    this.awake = val
    if (this.onAwakeChange) this.onAwakeChange(val)
    console.info(`[Live2D] 形象${val ? '点亮' : '休眠'}`)
  }

  /**
   * 销毁：释放资源
   */
  destroy() {
    this.stopEmotionSync()
    this._stopIdle()
    if (this.model) {
      try { this.model.destroy() } catch {}
      this.model = null
    }
    if (this.app) {
      try { this.app.destroy(true) } catch {}
      this.app = null
    }
    this.modelReady = false
  }

  /**
   * 窗口大小变化时重新适配
   */
  resize() {
    if (this.app) {
      this.app.renderer.resize(
        this.canvas.clientWidth,
        this.canvas.clientHeight
      )
    }
    this._fitModel()
  }

  _notifyError(msg) {
    if (this.onError) this.onError(msg)
  }

  /**
   * 当前状态摘要
   */
  status() {
    return {
      ready: this.modelReady,
      expression: this.currentExpression,
      modelLoaded: this.model !== null,
      emotionSync: this._eventSource !== null || this._emotionPollTimer !== null,
      awake: this.awake,
    }
  }
}

/**
 * 全局单例
 */
let _live2dManager = null
export function getLive2DManager() {
  if (!_live2dManager) {
    _live2dManager = new Live2DManager()
  }
  return _live2dManager
}
