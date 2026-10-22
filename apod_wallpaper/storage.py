"""Local settings and crash-safe state, separate from downloaded images."""

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


class AppError(Exception):
    """An error that can be shown to the user without a traceback."""


class BusyError(AppError):
    pass


def default_data_dir():
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "APODWallpaper"
    return Path.home() / ".local" / "share" / "APODWallpaper"


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return {} if default is None else default.copy()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
        return value
    except (OSError, ValueError) as exc:
        raise AppError(f"Cannot read {path.name}. Your wallpaper has not been changed. "
                       "Open the data folder to inspect or back up this file.") from exc


def write_json(path, value):
    """Write a complete temporary file, then replace the old file atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


DEFAULT_SETTINGS = {"fit": "fit", "notify_video": True, "keep_images": 7}


def read_settings(root):
    settings = DEFAULT_SETTINGS | read_json(Path(root) / "settings.json")
    if settings["fit"] not in ("fill", "fit", "span"):
        raise AppError("Invalid wallpaper fit in settings.json.")
    if not isinstance(settings["notify_video"], bool):
        raise AppError("Invalid notification setting in settings.json.")
    if type(settings["keep_images"]) is not int or not 1 <= settings["keep_images"] <= 30:
        raise AppError("keep_images must be between 1 and 30.")
    return settings


@contextmanager
def app_lock(root):
    """An OS lock prevents a scheduled update and a manual update from racing."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "update.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BusyError("Another update is already running. Try again in a moment.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
