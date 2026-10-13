<#
  NODO CORE - trasforma NODO in una vera app per Windows.

  Uso:  doppio click su crea-app-nodo.bat

  Cosa fa:
    1. prepara tutto (Python, dipendenze, database, pannello) come avvia-nodo.bat
    2. crea l'icona e la scorciatoia "NODO" sul Desktop e nel menu Start
    3. fa partire NODO da solo ad ogni accesso a Windows, SENZA finestre nere (solo l'icona N vicino all'orologio)
    4. accende NODO adesso
  Per togliere tutto:  powershell -ExecutionPolicy Bypass -File scripts\crea-app.ps1 -Rimuovi
#>
param([switch]$Rimuovi, [int]$Port = 8000)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Api = Join-Path $Root "apps\api"
$Pyw = Join-Path $Api ".venv\Scripts\pythonw.exe"
$Ico = Join-Path $Api "nodo.ico"
$Task = "NODO Core"
$desktop = [Environment]::GetFolderPath("Desktop")
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "NODO.lnk"
$desktopLnk = Join-Path $desktop "NODO.lnk"
function Say($m) { Write-Host "[NODO] $m" -ForegroundColor Cyan }

if ($Rimuovi) {
  Unregister-ScheduledTask -TaskName $Task -Confirm:$false -ErrorAction SilentlyContinue
  Remove-Item $desktopLnk, $startMenu -Force -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'nodo\.tray' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Say "Fatto: scorciatoie e avvio automatico rimossi, NODO spento."
  exit 0
}

Say "Preparo NODO (la prima volta ci vuole qualche minuto)..."
& (Join-Path $PSScriptRoot "avvia-nodo.ps1") -PrepareOnly -NoBrowser -Port $Port
if ($LASTEXITCODE -ne 0) { Write-Host "[NODO] Preparazione non riuscita." -ForegroundColor Yellow; exit 1 }
if (-not (Test-Path $Pyw)) { Write-Host "[NODO] Non trovo l'ambiente Python (apps\api\.venv)." -ForegroundColor Yellow; exit 1 }

Push-Location $Api
& (Join-Path $Api ".venv\Scripts\python.exe") -m nodo.tray --make-icon $Ico
Pop-Location
if (-not (Test-Path $Ico)) { Write-Host "[NODO] Icona non creata (manca 'pillow'?): le scorciatoie useranno l'icona di Python." -ForegroundColor Yellow }

$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in @($desktopLnk, $startMenu)) {
  $s = $shell.CreateShortcut($lnk)
  $s.TargetPath = $Pyw
  $s.Arguments = "-m nodo.tray --port $Port"
  $s.WorkingDirectory = $Api
  $s.Description = "NODO Core - il tuo assistente"
  if (Test-Path $Ico) { $s.IconLocation = $Ico }
  $s.Save()
}
Say "Scorciatoia NODO creata sul Desktop e nel menu Start."

$action = New-ScheduledTaskAction -Execute $Pyw -Argument "-m nodo.tray --port $Port --no-browser" -WorkingDirectory $Api
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0) -StartWhenAvailable
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $settings -Description "Avvia NODO Core all'accesso (icona vicino all'orologio)" -Force | Out-Null
Say "NODO partira' da solo ad ogni accesso a Windows, senza finestre."

# se c'era gia' un NODO acceso (finestra o app), lo si sostituisce con l'app
& (Join-Path $PSScriptRoot "avvia-nodo.ps1") -Tray -SkipBuild -Port $Port
Say "Fatto. Per aprire NODO: icona N vicino all'orologio, oppure la scorciatoia sul Desktop."
Say "Ricorda: di notte il PC non deve andare in sospensione (NODO lo tiene sveglio da solo mentre e' acceso)."
