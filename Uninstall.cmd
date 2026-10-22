@echo off
setlocal
set "APOD_ROOT=%LOCALAPPDATA%\APODWallpaper"
if not exist "%APOD_ROOT%\venv\Scripts\python.exe" goto missing
"%APOD_ROOT%\venv\Scripts\python.exe" "%APOD_ROOT%\app\uninstall.py"
pause
exit /b
:missing
echo APOD Wallpaper is not installed at the default location.
pause
