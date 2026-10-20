@echo off
REM Pauper meta tracker — daily job (Task Scheduler: 11:00 JST)
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv が見つかりません。README のセットアップを先に実行してください。
  exit /b 1
)

set PYTHONPATH=%~dp0src
".venv\Scripts\python.exe" -m pauper_meta run-daily %*
set EXITCODE=%ERRORLEVEL%
echo ExitCode=%EXITCODE%
exit /b %EXITCODE%
