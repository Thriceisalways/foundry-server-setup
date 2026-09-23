@echo off
REM Builds FoundryServerManager.exe, then bundles it inside FoundryServerSetup.exe.
REM Run from this folder on Windows with Python 3.10+ installed.
setlocal
cd /d "%~dp0"

python -m pip install --upgrade -r requirements.txt || goto :fail

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name FoundryServerManager server_manager.py || goto :fail

python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin ^
  --name FoundryServerSetup ^
  --add-data "dist\FoundryServerManager.exe;." ^
  installer.py || goto :fail

echo.
echo Done. Share dist\FoundryServerSetup.exe
pause
exit /b 0

:fail
echo.
echo Build failed.
pause
exit /b 1
