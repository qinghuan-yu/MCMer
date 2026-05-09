@echo off
setlocal

REM ============================================================
REM  MCMer 本地开发启动脚本
REM  用法:
REM    start-dev.cmd          启动前端+后端（全本地）
REM    start-dev.cmd frontend  只启动前端（后端用 Docker 时选这个）
REM    start-dev.cmd backend   只启动后端
REM ============================================================

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"
set "PYTHON_EXE=%ROOT%.venv\Scripts\python.exe"
set "MODE=%~1"

if "%MODE%"=="" set "MODE=all"

REM ---------- 校验公共依赖 ----------
if not exist "%PYTHON_EXE%" (
  echo [ERROR] 未找到 Python 虚拟环境: "%PYTHON_EXE%"
  echo 请先在项目根目录准备 .venv 环境。
  pause
  exit /b 1
)

where pnpm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 未检测到 pnpm，请先安装 Node.js 与 pnpm。
  pause
  exit /b 1
)

REM ---------- 检测后端端口冲突 ----------
if /i "%MODE%"=="all"  goto :check_port
if /i "%MODE%"=="backend" goto :check_port
goto :skip_port_check

:check_port
powershell -NoProfile -Command "$p = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue; if ($p) { Write-Host '[WARN] 端口 8000 已被占用，可能是 Docker 后端正在运行。'; Write-Host '       若 Docker 已提供后端，请改用: start-dev.cmd frontend'; exit 2 } else { exit 0 }"
if errorlevel 2 (
  echo.
  echo 如需强制启动本地后端，请先停止占用 8000 的进程。
  set /p FORCE=是否仍要继续？(y/N): 
  /i if not "%FORCE%"=="y" exit /b 1
)
:skip_port_check

REM ---------- 启动后端 ----------
if /i "%MODE%"=="all"  goto :start_backend
if /i "%MODE%"=="backend" goto :start_backend
goto :skip_backend

:start_backend
echo [INFO] 正在启动 MCMer 本地后端...
start "MCMer Backend" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location '%BACKEND_DIR%'; & '%PYTHON_EXE%' -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
:skip_backend

REM ---------- 启动前端 ----------
if /i "%MODE%"=="all"  goto :start_frontend
if /i "%MODE%"=="frontend" goto :start_frontend
goto :skip_frontend

:start_frontend
echo [INFO] 正在启动 MCMer 前端...
start "MCMer Frontend" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location '%FRONTEND_DIR%'; if (-not (Test-Path 'node_modules')) { pnpm install }; pnpm run dev --host 0.0.0.0 --port 5173"
:skip_frontend

REM ---------- 打印摘要 ----------
echo.
echo [DONE] 已按模式 [%MODE%] 打开终端窗口。
echo   前端地址: http://127.0.0.1:5173
if /i "%MODE%"=="frontend" (
  echo   后端来源: Docker（http://127.0.0.1:8000）
) else (
  echo   后端文档: http://127.0.0.1:8000/docs
)
echo   关闭服务时，在对应窗口按 Ctrl+C。
endlocal