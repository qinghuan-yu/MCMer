@echo off
setlocal

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"
set "PYTHON_EXE=%ROOT%.venv\Scripts\python.exe"

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

echo [INFO] 正在启动 MCMer 后端...
start "MCMer Backend" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location '%BACKEND_DIR%'; & '%PYTHON_EXE%' -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

echo [INFO] 正在启动 MCMer 前端...
start "MCMer Frontend" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location '%FRONTEND_DIR%'; if (-not (Test-Path 'node_modules')) { pnpm install }; pnpm run dev --host 0.0.0.0 --port 5173"

echo.
echo [DONE] 已打开前后端终端窗口。
echo 前端地址: http://127.0.0.1:5173
echo 后端文档: http://127.0.0.1:8000/docs
echo 关闭服务时，分别在两个新窗口里按 Ctrl+C。
endlocal