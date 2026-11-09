@echo off
chcp 65001 >nul
title AI合同审查系统 - 启动控制台

cd /d "%~dp0"

echo =======================================================
echo   AI合同审查系统 - 自动化控制台 (GUI Dashboard)
echo =======================================================
echo.

if exist ".venv\Scripts\python.exe" (
    echo [INFO] 正在启动图形化控制台...
    start "" ".venv\Scripts\pythonw.exe" start_gui.py
    if errorlevel 1 (
        ".venv\Scripts\python.exe" start_gui.py
    )
) else (
    echo [WARN] 未检测到 .venv 虚拟环境，尝试使用系统默认 Python...
    python start_gui.py
)

if errorlevel 1 (
    echo.
    echo [ERROR] 控制台启动失败，请检查 Python 环境及依赖配置。
    pause
)
