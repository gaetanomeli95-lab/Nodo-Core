@echo off
title NODO - avvio automatico
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\avvio-automatico.ps1"
echo.
pause
