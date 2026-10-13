@echo off
title NODO - crea l'app
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\crea-app.ps1"
echo.
pause
