@echo off
REM Reproducible build for My AI Assistant (Windows, one-file exe).
REM Run from anywhere: this script changes to its own directory first.
setlocal
cd /d "%~dp0"

echo [1/4] Building the frontend (npm run build in frontend\)
pushd frontend
call npm run build
if errorlevel 1 (
    echo.
    echo FAILED: frontend build. Nothing was packaged.
    popd
    exit /b 1
)
popd

echo.
echo [2/4] Checking the Python environment
if not exist ".venv\Scripts\pyinstaller.exe" (
    echo FAILED: .venv\Scripts\pyinstaller.exe not found.
    echo Create the environment first: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

echo.
echo [3/4] Packaging with PyInstaller (MyAIAssistant.spec)
".venv\Scripts\pyinstaller.exe" --noconfirm --clean MyAIAssistant.spec
if errorlevel 1 (
    echo.
    echo FAILED: PyInstaller returned an error. See the output above.
    exit /b 1
)

echo.
echo [4/4] Verifying the artifact
if not exist "dist\MyAIAssistant.exe" (
    echo FAILED: dist\MyAIAssistant.exe was not produced.
    exit /b 1
)

for %%F in ("dist\MyAIAssistant.exe") do echo Built: %%~fF  (%%~zF bytes)
echo.
echo Done. Double-click dist\MyAIAssistant.exe, or run scripts\create_shortcuts.ps1
echo to create Desktop and Start Menu shortcuts.
endlocal
exit /b 0
