@echo off
rem ===========================================================================
rem  Windows 11 widgets - doppelklick startet das Board in einem Terminal.
rem  Nichts zu installieren ausser Python + den Paketen aus requirements.txt.
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

%PY% -c "import psutil" >nul 2>nul
if not %errorlevel%==0 (
    if exist requirements.txt (
        echo  Erststart: installiere psutil ...
        %PY% -m pip install -q -r requirements.txt
    ) else (
        echo  Erststart: installiere psutil ...
        %PY% -m pip install -q psutil
    )
    %PY% -c "import psutil" >nul 2>nul
    if not %errorlevel%==0 (
        echo.
        echo  Das hat nicht geklappt. Bitte einmal von Hand:
        echo     %PY% -m pip install psutil
        pause
        exit /b 1
    )
)

echo  Beenden mit q  -  Tasten: space einfrieren, 1-7 Karten an/aus, c Farben
%PY% -m winwidgets %*
if errorlevel 1 pause
endlocal
