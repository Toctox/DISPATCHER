@echo off
setlocal EnableExtensions
title FactoryBridge - Clean Local Installer

set "TASK_NAME=FactoryBridge Supervisor"
set "ROOT=%LOCALAPPDATA%\FactoryBridge"
set "BIN_DIR=%ROOT%\bin"
set "MAILBOX_DIR=%ROOT%\mailbox"
set "STAGING_DIR=%ROOT%\staging"
set "SOURCE_PARENT=%ROOT%\source"
set "SOURCE_DIR=%SOURCE_PARENT%\DISPATCHER"
set "INSTALL_EXE=%BIN_DIR%\FactoryBridge.exe"
set "PREV_EXE=%BIN_DIR%\FactoryBridge.prev.exe"
set "STAGED_EXE=%STAGING_DIR%\FactoryBridge.next.exe"
set "CFG=%ROOT%\config.json"
set "REPO=https://github.com/Toctox/DISPATCHER.git"

echo ============================================================
echo  FactoryBridge - CLEAN LOCAL INSTALL
echo  Runtime: %%LOCALAPPDATA%%\FactoryBridge
echo  Bus:     GitHub Issue #7 / FACTORY_BUS_V2
echo  Source:  Toctox/DISPATCHER@main
echo ============================================================
echo.

where git.exe >nul 2>&1
if errorlevel 1 (
  echo ERROR: git.exe nao foi encontrado no PATH.
  goto :fail
)

where go.exe >nul 2>&1
if errorlevel 1 (
  echo ERROR: go.exe nao foi encontrado no PATH.
  goto :fail
)

if not exist "%CFG%" (
  echo ERROR: configuracao existente nao encontrada:
  echo   %CFG%
  echo.
  echo Este instalador preserva caminhos de Dispatcher/ProjectHub existentes
  echo e nao inventa credenciais.
  goto :fail
)

if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"
if errorlevel 1 goto :mkdir_fail
if not exist "%MAILBOX_DIR%" mkdir "%MAILBOX_DIR%"
if errorlevel 1 goto :mkdir_fail
if not exist "%STAGING_DIR%" mkdir "%STAGING_DIR%"
if errorlevel 1 goto :mkdir_fail
if not exist "%SOURCE_PARENT%" mkdir "%SOURCE_PARENT%"
if errorlevel 1 goto :mkdir_fail

echo [1/8] Parando instalacao anterior...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue'; $name='%TASK_NAME%'; $t=Get-ScheduledTask -TaskName $name; if($null -ne $t){Stop-ScheduledTask -TaskName $name}; Get-Process -Name 'FactoryBridge' | Stop-Process -Force"
timeout /t 1 /nobreak >nul

echo [2/8] Atualizando codigo-fonte local...
if exist "%SOURCE_DIR%\.git" (
  git -C "%SOURCE_DIR%" fetch --prune origin main
  if errorlevel 1 goto :git_fail
  git -C "%SOURCE_DIR%" checkout -f main
  if errorlevel 1 goto :git_fail
  git -C "%SOURCE_DIR%" reset --hard origin/main
  if errorlevel 1 goto :git_fail
) else (
  if exist "%SOURCE_DIR%" rmdir /s /q "%SOURCE_DIR%"
  git clone --depth 1 --branch main "%REPO%" "%SOURCE_DIR%"
  if errorlevel 1 goto :git_fail
)

for /f "delims=" %%H in ('git -C "%SOURCE_DIR%" rev-parse HEAD') do set "SOURCE_HEAD=%%H"
echo       commit=%SOURCE_HEAD%

echo [3/8] Executando testes do FactoryBridge...
pushd "%SOURCE_DIR%\factory_bridge"
go test ./...
if errorlevel 1 (
  popd
  goto :test_fail
)

echo [4/8] Compilando runtime em staging LOCAL...
if exist "%STAGED_EXE%" del /f /q "%STAGED_EXE%" >nul 2>&1
go build -trimpath -ldflags "-s -w" -o "%STAGED_EXE%" .
if errorlevel 1 (
  popd
  goto :build_fail
)
popd

if not exist "%STAGED_EXE%" (
  echo ERROR: o binario de staging nao foi criado.
  goto :fail
)

