@echo off
setlocal EnableExtensions
set "POLLER=%LOCALAPPDATA%\FactoryBridge\admin\factory-bridge-admin-poller.ps1"
if not exist "%POLLER%" (
  echo ERROR: fixed FactoryBridge admin poller is not installed:
  echo   %POLLER%
  exit /b 2
)
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%POLLER%"
exit /b %errorlevel%
