/**
 * Live2D 管理器（T3-01）
 *
 * 封装 pixi-live2d-display 的加载、渲染、动画控制逻辑。
 * Vue 组件通过此模块操作 Live2D，不直接碰 PIXI/Live2D API。
 *
 * 功能：
 * 1. 初始化 PIXI Application + Live2D 模型加载
 * 2. 待机动画（自动眨眼、呼吸、随机动作）
 * 3. 表情切换（setExpression，为 T3-02 预留）
 * 4. 动作播放（startMotion，如点头、挥手）
 * 5. 嘴型同步（lipSync，为 T2-06 情感 TTS 联动预留）
 * 6. 鼠标拖拽视角跟随
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
    this._blinkTimer = null
    // 回调
    this.onReady = null
    this.onError = null
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
   * 切换表情（为 T3-02 预留）
   * @param {string} name - 表情名称（happy/sad/angry/neutral/excited）
   */
  setExpression(name) {
    if (!this.model || !this.modelReady) return
    try {
      this.model.expression(name)
      this.currentExpression = name
      console.debug('[Live2D] 表情切换:', name)
    } catch (e) {
      console.warn('[Live2D] 表情切换失败:', e)
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
    }
  }
}

/**
 * AI 情绪标签 → Live2D 表情名称映射
 * Python 后端情绪系统输出的中文标签，映射到 Live2D 表情文件名
 * T3-02 表情情绪联动时使用
 */
export const EMOTION_TO_EXPRESSION = {
  '开心': 'happy',
  '难过': 'sad',
  '生气': 'angry',
  '中性': 'neutral',
  '期待': 'excited',
  '焦虑': 'sad',
  '疲惫': 'neutral',
  '平静': 'neutral',
  '害羞': 'happy',
  '孤独': 'sad',
  '委屈': 'sad',
}

/**
 * 把 AI 情绪标签转为 Live2D 表情名
 */
export function emotionToExpression(emotionLabel) {
  return EMOTION_TO_EXPRESSION[emotionLabel] || 'neutral'
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
