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
 * 6. 鼠标视角跟随（T3-01）
 * 7. 情绪轮询 / SSE 订阅（T3-02 自动切换表情）
 * 8. 进阶交互（T3-03）：点击模型动作反应 + 拖拽位移回弹
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
    // 自动动作定时器
    this._idleTimer = null
    // 情绪轮询定时器 / SSE
    this._emotionPollTimer = null
    this._eventSource = null
    this._lastExpression = null
    // 交互状态（T3-03）
    this._dragging = false
    this._dragOffset = null
    this._homeX = 0
    this._homeY = 0
    this._tapTimer = null       // 点击反应防抖
    this._snapTimer = null      // 拖拽回弹动画句柄
    // 回调（T3-01/T3-03）
    this.onReady = null
    this.onError = null
    this.onExpressionChange = null
    this.onBodyTap = null       // (partName, x, y) 点击模型反应
    this.onDragEnd = null       // () 拖拽结束
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
    this._homeX = (canvasW - modelW * scale) / 2
    this._homeY = (canvasH - modelH * scale) / 2
    this.model.x = this._homeX
    this.model.y = this._homeY
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
   * 进阶交互（T3-03）：点击身体反应 + 拖拽位移 + 视角跟随
   *
   * 三类行为：
   * 1. focus：模型视线跟随鼠标
   * 2. tap：点击命中模型 → 播放"被触摸"动作 + 随机反应表情（防抖）
   * 3. drag：按住拖动位移，松手后缓动回弹到 home 位置
   */
  _setupInteraction() {
    if (!this.model || !this.canvas) return
    const canvas = this.canvas

    // —— 视角跟随（未在拖拽时）——
    canvas.addEventListener('mousemove', (e) => {
      if (!this.model || !this.modelReady || this._dragging) return
      const rect = canvas.getBoundingClientRect()
      const x = (e.clientX - rect.left) / rect.width - 0.5
      const y = (e.clientY - rect.top) / rect.height - 0.5
      this.model.focusController?.focus(x * 2, -y * 2)
    })

    // —— 点击命中检测 + 拖拽 ——
    canvas.addEventListener('pointerdown', (e) => this._onPointerDown(e))
    canvas.addEventListener('pointermove', (e) => this._onPointerMove(e))
    canvas.addEventListener('pointerup', (e) => this._onPointerUp(e))
    canvas.addEventListener('pointerleave', (e) => this._onPointerUp(e))
    // 触摸端支持
    canvas.style.touchAction = 'none'
  }

  /** 点在模型上（用 stage 坐标 + hit box） */
  _hitModel(clientX, clientY) {
    if (!this.model || !this.app) return false
    const rect = this.canvas.getBoundingClientRect()
    const global = this.app.renderer.plugins.interaction.mouse.global
    global.set(clientX - rect.left, clientY - rect.top)
    try {
      // pixi-live2d-display 提供 hitTest，按模型 HitAreas 判定
      return this.model.hitTest ? this._hitTestRegions(global.x, global.y) : this._hitBounds(global.x, global.y)
    } catch (e) {
      return this._hitBounds(clientX - rect.left, clientY - rect.top)
    }
  }

  /** 用模型 HitAreas（Body/Head/TapLeft 等）做命中判定，返回命中的区域名 */
  _hitTestRegions(globalX, globalY) {
    const cands = ['Body', 'Head', 'TapLeft', 'TapRight', 'TapLeftEar', 'TapRightEar']
    for (const name of cands) {
      try {
        if (this.model.hitTest(name, globalX, globalY)) return name
      } catch (e) { /* 模型无该区域 */ }
    }
    return null
  }

  /** 兜底：用模型包围盒粗略判定 */
  _hitBounds(x, y) {
    try {
      const b = this.model.getBounds()
      return (x >= b.x && x <= b.x + b.width && y >= b.y && y <= b.y + b.height) ? 'Body' : null
    } catch (e) {
      return null
    }
  }

  _onPointerDown(e) {
    if (!this.model || !this.modelReady) return
    const rect = this.canvas.getBoundingClientRect()
    const ox = e.clientX - rect.left
    const oy = e.clientY - rect.top
    // 命中模型则视为点击/可拖拽起点
    const part = this._hitModel(e.clientX, e.clientY)
    if (part) {
      this._dragging = true
      this._dragOffset = { dx: ox - this.model.x, dy: oy - this.model.y, part }
      this.onBodyTap && this.onBodyTap(part, ox, oy)
      this._onTapReaction(part)
    }
  }

  _onPointerMove(e) {
    if (!this._dragging || !this.model) return
    const rect = this.canvas.getBoundingClientRect()
    const ox = e.clientX - rect.left
    const oy = e.clientY - rect.top
    const dx = ox - this._dragOffset.dx
    const dy = oy - this._dragOffset.dy
    // clamp 在 canvas 内
    const halfW = (this.model.width * this.model.scale.x) / 2
    const halfH = (this.model.height * this.model.scale.y) / 2
    this.model.x = Math.min(this.canvas.clientWidth - halfW, Math.max(halfW, dx))
    this.model.y = Math.min(this.canvas.clientHeight - halfH, Math.max(halfH * 0.5, dy))
  }

  _onPointerUp() {
    if (!this._dragging) return
    this._dragging = false
    this._dragOffset = null
    if (this.onDragEnd) this.onDragEnd()
    this._snapBack()
  }

  /** 松手后缓动回弹到 home 位置 */
  _snapBack() {
    if (!this.model) return
    this._clearSnap()
    const startX = this.model.x
    const startY = this.model.y
    const t0 = performance.now()
    const dur = 350
    const tick = (now) => {
      if (!this.model || this._dragging) return // 用户又按住则取消回弹
      const p = Math.min(1, (now - t0) / dur)
      const ease = 1 - Math.pow(1 - p, 3) // easeOutCubic
      this.model.x = startX + (this._homeX - startX) * ease
      this.model.y = startY + (this._homeY - startY) * ease
      if (p < 1) this._snapTimer = requestAnimationFrame(tick)
    }
    this._snapTimer = requestAnimationFrame(tick)
  }

  _clearSnap() {
    if (this._snapTimer) {
      cancelAnimationFrame(this._snapTimer)
      this._snapTimer = null
    }
  }

  /**
   * 点击反应：播放被触摸动作 + 短暂切换惊讶表情，随后回归
   * @param {string} part - 命中的区域名（Body/Head/TapLeft/...）
   */
  _onTapReaction(part) {
    // 防抖：点击间歇期不重复触发
    if (this._tapTimer) return
    const group = this._motionForPart(part)
    this.startMotion(group, 1)
    // 短暂惊讶表情（不覆盖 T3-02 情绪表情，短暂后回归）
    const prev = this.currentExpression
    if (prev !== 'surprised') {
      try { this.model.expression('surprised') } catch (e) { /* 无该表情则忽略 */ }
    }
    this._tapTimer = setTimeout(() => {
      this._tapTimer = null
      // 回归到 T3-02 情绪表情（或 neutral）
      try { this.model.expression(prev && prev !== 'surprised' ? prev : 'neutral') } catch (e) { /* 忽略 */ }
    }, 1200)
  }

  /** 命中区域 → 动作组名 */
  _motionForPart(part) {
    const map = {
      Body: 'TapBody', Head: 'FlickHead',
      TapLeft: 'TapRight', TapRight: 'TapLeft',
      TapLeftEar: 'TapRight', TapRightEar: 'TapLeft',
    }
    return map[part] || 'TapBody'
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
   * 唤醒响应（为 T3-05 预留）：播放唤醒动作 + 表情
   */
  onWake() {
    this.setExpression('excited')
    this.startMotion('TapBody', 1)
  }

  /**
   * 销毁：释放资源
   */
  destroy() {
    this.stopEmotionSync()
    this._stopIdle()
    this._clearSnap()
    if (this._tapTimer) {
      clearTimeout(this._tapTimer)
      this._tapTimer = null
    }
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
