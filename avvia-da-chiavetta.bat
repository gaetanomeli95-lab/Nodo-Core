@echo off
rem NODO CORE - avvio dalla chiavetta USB, senza installare niente sul PC (vedi scripts\avvia-chiavetta.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\avvia-chiavetta.ps1" %*
if errorlevel 1 pause
