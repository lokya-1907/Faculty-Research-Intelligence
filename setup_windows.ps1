$ErrorActionPreference = 'Stop'
Write-Host 'ResearchPulse - Windows setup' -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python was not found. Install Python 3.11+ and ensure it is on PATH.' }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js was not found. Install Node.js 20+ and ensure it is on PATH.' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'npm was not found. Reinstall Node.js 20+ with npm included.' }

if (-not (Test-Path '.\backend\.venv\Scripts\python.exe')) {
    python -m venv .\backend\.venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python virtual environment.' }
}

& .\backend\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Python package upgrade failed. Check your internet connection and try again.' }

& .\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed. The frontend was not started.' }

if (-not (Test-Path '.\backend\.env')) {
    Copy-Item '.\backend\.env.example' '.\backend\.env'
    Write-Host 'Created backend\.env from backend\.env.example' -ForegroundColor Yellow
}

Push-Location .\frontend
try {
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend npm install failed. Check the npm error above; no setup-complete message will be shown.' }
} finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Setup complete. Run .\start_windows.ps1' -ForegroundColor Green
