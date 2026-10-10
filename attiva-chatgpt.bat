@echo off
setlocal
title NODO - collega il tuo abbonamento ChatGPT (secondo cervello)
where codex >nul 2>nul
if errorlevel 1 (
  if exist "%APPDATA%\npm\codex.cmd" (
    set "PATH=%PATH%;%APPDATA%\npm"
  ) else (
    where npm >nul 2>nul
    if errorlevel 1 (
      echo Serve Node.js ^(lo stesso che usa NODO^): scaricalo da https://nodejs.org e riprova.
      pause
      exit /b 1
    )
    echo Installo Codex CLI, il programma ufficiale di OpenAI ^(1-2 minuti^)...
    call npm install -g @openai/codex
    set "PATH=%PATH%;%APPDATA%\npm"
  )
)
where codex >nul 2>nul
if errorlevel 1 (
  echo Non riesco a trovare Codex dopo l'installazione: mandami il messaggio qui sopra.
  pause
  exit /b 1
)
echo.
echo Ora si apre il browser: scegli "Sign in with ChatGPT" e accedi con il tuo account ChatGPT Plus.
echo.
pause
call codex login
echo.
call codex login status
echo.
echo Fatto. Riavvia NODO con avvia-nodo.bat: nella scheda Accessi la riga
echo "Cervello di riserva (abbonamento ChatGPT)" deve essere verde.
pause
