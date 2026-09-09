@echo off
setlocal EnableExtensions
title FactoryBridge - Stable HTTPS Gateway

net session >nul 2>&1
if not "%ERRORLEVEL%"=="0" (
  echo Administrator privileges are required for Tailscale Funnel on Windows.
  echo Requesting elevation...
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set "SOURCE=%LOCALAPPDATA%\FactoryBridge\source\DISPATCHER"
set "REPO=https://github.com/Toctox/DISPATCHER.git"

if not exist "%SOURCE%\.git" (
  if not exist "%LOCALAPPDATA%\FactoryBridge\source" mkdir "%LOCALAPPDATA%\FactoryBridge\source"
  echo Cloning DISPATCHER source...
  git clone --depth 1 --branch main "%REPO%" "%SOURCE%"
  if errorlevel 1 goto :fail
) else (
  echo Updating DISPATCHER source...
  git -C "%SOURCE%" fetch --prune origin main
  if errorlevel 1 goto :fail
  git -C "%SOURCE%" checkout -f main
  if errorlevel 1 goto :fail
  git -C "%SOURCE%" reset --hard origin/main
  if errorlevel 1 goto :fail
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SOURCE%\scripts\setup-factory-gateway-tailscale.ps1"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" goto :failcode
echo FACTORY_GATEWAY_STABLE_SETUP_OK
echo.
echo Return to ChatGPT and send: pronto
pause
exit /b 0

:fail
set "RC=1"
:failcode
echo.
echo FACTORY_GATEWAY_STABLE_SETUP_FAILED rc=%RC%
echo Send a photo of these final lines to ChatGPT.
echo.
pause
exit /b %RC%
