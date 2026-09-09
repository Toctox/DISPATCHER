@echo off
setlocal EnableExtensions
title FactoryBridge - Auto Update Bootstrap

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator permission...
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','""%~f0" elevated"' -Verb RunAs"
  exit /b 0
)

set "ROOT=%LOCALAPPDATA%\FactoryBridge"
set "SOURCE_PARENT=%ROOT%\source"
set "SOURCE_DIR=%SOURCE_PARENT%\DISPATCHER"
set "REPO=https://github.com/Toctox/DISPATCHER.git"

where git.exe >nul 2>&1
if errorlevel 1 (
  echo ERROR: git.exe not found.
  exit /b 2
)
if not exist "%SOURCE_PARENT%" mkdir "%SOURCE_PARENT%"

if exist "%SOURCE_DIR%\.git" (
  git -C "%SOURCE_DIR%" fetch --prune origin main
  if errorlevel 1 exit /b 3
  git -C "%SOURCE_DIR%" checkout -f main
  if errorlevel 1 exit /b 3
  git -C "%SOURCE_DIR%" reset --hard origin/main
  if errorlevel 1 exit /b 3
) else (
  if exist "%SOURCE_DIR%" rmdir /s /q "%SOURCE_DIR%"
  git clone --branch main "%REPO%" "%SOURCE_DIR%"
  if errorlevel 1 exit /b 3
)

call "%SOURCE_DIR%\factory_bridge\SETUP_FACTORY_BRIDGE_AUTOUPDATE.cmd" elevated
exit /b %errorlevel%
