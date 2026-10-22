"""Fetch, validate, and apply one APOD. No GUI code lives here."""

import hashlib
import io
import json
import logging
import os
import socket
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .storage import AppError, app_lock, read_json, read_settings, write_json

FEED_URL = "https://science.nasa.gov/wp-json/wp/v2/apod-basic?per_page=1"
APOD_HOME = "https://science.nasa.gov/apod/"
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_PIXELS = 60_000_000
LOG = logging.getLogger(__name__)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div"):
            self.parts.append(" ")


def plain_text(value):
    if not isinstance(value, str):
        return ""
    parser = PlainText()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


def https_url(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
        if any(ord(c) < 32 for c in value):
            return ""
        return value
    except ValueError:
        return ""


@dataclass(frozen=True)
class Apod:
    date: str
    title: str
    media_type: str
    image_urls: tuple
    page_url: str
    explanation: str
    credit: str

    @property
    def key(self):
        identity = f"{self.date}|{self.media_type}|{self.image_urls}"
        return hashlib.sha256(identity.encode()).hexdigest()[:16]


def parse_apod(payload):
    """The new NASA feed is a list; the legacy API returned one object."""
    if isinstance(payload, list):
        if not payload or not all(isinstance(item, dict) for item in payload):
            raise AppError("NASA returned an empty or invalid APOD feed. Try again later.")
        payload = max(payload, key=lambda item: str(item.get("date", "")))
    if not isinstance(payload, dict):
        raise AppError("NASA returned an unexpected response. Try again later.")
    try:
        day = payload["date"]
        date.fromisoformat(day)
        if len(day) != 10:
            raise ValueError("Invalid date")
        title = plain_text(payload["title"])
        media = payload["media_type"].lower()
        if not title or not media:
            raise ValueError("Missing title or media type")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AppError("NASA's response is missing valid APOD metadata.") from exc
    page = https_url(payload.get("permalink")) or f"https://apod.nasa.gov/apod/ap{date.fromisoformat(day):%y%m%d}.html"
    candidates = []
    if media == "image":
        if value := https_url(payload.get("hdurl")):
            candidates.append(value)
        # In the 2026 feed, `url` is an HTML article. Never treat it as a fallback image.
        value = https_url(payload.get("url"))
        if value and value not in candidates and not payload.get("permalink"):
            candidates.append(value)
        if not candidates:
            raise AppError("This APOD is marked as an image but has no downloadable image.")
    return Apod(day, title, media, tuple(candidates), page,
                plain_text(payload.get("explanation")),
                plain_text(payload.get("credit") or payload.get("copyright")))


class HTTPSOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not https_url(newurl):
            raise AppError("The download redirected to an unsupported address.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NasaClient:
    def __init__(self):
        try:
            import truststore
        except ImportError as exc:
            raise AppError("Run Install.cmd from the latest download to install the connection update.") from exc
        # Windows CryptoAPI verifies the certificate chain, including intermediate
        # certificates. Hostname and certificate verification remain required.
        self.context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.set_alpn_protocols(["http/1.1"])
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.context), HTTPSOnlyRedirect())

    def get_bytes(self, url, limit):
        if not https_url(url):
            raise AppError("NASA returned an invalid download address.")
        host = urllib.parse.urlsplit(url).hostname
        for attempt in range(2):
            try:
                return self._read_bytes(url, limit)
            except urllib.error.HTTPError as exc:
                failure = exc
                retry = exc.code in (500, 502, 503, 504)
                detail = f"{host} returned HTTP {exc.code}."
                if exc.code == 429:
                    detail += " NASA is limiting requests; wait before trying again."
            except (urllib.error.URLError, OSError) as exc:
                failure = exc
                reason = getattr(exc, "reason", exc)
                retry = isinstance(reason, (TimeoutError, ConnectionResetError, ConnectionAbortedError))
                if isinstance(reason, ssl.SSLCertVerificationError):
                    detail = f"Certificate verification failed for {host}: {getattr(reason, 'verify_message', str(reason))}."
                    retry = False
                elif isinstance(reason, ssl.SSLError):
                    detail = f"Secure connection failed for {host}: {reason}."
                    retry = False
                elif isinstance(reason, (TimeoutError, socket.timeout)):
                    detail = f"The request to {host} timed out after 30 seconds."
                elif isinstance(reason, socket.gaierror):
                    detail = f"DNS could not find {host}: {reason}."
                    retry = reason.errno == socket.EAI_AGAIN
                else:
                    detail = f"Could not connect to {host}: {reason}."
            # Retain the actual exception in the log; the previous release hid it.
            LOG.warning("Network attempt %s failed: %s", attempt + 1, detail)
            if retry and attempt == 0:
                time.sleep(1)
                continue
            raise AppError(detail + " Wallpaper unchanged. See app.log for details.") from failure

    def _read_bytes(self, url, limit):
        request = urllib.request.Request(url, headers={"User-Agent": "APODWallpaper/1.0.1", "Accept": "*/*"})
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=30) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > limit:
                    raise AppError("This download exceeds the app's size limit. Wallpaper unchanged.")
                data = bytearray()
                while chunk := response.read(min(65536, limit + 1 - len(data))):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise AppError("This download exceeds the app's size limit. Wallpaper unchanged.")
                    if time.monotonic() - started > 60:
                        raise AppError("The download took too long. The app will try again on its next check.")
                return bytes(data)
        except ValueError as exc:
            raise AppError("NASA returned an invalid download response.") from exc

    def fetch(self):
        try:
            return parse_apod(json.loads(self.get_bytes(FEED_URL, 2 * 1024 * 1024)))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AppError("NASA returned an unreadable feed. Try again later.") from exc

    def image(self, url):
        return self.get_bytes(url, MAX_IMAGE_BYTES)


