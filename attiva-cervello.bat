@echo off
setlocal
title NODO - attiva il cervello (abbonamento Claude)
set "CLAUDE=%USERPROFILE%\.local\bin\claude.exe"
if not exist "%CLAUDE%" (
  echo Claude Code non risulta installato.
  echo Apri PowerShell, incolla questa riga e premi Invio:
  echo.
  echo     irm https://claude.ai/install.ps1 ^| iex
  echo.
  echo Poi fai di nuovo doppio click su questo file.
  pause
  exit /b 1
)
powershell -NoProfile -Command "$d = Join-Path $env:USERPROFILE '.local\bin'; $p = [Environment]::GetEnvironmentVariable('Path','User'); if (-not $p) { $p = '' }; if (($p -split ';') -notcontains $d) { $n = if ($p) { $p.TrimEnd(';') + ';' + $d } else { $d }; [Environment]::SetEnvironmentVariable('Path', $n, 'User'); Write-Host ('Sistemato: da ora il comando claude funziona nelle nuove finestre di PowerShell.') }"
echo.
echo Ora si apre Claude Code. Se te lo chiede, scegli l'accesso con l'account Claude
echo (abbonamento Pro) e conferma nel browser. Quando vedi Claude Code pronto, scrivi  /exit  e premi Invio.
echo.
pause
"%CLAUDE%"
echo.
echo Fatto. Ora chiudi NODO (se e' aperto) e riavvialo con avvia-nodo.bat:
echo nella scheda Accessi la riga "Cervello" deve essere verde.
pause
