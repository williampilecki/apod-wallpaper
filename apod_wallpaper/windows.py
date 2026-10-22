"""The Windows-specific wallpaper and Task Scheduler adapters."""

import ctypes
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from .storage import AppError

STYLES = {"fill": ("10", "0"), "fit": ("6", "0"), "span": ("22", "0")}
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def require_windows():
    if sys.platform != "win32":
        raise AppError("Wallpaper changes and automatic scheduling require Windows 10 or 11.")


def run_windows(arguments, timeout=25):
    require_windows()
    result = subprocess.run(arguments, capture_output=True, timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    result.stdout = decode_output(result.stdout)
    result.stderr = decode_output(result.stderr)
    return result


def decode_output(data):
    # schtasks can emit UTF-16 XML when redirected, even on a non-Unicode console.
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if b"\x00" in data[:100]:
        return data.decode("utf-16le", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


class WindowsDesktop:
    def __init__(self, root):
        self.root = Path(root)

    def _spi(self, action, parameter, flags=0):
        require_windows()
        from ctypes import wintypes
        library = ctypes.WinDLL("user32", use_last_error=True)
        function = library.SystemParametersInfoW
        function.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
        function.restype = wintypes.BOOL
        size = len(parameter) if action == 0x0073 else 0  # SPI_GETDESKWALLPAPER
        if not function(action, size, parameter, flags):
            error = ctypes.get_last_error()
            raise AppError(f"Windows could not change or read the wallpaper (error {error}).")

    def _style(self):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
            values = []
            for name, default in (("WallpaperStyle", "6"), ("TileWallpaper", "0")):
                try:
                    values.append(str(winreg.QueryValueEx(key, name)[0]))
                except FileNotFoundError:
                    values.append(default)
        return values

    def _set_style(self, values):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0, winreg.KEY_SET_VALUE) as key:
            for name, value in zip(("WallpaperStyle", "TileWallpaper"), values):
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))

    def snapshot(self):
        require_windows()
        buffer = ctypes.create_unicode_buffer(32768)
        self._spi(0x0073, buffer)
        source = buffer.value
        backup = ""
        if source and Path(source).is_file():
            # Keep the original bytes in case its source file is later moved.
            destination = self.root / ("original-wallpaper" + (Path(source).suffix or ".jpg"))
            if Path(source).resolve() != destination.resolve():
                shutil.copy2(source, destination)
            backup = str(destination)
        return {"path": source, "backup": backup, "style": self._style()}

    def _set(self, path, style):
        require_windows()
        if path and not Path(path).is_file():
            raise AppError("The wallpaper file is missing. No change was made.")
        previous = self._style()
        try:
            self._set_style(style)
            self._spi(0x0014, ctypes.create_unicode_buffer(str(path)), 0x0001 | 0x0002)
        except Exception:
            self._set_style(previous)
            raise

    def apply(self, path, fit):
        self._set(str(Path(path).resolve()), STYLES[fit])

    def restore(self, original):
        path = original["path"]
        if path and not Path(path).is_file():
            path = original.get("backup", path)
        self._set(path, original["style"])


