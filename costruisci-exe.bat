@echo off
title NODO - costruisci NODO.exe
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\costruisci-exe.ps1"
echo.
pause
