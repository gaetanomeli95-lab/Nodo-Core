<#
  Collega NODO al tuo bot Telegram: chiede il token, trova da solo il tuo chat id e scrive le due righe in apps\api\.env.
  Prima: su Telegram cerca @BotFather, scrivi /newbot, scegli nome e username, copia il token che ti da.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root "apps\api\.env"
function Say($m, $c = "Cyan") { Write-Host "[NODO] $m" -ForegroundColor $c }

$token = (Read-Host "Incolla il token del bot (quello di BotFather) e premi Invio").Trim()
if ($token -notmatch '^\d+:[A-Za-z0-9_-]{20,}$') { Say "Il token non sembra valido (deve essere tipo 123456:ABC...). Riprova." Red; exit 1 }
try { $me = Invoke-RestMethod "https://api.telegram.org/bot$token/getMe" }
catch { Say "Telegram non riconosce questo token. Controlla di averlo copiato tutto." Red; exit 1 }
$bot = $me.result.username
Say "Bot trovato: @$bot"
Say "Ora apri Telegram, cerca @$bot, premi AVVIA e scrivigli «ciao». Aspetto fino a 2 minuti..." Yellow

$chat = $null
for ($i = 0; $i -lt 60 -and -not $chat; $i++) {
  Start-Sleep -Seconds 2
  try {
    $u = Invoke-RestMethod "https://api.telegram.org/bot$token/getUpdates?timeout=0"
    $m = $u.result | Where-Object { $_.message.chat.type -eq "private" } | Select-Object -Last 1
    if ($m) { $chat = [string]$m.message.chat.id }
  } catch { }
}
if (-not $chat) { Say "Non ho ricevuto nessun messaggio dal bot. Riprova e scrivigli «ciao»." Red; exit 1 }
Say "Trovato il tuo chat id."

$lines = @()
if (Test-Path $envFile) { $lines = Get-Content $envFile | Where-Object { $_ -notmatch '^\s*NODO_TELEGRAM_(BOT_TOKEN|CHAT_ID)\s*=' } }
$lines += "NODO_TELEGRAM_BOT_TOKEN=$token"
$lines += "NODO_TELEGRAM_CHAT_ID=$chat"
[System.IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))   # senza BOM

$body = @{ chat_id = $chat; text = "Ciao, sono NODO. Da ora ti scrivo qui: promemoria, resoconto del mattino e decisioni da approvare." }
try { Invoke-RestMethod "https://api.telegram.org/bot$token/sendMessage" -Method Post -Body $body | Out-Null; Say "Ti ho mandato un messaggio di prova su Telegram." }
catch { Say "Collegato, ma il messaggio di prova non e' partito." Yellow }
Say "Fatto. Riavvia NODO (avvia-nodo.bat) per attivarlo." Green
