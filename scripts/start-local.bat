@echo off
setlocal EnableExtensions

for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
set "BACKEND_ROOT=%REPO_ROOT%\backend"
set "FRONTEND_ROOT=%REPO_ROOT%\frontend"
set "PYTHON=%REPO_ROOT%\.venv\Scripts\python.exe"
set "DATABASE_FILE=%BACKEND_ROOT%\.local\standard-item-migration-v2.sqlite3"
set "SUBMISSION_FOLDER=%BACKEND_ROOT%\.local\submissions"
if defined PRICE_ANALYZER_BACKEND_PORT (
  set "BACKEND_PORT=%PRICE_ANALYZER_BACKEND_PORT%"
) else (
  set "BACKEND_PORT=8000"
)
if defined PRICE_ANALYZER_FRONTEND_PORT (
  set "FRONTEND_PORT=%PRICE_ANALYZER_FRONTEND_PORT%"
) else (
  set "FRONTEND_PORT=4173"
)
set "HEALTH_URL=http://127.0.0.1:%BACKEND_PORT%/api/health"
set "FRONTEND_URL=http://127.0.0.1:%FRONTEND_PORT%/"
set "VALIDATE_ONLY=0"
set "NO_BROWSER=0"

:parse_args
if "%~1"=="" goto validate_files
if /I "%~1"=="--validate-only" (
  set "VALIDATE_ONLY=1"
  shift
  goto parse_args
)
if /I "%~1"=="--no-browser" (
  set "NO_BROWSER=1"
  shift
  goto parse_args
)
echo Unknown option: %~1
echo Usage: scripts\start-local.bat [--validate-only] [--no-browser]
exit /b 2

:validate_files
if not exist "%PYTHON%" (
  echo Repository Python not found: %PYTHON%
  exit /b 1
)
if not exist "%FRONTEND_ROOT%\package.json" (
  echo Frontend package not found: %FRONTEND_ROOT%\package.json
  exit /b 1
)
for /f "delims=" %%I in ('where npm.cmd 2^>nul') do if not defined NPM set "NPM=%%I"
if not defined NPM (
  echo npm.cmd was not found on PATH.
  exit /b 1
)

netstat -ano -p tcp | findstr /R /C:":%BACKEND_PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo Port %BACKEND_PORT% is already in use. No process was stopped.
  exit /b 1
)
netstat -ano -p tcp | findstr /R /C:":%FRONTEND_PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo Port %FRONTEND_PORT% is already in use. No process was stopped.
  exit /b 1
)

if "%VALIDATE_ONLY%"=="1" (
  echo Launcher configuration is valid.
  echo Database: %DATABASE_FILE%
  echo Backend:  %HEALTH_URL%
  echo Frontend: %FRONTEND_URL%
  exit /b 0
)

if not exist "%BACKEND_ROOT%\.local" mkdir "%BACKEND_ROOT%\.local"
if not exist "%SUBMISSION_FOLDER%" mkdir "%SUBMISSION_FOLDER%"

pushd "%BACKEND_ROOT%"
"%PYTHON%" -m alembic upgrade head
set "MIGRATION_EXIT=%errorlevel%"
popd
if not "%MIGRATION_EXIT%"=="0" (
  echo Alembic upgrade failed with exit code %MIGRATION_EXIT%.
  exit /b %MIGRATION_EXIT%
)

start "Price Analyzer Backend" /min /D "%BACKEND_ROOT%" "%PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%

set /a HEALTH_ATTEMPTS=0
:wait_for_backend
"%PYTHON%" -c "import urllib.request; response=urllib.request.urlopen('%HEALTH_URL%', timeout=1); raise SystemExit(0 if response.status == 200 else 1)" >nul 2>&1
if not errorlevel 1 goto start_frontend
set /a HEALTH_ATTEMPTS+=1
if %HEALTH_ATTEMPTS% GEQ 30 (
  echo Backend health check timed out after 30 attempts.
  exit /b 1
)
ping 127.0.0.1 -n 2 >nul
goto wait_for_backend

:start_frontend
start "Price Analyzer Frontend" /min /D "%FRONTEND_ROOT%" "%NPM%" run dev -- --host 127.0.0.1 --port %FRONTEND_PORT% --strictPort

if "%NO_BROWSER%"=="0" start "" "%FRONTEND_URL%"

echo Price Analyzer is running at %FRONTEND_URL%
echo Backend health: %HEALTH_URL%
echo Database: %DATABASE_FILE%
exit /b 0
