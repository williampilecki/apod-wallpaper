@echo off
setlocal
set "APOD_ROOT=%LOCALAPPDATA%\APODWallpaper"
if not exist "%APOD_ROOT%\venv\Scripts\python.exe" goto missing
"%APOD_ROOT%\venv\Scripts\python.exe" "%APOD_ROOT%\app\app.pyw" --diagnose
pause
exit /b
:missing
echo Run Install.cmd first.
pause
