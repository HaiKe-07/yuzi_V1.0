#!/usr/bin/env python
"""打包 Python 后端为独立可执行文件（PyInstaller）。

产物：packaging/dist/backend/ai-companion-server(.exe)

用法（Windows 一键打包见 build.bat / macOS/Linux 见 build.sh）：
    python packaging/build_backend.py

说明：
- 依赖打包：onefile 单文件模式，运行时不依赖用户安装 Python
- 配置路径：运行时可设置 AI_COMPANION_HOME 环境变量指向用户数据目录
  （Electron 主进程打包态会自动设置，把 config.yaml/data/logs 放到用户目录）
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "dist" / "backend"
PYINSTALLER_WORK = ROOT / "packaging" / "build" / "pyinstaller"
SPEC_DIR = ROOT / "packaging" / "build"
NAME = "ai-companion-server"


def run(cmd: list[str]) -> None:
    print(f"==> {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main() -> None:
    # 1. 安装打包工具与后端依赖
    run([sys.executable, "-m", "pip", "install", "-q", "pyinstaller"])
    run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])

    # 2. 清理旧产物
    OUT.mkdir(parents=True, exist_ok=True)
    for p in OUT.iterdir():
        if p.is_file():
            p.unlink()
    shutil.rmtree(PYINSTALLER_WORK, ignore_errors=True)

    # 3. PyInstaller 打包 server.py
    #    --collect-submodules uvicorn/fastapi：PyInstaller 对动态导入的模块需要显式收集
    run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--name", NAME,
        "--distpath", str(OUT),
        "--workpath", str(PYINSTALLER_WORK),
        "--specpath", str(SPEC_DIR),
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "fastapi",
        "--collect-submodules", "loguru",
        "--hidden-import", "pydantic",
        "--hidden-import", "sounddevice",
        "--hidden-import", "soundfile",
        str(ROOT / "server.py"),
    ])

    exe = OUT / (NAME + (".exe" if sys.platform == "win32" else ""))
    if not exe.exists():
        sys.exit(f"[error] 打包失败：未生成 {exe}")

    # 4. 附带 Live2D 模型目录（有模型文件才复制，避免打包无意义内容）
    live2d_src = ROOT / "ui" / "live2d"
    live2d_dst = ROOT / "packaging" / "dist" / "live2d"
    model_files = [p for p in live2d_src.rglob("*") if p.is_file() and p.name != "README.md"]
    if model_files:
        if live2d_dst.exists():
            shutil.rmtree(live2d_dst)
        shutil.copytree(live2d_src, live2d_dst)
        print(f"==> 已附带 Live2D 模型: {live2d_dst}")
    else:
        live2d_dst.mkdir(parents=True, exist_ok=True)
        print("==> 无 Live2D 模型文件，跳过（后续放入 ui/live2d/default 后重新打包即可）")

    print(f"[ok] 后端可执行文件已生成: {exe}")


if __name__ == "__main__":
    main()
