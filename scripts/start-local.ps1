[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$ValidateOnly,
    [ValidateRange(1, 65535)]
    [int]$BackendPort = 8000,
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = 4173
)

$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repositoryRoot "backend"
$frontendRoot = Join-Path $repositoryRoot "frontend"
$pythonPath = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$databasePath = Join-Path $backendRoot ".local\standard-item-migration-v2.sqlite3"
$submissionPath = Join-Path $backendRoot ".local\submissions"
$healthUrl = "http://127.0.0.1:$BackendPort/api/health"
$frontendUrl = "http://127.0.0.1:$FrontendPort/"

function Assert-CommandPath {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label not found: $Path"
    }
}

function Assert-PortAvailable {
    param([int]$Port)
    $occupied = [System.Net.NetworkInformation.IPGlobalProperties]::
        GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq $Port } |
        Select-Object -First 1
    if ($null -ne $occupied) {
        throw "Port $Port is already occupied. No existing process was stopped."
    }
}

Assert-CommandPath -Path $pythonPath -Label "Repository Python"
$npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
if ($null -eq $npmCommand) {
    $npmCommand = Get-Command "npm" -ErrorAction Stop
}
Assert-CommandPath -Path (Join-Path $frontendRoot "package.json") `
    -Label "Frontend package"
Assert-PortAvailable -Port $BackendPort
Assert-PortAvailable -Port $FrontendPort

$env:DATABASE_FILE = $databasePath
$env:SUBMISSION_FOLDER = $submissionPath

if ($ValidateOnly) {
    Write-Host "Launcher configuration is valid."
    Write-Host "Database: $databasePath"
    Write-Host "Backend:  $healthUrl"
    Write-Host "Frontend: $frontendUrl"
    exit 0
}

New-Item -ItemType Directory -Force -Path (Split-Path $databasePath) |
    Out-Null
New-Item -ItemType Directory -Force -Path $submissionPath | Out-Null

Push-Location -LiteralPath $backendRoot
try {
    & $pythonPath -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic upgrade failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

$backendProcess = $null
$frontendProcess = $null
try {
    $backendProcess = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @(
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", "$BackendPort"
        ) `
        -WorkingDirectory $backendRoot `
        -WindowStyle Hidden `
        -PassThru

    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $healthy = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($backendProcess.HasExited) {
            throw "Backend exited before becoming healthy."
        }
        try {
            $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
            if ($response.status -eq "ok") {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $healthy) {
        throw "Backend health check timed out after 30 seconds."
    }

    $frontendProcess = Start-Process `
        -FilePath $npmCommand.Source `
        -ArgumentList @(
            "run", "dev", "--",
            "--host", "127.0.0.1",
            "--port", "$FrontendPort",
            "--strictPort"
        ) `
        -WorkingDirectory $frontendRoot `
        -WindowStyle Hidden `
        -PassThru

    Start-Sleep -Milliseconds 750
    if ($frontendProcess.HasExited) {
        throw "Frontend exited before accepting requests."
    }

    if (-not $NoBrowser) {
        Start-Process $frontendUrl
    }

    Write-Host "Price Analyzer is running at $frontendUrl"
    Write-Host "Backend health: $healthUrl"
    Write-Host "Database: $databasePath"
} catch {
    if ($null -ne $frontendProcess -and -not $frontendProcess.HasExited) {
        Stop-Process -Id $frontendProcess.Id
    }
    if ($null -ne $backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id
    }
    throw
}
