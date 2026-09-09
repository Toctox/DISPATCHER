@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-factory-gateway.ps1"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo FACTORY_GATEWAY_SETUP_FAILED rc=%RC%
) else (
  echo FACTORY_GATEWAY_SETUP_OK
)
echo.
pause
exit /b %RC%
