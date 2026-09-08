@echo off
setlocal EnableExtensions

set "TASK_NAME=FactoryBridge Supervisor"
set "LOCAL_EXE=%LOCALAPPDATA%\FactoryBridge\bin\FactoryBridge.exe"

echo === FactoryBridge recovery ===

if not exist "%LOCAL_EXE%" (
  echo ERROR: local FactoryBridge binary not found:
  echo   %LOCAL_EXE%
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $name='%TASK_NAME%'; $exe='%LOCAL_EXE%'; $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue; if($null -ne $task){ if([string]$task.State -eq 'Disabled'){Enable-ScheduledTask -TaskName $name | Out-Null}; Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue; Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 500; Start-ScheduledTask -TaskName $name; Write-Host 'RECOVERY_OK mode=scheduled-task' } else { Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Process -FilePath $exe -ArgumentList '--mode','supervisor' -WorkingDirectory (Split-Path -Parent $exe); Write-Host 'RECOVERY_OK mode=direct-fallback' }"
if errorlevel 1 (
  echo ERROR: FactoryBridge recovery failed.
  exit /b 1
)

echo FactoryBridge recovery command completed.
endlocal
