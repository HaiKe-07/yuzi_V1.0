// Preload 脚本：暴露受控的本地 HTTP 调用接口给渲染进程。
//
// 通过 contextBridge 暴露 window.companion API，渲染进程
// 不直接持有 Node 能力，只通过 ipcRenderer-like 接口与后端通信。

const { contextBridge } = require('electron')

const BACKEND_PORT = parseInt(process.env.BACKEND_PORT || '18731', 10)
const BASE = `http://127.0.0.1:${BACKEND_PORT}/api`

async function request(path, options = {}) {
  const url = `${BASE}${path}`
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    const detail = await resp.text()
    throw new Error(`API ${path} 失败 ${resp.status}: ${detail}`)
  }
  return resp.json()
}

contextBridge.exposeInMainWorld('companion', {
  // 健康检查
  health: () => request('/health'),
  // 状态
  status: () => request('/status'),
  // 历史
  history: (limit = 50) => request(`/history?limit=${limit}`),
  // 文本对话
  chat: (text) => request('/chat', {
    method: 'POST',
    body: JSON.stringify({ text }),
  }),
  // 仅 TTS 播报
  speak: (text) => request('/speak', {
    method: 'POST',
    body: JSON.stringify({ text }),
  }),
  // 读配置
  getSettings: () => request('/settings'),
  // 改配置（部分字段，重启生效）
  updateSettings: (updates) => request('/settings', {
    method: 'PUT',
    body: JSON.stringify(updates),
  }),
  // 窗口控制（无边框标题栏用）
  window: {
    minimize: () => {
      // 通过 ipcRenderer 走主进程；此处简化为 fetch 不需要
      // 真实实现需要 ipcRenderer.send
    },
    close: () => {},
  },
})
