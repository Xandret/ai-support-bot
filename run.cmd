@echo off
rem Demo bot launcher. ASCII only: cmd.exe misreads UTF-8 Cyrillic in batch files.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  py -3.12 -m venv .venv
  if errorlevel 1 python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Python 3.11+ is required.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env - fill it in, save, close Notepad, then run run.cmd again.
  start "" notepad ".env"
  exit /b 0
)

:loop
".venv\Scripts\python.exe" -m bot
if %errorlevel%==2 (
  echo.
  echo Fix .env and run run.cmd again.
  pause
  exit /b 2
)
echo Bot stopped. Restarting in 10 seconds, Ctrl+C to quit.
timeout /t 10 >nul
goto loop
