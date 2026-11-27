@echo off
rem ===========================================================================
rem  Einstellungen fuer die Desktop-Widgets (eigenes Fenster).
rem ===========================================================================
setlocal
chcp 65001 >nul 2>nul
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

%PY% -m winwidgets settings %*
if errorlevel 1 pause
endlocal
