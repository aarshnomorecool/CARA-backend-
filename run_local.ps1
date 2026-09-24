# Starts CARA's fully local stack (2026-09-24):
#   1. PostgreSQL 17 from %USERPROFILE%\cara-postgres (portable EDB binaries,
#      localhost-only, trust auth) - started only if it isn't already running.
#   2. The FastAPI backend on 0.0.0.0:8000 so the phone can reach it over the
#      LAN / hotspot.
#
# Usage (from the backend folder):  .\run_local.ps1
# .env must point DATABASE_URL at postgresql+psycopg://postgres@localhost:5432/cara
param(
    [string]$Python = ""
)

$pgRoot = Join-Path $env:USERPROFILE "cara-postgres"
$pgCtl = Join-Path $pgRoot "pgsql\bin\pg_ctl.exe"
$pgData = Join-Path $pgRoot "data"

if (-not (Test-Path $pgCtl)) {
    Write-Error "Local Postgres not found at $pgRoot - see README.md 'Running locally'."
    exit 1
}

& $pgCtl -D $pgData status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Starting local Postgres..."
    & $pgCtl -D $pgData -l (Join-Path $pgRoot "server.log") -w start
} else {
    Write-Host "Postgres already running."
}

if (-not $Python) {
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path $venvPython) { $venvPython } else { "python" }
}

$ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress
Write-Host ""
Write-Host "Backend will be reachable at:" -ForegroundColor Cyan
foreach ($ip in $ips) { Write-Host "  http://${ip}:8000/" -ForegroundColor Cyan }
Write-Host "Put the right one in CARA-android/local.properties as CARA_BASE_URL, then rebuild."
Write-Host ""

Set-Location $PSScriptRoot
& $Python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
