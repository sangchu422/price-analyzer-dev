@echo off
chcp 65001 > nul
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0scripts\start-local.bat" %*
if errorlevel 1 (
  echo.
  echo Price Analyzer failed to start. Check the message above.
  pause
  exit /b 1
)
endlocal
