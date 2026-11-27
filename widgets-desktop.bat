@echo off
rem ===========================================================================
rem  Windows 11 Widgets als Fenster auf dem Desktop.
rem  startet python -m winwidgets desktop  (tkinter ist in Python enthalten)
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

%PY% -m winwidgets desktop %*
if errorlevel 1 pause
endlocal
