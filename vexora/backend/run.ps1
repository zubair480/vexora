# Start the BeltGuard backend on http://127.0.0.1:8765
#   .\backend\run.ps1          real band 07 over Bluetooth (close the kit dashboard tab first)
#   .\backend\run.ps1 -Sim     the kit's pretend band (everything labelled SIMULATED)
param([switch]$Sim, [int]$Port = 8765)
Set-Location (Join-Path $PSScriptRoot "..")
$cmd = @("backend\app.py", "--port", $Port)
if ($Sim) { $cmd += "--sim" }
python -u @cmd
