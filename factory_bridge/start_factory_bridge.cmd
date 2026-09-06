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
  "$p='%CFG%'; $c=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json; $c | Add-Member -NotePropertyName allowGitPull -NotePropertyValue $true -Force; $c | Add-Member -NotePropertyName allowGitPush -NotePropertyValue $true -Force; $json=$c | ConvertTo-Json -Depth 10; [System.IO.File]::WriteAllText($p,$json,(New-Object System.Text.UTF8Encoding($false)))"
if errorlevel 1 (
  echo Failed to update Git permissions in config.
  exit /b 1
)

rem Ensure legacy direct executors cannot race the supervised executor.
taskkill /IM FactoryBridge.exe /F >nul 2>&1
taskkill /IM FactoryBridge-0.4.0.exe /F >nul 2>&1

start "FactoryBridge Supervisor" cmd /k ""%EXE%" --mode supervisor"
start "FactoryBridge Panel" cmd /k ""%EXE%" --mode panel"

endlocal
