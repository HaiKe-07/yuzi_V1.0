// Electron 主进程入口。
//
// 职责：
// 1. 启动 Python 后端 HTTP API（companion.server）作为子进程
// 2. 创建 BrowserWindow 加载 Vue 渲染进程（dev: Vite dev server / prod: 本地 dist）
// 3. 系统托盘常驻 + 单实例锁 + 开机自启（T3-04）
//
// T3-04 关键行为：
// - 单实例：重复启动聚焦已存在窗口
// - 托盘：显示/隐藏主窗口、打开主页、总在最前、开机自启开关、退出
// - 关闭窗口：按 config.ui.minimize_to_tray，最小化到托盘而非退出
// - 开机自启：读取 config.yaml 的 ui.auto_start，也可在托盘菜单实时切换
//
// 关键约束：
// - Python 解释器路径通过 PYTHON_BIN 环境变量覆盖，便于打包后定位
// - 后端端口通过 BACKEND_PORT 环境变量覆盖，与 config.server.port 对齐
// - 退出时清理子进程，避免端口残留

const { app, BrowserWindow, shell, Tray, Menu, nativeImage, ipcMain } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const http = require('http')

// 默认后端端口，与 config.yaml 的 server.port 一致
const BACKEND_PORT = parseInt(process.env.BACKEND_PORT || '18731', 10)
const BACKEND_HOST = '127.0.0.1'

// 项目根（ui/ 的上一级 = ai-companion/）
const PROJECT_ROOT = path.resolve(__dirname, '..', '..')
// server.py 路径（ai-companion/server.py）
const SERVER_PATH = path.join(PROJECT_ROOT, 'server.py')
// 托盘图标路径（ui/assets/tray.png）
const TRAY_ICON = path.join(__dirname, '..', 'assets', 'tray.png')

let pythonProc = null
let mainWindow = null
let tray = null
// 是否真正退出（托盘"退出"或系统退出）；false 表示仅隐藏到托盘
let isQuitting = false
// 开机自启当前状态（初始读 config，可被托盘菜单切换）
let isAutoStart = false