echo [5/8] Promovendo binario local com rollback...
if exist "%PREV_EXE%" del /f /q "%PREV_EXE%" >nul 2>&1
if exist "%INSTALL_EXE%" (
  copy /y "%INSTALL_EXE%" "%PREV_EXE%" >nul
  if errorlevel 1 (
    echo ERROR: nao foi possivel preservar FactoryBridge.prev.exe.
    goto :fail
  )
)
copy /y "%STAGED_EXE%" "%INSTALL_EXE%" >nul
if errorlevel 1 (
  echo ERROR: nao foi possivel promover o novo runtime.
  goto :fail
)

echo [6/8] Normalizando configuracao local e reduzindo superficie legada...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $p='%CFG%'; $c=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json; $c | Add-Member -NotePropertyName bridgeRoot -NotePropertyValue '%MAILBOX_DIR%' -Force; $c | Add-Member -NotePropertyName allowGitPull -NotePropertyValue $false -Force; $c | Add-Member -NotePropertyName allowGitPush -NotePropertyValue $false -Force; $json=$c | ConvertTo-Json -Depth 20; [System.IO.File]::WriteAllText($p,$json,(New-Object System.Text.UTF8Encoding($false)))"
if errorlevel 1 (
  echo ERROR: falha ao atualizar a configuracao local.
  goto :fail
)

echo [7/8] Recriando UM unico supervisor no Task Scheduler...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $name='%TASK_NAME%'; $exe='%INSTALL_EXE%'; $old=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue; if($null -ne $old){Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName $name -Confirm:$false}; $action=New-ScheduledTaskAction -Execute $exe -Argument '--mode supervisor' -WorkingDirectory '%BIN_DIR%'; $trigger=New-ScheduledTaskTrigger -AtLogOn; $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; $principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited; Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null; Enable-ScheduledTask -TaskName $name | Out-Null; Start-ScheduledTask -TaskName $name"
if errorlevel 1 (
  echo.
  echo ERROR: nao foi possivel registrar/iniciar a tarefa.
  echo Tente clicar com o botao direito neste arquivo e usar:
  echo   Executar como administrador
  goto :fail
)

echo [8/8] Validando processo e tarefa...
timeout /t 4 /nobreak >nul

powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $name='%TASK_NAME%'; $task=Get-ScheduledTask -TaskName $name; $procs=@(Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue); if($task.State -eq 'Disabled'){throw 'Scheduled Task permaneceu desabilitada.'}; if($procs.Count -lt 1){throw 'Nenhum processo FactoryBridge esta ativo.'}; Write-Host ('TASK_STATE=' + $task.State); Write-Host ('PROCESS_COUNT=' + $procs.Count); Write-Host ('PIDS=' + (($procs.Id | Sort-Object) -join ','))"
if errorlevel 1 (
  echo ERROR: validacao pos-instalacao falhou.
  goto :fail
)

echo.
echo ============================================================
echo  INSTALACAO CONCLUIDA
echo ============================================================
echo Runtime:
echo   %INSTALL_EXE%
echo Config:
echo   %CFG%
echo Source commit:
echo   %SOURCE_HEAD%
echo Task:
echo   %TASK_NAME%
echo.
echo Controle primario: GitHub Issue #7 / FACTORY_BUS_V2
echo Estado/evidencia: %%LOCALAPPDATA%%\FactoryBridge
echo Google Drive: documentacao e recovery somente.
echo Acoes legadas git.pull/git.push permanecem desabilitadas na config.
echo.
echo Pode fechar esta janela e voltar ao ChatGPT.
echo.
pause
exit /b 0

:mkdir_fail
echo ERROR: nao foi possivel criar a estrutura em %ROOT%.
goto :fail

:git_fail
echo ERROR: falha ao baixar/atualizar Toctox/DISPATCHER@main.
goto :fail

:test_fail
echo ERROR: go test ./... falhou. O runtime atual NAO foi substituido.
goto :fail

:build_fail
echo ERROR: go build falhou. O runtime atual NAO foi substituido.
goto :fail

:fail
echo.
echo INSTALACAO NAO CONCLUIDA.
echo Nenhuma falha de teste/build promove intencionalmente um binario novo.
echo Para recovery do runtime V1 conhecido, use RECOVER_FACTORY_BRIDGE_GOLDEN.cmd.
echo.
pause
exit /b 1
