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

rem ohne Konsolenfenster starten: pythonw.exe statt python.exe
set "PYW=%PY:python=pythonw%"
set "PYW=%PYW:py -3=py -3w%"
where %PYW% >nul 2>nul
if %errorlevel%==0 (
    start "" %PYW% -m winwidgets desktop %*
) else (
    %PY% -m winwidgets desktop %*
    if errorlevel 1 pause
)
endlocal
