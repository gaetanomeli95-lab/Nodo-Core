<#
  Fa partire NODO da solo quando accedi a Windows (in una finestra ridotta a icona).
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
$script = Join-Path $PSScriptRoot "avvia-nodo.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`" -NoBrowser"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0) -StartWhenAvailable
Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Description "Avvia NODO Core (turno di notte) all'accesso" -Force | Out-Null
Write-Host "[NODO] Fatto: NODO partira' da solo ogni volta che accedi a Windows." -ForegroundColor Cyan
Write-Host "[NODO] Ricorda: il PC non deve andare in sospensione di notte (NODO lo tiene sveglio solo mentre e' acceso)."
