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

const { app, BrowserWindow, shell, Tray, Menu, nativeImage, ipcMain, Notification } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const http = require('http')

// 默认后端端口，与 config.yaml 的 server.port 一致
const BACKEND_PORT = parseInt(process.env.BACKEND_PORT || '18731', 10)
const BACKEND_HOST = '127.0.0.1'

// 项目根（ui/ 的上一级 = ai-companion/）；打包态 app.asar 内
const PROJECT_ROOT = path.resolve(__dirname, '..', '..')
// server.py 路径（ai-companion/server.py）
const SERVER_PATH = path.join(PROJECT_ROOT, 'server.py')
// 打包态资源目录（electron-builder extraResources）
const RESOURCES_DIR = app.isPackaged ? process.resourcesPath : null
// 打包态用户数据目录：后端 config.yaml / .env / data / logs 的根（AI_COMPANION_HOME）
const USER_DATA_DIR = app.isPackaged ? app.getPath('userData') : null
// 托盘图标：打包态在 resources/assets，开发态在 ui/assets
const TRAY_ICON = app.isPackaged
  ? path.join(process.resourcesPath, 'assets', 'tray.png')
  : path.join(__dirname, '..', 'assets', 'tray.png')
// 配置读取路径：打包态优先用户数据目录（首启由 ensureUserConfig 从资源复制）
const CONFIG_YAML = app.isPackaged
  ? path.join(USER_DATA_DIR, 'config.yaml')
  : path.join(PROJECT_ROOT, 'config.yaml')

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
  const def = { autoStart: false, minimizeToTray: true, tooltip: 'AI 陪伴助手', notificationsEnabled: true }
  try {
    const p = CONFIG_YAML
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
    def.notificationsEnabled = val('notifications_enabled', 'true') !== 'false'
    return def
  } catch (e) {
    return def
  }
}
const uiConfig = readUiConfig()

// 打包态首启：把资源目录里的 config.yaml / .env 复制到用户数据目录（后续用户改的是这份）。
// 非打包态不做任何事。
function ensureUserConfig() {
  if (!app.isPackaged) return
  try {
    fs.mkdirSync(USER_DATA_DIR, { recursive: true })
    for (const name of ['config.yaml', '.env']) {
      const src = path.join(RESOURCES_DIR, name)
      const dst = path.join(USER_DATA_DIR, name)
      if (fs.existsSync(src) && !fs.existsSync(dst)) {
        fs.copyFileSync(src, dst)
        console.log(`[main] 已初始化用户配置: ${dst}`)
      }
    }
  } catch (e) {
    console.warn('[main] 初始化用户配置失败', e.message)
  }
}
ensureUserConfig()

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
  let cmd, args
  let cwd = PROJECT_ROOT
  if (app.isPackaged) {
    // 打包态：运行 PyInstaller 打包好的后端可执行文件（不依赖用户安装 Python）
    const backendBin = process.platform === 'win32'
      ? 'ai-companion-server.exe'
      : 'ai-companion-server'
    const bin = path.join(RESOURCES_DIR, 'backend', backendBin)
    if (!fs.existsSync(bin)) {
      console.error(`[main] 打包后端缺失: ${bin}`)
      return
    }
    cmd = bin
    args = []
    cwd = USER_DATA_DIR
  } else {
    // 开发态：用系统 Python 直接跑 server.py
    cmd = process.env.PYTHON_BIN || 'python'
    args = [SERVER_PATH]
  }
  console.log(`[main] 启动 Python 后端: ${cmd} ${args.join(' ')}`)

  pythonProc = spawn(cmd, args, {
    cwd,
    env: {
      ...process.env,
      // 让 uvicorn 知道端口
      BACKEND_PORT: String(BACKEND_PORT),
      // 打包态：告诉后端把 config.yaml / data / logs 放到用户数据目录
      AI_COMPANION_HOME: USER_DATA_DIR || undefined,
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

// 等待后端 /api/health 就绪（轮询，最长 60s；打包态 onefile 首启解压较慢）
function waitForBackend(maxRetries = 60, intervalMs = 1000) {
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
  // T3-05: 唤醒时若窗口已隐藏到托盘，则弹出并聚焦
  ipcMain.on('window:show', () => {
    showMainWindow()
  })
  // T3-06: 桌面通知（提醒/天气等，通过渲染进程触发）
  ipcMain.on('notify', (_e, payload) => {
    showDesktopNotification(payload)
  })
  // T3-06: 通知开关查询（供渲染进程判断）
  ipcMain.handle('notify:enabled', () => uiConfig.notificationsEnabled)
}

// T3-06: 发送桌面通知。
// 遵循 config.ui.notifications_enabled 开关；通知点击时聚焦窗口。
function showDesktopNotification(payload = {}) {
  if (!uiConfig.notificationsEnabled) {
    console.log('[notify] 桌面通知已被 config.ui.notifications_enabled 关闭')
    return false
  }
  if (!Notification.isSupported()) {
    console.warn('[notify] 当前系统不支持桌面通知')
    return false
  }
  const title = payload.title || 'AI 陪伴助手'
  const body = payload.body || ''
  const opt = { title, body }
  if (TRAY_ICON) opt.icon = TRAY_ICON
  const n = new Notification(opt)
  n.on('click', () => showMainWindow())
  n.show()
  console.log(`[notify] ${title}: ${body}`)
  return true
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