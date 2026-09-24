@echo off
chcp 65001 >nul
rem ============================================================
rem  AI 陪伴助手 - Windows 一键打包脚本
rem  产物：ui\dist-electron\AI 陪伴助手 Setup *.exe（NSIS 安装包）
rem  前置：已安装 Python 3.11+、Node.js 18+
rem ============================================================
cd /d "%~dp0.."

echo [1/4] 打包 Python 后端...
python packaging\build_backend.py
if errorlevel 1 goto :error

echo [2/4] 安装前端依赖...
cd ui
call npm install
if errorlevel 1 goto :error

echo [3/4] 构建前端静态资源...
call npm run build
if errorlevel 1 goto :error

echo [4/4] 打包 Electron 桌面应用（Windows NSIS）...
call npx electron-builder --win nsis
if errorlevel 1 goto :error

echo.
echo ============================================================
echo  打包完成！安装包位于：ui\dist-electron\
echo ============================================================
goto :eof

:error
echo.
echo 打包失败，请查看上方错误信息。
exit /b 1
