"""Per-user installer. Run through Install.cmd; no administrator rights needed."""

import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


def main():
    if sys.platform != "win32":
        raise SystemExit("This installer is for Windows 10 and 11.")
    if sys.version_info < (3, 11):
        raise SystemExit("Install Python 3.11 or newer first.")
    source = Path(__file__).resolve().parent
    root = Path(os.environ["LOCALAPPDATA"]) / "APODWallpaper"
    app = root / "app"
    environment = root / "venv"
    python = environment / "Scripts" / "python.exe"
    pythonw = environment / "Scripts" / "pythonw.exe"
    print("Close any existing APOD Wallpaper windows before updating an installation.")
    root.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        print("Creating a private Python environment...")
        venv.EnvBuilder(with_pip=True).create(environment)
    print("Installing image and connection libraries (Pillow and truststore)...")
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "--only-binary=:all:", "-r", str(source / "requirements.txt")], check=True)
    app.mkdir(exist_ok=True)
    shutil.copytree(source / "apod_wallpaper", app / "apod_wallpaper", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("app.pyw", "uninstall.py", "README.md", "requirements.txt", "Uninstall.cmd", "Diagnose.cmd"):
        shutil.copy2(source / name, app / name)
    subprocess.run([str(python), "-c", "import tkinter, truststore; from PIL import Image, ImageTk"], check=True)

    # Data goes through environment variables, not interpolation into PowerShell code.
    shortcut_script = r'''
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$desktop = $shell.SpecialFolders.Item('Desktop')
$programs = $shell.SpecialFolders.Item('Programs')
foreach ($folder in @($desktop, $programs)) {
    $shortcut = $shell.CreateShortcut((Join-Path $folder 'APOD Wallpaper.lnk'))
    $shortcut.TargetPath = $env:APOD_PYTHONW
    $shortcut.Arguments = '"' + $env:APOD_SCRIPT + '"'
    $shortcut.WorkingDirectory = $env:APOD_APP
    $shortcut.Description = 'NASA Astronomy Picture of the Day wallpaper'
    $shortcut.Save()
}
'''
    encoded = base64.b64encode(shortcut_script.encode("utf-16le")).decode("ascii")
    environment_vars = os.environ | {"APOD_PYTHONW": str(pythonw), "APOD_SCRIPT": str(app / "app.pyw"), "APOD_APP": str(app)}
    shortcuts = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], env=environment_vars)
    if shortcuts.returncode:
        print("Could not make shortcuts. You can still open the app with Launch.cmd.")
    print("Enabling hourly and sign-in checks for your Windows account...")
    schedule = subprocess.run([str(python), str(app / "app.pyw"), "--enable"])
    if schedule.returncode:
        print("Automatic setup did not succeed. Use the app's Enable automatic updates button or inspect app.log.")
    print("Setup complete. Opening APOD Wallpaper...")
    subprocess.Popen([str(pythonw), str(app / "app.pyw"), "--first-run"], cwd=app)
    print("Your app and data are in:", root)


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.SubprocessError) as exc:
        print("Setup could not finish:", exc)
        print("No administrator mode is needed. Check your connection and Python installation.")
        raise SystemExit(1)