def prepare_image(data, path):
    """Decode all pixels before producing a Windows-compatible JPEG."""
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("This image exceeds the download size limit.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width * height > MAX_PIXELS or min(width, height) < 16:
                    raise AppError("This image's dimensions are not supported. Wallpaper unchanged.")
                image.load()  # A valid header alone does not make a valid image.
                image = ImageOps.exif_transpose(image)
                if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    rgb = Image.new("RGB", rgba.size, "black")
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                    image = rgb
                else:
                    image = image.convert("RGB")
                image.thumbnail((7680, 7680), Image.Resampling.LANCZOS)
                fd, temporary = tempfile.mkstemp(suffix=".jpg", dir=path.parent)
                with os.fdopen(fd, "wb") as out:
                    image.save(out, format="JPEG", quality=93)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, path)
                return image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise AppError("The downloaded file is not a complete, supported image. Wallpaper unchanged.") from exc
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


@dataclass
class UpdateResult:
    kind: str
    message: str
    apod: Apod | None = None
    image_path: str = ""
    notify: bool = False


class Updater:
    def __init__(self, root, desktop, client=None):
        self.root = Path(root)
        self.desktop = desktop
        self.client = client or NasaClient()

    def update(self, force=False):
        with app_lock(self.root):
            settings = read_settings(self.root)
            state_path = self.root / "state.json"
            state = read_json(state_path)
            try:
                entry = self.client.fetch()
                state["checked_at"] = datetime.now(timezone.utc).isoformat()
                state["latest"] = asdict(entry)
                state.pop("error", None)
                if entry.date < state.get("applied_date", ""):
                    result = UpdateResult("stale", "NASA returned an older entry. Keeping your newer wallpaper.", entry)
                elif entry.media_type != "image":
                    # A deliberate manual check always offers the video link.
                    # Automatic checks notify only once, and respect the toggle.
                    notify = force or (settings["notify_video"] and state.get("notified_key") != entry.key)
                    media_name = "a video" if entry.media_type == "video" else "an interactive entry"
                    result = UpdateResult("video", f"The latest APOD is {media_name}. Wallpaper unchanged.", entry,
                                          state.get("image_path", ""), notify)
                elif (not force and state.get("applied_key") == entry.key
                      and state.get("applied_fit") == settings["fit"]
                      and Path(state.get("image_path", "")).is_file()):
                    result = UpdateResult("current", "You already have the latest image.", entry, state["image_path"])
                else:
                    errors = []
                    for url in entry.image_urls:
                        try:
                            data = self.client.image(url)
                            digest = hashlib.sha256(data).hexdigest()[:12]
                            destination = self.root / "images" / f"apod-{entry.date}-{entry.key}-{digest}.jpg"
                            size = prepare_image(data, destination)
                            break
                        except AppError as exc:
                            errors.append(exc)
                    else:
                        raise errors[-1]
                    # Persist the original settings before making the first desktop change.
                    # If applying fails, the original wallpaper can still be restored.
                    if "original" not in state:
                        state["original"] = self.desktop.snapshot()
                        write_json(state_path, state)
                    self.desktop.apply(destination, settings["fit"])
                    state.update(applied_key=entry.key, applied_date=entry.date,
                                 image_path=str(destination), applied_fit=settings["fit"],
                                 applied_title=entry.title, applied_credit=entry.credit,
                                 dimensions=list(size))
                    result = UpdateResult("updated", "Your wallpaper is up to date.", entry, str(destination))
                state["status"] = result.message
                write_json(state_path, state)
                self._prune(state.get("image_path"), settings["keep_images"])
                LOG.info("%s: %s", result.kind, entry.date)
                return result
            except (AppError, OSError) as exc:
                state["error"] = str(exc)
                state["checked_at"] = datetime.now(timezone.utc).isoformat()
                write_json(state_path, state)
                LOG.warning("Update failed: %s", exc)
                raise

    def mark_notified(self, key):
        with app_lock(self.root):
            state = read_json(self.root / "state.json")
            state["notified_key"] = key
            write_json(self.root / "state.json", state)

    def restore(self):
        with app_lock(self.root):
            state = read_json(self.root / "state.json")
            if "original" not in state:
                raise AppError("There is no saved original wallpaper yet.")
            self.desktop.restore(state["original"])
            for key in ("original", "applied_key", "applied_date", "image_path", "applied_fit", "applied_title", "applied_credit", "dimensions"):
                state.pop(key, None)
            state["status"] = "Your original wallpaper was restored."
            write_json(self.root / "state.json", state)

    def _prune(self, current, keep):
        try:
            images = sorted((self.root / "images").glob("apod-*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
            preserved = {Path(current)} if current else set()
            for image in images:
                if image in preserved:
                    continue
                if len(preserved) < keep:
                    preserved.add(image)
                else:
                    image.unlink(missing_ok=True)
        except OSError:
            LOG.warning("Could not remove an old cached image.")
