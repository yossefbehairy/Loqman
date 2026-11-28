@echo off
REM ============================================================
REM  Loqman Launcher — double-click this file to open the app.
REM  Prefers the portable build, falls back to an installed copy.
REM ============================================================
setlocal
set "ELECTRON_RUN_AS_NODE="

REM Run the current project when its dependencies are installed.
if exist "%~dp0frontend\node_modules\electron\dist\electron.exe" if exist "%~dp0backend\.venv\Scripts\python.exe" (
  pushd "%~dp0frontend"
  call npm run desktop
  if errorlevel 1 pause
  popd
  exit /b
)

REM Prefer the latest build made alongside a running/locked copy of the app.
set "APP=%~dp0frontend\release-next\win-unpacked\Loqman.exe"
if exist "%APP%" (
  start "" "%APP%"
  exit /b 0
)

set "APP=%~dp0frontend\release\win-unpacked\Loqman.exe"
if exist "%APP%" (
  start "" "%APP%"
  exit /b 0
)

set "INSTALLED_USER=%LOCALAPPDATA%\Programs\Loqman\Loqman.exe"
if exist "%INSTALLED_USER%" (
  start "" "%INSTALLED_USER%"
  exit /b 0
)

set "INSTALLED_MACHINE=%ProgramFiles%\Loqman\Loqman.exe"
if exist "%INSTALLED_MACHINE%" (
  start "" "%INSTALLED_MACHINE%"
  exit /b 0
)

echo [Loqman] No built app found.
echo Build it first with:
echo   cd frontend ^&^& npm run desktop:dist
echo.
pause
