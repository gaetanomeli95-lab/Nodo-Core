<#
NODO Core local speech setup (Windows x64, Python 3.12+).
Explicit one-time install + model downloads. Never changes Gemini/Claude/OpenAI tokens.
Execute from the repository by double-clicking installa-voce-locale.bat.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Api = Join-Path $Root "apps\api"
$Py = Join-Path $Api ".venv\Scripts\python.exe"
$EnvPath = Join-Path $Api ".env"
$Assets = Join-Path $Api ".local-voice"
$Whisper = Join-Path $Assets "whisper\base"
$Piper = Join-Path $Assets "piper"

function Step($Message) { Write-Host "[NODO VOCE] $Message" -ForegroundColor Cyan }
function CheckExit($StepName) {
  if ($LASTEXITCODE -ne 0) { throw "$StepName non riuscito (codice $LASTEXITCODE)." }
}
function SetEnvValue([string]$Text, [string]$Name, [string]$Value) {
  $line = "$Name=$Value"
  if ($Text -match "(?m)^$([regex]::Escape($Name))=") {
    return [regex]::Replace($Text, "(?m)^$([regex]::Escape($Name))=.*$", $line)
  }
  return ($Text.TrimEnd() + "`r`n" + $line + "`r`n")
}

try {
  if (-not (Test-Path $Py)) {
    throw "Non trovo Python di NODO. Avvia prima avvia-nodo.bat per creare apps/api/.venv."
  }
  Step "Installo i motori vocali offline nell'ambiente esistente (nessuna API key richiesta)."
  Push-Location $Api
  try {
    & $Py -m pip install -e ".[local-voice]"
    CheckExit "Installazione Python"
    New-Item -ItemType Directory -Force -Path $Whisper, $Piper | Out-Null
    Step "Scarico il modello Whisper multilingua base (una volta sola)."
    if (-not (Test-Path (Join-Path $Whisper "model.bin"))) {
      & $Py -c "import sys; from faster_whisper import download_model; download_model('base', output_dir=sys.argv[1])" $Whisper
      CheckExit "Download Whisper"
    }
    Step "Scarico Piper italiano Paola medium (una volta sola)."
    if (-not (Test-Path (Join-Path $Piper "it_IT-paola-medium.onnx")) -or
        -not (Test-Path (Join-Path $Piper "it_IT-paola-medium.onnx.json"))) {
      & $Py -m piper.download_voices --data-dir $Piper it_IT-paola-medium
      CheckExit "Download Piper"
    }
  } finally { Pop-Location }

  if (-not (Test-Path $EnvPath)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvPath
  }
  $text = [IO.File]::ReadAllText($EnvPath)
  # This is a deliberate local speech choice: leave the Gemini key stored but stop using Gemini Live.
  $text = SetEnvValue $text "NODO_VOICE_ENGINE" "browser"
  $text = SetEnvValue $text "NODO_STT_PROVIDER" "local"
  $text = SetEnvValue $text "NODO_TTS_PROVIDER" "local"
  $text = SetEnvValue $text "NODO_LOCAL_VOICE_DIR" ".local-voice"
  $text = SetEnvValue $text "NODO_WHISPER_MODEL" "base"
  $text = SetEnvValue $text "NODO_WHISPER_DEVICE" "cpu"
  $text = SetEnvValue $text "NODO_WHISPER_COMPUTE_TYPE" "int8"
  $text = SetEnvValue $text "NODO_PIPER_VOICE" "it_IT-paola-medium"
  [IO.File]::WriteAllText($EnvPath, $text, (New-Object System.Text.UTF8Encoding($false)))

  Step "Installazione completata. Chiudi e riapri NODO con avvia-nodo.bat."
  Step "In Accessi/Voce controlla che STT=faster-whisper e TTS=piper risultino pronti."
  Step "Questa installazione e' offline DURANTE L'USO della voce; il cervello puo' usare modelli cloud."
} catch {
  Write-Host "[NODO VOCE] ERRORE: $_" -ForegroundColor Red
  Write-Host "Il database non e' stato modificato. Controlla i messaggi sopra."
} finally {
  Read-Host "Premi Invio per chiudere"
}
