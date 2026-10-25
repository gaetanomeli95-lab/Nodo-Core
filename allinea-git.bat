@echo off
rem Mette in ordine git dopo aver copiato l aggiornamento sopra la cartella di NODO (facoltativo).
cd /d "%~dp0"
if not exist .git ( echo Questa cartella non usa git: NODO funziona lo stesso. & pause & exit /b 0 )
where git >/dev/null 2>/dev/null || ( echo Git non e installato: NODO funziona lo stesso. & pause & exit /b 0 )
if exist .git\index.lock del /f .git\index.lock
if exist .git\objects\maintenance.lock del /f .git\objects\maintenance.lock
git fetch -q nodo-storia.bundle work/pr8-fixes || ( echo Non riesco a leggere l aggiornamento. & pause & exit /b 1 )
git reset -q FETCH_HEAD
echo Fatto: git ora e allineato a NODO 2492.
pause
