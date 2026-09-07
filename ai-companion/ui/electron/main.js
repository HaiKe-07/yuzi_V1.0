// Electron 主进程入口。
//
// 职责：
// 1. 启动 Python 后端 HTTP API（companion.server）作为子进程
// 2. 创建 BrowserWindow 加载 Vue 渲染进程（dev: Vite dev server / prod: 本地 dist）
// 3. 系统托盘 + 单实例锁（T3-04 阶段补全）
//
// 关键约束：
// - Python 解释器路径通过 PYTHON_BIN 环境变量覆盖，便于打包后定位
// - 后端端口通过 BACKEND_PORT 环境变量覆盖，与 config.server.port 对齐
// - 退出时清理子进程，避免端口残留

const { app, BrowserWindow, shell } = require('electron')
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

let pythonProc = null
let mainWindow = null

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
    frame: false,           // 无边框，T3-04 自绘标题栏
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

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// ============================================================
// 3. App 生命周期
// ============================================================
app.whenReady().then(async () => {
  // 启动后端
  startPythonBackend()
  try {
    await waitForBackend()
    console.log('[main] 后端就绪')
  } catch (e) {
    console.error('[main] 后端未就绪，将直接加载前端', e.message)
  }

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  stopPythonBackend()
})

// 退出时兜底清理子进程
process.on('exit', () => stopPythonBackend())
process.on('SIGINT', () => {
  stopPythonBackend()
  process.exit(0)
})
