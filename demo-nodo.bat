@echo off
rem NODO CORE - demo simulata: nessun accesso necessario, niente esce dal computer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\avvia-nodo.ps1" -Demo %*
if errorlevel 1 pause
