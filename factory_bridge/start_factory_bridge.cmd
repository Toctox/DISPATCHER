@echo off
setlocal

set "BASE=%~dp0"
set "EXE=%BASE%FactoryBridge.exe"

if not exist "%EXE%" (
  echo FactoryBridge.exe not found in:
  echo   %BASE%
  echo.
  echo Build it first from this folder:
  echo   go test ./...
  echo   go build -trimpath -ldflags "-s -w" -o FactoryBridge.exe .
  exit /b 1
)

start "FactoryBridge Executor" cmd /k ""%EXE%" --mode executor"
start "FactoryBridge Panel" cmd /k ""%EXE%" --mode panel"

endlocal
