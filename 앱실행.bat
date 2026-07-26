@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-local.ps1"
if errorlevel 1 (
  echo.
  echo 실행에 실패했습니다. 위 오류를 확인하세요.
  pause
  exit /b 1
)
echo.
echo 브라우저에서 http://127.0.0.1:4173/ 을 확인하세요.
endlocal
