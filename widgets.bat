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

rem psutil is optional - without it the board reads the Windows API directly.
%PY% -c "import psutil" >nul 2>nul
if errorlevel 1 (
    if exist requirements.txt (
        echo  Versuche psutil zu installieren (nur fuer die Netzwerk-Karte) ...
        %PY% -m pip install -q psutil >nul 2>nul
    )
    %PY% -c "import psutil" >nul 2>nul
    if errorlevel 1 (
        echo  psutil nicht verfuegbar - macht nichts, alles ausser Netzwerk laeuft.
    )
)

echo  Beenden mit q  -  Tasten: space einfrieren, 1-7 Karten an/aus, c Farben
echo  Fenster-Version (auf dem Desktop): widgets-desktop.bat
%PY% -m winwidgets %*
if errorlevel 1 pause
endlocal
