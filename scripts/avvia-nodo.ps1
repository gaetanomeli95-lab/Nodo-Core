<#
  NODO CORE - avvio su Windows (un solo processo, un solo indirizzo: http://127.0.0.1:8000)

  Uso:   doppio click su avvia-nodo.bat      (normale)
         doppio click su demo-nodo.bat       (demo simulata, nessun accesso necessario)
  Opzioni: -Demo  -NoBrowser  -Port 8000  -SkipBuild  -PrepareOnly (prepara tutto e basta)  -Tray (icona vicino all'orologio, senza finestra)

  Cosa fa, solo la prima volta o quando serve:
    1. trova Python 3.12+  2. crea apps\api\.venv e installa NODO  3. installa Chromium per i controlli
    4. crea apps\api\.env da .env.example  5. aggiorna il database  6. costruisce il pannello web
  Poi avvia NODO. Il turno di notte parte da solo finche' questa finestra resta aperta.
#>
param([switch]$Demo, [switch]$NoBrowser, [int]$Port = 8000, [switch]$SkipBuild, [switch]$PrepareOnly, [switch]$Tray, [switch]$Console)

$ErrorActionPreference = "Continue"  # native tools write warnings on stderr: check $LASTEXITCODE instead
$Root = Split-Path -Parent $PSScriptRoot
$Api = Join-Path $Root "apps\api"
$Web = Join-Path $Root "apps\web"
$Venv = Join-Path $Api ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Say($msg) { Write-Host "[NODO] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[NODO] $msg" -ForegroundColor Yellow }

# Reopen the unchanged, healthy local instance before dependency checks and migrations.
if ((Test-Path $Py) -and -not $Demo -and -not $PrepareOnly -and -not $Console) {
  Push-Location $Api
  $reuseArgs = @("-m", "nodo.launch", "--port", "$Port")
  if ($NoBrowser) { $reuseArgs += "--no-browser" }
  & $Py @reuseArgs
  $reused = $LASTEXITCODE -eq 0
  Pop-Location
  if ($reused) { exit 0 }
}

# 1. Python 3.12+
function Find-Python {
  foreach ($v in @("3.13", "3.12")) {
    try { $out = & py "-$v" -c "import sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $out) { return $out.Trim() } } catch {}
  }
  try {
    $ok = & python -c "import sys; print(sys.executable if sys.version_info >= (3, 12) else '')" 2>$null
    if ($ok) { return $ok.Trim() }
  } catch {}
  return $null
}

if (-not (Test-Path $Py)) {
  $base = Find-Python
  if (-not $base) {
    Warn "Serve Python 3.12 o piu' recente: https://www.python.org/downloads/ (spunta 'Add python.exe to PATH')."
    Read-Host "Premi Invio per chiudere"; exit 1
  }
  Say "Creo l'ambiente Python ($base)..."
  & $base -m venv $Venv
}

# 2. install / update NODO when pyproject changed
$stamp = Join-Path $Venv ".nodo-installed"
$pyproject = Join-Path $Api "pyproject.toml"
if (-not (Test-Path $stamp) -or ((Get-Item $pyproject).LastWriteTime -gt (Get-Item $stamp).LastWriteTime)) {
  Say "Installo le dipendenze di NODO (qualche minuto la prima volta)..."
  & $Py -m pip install --upgrade pip --quiet
  Push-Location $Api
  & $Py -m pip install -e ".[browser,live,tray]" --quiet
  if ($LASTEXITCODE -ne 0) { Warn "Installo senza i controlli via browser..."; & $Py -m pip install -e ".[live,tray]" --quiet }
  if ($LASTEXITCODE -ne 0) { Warn "Installo solo il minimo..."; & $Py -m pip install -e . --quiet }
  $installOk = $LASTEXITCODE -eq 0
  Pop-Location
  if (-not $installOk) {
    Warn "Installazione non riuscita: controlla gli errori qui sopra e riprova."
    Read-Host "Premi Invio per chiudere"; exit 1
  }
  # 3. Chromium for the browser checks (optional, ~150 MB)
  & $Py -c "import playwright" 2>$null
  if ($LASTEXITCODE -eq 0) {
    Say "Scarico Chromium per vedere anche gli errori JavaScript dei siti..."
    & $Py -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Warn "Chromium non installato: NODO controllera' i siti via HTTP." }
  }
  New-Item -ItemType File -Force -Path $stamp | Out-Null
}

# 4. configuration
$envFile = Join-Path $Api ".env"
if (-not (Test-Path $envFile)) {
  Copy-Item (Join-Path $Root ".env.example") $envFile
  $lines = (Get-Content $envFile) -replace '^NODO_ENV=DEV$', 'NODO_ENV=LOCAL'
  [IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding($false)))  # UTF-8 without BOM
  Warn "Ho creato apps\api\.env: inserisci li' i tuoi accessi (guida: docs\ACCESSI.md), poi riavvia NODO."
}

# 5. database
Push-Location $Api
if ($Demo) {
  $env:NODO_ENV = "DEMO"
  $env:NODO_DATABASE_URL = "sqlite:///./nodo-demo.db"
  if (Test-Path (Join-Path $Api "nodo-demo.db")) { Remove-Item (Join-Path $Api "nodo-demo.db") }
  Say "Modalita' DEMO: siti, deploy e correzioni simulati. Niente esce dal tuo computer."
}
& $Py -m nodo.db.migrate
if ($LASTEXITCODE -ne 0) { Warn "Aggiornamento del database non riuscito (vedi sopra)."; Pop-Location; Read-Host "Premi Invio per chiudere"; exit 1 }
Pop-Location

# 6. web dashboard (rebuilt when sources are newer than the build)
if (-not $SkipBuild) {
  $dist = Join-Path $Web "dist\index.html"
  $sources = @(Get-ChildItem (Join-Path $Web "src"), (Join-Path $Web "public") -Recurse -File)
  $sources += Get-Item (Join-Path $Web "package.json"), (Join-Path $Web "package-lock.json"), (Join-Path $Web "index.html")
  $newest = ($sources | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
  if (-not (Test-Path $dist) -or ((Get-Item $dist).LastWriteTime -lt $newest)) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
      Say "Costruisco il pannello web..."
      Push-Location $Web
      if (-not (Test-Path "node_modules")) { & npm install --no-fund --no-audit }
      & npm run build
      if ($LASTEXITCODE -ne 0) { Warn "Build del pannello non riuscita: uso la versione precedente, se c'e'." }
      Pop-Location
    } else {
      Warn "Node.js non trovato: installa la versione LTS da https://nodejs.org per vedere il pannello aggiornato."
    }
  }
}

