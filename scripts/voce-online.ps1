<#
Rimette la voce "dal vivo": Gemini ascolta e parla, Claude pensa (pulsante Veloce).
Annulla la scelta fatta da installa-voce-locale.bat (Whisper/Piper sul PC), utile sui PC con poca memoria.
Cambia SOLO tre righe di apps/api/.env; le chiavi non vengono lette ne' mostrate.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $Root "apps\api\.env"
function SetEnvValue([string]$Text, [string]$Name, [string]$Value) {
  $line = "$Name=$Value"
  if ($Text -match "(?m)^$([regex]::Escape($Name))=") {
    return [regex]::Replace($Text, "(?m)^$([regex]::Escape($Name))=.*$", $line)
  }
  return ($Text.TrimEnd() + "`r`n" + $line + "`r`n")
}
try {
  if (-not (Test-Path $EnvPath)) { throw "Non trovo apps\api\.env: avvia prima avvia-nodo.bat." }
  $text = [IO.File]::ReadAllText($EnvPath)
  $text = SetEnvValue $text "NODO_VOICE_ENGINE" "auto"
  $text = SetEnvValue $text "NODO_STT_PROVIDER" "auto"
  $text = SetEnvValue $text "NODO_TTS_PROVIDER" "auto"
  [IO.File]::WriteAllText($EnvPath, $text, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "[NODO VOCE] Fatto: voce dal vivo (Gemini ascolta e parla, Claude pensa)." -ForegroundColor Cyan
  Write-Host "Chiudi NODO e riaprilo con avvia-nodo.bat."
} catch {
  Write-Host "[NODO VOCE] ERRORE: $_" -ForegroundColor Red
} finally {
  Read-Host "Premi Invio per chiudere"
}
