@echo off
title NODO - collega Telegram
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\attiva-telegram.ps1"
echo.
pause
