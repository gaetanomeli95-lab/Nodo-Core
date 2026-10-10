@echo off
rem NODO CORE - doppio click per avviare (vedi COME-INIZIARE.md)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\avvia-nodo.ps1" %*
if errorlevel 1 pause
