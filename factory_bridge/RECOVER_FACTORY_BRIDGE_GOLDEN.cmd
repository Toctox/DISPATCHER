@echo off
setlocal EnableExtensions
title FactoryBridge - Golden Recovery

set "GOLDEN_SHA=d8ffcb2077e2cf1ba5ba24c9240e1c82b0392df0"
set "REPO=https://github.com/Toctox/DISPATCHER.git"
set "TASK_NAME=FactoryBridge Supervisor"
set "ROOT=%LOCALAPPDATA%\FactoryBridge"
set "BIN_DIR=%ROOT%\bin"
set "STAGING_DIR=%ROOT%\staging"
set "GOLDEN_PARENT=%ROOT%\golden-source"
set "GOLDEN_DIR=%GOLDEN_PARENT%\DISPATCHER"
set "INSTALL_EXE=%BIN_DIR%\FactoryBridge.exe"
set "PREV_EXE=%BIN_DIR%\FactoryBridge.prev.exe"
set "STAGED_EXE=%STAGING_DIR%\FactoryBridge.golden.exe"
set "CFG=%ROOT%\config.json"

echo ============================================================
echo FactoryBridge GOLDEN RECOVERY
echo Pinned source: %GOLDEN_SHA%
echo ============================================================
echo.

where git.exe >nul 2>&1 || goto :missing_git
where go.exe >nul 2>&1 || goto :missing_go
if not exist "%CFG%" goto :missing_cfg
if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"
if not exist "%STAGING_DIR%" mkdir "%STAGING_DIR%"
if not exist "%GOLDEN_PARENT%" mkdir "%GOLDEN_PARENT%"

echo [1/6] Fetching exact golden commit...
if not exist "%GOLDEN_DIR%\.git" (
  if exist "%GOLDEN_DIR%" rmdir /s /q "%GOLDEN_DIR%"
  git clone --no-checkout "%REPO%" "%GOLDEN_DIR%" || goto :git_fail
)
git -C "%GOLDEN_DIR%" fetch --prune origin %GOLDEN_SHA% || goto :git_fail
git -C "%GOLDEN_DIR%" checkout --detach -f %GOLDEN_SHA% || goto :git_fail
for /f "delims=" %%H in ('git -C "%GOLDEN_DIR%" rev-parse HEAD') do set "ACTUAL_SHA=%%H"
if /I not "%ACTUAL_SHA%"=="%GOLDEN_SHA%" goto :sha_fail

echo [2/6] Running golden source tests...
pushd "%GOLDEN_DIR%\factory_bridge"
go test ./... || (popd & goto :test_fail)

echo [3/6] Building golden binary in local staging...
if exist "%STAGED_EXE%" del /f /q "%STAGED_EXE%" >nul 2>&1
go build -trimpath -ldflags "-s -w" -o "%STAGED_EXE%" . || (popd & goto :build_fail)
popd
if not exist "%STAGED_EXE%" goto :build_fail

echo [4/6] Stopping current runtime...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue'; $t=Get-ScheduledTask -TaskName '%TASK_NAME%'; if($null -ne $t){Stop-ScheduledTask -TaskName '%TASK_NAME%'}; Get-Process -Name 'FactoryBridge' | Stop-Process -Force"
timeout /t 1 /nobreak >nul

echo [5/6] Promoting golden binary with rollback copy...
if exist "%PREV_EXE%" del /f /q "%PREV_EXE%" >nul 2>&1
if exist "%INSTALL_EXE%" copy /y "%INSTALL_EXE%" "%PREV_EXE%" >nul
copy /y "%STAGED_EXE%" "%INSTALL_EXE%" >nul || goto :promote_fail

echo [6/6] Restarting supervisor...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $name='%TASK_NAME%'; $exe='%INSTALL_EXE%'; $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue; if($null -ne $task){if([string]$task.State -eq 'Disabled'){Enable-ScheduledTask -TaskName $name | Out-Null}; Start-ScheduledTask -TaskName $name} else {Start-Process -FilePath $exe -ArgumentList '--mode','supervisor' -WorkingDirectory '%BIN_DIR%'}"
if errorlevel 1 goto :restart_fail
timeout /t 4 /nobreak >nul
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$p=@(Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue); if($p.Count -lt 1){exit 1}; Write-Host ('GOLDEN_RECOVERY_OK sha=%GOLDEN_SHA% processCount=' + $p.Count)"
if errorlevel 1 goto :restart_fail

echo.
echo GOLDEN RECOVERY COMPLETE
echo Runtime source is pinned to:
echo   %GOLDEN_SHA%
echo This is a recovery baseline, not the latest source.
exit /b 0

:missing_git
echo ERROR: git.exe not found.
goto :fail
:missing_go
echo ERROR: go.exe not found.
goto :fail
:missing_cfg
echo ERROR: local FactoryBridge config not found: %CFG%
goto :fail
:git_fail
echo ERROR: could not fetch pinned golden source.
goto :fail
:sha_fail
echo ERROR: fetched checkout does not equal pinned golden SHA.
goto :fail
:test_fail
echo ERROR: golden go test failed; current runtime was not replaced.
goto :fail
:build_fail
echo ERROR: golden go build failed; current runtime was not replaced.
goto :fail
:promote_fail
echo ERROR: could not promote golden binary.
goto :fail
:restart_fail
echo ERROR: golden binary was promoted but restart validation failed.
goto :fail
:fail
echo GOLDEN RECOVERY FAILED.
exit /b 1
