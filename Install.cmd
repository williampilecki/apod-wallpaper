@echo off
setlocal
cd /d "%~dp0"
echo.
echo APOD Wallpaper - Windows setup
echo.
py -3 -c "import sys, tkinter; assert sys.version_info >= (3, 11)" >nul 2>&1
if not errorlevel 1 (
    py -3 install.py
    goto finish
)
python -c "import sys, tkinter; assert sys.version_info >= (3, 11)" >nul 2>&1
if not errorlevel 1 (
    python install.py
    goto finish
)
echo Python 3.11 or newer, with Tk, is needed for this source release.
echo Install Python for Windows from https://www.python.org/downloads/windows/
echo Keep the Tcl/Tk option enabled, then run Install.cmd again.
:finish
echo.
pause
