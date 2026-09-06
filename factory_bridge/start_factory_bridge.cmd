@echo off
setlocal

set "BASE=%~dp0"
set "EXE=%BASE%FactoryBridge.exe"
set "CFG=%LOCALAPPDATA%\FactoryBridge\config.json"

if not exist "%EXE%" (
  echo FactoryBridge.exe not found in:
  echo   %BASE%
  exit /b 1
)

if not exist "%CFG%" (
  echo Config not found:
  echo   %CFG%
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%CFG%'; $c=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json; $c | Add-Member -NotePropertyName allowGitPull -NotePropertyValue $true -Force; $c | Add-Member -NotePropertyName allowGitPush -NotePropertyValue $true -Force; $c | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $p -Encoding UTF8"
if errorlevel 1 (
  echo Failed to update Git permissions in config.
  exit /b 1
)

start "FactoryBridge Executor" cmd /k ""%EXE%" --mode executor"
start "FactoryBridge Panel" cmd /k ""%EXE%" --mode panel"

endlocal
