#!/usr/bin/env bash
# ============================================================
#  AI 陪伴助手 - macOS/Linux 一键打包脚本
#  产物：ui/dist-electron/ 下对应平台安装包
#    Linux : AppImage / deb
#    macOS : dmg（需在 macOS 上执行）
#  前置：已安装 Python 3.11+、Node.js 18+
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[1/4] 打包 Python 后端..."
python3 packaging/build_backend.py

echo "[2/4] 安装前端依赖..."
(cd ui && npm install)

echo "[3/4] 构建前端静态资源..."
(cd ui && npm run build)

echo "[4/4] 打包 Electron 桌面应用..."
if [[ "$(uname)" == "Darwin" ]]; then
  (cd ui && npx electron-builder --mac dmg)
else
  (cd ui && npx electron-builder --linux AppImage)
fi

echo
echo "============================================================"
echo " 打包完成！安装包位于：ui/dist-electron/"
echo "============================================================"