// ============================================================
// 0. 读取 T3-04 配置（纯文本解析 config.yaml 的 ui 段，避免引入 yaml 依赖）
// ============================================================
function readUiConfig() {
  const def = { autoStart: false, minimizeToTray: true, tooltip: 'AI 陪伴助手' }
  try {
    const p = path.join(PROJECT_ROOT, 'config.yaml')
    if (!fs.existsSync(p)) return def
    const text = fs.readFileSync(p, 'utf8')
    // 只取 ui: 块，避免误匹配其它同名 key
    const blockMatch = text.match(/^ui:\s*\n([\s\S]*?)(?=\n\S[^:\n]+:\s*$)/m)
    const block = blockMatch ? blockMatch[1] : ''
    const val = (key, dft) => {
      const m = block.match(new RegExp('^\\s*' + key + '\\s*:\\s*(\\S+)', 'm'))
      return m ? m[1] : dft
    }
    def.autoStart = val('auto_start', 'false') === 'true'
    def.minimizeToTray = val('minimize_to_tray', 'true') !== 'false'
    def.tooltip = (val('tray_tooltip', 'AI 陪伴助手') || 'AI 陪伴助手').replace(/['"]/g, '')
    return def
  } catch (e) {
    return def
  }
}
const uiConfig = readUiConfig()

function applyAutoStartPreference() {
  // 打包安装后需要有 app.setLoginItemSettings；开发态一般不可用，静默忽略
  try {
    app.setLoginItemSettings({ openAtLogin: isAutoStart })
    console.log(`[main] 开机自启: ${isAutoStart ? '开' : '关'}`)
  } catch (e) {
    console.warn('[main] 设置开机自启失败（开发态通常忽略）', e.message)
  }
}

// ============================================================
// 1. Python 后端子进程
// ============================================================
function startPythonBackend() {
  const pythonBin = process.env.PYTHON_BIN || 'python'
  console.log(`[main] 启动 Python 后端: ${pythonBin} ${SERVER_PATH}`)

  pythonProc = spawn(pythonBin, [SERVER_PATH], {
    cwd: PROJECT_ROOT,
    env: {
      ...process.env,
      // 让 uvicorn 知道端口
      BACKEND_PORT: String(BACKEND_PORT),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  pythonProc.stdout.on('data', (data) => {
    console.log(`[py] ${data.toString().trim()}`)
  })
  pythonProc.stderr.on('data', (data) => {
    console.error(`[py] ${data.toString().trim()}`)
  })
  pythonProc.on('exit', (code) => {
    console.log(`[main] Python 后端退出 code=${code}`)
    pythonProc = null
  })
}

function stopPythonBackend() {
  if (pythonProc) {
    console.log('[main] 停止 Python 后端')
    pythonProc.kill('SIGTERM')
    pythonProc = null
  }
}

// 等待后端 /api/health 就绪（轮询，最长 30s）
function waitForBackend(maxRetries = 30, intervalMs = 1000) {
  return new Promise((resolve, reject) => {
    let tries = 0
    const check = () => {
      const req = http.get(
        `http://${BACKEND_HOST}:${BACKEND_PORT}/api/health`,
        (res) => {
          if (res.statusCode === 200) {
            resolve()
          } else if (++tries >= maxRetries) {
            reject(new Error('后端启动超时'))
          } else {
            setTimeout(check, intervalMs)
          }
        }
      )
      req.on('error', () => {
        if (++tries >= maxRetries) {
          reject(new Error('后端启动超时'))
        } else {
          setTimeout(check, intervalMs)
        }
      })
      req.end()
    }
    check()
  })
}

// ============================================================
// 2. BrowserWindow
// ============================================================
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 720,
    minWidth: 720,
    minHeight: 540,
    frame: false,           // 无边框，自绘标题栏
    transparent: false,
    backgroundColor: '#1a1a2e',
    title: 'AI 陪伴助手',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // 开发态加载 Vite dev server，生产态加载打包后的 dist/index.html
  if (process.env.NODE_ENV === 'development') {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }

  // 外链用系统浏览器打开
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  // T3-04: 关闭按钮（window:close）触发 close 事件，
  // 若配了最小化到托盘则拦截隐藏，而非真正退出
  mainWindow.on('close', (e) => {
    if (uiConfig.minimizeToTray && !isQuitting) {
      e.preventDefault()
      mainWindow.hide()
      console.log('[main] 已最小化到托盘（右键托盘图标可退出）')
    }
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// ipc：渲染进程标题栏按钮 → 主进程
function registerIpc() {
  ipcMain.on('window:minimize', () => {
    if (mainWindow) mainWindow.minimize()
  })
  ipcMain.on('window:close', () => {
    // 触发 close 事件；minimize_to_tray 时被拦截为隐藏
    if (mainWindow) mainWindow.close()
  })
}

// ============================================================
// 3. 系统托盘（T3-04）
// ============================================================
function showMainWindow() {
  if (!mainWindow) {
    createWindow()
    return
  }
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

function refreshTrayMenu() {
  if (!tray) return
  const alwaysOnTop = mainWindow ? mainWindow.isAlwaysOnTop() : false
  const visible = mainWindow && mainWindow.isVisible()
  const menu = Menu.buildFromTemplate([
    { label: visible ? '隐藏窗口' : '显示窗口', click: () => {
      if (visible) mainWindow.hide()
      else showMainWindow()
    } },
    { type: 'separator' },
    {
      label: '总在最前',
      type: 'checkbox',
      checked: alwaysOnTop,
      click: (item) => {
        if (mainWindow) mainWindow.setAlwaysOnTop(item.checked)
      },
    },
    {
      label: `开机自启 ${isAutoStart ? '✔' : ''}`,
      type: 'checkbox',
      checked: isAutoStart,
      click: (item) => {
        isAutoStart = item.checked
        applyAutoStartPreference()
        refreshTrayMenu()
      },
    },
    { type: 'separator' },
    { label: '退出', click: () => {
      isQuitting = true
      app.quit()
    } },
  ])
  tray.setContextMenu(menu)
}

function createTray() {
  let icon
  try {
    icon = nativeImage.createFromPath(TRAY_ICON)
    if (icon.isEmpty()) icon = nativeImage.createEmpty()
  } catch (e) {
    icon = nativeImage.createEmpty()
  }
  tray = new Tray(icon)
  tray.setToolTip(uiConfig.tooltip)
  // 单击托盘图标：显示 / 隐藏主窗口
  tray.on('click', () => {
    if (mainWindow && mainWindow.isVisible()) mainWindow.hide()
    else showMainWindow()
  })
  refreshTrayMenu()
}

// ============================================================
// 4. 单实例锁（T3-04）
// ============================================================
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  // 已有实例在运行，聚焦它（对应窗口）并退出当前进程
  app.quit()
} else {
  app.on('second-instance', () => {
    showMainWindow()
  })
}

// ============================================================
// 5. App 生命周期
// ============================================================
if (gotLock) {
  app.whenReady().then(async () => {
    // 开机自启初始状态来自 config
    isAutoStart = uiConfig.autoStart
    if (isAutoStart) applyAutoStartPreference()

    // 启动后端
    startPythonBackend()
    try {
      await waitForBackend()
      console.log('[main] 后端就绪')
    } catch (e) {
      console.error('[main] 后端未就绪，将直接加载前端', e.message)
    }

    createWindow()
    registerIpc()
    createTray()

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })

  // 托盘常驻：窗口全关（只有真正退出才会触发）不再自动退出
  app.on('window-all-closed', () => {
    if (isQuitting) app.quit()
    // 否则保持托盘常驻
  })

  app.on('before-quit', () => {
    isQuitting = true
    stopPythonBackend()
    if (tray) {
      tray.destroy()
      tray = null
    }
  })

  // 退出时兜底清理子进程
  process.on('exit', () => stopPythonBackend())
  process.on('SIGINT', () => {
    stopPythonBackend()
    process.exit(0)
  })
}