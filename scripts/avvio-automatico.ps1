<#
  Fa partire NODO da solo quando accedi a Windows.
  Se NODO e' gia' installato come app (crea-app-nodo.bat) parte come icona vicino all'orologio, senza finestre;
  altrimenti parte in una finestra ridotta a icona.
  Installa:   powershell -ExecutionPolicy Bypass -File scripts\avvio-automatico.ps1
  Rimuovi:    powershell -ExecutionPolicy Bypass -File scripts\avvio-automatico.ps1 -Rimuovi
#>
param([switch]$Rimuovi)
$ErrorActionPreference = "Stop"
$Name = "NODO Core"
if ($Rimuovi) {
  Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "[NODO] Avvio automatico rimosso." -ForegroundColor Cyan
  exit 0
}
$Root = Split-Path -Parent $PSScriptRoot
$Api = Join-Path $Root "apps\api"
$Pyw = Join-Path $Api ".venv\Scripts\pythonw.exe"
function Has-Tray {
  if (-not (Test-Path $Pyw)) { return $false }
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  & (Join-Path $Api ".venv\Scripts\python.exe") -c "import pystray" 2>$null
  $ok = ($LASTEXITCODE -eq 0)
  $ErrorActionPreference = $old
  return $ok
}
if (Has-Tray) {
  $action = New-ScheduledTaskAction -Execute $Pyw -Argument "-m nodo.tray --no-browser" -WorkingDirectory $Api
  $how = "come app (icona vicino all'orologio)"
} else {
  $script = Join-Path $PSScriptRoot "avvia-nodo.ps1"
  $action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`" -NoBrowser"
  $how = "in una finestra ridotta a icona (per l'app senza finestre: crea-app-nodo.bat)"
}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0) -StartWhenAvailable
Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Description "Avvia NODO Core (turno di notte) all'accesso" -Force | Out-Null
Write-Host "[NODO] Fatto: NODO partira' da solo ogni volta che accedi a Windows, $how." -ForegroundColor Cyan
Write-Host "[NODO] Ricorda: il PC non deve andare in sospensione di notte (NODO lo tiene sveglio solo mentre e' acceso)."
