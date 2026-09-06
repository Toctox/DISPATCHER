@echo off
setlocal EnableExtensions

set "BASE=%~dp0"
set "SOURCE_EXE=%BASE%FactoryBridge.exe"
set "INSTALL_DIR=%LOCALAPPDATA%\FactoryBridge\bin"
set "INSTALL_EXE=%INSTALL_DIR%\FactoryBridge.exe"
set "CFG=%LOCALAPPDATA%\FactoryBridge\config.json"
set "TASK_NAME=FactoryBridge Supervisor"

echo === FactoryBridge 0.5 resilient installation ===

if not exist "%SOURCE_EXE%" (
  echo ERROR: FactoryBridge.exe not found in:
  echo   %BASE%
  exit /b 1
)
if not exist "%CFG%" (
  echo ERROR: FactoryBridge config not found:
  echo   %CFG%
  exit /b 1
)

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if errorlevel 1 (
  echo ERROR: could not create install directory.
  exit /b 1
)

rem Stop previous scheduled instance and legacy direct executors.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$t=Get-ScheduledTask -TaskName '%TASK_NAME%' -ErrorAction SilentlyContinue; if($t){Stop-ScheduledTask -TaskName '%TASK_NAME%' -ErrorAction SilentlyContinue}"
taskkill /IM FactoryBridge.exe /F >nul 2>&1
taskkill /IM FactoryBridge-0.4.0.exe /F >nul 2>&1

copy /Y "%SOURCE_EXE%" "%INSTALL_EXE%" >nul
if errorlevel 1 (
  echo ERROR: failed to copy FactoryBridge.exe to local installation.
  exit /b 1
)

rem Enable controlled Git pull/push and write strict UTF-8 without BOM.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%CFG%'; $c=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json; $c | Add-Member -NotePropertyName allowGitPull -NotePropertyValue $true -Force; $c | Add-Member -NotePropertyName allowGitPush -NotePropertyValue $true -Force; $json=$c | ConvertTo-Json -Depth 10; [System.IO.File]::WriteAllText($p,$json,(New-Object System.Text.UTF8Encoding($false)))"
if errorlevel 1 (
  echo ERROR: failed to update FactoryBridge config.
  exit /b 1
)

rem Register a per-user task. The supervisor heals the executor; Task Scheduler heals the supervisor.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $name='%TASK_NAME%'; $exe='%INSTALL_EXE%'; $action=New-ScheduledTaskAction -Execute $exe -Argument '--mode supervisor'; $trigger=New-ScheduledTaskTrigger -AtLogOn; $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew; $principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited; Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null; Start-ScheduledTask -TaskName $name"
if errorlevel 1 (
  echo ERROR: could not register/start the scheduled task.
  echo Try running this installer with Run as administrator.
  exit /b 1
)

echo.
echo SUCCESS: FactoryBridge supervisor installed.
echo Binary: %INSTALL_EXE%
echo Task:   %TASK_NAME%
echo Git pull/push: enabled in local config.
echo The supervisor will restart the executor automatically.
echo If the supervisor itself exits, Task Scheduler retries it every 1 minute.
echo.
echo You may close this window.

endlocal
