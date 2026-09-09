@echo off
setlocal EnableExtensions
title FactoryBridge - Run Admin Poller Now

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator permission...
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','""%~f0" elevated"' -Verb RunAs"
  exit /b 0
)

powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $name='FactoryBridge Admin Poller'; $task=Get-ScheduledTask -TaskName $name -ErrorAction Stop; $trigger=New-ScheduledTaskTrigger -Once -At ((Get-Date).AddSeconds(10)) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650); Set-ScheduledTask -TaskName $name -Trigger $trigger | Out-Null; Enable-ScheduledTask -TaskName $name | Out-Null; Start-ScheduledTask -TaskName $name; Write-Host 'FACTORYBRIDGE_ADMIN_POLLER_STARTED'; Write-Host 'CADENCE=1 minute';"
if errorlevel 1 goto :fail

echo.
echo The fixed FactoryBridge Admin Poller was started immediately.
echo Its recurring cadence is now one minute.
echo This launcher does not accept remote commands, paths, scripts or arguments.
echo.
exit /b 0

:fail
echo.
echo FACTORYBRIDGE_ADMIN_POLLER_START_FAILED
exit /b 1