if ($PrepareOnly) { Say "Tutto pronto."; exit 0 }

# start: an older NODO still running (also one started in background by a coding agent) keeps the port busy
# and the new version would not start. Stop it first.
# se NODO gira gia' come app (icona vicino all'orologio), chiudi prima l'app: altrimenti rilancerebbe il server.
# Chiudi anche i server rimasti orfani (chiudere l'app con la forza non chiude il suo server).
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'nodo\.tray|nodo\.main:app' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
  Start-Sleep -Seconds 2
  if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Warn "La porta $Port e' occupata da un altro processo: chiudilo oppure avvia NODO con  avvia-nodo.bat -Port 8010"
    Read-Host "Premi Invio per chiudere"; exit 1
  }
}
$url = "http://127.0.0.1:$Port"
# NODO runs as an app (icon by the clock, server supervised in the background, log in apps\api\nodo.log) whenever
# the icon library is installed: no black window to keep open, nothing that dies if that window is closed.
# -Console keeps the old way (server in this window) for debugging.
if (-not $Console -and -not $Demo -and -not $Tray) {
  & $Py -c "import pystray" 2>$null
  if ($LASTEXITCODE -eq 0) { $Tray = $true }
}
if ($Tray) {
  # app vera: nessuna finestra nera, icona vicino all'orologio (verde = acceso), riparte da sola se cade
  $pyw = Join-Path $Venv "Scripts\pythonw.exe"
  $trayArgs = @("-m", "nodo.tray", "--port", "$Port")
  if ($NoBrowser) { $trayArgs += "--no-browser" }
  Start-Process -FilePath $pyw -ArgumentList $trayArgs -WorkingDirectory $Api
  Say "NODO si sta accendendo: tra pochi secondi si apre da solo. L'icona N vicino all'orologio lo apre o lo spegne."
  exit 0
}
Say "NODO e' acceso su $url  (chiudi questa finestra per spegnerlo)"
if (-not $NoBrowser) {
  # opens the moment NODO answers (not after a fixed wait), as an app window with its own profile: sound and the
  # greeting work at once even if Edge is already open, and the microphone permission is remembered
  Start-Job -ScriptBlock {
    param($u)
    for ($i = 0; $i -lt 120; $i++) {
      try { Invoke-WebRequest "$u/health" -UseBasicParsing -TimeoutSec 1 | Out-Null; break } catch { Start-Sleep -Milliseconds 400 }
    }
    $prof = Join-Path $env:LOCALAPPDATA "NODO\finestra"
    $browsers = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
                  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
    $exe = $browsers | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($exe) {
      Start-Process $exe -ArgumentList @("--app=$u", "--autoplay-policy=no-user-gesture-required", "--user-data-dir=$prof",
                                        "--no-first-run", "--no-default-browser-check", "--window-size=1440,900")
    } else { Start-Process $u }
  } -ArgumentList $url | Out-Null
}
Push-Location $Api
# il log resta anche in apps/api/nodo.log: utile per capire cosa e' successo dopo un errore
$log = Join-Path $Api "nodo.log"
try { $fs = [IO.File]::Open($log, 'Append', 'Write', 'ReadWrite'); $fs.Close() }
catch { $log = Join-Path $Api ("nodo-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log") }  # nodo.log locked by another program
# se NODO si ferma da solo (errore imprevisto) riparte dopo 10 secondi: di notte nessuno può riavviarlo
while ($true) {
  # plain file redirection by cmd: UTF-8 as Python writes it, and nothing between NODO and the file that can stall
  & cmd /c "`"$Py`" -m uvicorn nodo.main:app --host 127.0.0.1 --port $Port >> `"$log`" 2>&1"
  Warn "NODO si e' fermato (codice $LASTEXITCODE). Riparto tra 10 secondi... (chiudi la finestra per spegnerlo)"
  Start-Sleep -Seconds 10
}
Pop-Location
