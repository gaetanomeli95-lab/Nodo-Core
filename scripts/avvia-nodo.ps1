<#
  NODO CORE - avvio su Windows (un solo processo, un solo indirizzo: http://127.0.0.1:8000)

  Uso:   doppio click su avvia-nodo.bat      (normale)
         doppio click su demo-nodo.bat       (demo simulata, nessun accesso necessario)
  Opzioni: -Demo  -NoBrowser  -Port 8000  -SkipBuild

  Cosa fa, solo la prima volta o quando serve:
    1. trova Python 3.12+  2. crea apps\api\.venv e installa NODO  3. installa Chromium per i controlli
    4. crea apps\api\.env da .env.example  5. aggiorna il database  6. costruisce il pannello web
  Poi avvia NODO. Il turno di notte parte da solo finche' questa finestra resta aperta.
#>
param([switch]$Demo, [switch]$NoBrowser, [int]$Port = 8000, [switch]$SkipBuild)

$ErrorActionPreference = "Continue"  # native tools write warnings on stderr: check $LASTEXITCODE instead
$Root = Split-Path -Parent $PSScriptRoot
$Api = Join-Path $Root "apps\api"
$Web = Join-Path $Root "apps\web"
$Venv = Join-Path $Api ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Say($msg) { Write-Host "[NODO] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[NODO] $msg" -ForegroundColor Yellow }

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
  & $Py -m pip install -e ".[browser,live]" --quiet
  if ($LASTEXITCODE -ne 0) { Warn "Installo senza i controlli via browser..."; & $Py -m pip install -e . --quiet }
  Pop-Location
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
  $newest = (Get-ChildItem (Join-Path $Web "src") -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
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

# start
$url = "http://127.0.0.1:$Port"
Say "NODO e' acceso su $url  (chiudi questa finestra per spegnerlo)"
if (-not $NoBrowser) { Start-Job -ScriptBlock { param($u) Start-Sleep 4; Start-Process $u } -ArgumentList $url | Out-Null }
Push-Location $Api
# il log resta anche in apps/api/nodo.log: utile per capire cosa e' successo dopo un errore
& $Py -m uvicorn nodo.main:app --host 127.0.0.1 --port $Port 2>&1 | Tee-Object -FilePath (Join-Path $Api "nodo.log")
Pop-Location
