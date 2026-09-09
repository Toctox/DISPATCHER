@echo off
setlocal EnableExtensions
title FactoryBridge - Auto Update Setup

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator permission for one-time bootstrap...
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','""%~f0" elevated"' -Verb RunAs"
  exit /b 0
)

set "ROOT=%LOCALAPPDATA%\FactoryBridge"
set "ADMIN_DIR=%ROOT%\admin"
set "STATE_DIR=%ROOT%\state"
set "TASK_NAME=FactoryBridge Admin Poller"
set "POLLER_SRC=%~dp0..\scripts\factory-bridge-admin-poller.ps1"
set "UPDATER_SRC=%~dp0..\scripts\factory-bridge-autoupdate.ps1"
set "CMD_SRC=%~dp0AUTOUPDATE_FACTORY_BRIDGE.cmd"
set "POLLER_DST=%ADMIN_DIR%\factory-bridge-admin-poller.ps1"
set "UPDATER_DST=%ADMIN_DIR%\factory-bridge-autoupdate.ps1"
set "CMD_DST=%ADMIN_DIR%\AUTOUPDATE_FACTORY_BRIDGE.cmd"

if not exist "%POLLER_SRC%" (
  echo ERROR: poller source missing: %POLLER_SRC%
  goto :fail
)
if not exist "%UPDATER_SRC%" (
  echo ERROR: updater source missing: %UPDATER_SRC%
  goto :fail
)
if not exist "%CMD_SRC%" (
  echo ERROR: fixed command source missing: %CMD_SRC%
  goto :fail
)
if not exist "%ADMIN_DIR%" mkdir "%ADMIN_DIR%"
if errorlevel 1 goto :fail
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"
if errorlevel 1 goto :fail

copy /y "%POLLER_SRC%" "%POLLER_DST%" >nul
if errorlevel 1 goto :fail
copy /y "%UPDATER_SRC%" "%UPDATER_DST%" >nul
if errorlevel 1 goto :fail
copy /y "%CMD_SRC%" "%CMD_DST%" >nul
if errorlevel 1 goto :fail

echo Registering fixed elevated allowcommand task...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $name='%TASK_NAME%'; $old=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue; if($null -ne $old){Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName $name -Confirm:$false}; $action=New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c ""%CMD_DST%""') -WorkingDirectory '%ADMIN_DIR%'; $trigger=New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650); $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; $principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest; Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null; Enable-ScheduledTask -TaskName $name | Out-Null; $task=Get-ScheduledTask -TaskName $name; Write-Host ('TASK=' + $task.TaskName); Write-Host ('STATE=' + $task.State); Write-Host ('RUN_LEVEL=Highest');"
if errorlevel 1 goto :fail

echo.
echo FACTORYBRIDGE_AUTOUPDATE_SETUP_OK
echo Fixed admin command:
echo   %CMD_DST%
echo Scheduled task:
echo   %TASK_NAME%
echo Poll cadence:
echo   every minute
echo Allowed remote action:
echo   bridge.self_update ONLY via FACTORY_ADMIN_V1 approval on GitHub Issue #7
echo No arbitrary command, path, shell or remote arguments are accepted.
echo.
exit /b 0

:fail
echo.
echo FACTORYBRIDGE_AUTOUPDATE_SETUP_FAILED
exit /b 1
