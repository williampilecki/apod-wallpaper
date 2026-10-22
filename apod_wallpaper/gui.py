"""Small Tk desktop window. Network and system work runs off the UI thread."""

import os
import logging
import queue
import random
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from PIL import Image, ImageOps, ImageTk

from .core import APOD_HOME, https_url
from .storage import AppError, app_lock, read_json, read_settings, write_json

BG, PANEL, PREVIEW = "#0b111b", "#141e2d", "#070d17"
TEXT, MUTED, ACCENT, LINE = "#edf3fa", "#9baec5", "#aff0d8", "#29364b"
FONT = "Segoe UI" if sys.platform == "win32" else "Arial"


def label(parent, text="", size=10, color=TEXT, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), **kwargs)


def button(parent, text, command, primary=False):
    return tk.Button(parent, text=text, command=command, relief="flat", borderwidth=0,
                     bg=ACCENT if primary else "#223149", fg=BG if primary else TEXT,
                     activebackground="#cdf6e7" if primary else "#314461",
                     activeforeground=BG if primary else TEXT, disabledforeground="#72869b",
                     font=(FONT, 10, "bold"), padx=14, pady=10, cursor="hand2",
                     highlightthickness=1, highlightbackground=LINE, takefocus=True)


def open_url(url):
    if value := https_url(url):
        webbrowser.open(value)


