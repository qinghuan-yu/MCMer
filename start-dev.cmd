@echo off
setlocal

REM MCMer local dev launcher
REM Usage:
REM   start-dev.cmd
REM   start-dev.cmd backend
REM   start-dev.cmd frontend

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"
set "PYTHON_EXE=%ROOT%.venv\Scripts\python.exe"
set "MODE=%~1"

if "%MODE%"=="" set "MODE=all"

if /I not "%MODE%"=="all" if /I not "%MODE%"=="backend" if /I not "%MODE%"=="frontend" (
  echo [ERROR] Invalid mode: "%MODE%"
  echo Usage: start-dev.cmd [all^|backend^|frontend]
  pause
  exit /b 1
)

set "NEED_BACKEND=0"
set "NEED_FRONTEND=0"
if /I "%MODE%"=="all" (
  set "NEED_BACKEND=1"
  set "NEED_FRONTEND=1"
)
if /I "%MODE%"=="backend" set "NEED_BACKEND=1"
if /I "%MODE%"=="frontend" set "NEED_FRONTEND=1"

REM Validate backend dependency only when needed
if "%NEED_BACKEND%"=="1" (
  if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python virtualenv not found: "%PYTHON_EXE%"
    echo Please create .venv in project root first.
    pause
    exit /b 1
  )
)

REM Validate frontend dependency only when needed
if "%NEED_FRONTEND%"=="1" (
  where pnpm >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] pnpm is not installed or not in PATH.
    echo Install Node.js and pnpm first.
    pause
    exit /b 1
  )
)

REM Check backend port conflict only when backend is requested
if "%NEED_BACKEND%"=="1" (
  powershell -NoProfile -Command "$p = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue; if ($p) { Write-Host '[WARN] Port 8000 is in use.'; Write-Host '[WARN] If Docker backend is already running, use: start-dev.cmd frontend'; exit 2 } else { exit 0 }"
  if errorlevel 2 (
    echo.
    choice /C YN /N /M "Continue launching local backend anyway? [Y/N]: "
    if errorlevel 2 exit /b 1
  )
)

if "%NEED_BACKEND%"=="1" (
  echo [INFO] Starting local backend...
  start "MCMer Backend" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location '%BACKEND_DIR%'; & '%PYTHON_EXE%' -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
)

if "%NEED_FRONTEND%"=="1" (
  echo [INFO] Starting local frontend...
  start "MCMer Frontend" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location '%FRONTEND_DIR%'; if (-not (Test-Path 'node_modules')) { pnpm install }; pnpm run dev --host 0.0.0.0 --port 5173"
)

echo.
echo [DONE] Started mode: [%MODE%]
if "%NEED_FRONTEND%"=="1" echo Frontend: http://127.0.0.1:5173
if "%NEED_BACKEND%"=="1" (
  echo Backend docs: http://127.0.0.1:8000/docs
) else (
  echo Backend source: Docker or external service at http://127.0.0.1:8000
)
echo Stop each service with Ctrl+C in its own terminal window.

endlocal
exit /b 0