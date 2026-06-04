@echo off
setlocal
cd /d "%~dp0"

python -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 exit /b 1

rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
mkdir release 2>nul

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --name LightNovelEPUBWorkbench ^
  linovelib_gui.py
if errorlevel 1 exit /b 1

set "ISCC=iscc"
where iscc >nul 2>nul
if errorlevel 1 set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if not exist "%ISCC%" (
  echo Inno Setup compiler is not installed or not in PATH.
  echo Install it with: winget install --id JRSoftware.InnoSetup --exact
  exit /b 1
)

"%ISCC%" packaging\LightNovelEPUBWorkbench.iss
if errorlevel 1 exit /b 1

echo.
echo Installer created in release\
endlocal
