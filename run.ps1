# Vexora: start the server and open the three pages (Windows PowerShell, Python 3.11+, Chrome).
#   powershell -ExecutionPolicy Bypass -File .\run.ps1
# Don't run this while a Vexora server is already running on port 8765.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = if (Test-Path "$root\vexora\backend\app.py") { "$root\vexora" } else { "$root\beltguard" }
Set-Location $app

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created $app\.env. Put your Deepgram key in it (DEEPGRAM_API_KEY=...) for the voice features."
}
Write-Host "Installing Python packages..."
python -m pip install -r backend\requirements.txt --quiet

Write-Host "Starting the Vexora server on http://127.0.0.1:8765 (it opens in its own window)..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$app'; python -u backend\app.py"

$up = $false
for ($i = 0; $i -lt 40 -and -not $up; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:8765/api/health" -TimeoutSec 1 | Out-Null; $up = $true } catch { Start-Sleep -Seconds 1 }
}
if (-not $up) { Write-Host "The server did not answer on port 8765. Check its window for the error."; exit 1 }

Start-Process "chrome" "http://127.0.0.1:8765/bridge.html"        # Band link: holds band 07 + the mic
Start-Process "chrome" "http://127.0.0.1:8765/"                   # technician dashboard
Start-Process "chrome" "http://127.0.0.1:8765/supervisor.html"    # supervisor control room

Write-Host ""
Write-Host "Next:"
Write-Host " 1. Band link tab: click 'Connect band 07' and pick Vexo-HACK-07. Keep exactly ONE Band link tab open."
Write-Host " 2. Dashboard: Reset shift, then Start, pick a tool, then Start using the tool."
Write-Host " 3. Talk: 'Conveyor's isolated and locked out', 'Guard's off', 'New idler fitted', 'Job done'."
Write-Host " 4. 'Vexora, idler change needed at station three' drafts a work order + PDF for the control room."
Write-Host " 5. Control room: Work orders card, then Open PDF or Run work order (the flow page is /workorder)."
