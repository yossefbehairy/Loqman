@echo off
REM ============================================================
REM  AgentOS Launcher — double-click this file to open the app.
REM  Prefers the portable build, falls back to an installed copy.
REM ============================================================
setlocal

REM Prefer the latest build made alongside a running/locked copy of the app.
set "APP=%~dp0frontend\release-next\win-unpacked\AgentOS.exe"
if exist "%APP%" (
  start "" "%APP%"
  exit /b 0
)

set "APP=%~dp0frontend\release\win-unpacked\AgentOS.exe"
if exist "%APP%" (
  start "" "%APP%"
  exit /b 0
)

set "INSTALLED_USER=%LOCALAPPDATA%\Programs\AgentOS\AgentOS.exe"
if exist "%INSTALLED_USER%" (
  start "" "%INSTALLED_USER%"
  exit /b 0
)

set "INSTALLED_MACHINE=%ProgramFiles%\AgentOS\AgentOS.exe"
if exist "%INSTALLED_MACHINE%" (
  start "" "%INSTALLED_MACHINE%"
  exit /b 0
)

echo [AgentOS] No built app found.
echo Build it first with:
echo   cd frontend ^&^& npm run desktop:dist
echo.
pause
