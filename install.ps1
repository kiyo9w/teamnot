#requires -Version 5.1
<#
.SYNOPSIS
    TeamNoT one-shot installer for Windows.

.DESCRIPTION
    Creates a .venv next to this script, installs TeamNoT in editable mode,
    and verifies the install by running `teamnot doctor`. Idempotent — re-running
    upgrades the install in place.

.PARAMETER WithTelegram
    Also install the [telegram] extra (aiogram).

.PARAMETER WithHttp
    Also install the [http] extra (FastAPI + uvicorn).

.PARAMETER All
    Install every optional extra.

.PARAMETER Dev
    Install dev tools (pytest, ruff, mypy).

.PARAMETER Python
    Override the Python executable used to create the venv. Defaults to `py -3` if
    present, otherwise `python`.

.EXAMPLE
    .\install.ps1                  # core only
    .\install.ps1 -WithTelegram    # core + Telegram gateway
    .\install.ps1 -All -Dev        # everything
#>
[CmdletBinding()]
param(
    [switch]$WithTelegram,
    [switch]$WithHttp,
    [switch]$All,
    [switch]$Dev,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Resolve-Python {
    if ($Python) { return $Python }
    # Prefer the launcher; fall back to plain python
    if (Get-Command "py" -ErrorAction SilentlyContinue) { return "py -3" }
    if (Get-Command "python" -ErrorAction SilentlyContinue) { return "python" }
    throw "No Python 3 found. Install Python 3.11+ from https://python.org and re-run."
}

$pyCmd = Resolve-Python
Write-Host "[teamnot] Using Python: $pyCmd" -ForegroundColor Cyan

# Verify version >= 3.11
$verCheck = & cmd /c "$pyCmd -c `"import sys; print(sys.version_info[:3])`" 2>&1"
Write-Host "[teamnot] $verCheck"

# Create venv
$venv = Join-Path $root ".venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "[teamnot] Creating venv at $venv" -ForegroundColor Cyan
    & cmd /c "$pyCmd -m venv `"$venv`""
} else {
    Write-Host "[teamnot] Reusing existing venv at $venv" -ForegroundColor Yellow
}

$venvPy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    throw "venv creation failed — $venvPy not found"
}

# Upgrade pip
& $venvPy -m pip install --upgrade pip wheel setuptools | Out-Null

# Build extras list
$extras = @()
if ($All -or $WithTelegram) { $extras += "telegram" }
if ($All -or $WithHttp)     { $extras += "http" }
if ($Dev)                   { $extras += "dev" }

$spec = "."
if ($extras.Count -gt 0) {
    $spec = ".[$($extras -join ',')]"
}

Write-Host "[teamnot] Installing $spec" -ForegroundColor Cyan
& $venvPy -m pip install -e $spec
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed"
}

# Activate hint
Write-Host ""
Write-Host "[teamnot] Install complete." -ForegroundColor Green
Write-Host ""
Write-Host "Activate the venv in your shell:"
Write-Host "    .\.venv\Scripts\Activate.ps1"
Write-Host ""
Write-Host "Then verify:"
Write-Host "    teamnot doctor"
Write-Host "    teamnot --help"
Write-Host ""

# Run doctor inside the venv as a final check (non-fatal — if it fails, the user
# still has a working install, just with optional pieces missing).
Write-Host "[teamnot] Running doctor inside the venv..." -ForegroundColor Cyan
& $venvPy -m teamnot.cli doctor
if ($LASTEXITCODE -ne 0) {
    Write-Host "[teamnot] Doctor reported missing optional pieces — see above." -ForegroundColor Yellow
}
