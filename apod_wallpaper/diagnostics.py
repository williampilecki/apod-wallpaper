"""A read-only connection check for debugging another Windows computer."""

from datetime import datetime, timezone
from pathlib import Path
import platform
import ssl
import sys
import tempfile

from . import __version__
from .core import FEED_URL, NasaClient, prepare_image


def diagnose(root):
    lines = [f"APOD Wallpaper {__version__} connection check",
             datetime.now(timezone.utc).isoformat(),
             f"Platform: {platform.system()} {platform.release()}",
             f"Python: {sys.version.split()[0]}",
             f"TLS: {ssl.OPENSSL_VERSION}", f"Feed: {FEED_URL}",
             "This check does not change the wallpaper or the automatic schedule."]
    success = False
    try:
        client = NasaClient()
        lines.append("Certificate verification: native system trust store, enabled")
        entry = client.fetch()
        lines.extend(["Metadata: PASS", f"Entry: {entry.date} / {entry.title}",
                      f"Media type: {entry.media_type}"])
        if entry.media_type == "image":
            data = client.image(entry.image_urls[0])
            with tempfile.TemporaryDirectory() as directory:
                dimensions = prepare_image(data, Path(directory) / "check.jpg")
            lines.append(f"Image download and decode: PASS ({dimensions[0]} x {dimensions[1]})")
        else:
            lines.append("Video/interactive handling: skip image download; preserve wallpaper")
        success = True
    except Exception as exc:
        lines.append(f"FAILED: {type(exc).__name__}: {exc}")
        if exc.__cause__:
            lines.append(f"Underlying error: {type(exc.__cause__).__name__}: {exc.__cause__}")
    path = Path(root) / "diagnostics.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = "\n".join(lines) + "\n"
    path.write_text(report, encoding="utf-8")
    print(report)
    print("Saved connection report:", path)
    return 0 if success else 1
