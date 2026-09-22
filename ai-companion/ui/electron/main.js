// Electron 主进程入口。
//
// 职责：
// 1. 启动 Python 后端 HTTP API（companion.server）作为子进程
// 2. 创建 BrowserWindow 加载 Vue 渲染进程（dev: Vite dev server / prod: 本地 dist）
// 3. 系统托盘 + 单实例锁（T3-04 阶段补全）
// 4. 开机自启（Windows 注册表，T3-04）
// 5. 关闭窗口 → 隐藏到托盘；唤醒 → 窗口弹出（T3-04）
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
// 托盘图标路径（build 产物中放在资源目录）
const ICON_PATH = app.isPackaged
  ? path.join(process.resourcesPath, 'icon.png')
  : path.join(__dirname, 'icon.png')

let pythonProc = null
let mainWindow = null
let tray = null
// 是否正在退出（避免隐藏到托盘挡住真正的退出）
let isQuitting = false
// 唤醒弹出轮询句柄
let wakePollTimer = null

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

// 获取后端 JSON（供唤醒轮询 / 托盘状态用）
function fetchBackend(pathname) {
  return new Promise((resolve) => {
    const req = http.get(
      `http://${BACKEND_HOST}:${BACKEND_PORT}${pathname}`,
      (res) => {
        let body = ''
        res.on('data', (c) => (body += c))
        res.on('end', () => {
          try {
            resolve(JSON.parse(body))
          } catch (e) {
            resolve(null)
          }
        })
      }
    )
    req.on('error', () => resolve(null))
    req.setTimeout(3000, () => { req.destroy(); resolve(null) })
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
    frame: false,           // 无边框，T3-04 自绘标题栏
    transparent: false,
    backgroundColor: '#1a1a2e',
    title: 'AI 陪伴助手',
    icon: fs.existsSync(ICON_PATH) ? ICON_PATH : undefined,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    show: false, // 初始化后手动显示，避免首帧白屏
  })

  // 开发态加载 Vite dev server，生产态加载打包后的 dist/index.html
  if (process.env.NODE_ENV === 'development') {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }

  mainWindow.once('ready-to-show', () => {
    // 默认启动即显示；如需静默皆可从托盘退出
    if (!process.env.START_HIDDEN) {
      mainWindow.show()
    }
  })

  // 外链用系统浏览器打开
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  // T3-04：点关闭 → 隐藏到托盘，不退出
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault()
      hideToTray()
    }
  })

  mainWindow.on('closed', () => {
    stopWakePolling()
    mainWindow = null
  })
}

// 显示主窗口（从托盘弹出）
function showWindow() {
  if (!mainWindow) {
    createWindow()
    return
  }
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

// 隐藏到托盘
function hideToTray() {
  if (mainWindow) mainWindow.hide()
}

// 切换显示/隐藏
function toggleWindow() {
  if (mainWindow && mainWindow.isVisible()) {
    hideToTray()
  } else {
    showWindow()
  }
}

// ============================================================
// 3. 系统托盘（T3-04）
// ============================================================
function getTrayIcon(small = false) {
  const icon = fs.existsSync(ICON_PATH) ? ICON_PATH : null
  if (!icon) {
    // 无图标文件时用一张 1x1 透明 PNG 兜底
    const empty = nativeImage.createEmpty()
    return empty
  }
  const img = nativeImage.createFromPath(icon)
  if (small) {
    // 小尺寸（通常 16x16 / 20x20）
    const size = process.platform === 'darwin' ? 16 : 16
    return img.resize({ width: size, height: size })
  }
  return img
}

function createTray() {
  tray = new Tray(getTrayIcon(true))
  tray.setToolTip('AI 陪伴助手')
  rebuildTrayMenu()
  tray.on('click', () => showWindow()) // 单击托盘 → 弹出窗口
  tray.on('double-click', () => showWindow())
}

function rebuildTrayMenu() {
  if (!tray) return
  const menu = Menu.buildFromTemplate([
    {
      label: mainWindow && mainWindow.isVisible() ? '隐藏到托盘' : '显示主窗口',
      click: () => toggleWindow(),
    },
    { type: 'separator' },
    {
      label: '开机自启',
      type: 'checkbox',
      checked: getAutoLaunch(),
      click: (item) => setAutoLaunch(item.checked),
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        isQuitting = true
        app.quit()
      },
    },
  ])
  tray.setContextMenu(menu)
}

