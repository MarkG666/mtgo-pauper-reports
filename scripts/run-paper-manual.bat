@echo off
REM Paper manual handoff (Windows)
REM Usage:
REM   run-paper-manual.bat --html path\to\event-12345.html --source-url https://mtgdecks.net/Pauper/...-12345
REM   run-paper-manual.bat --html-dir data\raw\paper_drops
REM   run-paper-manual.bat --url https://mtgdecks.net/Pauper/...-12345
REM
REM Prefer saved HTML. Live --url often hits Cloudflare even on PC automation;
REM normal Chrome/Edge "Save Page" is the reliable path.

setlocal
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv がありません。先に SETUP / pip install してください。
  echo     python -m venv .venv
  echo     .venv\Scripts\activate
  echo     pip install -r requirements.txt
  exit /b 1
)

set PYTHONPATH=%CD%\src
".venv\Scripts\python.exe" -m pauper_meta run-paper-manual %*
set EXITCODE=%ERRORLEVEL%
echo.
echo exit=%EXITCODE%
endlocal & exit /b %EXITCODE%
