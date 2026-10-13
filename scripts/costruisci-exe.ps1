<#
  Costruisce NODO.exe (il lanciatore dell'app) nella cartella di NODO.
  Uso: doppio click su costruisci-exe.bat   (serve aver fatto prima crea-app-nodo.bat)
  NODO.exe non contiene NODO: avvia quello installato qui, quindi non va ricostruito a ogni aggiornamento.
#>
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Api = Join-Path $Root "apps\api"
$Py = Join-Path $Api ".venv\Scripts\python.exe"
function Say($m) { Write-Host "[NODO] $m" -ForegroundColor Cyan }
if (-not (Test-Path $Py)) { Write-Host "[NODO] Prima fai doppio click su crea-app-nodo.bat." -ForegroundColor Yellow; exit 1 }
$ico = Join-Path $Api "nodo.ico"
Push-Location $Api
if (-not (Test-Path $ico)) { & $Py -m nodo.tray --make-icon $ico }
Pop-Location
Say "Installo lo strumento di costruzione (PyInstaller)..."
& $Py -m pip install pyinstaller --quiet
$work = Join-Path $env:TEMP "nodo-exe-build"
$args = @("--onefile", "--noconsole", "--name", "NODO", "--distpath", $Root, "--workpath", $work, "--specpath", $work)
if (Test-Path $ico) { $args += @("--icon", $ico) }
$args += (Join-Path $PSScriptRoot "nodo_exe.py")
& $Py -m PyInstaller @args
if (Test-Path (Join-Path $Root "NODO.exe")) {
  Say "Fatto: NODO.exe e' nella cartella di NODO. Doppio click per accenderlo; tasto destro -> Aggiungi alla barra delle applicazioni."
} else {
  Write-Host "[NODO] Non sono riuscito a costruire NODO.exe (vedi sopra). La scorciatoia sul Desktop funziona lo stesso." -ForegroundColor Yellow
}
