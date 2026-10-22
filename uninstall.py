"""Disable and remove integration; leave the current wallpaper file usable."""

import base64
import subprocess
import sys

from apod_wallpaper.storage import default_data_dir
from apod_wallpaper.windows import Scheduler


def main():
    if sys.platform != "win32":
        raise SystemExit("This uninstaller is for Windows.")
    root = default_data_dir()
    print("Removing APOD Wallpaper's automatic task and shortcuts...")
    Scheduler(root).remove()
    script = r'''
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($name in @('Desktop', 'Programs')) {
    $path = Join-Path $shell.SpecialFolders.Item($name) 'APOD Wallpaper.lnk'
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
}
'''
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], check=True)
    print("Automatic updates and shortcuts were removed.")
    print("Your current wallpaper and local files were kept at:", root)
    print("To finish removing all files, choose another wallpaper in Windows Settings, close APOD,")
    print("then delete the APODWallpaper folder shown above.")
    print("To restore your pre-APOD wallpaper first, use Restore original wallpaper in the app.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Uninstall could not finish:", exc)
        raise SystemExit(1)
