@echo off
REM Thin Paper discovery (Melee ≥50 + Goldfish names). No MTGDecks crawl.
REM Writes reports\paper\alerts\YYYY-MM-DD.json and .md

setlocal
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv がありません。先に venv を作って pip install -r requirements.txt
  exit /b 1
)

set PYTHONPATH=%CD%\src
".venv\Scripts\python.exe" -m pauper_meta discover-paper --discord-dry-run %*
set EXITCODE=%ERRORLEVEL%
echo.
echo Alerts: reports\paper\alerts\
echo exit=%EXITCODE%
endlocal & exit /b %EXITCODE%
