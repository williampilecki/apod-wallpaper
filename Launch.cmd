@echo off
setlocal
set "APOD_ROOT=%LOCALAPPDATA%\APODWallpaper"
if not exist "%APOD_ROOT%\venv\Scripts\pythonw.exe" goto missing
if not exist "%APOD_ROOT%\app\app.pyw" goto missing
start "" "%APOD_ROOT%\venv\Scripts\pythonw.exe" "%APOD_ROOT%\app\app.pyw"
exit /b
:missing
echo Run Install.cmd first.
pause
