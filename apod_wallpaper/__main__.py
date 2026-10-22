import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from .core import NasaClient, Updater
from .storage import AppError, BusyError, default_data_dir
from .windows import Scheduler, WindowsDesktop


def main(argv=None):
    parser = argparse.ArgumentParser(description="NASA APOD wallpaper for Windows")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true", help="Update once and exit")
    modes.add_argument("--background", action="store_true", help="Scheduled update, with optional video popup")
    modes.add_argument("--enable", action="store_true", help="Enable the current user's automatic schedule")
    modes.add_argument("--disable", action="store_true", help="Pause automatic updates")
    modes.add_argument("--remove-task", action="store_true", help="Remove this user's automatic task")
    modes.add_argument("--restore", action="store_true", help="Pause automatic updates and restore the original wallpaper")
    modes.add_argument("--check", action="store_true", help="Read live APOD metadata without changing anything")
    modes.add_argument("--diagnose", action="store_true", help="Save a connection report without changing the wallpaper")
    modes.add_argument("--preview", action="store_true", help="Open the GUI without changing wallpaper or scheduling")
    parser.add_argument("--first-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args(argv)
    if args.check:
        try:
            from dataclasses import asdict
            print(json.dumps(asdict(NasaClient().fetch()), indent=2, ensure_ascii=False))
            return 0
        except AppError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    args.data_dir = args.data_dir.resolve()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(args.data_dir / "app.log", maxBytes=500_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[handler])
    try:
        if args.diagnose:
            from .diagnostics import diagnose
            return diagnose(args.data_dir)
        updater = Updater(args.data_dir, WindowsDesktop(args.data_dir))
        scheduler = Scheduler(args.data_dir)
        if args.enable:
            scheduler.enable()
        elif args.disable:
            scheduler.disable()
        elif args.remove_task:
            scheduler.remove()
        elif args.restore:
            scheduler.disable()
            updater.restore()
        elif args.once or args.background:
            # Disabled/deleted tasks must not change the desktop if an old launch was queued.
            if args.background and scheduler.status() != "on":
                return 0
            result = updater.update()
            if args.background and result.notify:
                import tkinter as tk
                from .gui import video_popup
                root = tk.Tk()
                root.withdraw()
                popup = video_popup(root, result.apod)
                updater.mark_notified(result.apod.key)
                root.wait_window(popup)
                root.destroy()
            elif sys.stdout is not None:
                print(result.message)
        else:
            import tkinter as tk
            from .gui import App
            if sys.platform == "win32":
                import ctypes
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(1)
                except (AttributeError, OSError):
                    pass
            root = tk.Tk()
            application = App(root, args.data_dir, updater, scheduler, args.first_run, args.preview)
            if args.preview:
                for control in application.controls:
                    control.configure(state="disabled")
            root.mainloop()
        return 0
    except BusyError as exc:
        logging.info(str(exc))
        if sys.stderr:
            print(str(exc), file=sys.stderr)
        return 0 if args.background else 1
    except Exception as exc:
        logging.exception("Operation failed")
        if sys.stderr:
            print(str(exc), file=sys.stderr)
        if not any((args.once, args.background, args.enable, args.disable, args.remove_task, args.restore, args.diagnose)):
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("APOD Wallpaper", f"{exc}\n\nDetails are in {args.data_dir / 'app.log'}")
            root.destroy()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
