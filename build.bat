@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -m venv .venv 2>nul || python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
pip install -r requirements.txt pyinstaller -q

echo Building standalone executable...
pyinstaller --noconfirm --clean CopilotMonitor.spec

if exist "dist\CopilotMonitor.exe" (
  echo.
  echo Build complete: dist\CopilotMonitor.exe
  echo First run creates config.json beside the exe with all accounts disabled.
) else (
  echo Build failed.
  exit /b 1
)
