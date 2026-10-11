@echo off
setlocal
set "NODO_PY=%~dp0apps\api\.venv\Scripts\python.exe"
if not exist "%NODO_PY%" (
  echo Avvia prima NODO con avvia-nodo.bat, poi riprova questa diagnosi.
  pause
  exit /b 1
)
"%NODO_PY%" "%~dp0apps\api\nodo\voice\diagnostics.py" --output "%~dp0nodo-diagnosi.json"
if errorlevel 1 (
  echo Diagnosi non riuscita. Copia il messaggio qui sopra.
  pause
  exit /b 1
)
start "" notepad.exe "%~dp0nodo-diagnosi.json"
