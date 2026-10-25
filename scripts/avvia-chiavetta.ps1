<#
  NODO CORE - avvio dalla chiavetta USB, senza installare niente sul PC.

  Uso:   doppio click su avvia-da-chiavetta.bat  (dalla cartella di NODO sulla chiavetta)
  Opzioni: -NoBrowser  -Port 8000  -PrepareOnly (prepara la chiavetta e basta)  -NoChromium

  Tutto resta nella cartella runtime\ accanto a NODO:
    runtime\python         Python 3.12 portatile con le librerie di NODO (niente venv: funziona con ogni lettera di unita')
    runtime\ms-playwright  Chromium per i controlli dei siti
    runtime\finestra       profilo della finestra di NODO (permesso del microfono compreso)
  Database, file e accessi (apps\api\nodo.db, apps\api\files, apps\api\.env) sono quelli di sempre, sulla chiavetta.
  Il pannello web e' gia' costruito (apps\web\dist): Node.js non serve.
  La prima volta scarica Python (~30 MB), le librerie e Chromium (~150 MB): serve internet, qualche minuto.
  Sul PC non scrive niente: niente scorciatoie, niente avvio automatico; anche i file temporanei vanno in runtime\temp.
  La prima volta su ogni PC chiede il permesso all'utente (ricordato in runtime\pc-autorizzati.txt).
#>
param([switch]$NoBrowser, [int]$Port = 8000, [switch]$PrepareOnly, [switch]$NoChromium)

$ErrorActionPreference = "Continue"  # native tools write warnings on stderr: check $LASTEXITCODE instead
$ProgressPreference = "SilentlyContinue"  # the progress bar makes Invoke-WebRequest many times slower
$Root = Split-Path -Parent $PSScriptRoot
$Api = Join-Path $Root "apps\api"
$Runtime = Join-Path $Root "runtime"
$PyDir = Join-Path $Runtime "python"
$Py = Join-Path $PyDir "python.exe"
$Pyw = Join-Path $PyDir "pythonw.exe"
$PyVersion = "3.12.10"

function Say($msg) { Write-Host "[NODO] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[NODO] $msg" -ForegroundColor Yellow }
function Fail($msg) { Warn $msg; Read-Host "Premi Invio per chiudere"; exit 1 }

# inherited by the tray and by the server it supervises: what NODO writes stays on the stick, not on the PC
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Runtime "ms-playwright"
$env:NODO_PROFILE_DIR = Join-Path $Runtime "finestra"
$env:TEMP = $env:TMP = Join-Path $Runtime "temp"
$env:PIP_NO_CACHE_DIR = "1"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
New-Item -ItemType Directory -Force -Path $Runtime, $env:TEMP | Out-Null

# NODO uses this PC (its folders, its microphone, its night) only after its user says yes, once per PC
$allowed = Join-Path $Runtime "pc-autorizzati.txt"
$pcId = "$env:COMPUTERNAME\$env:USERNAME"
if (-not ((Test-Path $allowed) -and ((Get-Content $allowed) -contains $pcId))) {
  Add-Type -AssemblyName System.Windows.Forms
  $msg = "Avviare NODO su questo PC ($pcId)?`n`n" +
         "Sul PC non viene installato niente: NODO gira dalla chiavetta e i suoi dati restano li'.`n" +
         "Finche' e' acceso usa questo PC: cerca i file in Desktop, Documenti e Download, usa il microfono " +
         "se glielo permetti e di notte tiene il PC sveglio per il turno di notte.`n`n" +
         "La risposta viene ricordata sulla chiavetta per questo PC."
  $answer = [System.Windows.Forms.MessageBox]::Show($msg, "NODO", "YesNo", "Question")
  if ($answer -ne "Yes") { Say "NODO non e' stato avviato su questo PC."; exit 0 }
  Add-Content -Path $allowed -Value $pcId
}

# Reopen the unchanged, healthy instance before any check.
$stamp = Join-Path $Runtime ".nodo-installed"
if ((Test-Path $stamp) -and -not $PrepareOnly) {
  Push-Location $Api
  $reuseArgs = @("-m", "nodo.launch", "--port", "$Port")
  if ($NoBrowser) { $reuseArgs += "--no-browser" }
  & $Py @reuseArgs
  $reused = $LASTEXITCODE -eq 0
  Pop-Location
  if ($reused) { exit 0 }
}

# 1. portable Python (the official "embeddable" build from python.org)
if (-not (Test-Path $Py)) {
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  $zip = Join-Path $Runtime "python-embed.zip"
  Say "Scarico Python $PyVersion portatile da python.org..."
  try { Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" -OutFile $zip -UseBasicParsing }
  catch { Fail "Download di Python non riuscito: serve internet la prima volta. ($($_.Exception.Message))" }
  Expand-Archive -Path $zip -DestinationPath $PyDir -Force
  Remove-Item $zip -Force
  # the ._pth file is the whole sys.path of the embeddable build: relative paths, so any drive letter works;
  # apps\api on the path lets "python -m nodo.*" find NODO without installing it
  $pth = Get-ChildItem $PyDir -Filter "python3*._pth" | Select-Object -First 1
  $zipName = (Get-ChildItem $PyDir -Filter "python3*.zip" | Select-Object -First 1).Name
  [IO.File]::WriteAllLines($pth.FullName, @($zipName, ".", "Lib\site-packages", "..\..\apps\api", "import site"),
                           (New-Object System.Text.UTF8Encoding($false)))
  Say "Installo pip..."
  $getPip = Join-Path $Runtime "get-pip.py"
  try { Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing }
  catch { Fail "Download di pip non riuscito. ($($_.Exception.Message))" }
  & $Py $getPip --no-warn-script-location --quiet
  if ($LASTEXITCODE -ne 0) { Fail "Installazione di pip non riuscita (vedi sopra)." }
  Remove-Item $getPip -Force
}

# 2. NODO's libraries, when pyproject changed. Only the dependencies: the code stays in apps\api and runs from there.
$pyproject = Join-Path $Api "pyproject.toml"
if (-not (Test-Path $stamp) -or ((Get-Item $pyproject).LastWriteTime -gt (Get-Item $stamp).LastWriteTime)) {
  Say "Installo le librerie di NODO sulla chiavetta (qualche minuto la prima volta)..."
  $req = Join-Path $Runtime "requisiti.txt"
  $ok = $false
  $all = "browser,live,tray"
  if (Test-Path (Join-Path $Api ".local-voice")) { $all += ",local-voice" }  # installa-voce-locale.bat was used
  foreach ($extras in @($all, "browser,live,tray", "live,tray", "")) {
    $code = "import sys, tomllib; p = tomllib.load(open(sys.argv[1], 'rb'))['project']; " +
            "print('\n'.join(p['dependencies'] + [d for e in sys.argv[2].split(',') if e for d in p['optional-dependencies'][e]]))"
    & $Py -c $code $pyproject $extras | Set-Content -Path $req -Encoding ascii
    & $Py -m pip install -r $req --no-warn-script-location --quiet
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Warn "Installazione non riuscita, riprovo con meno extra..."
  }
  Remove-Item $req -Force -ErrorAction SilentlyContinue
  if (-not $ok) { Fail "Installazione delle librerie non riuscita: controlla gli errori qui sopra e riprova." }
  # 3. Chromium for the browser checks (optional, ~150 MB), into runtime\ms-playwright
  if (-not $NoChromium) {
    & $Py -c "import playwright" 2>$null
    if ($LASTEXITCODE -eq 0) {
      Say "Scarico Chromium sulla chiavetta per vedere anche gli errori JavaScript dei siti..."
      & $Py -m playwright install chromium
      if ($LASTEXITCODE -ne 0) { Warn "Chromium non installato: NODO controllera' i siti via HTTP." }
    }
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
& $Py -m nodo.db.migrate
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Aggiornamento del database non riuscito (vedi sopra)." }
Pop-Location

if (-not (Test-Path (Join-Path $Root "apps\web\dist\index.html"))) {
  Warn "Manca il pannello web (apps\web\dist): costruiscilo una volta su un PC con Node.js (avvia-nodo.bat)."
}
if ($PrepareOnly) { Say "Chiavetta pronta."; exit 0 }

# 6. start: an older NODO (from the PC or from the stick) keeps the port busy and the new one would not start
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'nodo\.tray|nodo\.main:app' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  Start-Sleep -Seconds 2
  if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Fail "La porta $Port e' occupata da un altro processo: chiudilo oppure avvia con  avvia-da-chiavetta.bat -Port 8010"
  }
}
# as an app: no black window, icon N by the clock, server restarted if it falls, log in apps\api\nodo.log
$trayArgs = @("-m", "nodo.tray", "--port", "$Port")
if ($NoBrowser) { $trayArgs += "--no-browser" }
Start-Process -FilePath $Pyw -ArgumentList $trayArgs -WorkingDirectory $Api
Say "NODO si sta accendendo dalla chiavetta: tra pochi secondi si apre da solo."
Say "Prima di togliere la chiavetta spegni NODO (icona N vicino all'orologio > Esci): il database e' li' sopra."
