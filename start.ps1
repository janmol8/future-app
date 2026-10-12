# Future Chart - Startup Script (Windows)
param(
    [switch]$SkipDeps
)

Write-Host "Starting Future Chart..." -ForegroundColor Cyan

# Check if Python is available on PATH
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    # Fallback to standard User-Programs path
    $userPath = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
    if (Test-Path $userPath) {
        $python = $userPath
    } else {
        Write-Host "Python not found. Please install Python 3.10+ from python.org" -ForegroundColor Red
        exit 1
    }
} else {
    $python = "python"
}

# Install dependencies only when requirements changed. This keeps normal startup fast.
$requirements = Join-Path $PSScriptRoot "backend\requirements.txt"
$stampDir = Join-Path $PSScriptRoot ".startup"
$stamp = Join-Path $stampDir "requirements.ok"
if (-not $SkipDeps) {
    $needsInstall = -not (Test-Path $stamp)
    if (-not $needsInstall -and (Test-Path $requirements)) {
        $needsInstall = (Get-Item $requirements).LastWriteTimeUtc -gt (Get-Item $stamp).LastWriteTimeUtc
    }
    if ($needsInstall) {
        Write-Host "Installing/checking Python dependencies..." -ForegroundColor Yellow
        & $python -m pip install -r $requirements -q
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Dependency install failed. Re-run without -SkipDeps after fixing pip/network." -ForegroundColor Red
            exit $LASTEXITCODE
        }
        if (-not (Test-Path $stampDir)) { New-Item -ItemType Directory -Path $stampDir | Out-Null }
        Set-Content -Path $stamp -Value (Get-Date).ToString("o")
    } else {
        Write-Host "Dependencies already checked. Use .\start.ps1 without -SkipDeps after changing requirements." -ForegroundColor DarkGray
    }
} else {
    Write-Host "Skipping dependency check." -ForegroundColor DarkGray
}

# Start the server
Write-Host "Starting server on http://localhost:5000" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop" -ForegroundColor Gray
& $python (Join-Path $PSScriptRoot "backend\app.py")