def task_xml(python_path, script_path, data_dir, sid, start=None):
    """Public function so scheduling can be checked without registering a task."""
    ET.register_namespace("", NS)
    task = ET.Element(f"{{{NS}}}Task", version="1.3")

    def child(parent, name, text=None, **attributes):
        node = ET.SubElement(parent, f"{{{NS}}}{name}", attributes)
        node.text = text
        return node

    registration = child(task, "RegistrationInfo")
    child(registration, "Description", "Check NASA APOD hourly and at sign-in. Only new images change the wallpaper.")
    triggers = child(task, "Triggers")
    daily = child(triggers, "CalendarTrigger")
    child(daily, "Enabled", "true")
    start = start or (datetime.now() + timedelta(minutes=1)).replace(microsecond=0)
    child(daily, "StartBoundary", start.isoformat())
    repetition = child(daily, "Repetition")
    child(repetition, "Interval", "PT1H")
    child(repetition, "Duration", "P1D")
    child(repetition, "StopAtDurationEnd", "false")
    by_day = child(daily, "ScheduleByDay")
    child(by_day, "DaysInterval", "1")
    logon = child(triggers, "LogonTrigger")
    child(logon, "Enabled", "true")
    child(logon, "UserId", sid)
    child(logon, "Delay", "PT45S")
    principals = child(task, "Principals")
    principal = child(principals, "Principal", id="Author")
    child(principal, "UserId", sid)
    child(principal, "LogonType", "InteractiveToken")
    child(principal, "RunLevel", "LeastPrivilege")
    settings = child(task, "Settings")
    for key, value in (("MultipleInstancesPolicy", "IgnoreNew"),
                       ("DisallowStartIfOnBatteries", "false"), ("StopIfGoingOnBatteries", "false"),
                       ("StartWhenAvailable", "true"), ("RunOnlyIfNetworkAvailable", "false"),
                       ("Enabled", "true"), ("Hidden", "false"), ("WakeToRun", "false"),
                       ("ExecutionTimeLimit", "PT5M")):
        child(settings, key, value)
    actions = child(task, "Actions", Context="Author")
    execute = child(actions, "Exec")
    child(execute, "Command", str(python_path))
    child(execute, "Arguments", subprocess.list2cmdline([str(script_path), "--background", "--data-dir", str(data_dir)]))
    child(execute, "WorkingDirectory", str(Path(script_path).parent))
    return ET.tostring(task, encoding="unicode", xml_declaration=True)


class Scheduler:
    def __init__(self, root, script_path=None):
        self.root = Path(root)
        self.script = Path(script_path or Path(__file__).resolve().parents[1] / "app.pyw")
        self._sid = None

    @property
    def sid(self):
        if self._sid is None:
            result = run_windows(["whoami.exe", "/user", "/fo", "csv", "/nh"])
            match = re.search(r"S-1-\d+(?:-\d+)+", result.stdout)
            if result.returncode or not match:
                raise AppError("Could not identify your Windows account for automatic updates.")
            self._sid = match.group()
        return self._sid

    @property
    def name(self):
        return "APOD Wallpaper - " + self.sid

    def status(self):
        result = run_windows(["schtasks.exe", "/Query", "/TN", self.name, "/XML"])
        if result.returncode:
            return "off"
        try:
            tree = ET.fromstring(result.stdout.lstrip("\ufeff"))
            enabled = tree.find(f"{{{NS}}}Settings/{{{NS}}}Enabled")
            return "on" if enabled is None or enabled.text != "false" else "off"
        except ET.ParseError as exc:
            raise AppError("Could not read the automatic update task.") from exc

    def enable(self):
        require_windows()
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file() or not self.script.is_file():
            raise AppError("Run Install.cmd first so Windows can find the app and Python.")
        xml = task_xml(pythonw, self.script, self.root, self.sid)
        self.root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=".xml", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-16") as out:
                # The XML declaration must agree with the actual encoding.
                out.write(xml.replace("encoding='utf-8'", "encoding='utf-16'"))
            result = run_windows(["schtasks.exe", "/Create", "/TN", self.name, "/XML", name, "/F"])
            if result.returncode:
                detail = (result.stderr or result.stdout).strip()
                raise AppError("Windows could not enable automatic updates. " + detail)
        finally:
            Path(name).unlink(missing_ok=True)

    def disable(self):
        if self.status() == "off":
            return
        result = run_windows(["schtasks.exe", "/Change", "/TN", self.name, "/DISABLE"])
        if result.returncode:
            raise AppError("Windows could not pause automatic updates.")

    def remove(self):
        query = run_windows(["schtasks.exe", "/Query", "/TN", self.name])
        if query.returncode:
            return
        result = run_windows(["schtasks.exe", "/Delete", "/TN", self.name, "/F"])
        if result.returncode:
            raise AppError("Windows could not remove the automatic update task.")