// ============================================================
// 4. 开机自启（T3-04，Windows 注册表 / macOS LaunchAgent）
// ============================================================
function getAutoLaunch() {
  if (process.platform === 'win32' || process.platform === 'darwin') {
    return app.getLoginItemSettings().openAtLogin
  }
  return false
}

function setAutoLaunch(enabled) {
  if (process.platform === 'win32' || process.platform === 'darwin') {
    app.setLoginItemSettings({ openAtLogin: enabled })
    console.log(`[main] 开机自启: ${enabled ? '开启' : '关闭'}`)
  }
  if (tray) rebuildTrayMenu()
}

// ============================================================
// 5. 唤醒 → 从托盘弹出（T3-04）
// 后端唤醒词检测通过 /api/live2d/status 暴露 wake_listening。
// 当后端感知到唤醒（底层状态非 IDLE / 唤醒监听开启）时弹出窗口。
// ============================================================
function startWakePolling() {
  if (wakePollTimer) return
  let lastState = null
  wakePollTimer = setInterval(async () => {
    if (!mainWindow || mainWindow.isVisible()) return // 已显示则不处理
    const data = await fetchBackend('/api/live2d/status')
    if (!data) return
    const now = data.state
    // 状态从 IDLE 变化（如进入 LISTENING/SPEAKING）视为"被唤醒" → 弹出
    if (lastState !== null && lastState !== now && now) {
      console.log(`[main] 检测到唤醒（${lastState} → ${now}），弹出窗口`)
      showWindow()
      lastState = now
    }
    if (lastState === null) lastState = now
  }, 1000)
}

function stopWakePolling() {
  if (wakePollTimer) {
    clearInterval(wakePollTimer)
    wakePollTimer = null
  }
}

// ============================================================
// 6. IPC（渲染进程窗口控制 / 托盘，T3-04）
// ============================================================
function setupIPC() {
  ipcMain.on('window:minimize', () => {
    if (mainWindow) mainWindow.minimize()
  })
  ipcMain.on('window:close', () => {
    // 关闭 = 隐藏到托盘
    hideToTray()
  })
  ipcMain.on('window:hide', () => hideToTray())
  ipcMain.on('window:show', () => showWindow())
  ipcMain.handle('app:getAutoLaunch', () => getAutoLaunch())
  ipcMain.handle('app:setAutoLaunch', (_e, enabled) => {
    setAutoLaunch(!!enabled)
    return getAutoLaunch()
  })
}

// ============================================================
// 7. App 生命周期
// ============================================================

// T3-04：单实例锁，重复启动直接激活已有窗口
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    showWindow()
  })

  app.whenReady().then(async () => {
    setupIPC()
    // 启动后端
    startPythonBackend()
    try {
      await waitForBackend()
      console.log('[main] 后端就绪')
    } catch (e) {
      console.error('[main] 后端未就绪，将直接加载前端', e.message)
    }

    createWindow()
    createTray()
    startWakePolling()

    app.on('activate', () => {
      // macOS 点击 Dock 图标时恢复窗口
      showWindow()
    })
  })

  app.on('window-all-closed', () => {
    // T3-04：常驻托盘，关闭窗口不退出应用
    if (process.platform !== 'darwin') {
      // 不再 app.quit()，保持托盘常驻
    }
  })

  app.on('before-quit', () => {
    isQuitting = true
    stopPythonBackend()
  })

  // 退出时兜底清理子进程
  process.on('exit', () => stopPythonBackend())
  process.on('SIGINT', () => {
    isQuitting = true
    stopPythonBackend()
    process.exit(0)
  })
}