@echo off
setlocal EnableExtensions EnableDelayedExpansion
title FactoryBridge - Offline Golden Recovery

set "TASK_NAME=FactoryBridge Supervisor"
set "ROOT=%LOCALAPPDATA%\FactoryBridge"
set "BIN_DIR=%ROOT%\bin"
set "STATE_DIR=%ROOT%\state"
set "GOLDEN_DIR=%ROOT%\golden"
set "INSTALL_EXE=%BIN_DIR%\FactoryBridge.exe"
set "PREV_EXE=%BIN_DIR%\FactoryBridge.prev.exe"
set "GOLDEN_EXE=%GOLDEN_DIR%\FactoryBridge.golden.exe"
set "GOLDEN_HASH=%GOLDEN_DIR%\FactoryBridge.golden.sha256"
set "GOLDEN_META=%GOLDEN_DIR%\golden-runtime.json"
set "RECOVERY_RESULT=%STATE_DIR%\offline-recovery-result.json"
set "CFG=%ROOT%\config.json"

echo ============================================================
echo FactoryBridge OFFLINE GOLDEN RECOVERY
echo No GitHub, git.exe or go.exe is used by this path.
echo ============================================================
echo.

where powershell.exe >nul 2>&1 || goto :missing_powershell
if not exist "%CFG%" goto :missing_cfg
if not exist "%GOLDEN_EXE%" goto :missing_golden
if not exist "%GOLDEN_HASH%" goto :missing_hash
if not exist "%GOLDEN_META%" goto :missing_meta
if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"

set /p EXPECTED_HASH=<"%GOLDEN_HASH%"
for /f "usebackq delims=" %%H in (`powershell.exe -NoLogo -NoProfile -NonInteractive -Command "(Get-FileHash -LiteralPath '%GOLDEN_EXE%' -Algorithm SHA256).Hash.ToLowerInvariant()"`) do set "ACTUAL_HASH=%%H"
if /I not "%EXPECTED_HASH%"=="%ACTUAL_HASH%" goto :hash_fail

for /f "usebackq delims=" %%S in (`powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$m=Get-Content -LiteralPath '%GOLDEN_META%' -Raw|ConvertFrom-Json; [string]$m.sourceCommit"`) do set "GOLDEN_SOURCE=%%S"
if "%GOLDEN_SOURCE%"=="" goto :meta_fail

echo [1/5] Verified frozen binary SHA-256: %ACTUAL_HASH%
echo [2/5] Stopping current runtime...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue'; $t=Get-ScheduledTask -TaskName '%TASK_NAME%' -ErrorAction SilentlyContinue; if($null -ne $t){Stop-ScheduledTask -TaskName '%TASK_NAME%'}; Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force"
timeout /t 1 /nobreak >nul

echo [3/5] Installing frozen binary with rollback copy...
if exist "%PREV_EXE%" del /f /q "%PREV_EXE%" >nul 2>&1
if exist "%INSTALL_EXE%" copy /y "%INSTALL_EXE%" "%PREV_EXE%" >nul || goto :promote_fail
copy /y "%GOLDEN_EXE%" "%INSTALL_EXE%" >nul || goto :promote_fail
for /f "usebackq delims=" %%H in (`powershell.exe -NoLogo -NoProfile -NonInteractive -Command "(Get-FileHash -LiteralPath '%INSTALL_EXE%' -Algorithm SHA256).Hash.ToLowerInvariant()"`) do set "INSTALLED_HASH=%%H"
if /I not "%EXPECTED_HASH%"=="%INSTALLED_HASH%" goto :promote_fail

echo [4/5] Restarting supervisor...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $name='%TASK_NAME%'; $exe='%INSTALL_EXE%'; $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue; if($null -ne $task){if([string]$task.State -eq 'Disabled'){Enable-ScheduledTask -TaskName $name|Out-Null}; Start-ScheduledTask -TaskName $name} else {Start-Process -FilePath $exe -ArgumentList '--mode','supervisor' -WorkingDirectory '%BIN_DIR%'}"
if errorlevel 1 goto :restart_fail

echo [5/5] Verifying local health and exact frozen identity...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $wantHash='%EXPECTED_HASH%'.ToLowerInvariant(); $wantSource='%GOLDEN_SOURCE%'.ToLowerInvariant(); $ok=$false; $h=$null; for($i=0;$i -lt 30;$i++){Start-Sleep -Seconds 1; try{$h=Invoke-RestMethod -Uri 'http://127.0.0.1:8787/public/health' -TimeoutSec 2; if($h.ok -eq $true -and $h.executorOnline -eq $true -and ([string]$h.binarySha256).ToLowerInvariant() -eq $wantHash -and ([string]$h.sourceCommit).ToLowerInvariant() -eq $wantSource){$ok=$true;break}}catch{}}; if(-not $ok){exit 1}; $r=[ordered]@{state='DONE';sourceCommit=$wantSource;binarySha256=$wantHash;protocolVersion=[string]$h.protocolVersion;policyVersion=[string]$h.policyVersion;recoveredAt=(Get-Date).ToUniversalTime().ToString('o');offline=$true}; $tmp='%RECOVERY_RESULT%.tmp'; [IO.File]::WriteAllText($tmp,($r|ConvertTo-Json -Depth 5),(New-Object Text.UTF8Encoding($false))); Move-Item -LiteralPath $tmp -Destination '%RECOVERY_RESULT%' -Force; Write-Host ('OFFLINE_GOLDEN_RECOVERY_OK source='+$wantSource+' sha256='+$wantHash)"
if errorlevel 1 goto :health_fail

echo.
echo OFFLINE GOLDEN RECOVERY COMPLETE
echo sourceCommit=%GOLDEN_SOURCE%
echo binarySha256=%EXPECTED_HASH%
exit /b 0

:health_fail
echo ERROR: frozen runtime failed exact local health validation. Restoring previous binary...
goto :rollback
:restart_fail
echo ERROR: frozen runtime could not restart. Restoring previous binary...
goto :rollback
:promote_fail
echo ERROR: frozen binary could not be promoted. Restoring previous binary if available...
goto :rollback
:rollback
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue'; Get-Process -Name 'FactoryBridge'|Stop-Process -Force; if(Test-Path -LiteralPath '%PREV_EXE%' -PathType Leaf){Copy-Item -LiteralPath '%PREV_EXE%' -Destination '%INSTALL_EXE%' -Force; $t=Get-ScheduledTask -TaskName '%TASK_NAME%' -ErrorAction SilentlyContinue; if($null -ne $t){Start-ScheduledTask -TaskName '%TASK_NAME%'}else{Start-Process -FilePath '%INSTALL_EXE%' -ArgumentList '--mode','supervisor' -WorkingDirectory '%BIN_DIR%'}}"
goto :fail
:hash_fail
echo ERROR: frozen binary SHA-256 does not match its recorded digest. Nothing was changed.
goto :fail
:meta_fail
echo ERROR: golden metadata has no source commit. Nothing was changed.
goto :fail
:missing_powershell
echo ERROR: powershell.exe is required by the local recovery script.
goto :fail
:missing_cfg
echo ERROR: local FactoryBridge config not found: %CFG%
goto :fail
:missing_golden
echo ERROR: frozen golden binary not found: %GOLDEN_EXE%
goto :fail
:missing_hash
echo ERROR: frozen golden SHA-256 file not found: %GOLDEN_HASH%
goto :fail
:missing_meta
echo ERROR: frozen golden metadata not found: %GOLDEN_META%
goto :fail
:fail
echo OFFLINE GOLDEN RECOVERY FAILED.
exit /b 1
