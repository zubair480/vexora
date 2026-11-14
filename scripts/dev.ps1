# BeltGuard dev launcher: backend (uvicorn, port 8010) and dashboard (Vite, port 5173),
# each in its own PowerShell window.
#
#     .\scripts\dev.ps1
#
# This script never sets, reads or guesses a band number. The band number lives only in
# VEXO_BAND in your .env (see README.md, "Device setup"). Nothing here connects to a band.

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$backendPort = 8010
$frontendPort = 5173

function Test-Tool($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

if (-not (Test-Tool 'python')) {
    Write-Host 'Python was not found on PATH. Install Python 3.12, then run this again.' -ForegroundColor Red
    exit 1
}
if (-not (Test-Tool 'npm')) {
    Write-Host 'npm was not found on PATH. Install Node.js (it includes npm), then run this again.' -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $root 'backend\main.py'))) {
    Write-Host "backend\main.py was not found under $root. Run this script from the BeltGuard repo." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $root 'app\node_modules'))) {
    Write-Host 'Frontend packages are not installed. Run:  npm --prefix app install' -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $root '.env'))) {
    Write-Host 'Note: no .env file. Copy .env.example to .env and fill it in yourself (README.md).' -ForegroundColor Yellow
    Write-Host '      The server still starts; band, speech and AI calls will say what is missing.' -ForegroundColor Yellow
}

$backend = "`$Host.UI.RawUI.WindowTitle = 'BeltGuard backend :$backendPort'; python -m uvicorn backend.main:app --port $backendPort"
$frontend = "`$Host.UI.RawUI.WindowTitle = 'BeltGuard dashboard :$frontendPort'; npm --prefix app run dev -- --port $frontendPort --strictPort"

Start-Process powershell -WorkingDirectory $root -ArgumentList '-NoExit', '-Command', $backend
Start-Process powershell -WorkingDirectory $root -ArgumentList '-NoExit', '-Command', $frontend

Write-Host "Backend:   http://127.0.0.1:$backendPort  (API list: http://127.0.0.1:$backendPort/docs)"
Write-Host "Dashboard: http://localhost:$frontendPort"
Write-Host 'Close the two windows to stop them.'