class App:
    def __init__(self, root, data_dir, updater, scheduler, first_run=False, preview=False):
        self.root, self.data_dir = root, Path(data_dir)
        self.updater, self.scheduler, self.preview = updater, scheduler, preview
        self.busy = False
        self.results = queue.Queue()
        self.controls = []
        self.photo = None
        self.page_url = APOD_HOME
        self.automatic = False
        self.root.title("APOD Wallpaper 1.0.1")
        self.root.configure(bg=BG)
        self.root.geometry("1000x860")
        self.root.minsize(900, 800)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda event: self.close())
        self._build()
        self.refresh()
        self.root.after(100, self.poll)
        if first_run and not preview:
            self.root.after(250, self.update)
        elif not preview:
            self.run_job(lambda: None, "Checking automatic updates...")

    def _build(self):
        container = tk.Frame(self.root, bg=BG, padx=28, pady=22)
        container.pack(fill="both", expand=True)
        top = tk.Frame(container, bg=BG)
        top.pack(fill="x")
        label(top, "APOD  /  WALLPAPER", 11, ACCENT, True).pack(side="left")
        label(top, "NASA'S ASTRONOMY PICTURE OF THE DAY", 9, MUTED).pack(side="right")
        label(container, "A new view. Every day.", 28, TEXT, True, anchor="w").pack(fill="x", pady=(17, 3))
        label(container, "One image from the universe, delivered to your desktop.", 11, MUTED, anchor="w").pack(fill="x", pady=(0, 22))

        body = tk.Frame(container, bg=BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=270)
        body.rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=PANEL, padx=18, pady=16)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        self.eyebrow = label(left, "YOUR VIEW", 9, ACCENT, True, anchor="w")
        self.eyebrow.pack(fill="x", pady=(0, 12))
        self.canvas = tk.Canvas(left, height=280, bg=PREVIEW, highlightthickness=0)
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", lambda event: self.draw_preview())
        self.title = label(left, "The universe is waiting.", 19, TEXT, True, anchor="w", justify="left", wraplength=530)
        self.title.pack(fill="x", pady=(16, 5))
        self.title.bind("<Configure>", lambda event: self.title.configure(wraplength=max(100, event.width)))
        self.credit = label(left, "", 9, MUTED, anchor="w", justify="left", wraplength=530)
        self.credit.pack(fill="x", pady=(0, 8))
        self.credit.bind("<Configure>", lambda event: self.credit.configure(wraplength=max(100, event.width)))
        self.explanation = tk.Text(left, height=4, bg=PANEL, fg=MUTED, wrap="word", relief="flat",
                                   font=(FONT, 10), cursor="arrow", padx=0, borderwidth=0,
                                   highlightthickness=0)
        self.explanation.pack(fill="both", expand=True, pady=(0, 10))
        self.explanation.configure(state="disabled")
        button(left, "Read the story on NASA  ↗", lambda: open_url(self.page_url)).pack(anchor="w")

        right = tk.Frame(body, bg=PANEL, width=270, padx=18, pady=16)
        right.grid(row=0, column=1, sticky="nsew")
        label(right, "ON YOUR DESKTOP", 9, ACCENT, True, anchor="w").pack(fill="x")
        self.auto_label = label(right, "Automatic updates are off", 12, TEXT, True, anchor="w", wraplength=232, justify="left")
        self.auto_label.pack(fill="x", pady=(12, 5))
        label(right, "Checks hourly and at sign-in.\nClosing this window is fine.", 10, MUTED, anchor="w", justify="left").pack(fill="x", pady=(0, 16))
        self.update_button = button(right, "Update now", self.update, True)
        self.update_button.pack(fill="x", pady=(0, 8))
        self.auto_button = button(right, "Enable automatic updates", self.toggle_auto)
        self.auto_button.pack(fill="x")
        self.controls += [self.update_button, self.auto_button]
        tk.Frame(right, bg=LINE, height=1).pack(fill="x", pady=14)
        label(right, "IMAGE FIT", 9, MUTED, True, anchor="w").pack(fill="x", pady=(0, 9))
        self.fit = tk.StringVar(value="fit")
        options = tk.Frame(right, bg=PANEL)
        options.pack(fill="x")
        for value in ("fit", "fill", "span"):
            radio = tk.Radiobutton(options, text=value.title(), variable=self.fit, value=value,
                                   bg=PANEL, fg=TEXT, selectcolor="#283b54", activebackground=PANEL,
                                   activeforeground=ACCENT, font=(FONT, 10),
                                   command=self.save_options, padx=4)
            radio.pack(side="left")
            self.controls.append(radio)
        label(right, "Fit keeps the whole image.\nFill crops. Span crosses monitors.", 9, MUTED, anchor="w", justify="left").pack(fill="x", pady=(8, 14))
        self.notify = tk.BooleanVar(value=True)
        notification = tk.Checkbutton(right, text="Background video alerts", variable=self.notify,
                                      bg=PANEL, fg=TEXT, selectcolor="#283b54", activebackground=PANEL,
                                      activeforeground=ACCENT, font=(FONT, 10),
                                      command=self.save_options, anchor="w")
        notification.pack(fill="x")
        self.controls.append(notification)
        label(right, "Your current wallpaper stays put.", 9, MUTED, anchor="w").pack(fill="x", pady=(3, 8))
        tk.Frame(right, bg=LINE, height=1).pack(fill="x", pady=15)
        restore = button(right, "Restore original wallpaper", self.restore)
        restore.pack(fill="x", pady=(0, 8))
        self.controls.append(restore)
        button(right, "Open app data", self.open_data).pack(fill="x")

        footer = tk.Frame(container, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(17, 0), before=body)
        self.status = label(footer, "Ready when you are.", 10, MUTED, anchor="w", justify="left", wraplength=920)
        self.status.pack(fill="x")
        self.timestamp = label(footer, "No account. No API key. Just space.", 9, MUTED, anchor="w")
        self.timestamp.pack(fill="x", pady=(5, 0))

    def refresh(self):
        try:
            self.state = read_json(self.data_dir / "state.json")
            settings = read_settings(self.data_dir)
            self.fit.set(settings["fit"])
            self.notify.set(settings["notify_video"])
            latest = self.state.get("latest", {})
            self.page_url = latest.get("page_url", APOD_HOME)
            self.title.configure(text=latest.get("title", "The universe is waiting."))
            self.credit.configure(text=latest.get("credit", "Click Update now to get the latest APOD."))
            day = latest.get("date", "")
            media = latest.get("media_type", "image")
            self.eyebrow.configure(text=f"LATEST APOD  /  {day}" if day else "YOUR NEXT VIEW")
            explanation = latest.get("explanation", "Daily astronomy, without another tab to remember. The app only replaces your wallpaper after an image has downloaded and passed validation.")
            if media != "image":
                explanation = "Today's entry is a video or interactive feature. The preview shows your previous wallpaper.\n\n" + explanation
            self.explanation.configure(state="normal")
            self.explanation.delete("1.0", "end")
            self.explanation.insert("1.0", explanation)
            self.explanation.configure(state="disabled")
            self.status.configure(text=self.state.get("error") or self.state.get("status", "Ready when you are."),
                                  fg="#f0b68b" if self.state.get("error") else MUTED)
            checked = self.state.get("checked_at")
            if checked:
                try:
                    local = datetime.fromisoformat(checked).astimezone()
                    self.timestamp.configure(text=f"Last checked {local:%b %d, %I:%M %p}   ·   Images credited to their creators")
                except ValueError:
                    pass
            self.auto_label.configure(text="Automatic updates are on" if self.automatic else "Automatic updates are off")
            self.auto_button.configure(text="Pause automatic updates" if self.automatic else "Enable automatic updates")
            self.draw_preview()
        except AppError as exc:
            self.status.configure(text=str(exc), fg="#f0b68b")

    def draw_preview(self):
        self.canvas.delete("all")
        width, height = max(self.canvas.winfo_width(), 400), 280
        path = getattr(self, "state", {}).get("image_path", "")
        if path and Path(path).is_file():
            try:
                with Image.open(path) as original:
                    image = ImageOps.contain(original.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
                self.photo = ImageTk.PhotoImage(image)
                self.canvas.create_image(width / 2, height / 2, image=self.photo)
                return
            except (OSError, ValueError):
                pass
        rng = random.Random(67)
        for _ in range(55):
            x, y = rng.randrange(width), rng.randrange(height)
            r = rng.choice([1, 1, 1, 2])
            self.canvas.create_oval(x, y, x + r, y + r, fill=rng.choice(["#29435e", "#63809b", "#a8bed1"]), outline="")
        cx, cy = width / 2, height / 2 - 10
        self.canvas.create_oval(cx - 38, cy - 38, cx + 38, cy + 38, fill="#193749", outline="#467069", width=2)
        self.canvas.create_oval(cx - 77, cy - 22, cx + 77, cy + 22, outline=ACCENT, width=2)
        self.canvas.create_text(cx, height - 40, text="YOUR DAILY WINDOW INTO SPACE", fill=MUTED, font=(FONT, 9))

    def run_job(self, function, message):
        if self.busy:
            return
        self.busy = True
        self.status.configure(text=message, fg=ACCENT)
        for control in self.controls:
            control.configure(state="disabled")

        def work():
            try:
                result = function()
                automatic = self.scheduler.status() == "on"
                self.results.put((result, automatic, None))
            except Exception as exc:
                logging.exception("Desktop operation failed")
                self.results.put((None, None, exc))

        threading.Thread(target=work, daemon=False).start()

    def poll(self):
        try:
            result, automatic, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            for control in self.controls:
                control.configure(state="normal")
            if automatic is not None:
                self.automatic = automatic
            self.refresh()
            if error:
                self.status.configure(text=str(error), fg="#f0b68b")
            elif result and getattr(result, "kind", "") == "video" and result.notify:
                try:
                    video_popup(self.root, result.apod)
                    self.updater.mark_notified(result.apod.key)
                except AppError as exc:
                    self.status.configure(text=str(exc), fg="#f0b68b")
        self.root.after(100, self.poll)

    def update(self):
        self.run_job(lambda: self.updater.update(force=True), "Checking NASA and preparing your wallpaper...")

    def toggle_auto(self):
        action = self.scheduler.disable if self.automatic else self.scheduler.enable
        self.run_job(action, "Updating your automatic schedule...")

    def save_options(self):
        try:
            with app_lock(self.data_dir):
                settings = read_settings(self.data_dir)
                settings.update(fit=self.fit.get(), notify_video=self.notify.get())
                write_json(self.data_dir / "settings.json", settings)
            self.status.configure(text="Saved. Image fit takes effect on the next update.", fg=MUTED)
        except AppError as exc:
            self.status.configure(text=str(exc), fg="#f0b68b")

    def restore(self):
        if not messagebox.askyesno("Restore original wallpaper", "Pause automatic updates and restore the wallpaper you had before APOD?", parent=self.root):
            return

        def work():
            self.scheduler.disable()
            self.updater.restore()

        self.run_job(work, "Restoring your original wallpaper...")

    def open_data(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(self.data_dir))
        else:
            self.status.configure(text=str(self.data_dir))

    def close(self):
        if self.busy:
            self.status.configure(text="Finishing the current operation. You can close the window in a moment.", fg=ACCENT)
            return
        self.root.destroy()


def video_popup(parent, entry):
    window = tk.Toplevel(parent)
    window.title("A different kind of APOD")
    window.configure(bg=PANEL)
    window.resizable(False, False)
    panel = tk.Frame(window, bg=PANEL, padx=24, pady=22)
    panel.pack()
    label(panel, "TODAY, PRESS PLAY.", 10, ACCENT, True, anchor="w").pack(fill="x")
    label(panel, entry.title, 17, TEXT, True, wraplength=380, justify="left").pack(fill="x", pady=(12, 8))
    label(panel, "The latest APOD is a video or interactive entry.\nYour wallpaper has stayed exactly as it was.", 10, MUTED, justify="left").pack(fill="x", pady=(0, 16))
    button(panel, "Open today's APOD  ↗", lambda: (open_url(entry.page_url), window.destroy()), True).pack(side="left", padx=(0, 8))
    button(panel, "Dismiss", window.destroy).pack(side="right")
    window.update_idletasks()
    x = max(0, window.winfo_screenwidth() - window.winfo_reqwidth() - 30)
    y = max(0, window.winfo_screenheight() - window.winfo_reqheight() - 85)
    window.geometry(f"+{x}+{y}")
    window.after(60000, window.destroy)
    return window
