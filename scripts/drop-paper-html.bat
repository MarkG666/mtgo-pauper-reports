@echo off
REM Copy saved MTGDecks HTML into the drop folder and run manual ingest.
REM Drag-and-drop one or more .html files onto this bat, or:
REM   drop-paper-html.bat C:\Users\...\event-12345-ursa.html

setlocal
cd /d "%~dp0\.."

if "%~1"=="" (
  echo Usage: drop-paper-html.bat path\to\event-NNNNN-name.html [...]
  echo Or drag HTML files onto this bat.
  exit /b 1
)

if not exist "data\raw\paper_drops" mkdir "data\raw\paper_drops"

:copy_loop
if "%~1"=="" goto run
echo Copying "%~1" -^> data\raw\paper_drops\
copy /Y "%~1" "data\raw\paper_drops\" >nul
shift
goto copy_loop

:run
call "%~dp0run-paper-manual.bat" --html-dir data\raw\paper_drops --discord-dry-run
endlocal & exit /b %ERRORLEVEL%
