@echo off
rem ===========================================================================
rem  Windows 11 widgets - doppelklick startet das Board in einem Terminal.
rem  Nichts zu installieren ausser Python + den Paketen aus requirements.txt.
rem ===========================================================================
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

%PY% -c "import psutil" >nul 2>nul
if not %errorlevel%==0 (
    echo  psutil fehlt - installiere es einmalig:
    echo     %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
)

%PY% -m winwidgets %*
if errorlevel 1 pause
endlocal
