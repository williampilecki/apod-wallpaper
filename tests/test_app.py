import io
import json
import socket
import ssl
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from PIL import Image

from apod_wallpaper.core import Apod, NasaClient, Updater, parse_apod, prepare_image
from apod_wallpaper.storage import AppError, BusyError, app_lock, read_json, write_json
from apod_wallpaper.windows import NS, decode_output, task_xml


def image_bytes(color="navy", fmt="PNG"):
    out = io.BytesIO()
    Image.new("RGB", (240, 160), color).save(out, fmt)
    return out.getvalue()


def entry(day="2026-10-17", media="image"):
    return Apod(day, "A test nebula", media, ("https://example.com/image.png",),
                "https://science.nasa.gov/apod/", "A test entry.", "Example photographer")


class FakeDesktop:
    def __init__(self):
        self.applied = []
        self.restored = []
        self.snapshots = 0

    def snapshot(self):
        self.snapshots += 1
        return {"path": "original.jpg", "style": ["6", "0"]}

    def apply(self, path, fit):
        self.applied.append((path, fit))

    def restore(self, original):
        self.restored.append(original)


class FakeClient:
    def __init__(self, apod=None):
        self.apod = apod or entry()
        self.content = image_bytes()
        self.downloads = 0
        self.error = None

    def fetch(self):
        if self.error:
            raise self.error
        return self.apod

    def image(self, url):
        self.downloads += 1
        return self.content


class ParsingTests(unittest.TestCase):
    def test_new_feed_never_downloads_article_url(self):
        item = {"date": "2026-10-17", "title": "Stars &amp; galaxies", "media_type": "image",
                "permalink": "https://science.nasa.gov/article/", "url": "https://science.nasa.gov/article/",
                "hdurl": "https://assets.science.nasa.gov/image.jpg", "credit": "<a href='/'>Ada</a>",
                "explanation": "<strong>Explanation:</strong> Stars.<br>Tomorrow."}
        result = parse_apod([item])
        self.assertEqual(result.image_urls, (item["hdurl"],))
        self.assertEqual(result.title, "Stars & galaxies")
        self.assertEqual(result.credit, "Ada")
        self.assertEqual(result.explanation, "Explanation: Stars. Tomorrow.")

    def test_latest_date_wins_even_if_feed_is_reordered(self):
        base = {"title": "Movie", "media_type": "video"}
        result = parse_apod([base | {"date": "2026-10-15"}, base | {"date": "2026-10-17"}, base | {"date": "2026-10-16"}])
        self.assertEqual(result.date, "2026-10-17")

    def test_legacy_image_url_is_accepted(self):
        result = parse_apod({"date": "2026-10-17", "title": "Stars", "media_type": "image", "url": "https://example.com/a.jpg"})
        self.assertEqual(result.image_urls, ("https://example.com/a.jpg",))

    def test_video_thumbnail_is_never_a_wallpaper_candidate(self):
        result = parse_apod({"date": "2026-10-17", "title": "Movie", "media_type": "video", "hdurl": "https://example.com/thumb.jpg"})
        self.assertEqual(result.image_urls, ())

    def test_unknown_media_is_preserved_for_safe_skip(self):
        result = parse_apod({"date": "2026-10-17", "title": "Interactive", "media_type": "iframe"})
        self.assertEqual(result.media_type, "iframe")

    def test_rejects_missing_image_in_new_response(self):
        with self.assertRaises(AppError):
            parse_apod({"date": "2026-10-17", "title": "Stars", "media_type": "image", "permalink": "https://example.com/page", "url": "https://example.com/page"})

    def test_rejects_invalid_metadata(self):
        for payload in ([], None, {}, {"date": "not-a-date"}, ["bad"]):
            with self.subTest(payload=payload), self.assertRaises(AppError):
                parse_apod(payload)

    def test_rejects_local_and_insecure_image_urls(self):
        for url in ("file:///C:/secret", "javascript:alert(1)", "http://example.com/a.jpg", "https://user:password@example.com/a.jpg"):
            with self.subTest(url=url), self.assertRaises(AppError):
                parse_apod({"date": "2026-10-17", "title": "Stars", "media_type": "image", "hdurl": url})


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.desktop = FakeDesktop()
        self.client = FakeClient()
        self.updater = Updater(self.root, self.desktop, self.client)

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        return read_json(self.root / "state.json")

    def test_first_image_is_decoded_and_backed_up_before_apply(self):
        result = self.updater.update()
        self.assertEqual(result.kind, "updated")
        with Image.open(result.image_path) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (240, 160))
        self.assertEqual(self.desktop.snapshots, 1)
        self.assertEqual(self.state()["original"]["path"], "original.jpg")

    def test_same_entry_is_not_downloaded_or_applied_twice(self):
        self.updater.update()
        self.assertEqual(self.updater.update().kind, "current")
        self.assertEqual(self.client.downloads, 1)
        self.assertEqual(len(self.desktop.applied), 1)

    def test_changed_fit_reapplies_without_replacing_original_backup(self):
        self.updater.update()
        write_json(self.root / "settings.json", {"fit": "fill"})
        self.updater.update()
        self.assertEqual(self.desktop.applied[-1][1], "fill")
        self.assertEqual(self.desktop.snapshots, 1)

    def test_video_keeps_previous_wallpaper_and_notifies_once(self):
        first = self.updater.update()
        self.client.apod = entry("2026-10-18", "video")
        result = self.updater.update()
        self.assertEqual(result.kind, "video")
        self.assertTrue(result.notify)
        self.assertEqual(self.state()["image_path"], first.image_path)
        self.assertEqual(len(self.desktop.applied), 1)
        self.assertEqual(self.client.downloads, 1)
        self.updater.mark_notified(result.apod.key)
        self.assertFalse(self.updater.update().notify)

    def test_video_on_first_run_makes_no_image_or_desktop_changes(self):
        self.client.apod = entry(media="video")
        result = self.updater.update()
        self.assertEqual(result.kind, "video")
        self.assertFalse((self.root / "images").exists())
        self.assertEqual(self.desktop.snapshots, 0)

    def test_video_notification_can_be_disabled(self):
        self.client.apod = entry(media="iframe")
        write_json(self.root / "settings.json", {"notify_video": False})
        self.assertFalse(self.updater.update().notify)

    def test_manual_video_check_offers_link_even_after_background_notification(self):
        self.client.apod = entry(media="video")
        first = self.updater.update()
        self.updater.mark_notified(first.apod.key)
        self.assertTrue(self.updater.update(force=True).notify)
        self.assertEqual(self.client.downloads, 0)
        self.assertEqual(self.desktop.applied, [])

    def test_manual_video_check_offers_link_with_background_alerts_off(self):
        self.client.apod = entry(media="video")
        write_json(self.root / "settings.json", {"notify_video": False})
        self.assertTrue(self.updater.update(force=True).notify)
        self.assertEqual(self.client.downloads, 0)

    def test_network_failure_keeps_previous_file_and_state(self):
        result = self.updater.update()
        original = Path(result.image_path).read_bytes()
        self.client.error = AppError("Offline")
        with self.assertRaises(AppError):
            self.updater.update()
        self.assertEqual(Path(result.image_path).read_bytes(), original)
        self.assertEqual(self.state()["applied_date"], "2026-10-17")
        self.assertEqual(len(self.desktop.applied), 1)

    def test_html_disguised_as_an_image_does_not_change_wallpaper(self):
        self.client.content = b"<html>temporarily unavailable</html>"
        with self.assertRaises(AppError):
            self.updater.update()
        self.assertEqual(self.desktop.snapshots, 0)
        self.assertEqual(self.desktop.applied, [])

    def test_truncated_jpeg_does_not_change_wallpaper(self):
        self.client.content = image_bytes(fmt="JPEG")[:100]
        with self.assertRaises(AppError):
            self.updater.update()
        self.assertEqual(self.desktop.applied, [])

    def test_windows_failure_is_retryable_and_original_backup_survives(self):
        self.desktop.apply = Mock(side_effect=AppError("Windows error"))
        with self.assertRaises(AppError):
            self.updater.update()
        self.assertNotIn("applied_key", self.state())
        self.assertIn("original", self.state())
        self.desktop.apply = Mock()
        self.assertEqual(self.updater.update().kind, "updated")
        self.assertEqual(self.desktop.snapshots, 1)

    def test_changed_bytes_use_new_filename_even_on_same_entry(self):
        first = self.updater.update()
        original = Path(first.image_path).read_bytes()
        self.client.content = image_bytes("red")
        second = self.updater.update(force=True)
        self.assertNotEqual(first.image_path, second.image_path)
        self.assertEqual(Path(first.image_path).read_bytes(), original)

    def test_stale_feed_does_not_roll_back_wallpaper(self):
        self.updater.update()
        self.client.apod = entry("2026-10-16")
        self.assertEqual(self.updater.update().kind, "stale")
        self.assertEqual(len(self.desktop.applied), 1)

    def test_restore_clears_applied_marker_for_next_manual_update(self):
        self.updater.update()
        self.updater.restore()
        self.assertEqual(self.desktop.restored[0]["path"], "original.jpg")
        self.assertNotIn("applied_key", self.state())
        self.assertEqual(self.updater.update().kind, "updated")

    def test_corrupt_state_is_not_silently_overwritten(self):
        (self.root / "state.json").write_text("broken json")
        with self.assertRaises(AppError):
            self.updater.update()
        self.assertEqual((self.root / "state.json").read_text(), "broken json")
        self.assertEqual(self.client.downloads, 0)

    def test_cache_limit_keeps_current_image(self):
        write_json(self.root / "settings.json", {"keep_images": 2})
        for day in ("2026-10-15", "2026-10-16", "2026-10-17"):
            self.client.apod = entry(day)
            result = self.updater.update()
        self.assertEqual(len(list((self.root / "images").glob("*.jpg"))), 2)
        self.assertTrue(Path(result.image_path).exists())


class InfrastructureTests(unittest.TestCase):
    def test_native_tls_still_checks_hostname_and_certificate(self):
        client = NasaClient()
        self.assertTrue(client.context.check_hostname)
        self.assertEqual(client.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreaterEqual(client.context.minimum_version, ssl.TLSVersion.TLSv1_2)

    @patch("apod_wallpaper.core.time.sleep")
    def test_temporary_timeout_retries_once_and_can_recover(self, sleep):
        client = NasaClient()
        client._read_bytes = Mock(side_effect=[urllib.error.URLError(TimeoutError("timed out")), b"success"])
        with self.assertLogs("apod_wallpaper.core", "WARNING"):
            self.assertEqual(client.get_bytes("https://science.nasa.gov/feed", 100), b"success")
        self.assertEqual(client._read_bytes.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("apod_wallpaper.core.time.sleep")
    def test_persistent_timeout_has_specific_error_and_bounded_retry(self, sleep):
        client = NasaClient()
        client._read_bytes = Mock(side_effect=urllib.error.URLError(TimeoutError("timed out")))
        with self.assertLogs("apod_wallpaper.core", "WARNING"), self.assertRaisesRegex(AppError, "timed out"):
            client.get_bytes("https://science.nasa.gov/feed", 100)
        self.assertEqual(client._read_bytes.call_count, 2)

    def test_certificate_failure_keeps_original_reason_and_does_not_retry(self):
        client = NasaClient()
        client._read_bytes = Mock(side_effect=urllib.error.URLError(ssl.SSLCertVerificationError("missing issuer")))
        with self.assertLogs("apod_wallpaper.core", "WARNING"), self.assertRaisesRegex(AppError, "Certificate verification failed.*missing issuer"):
            client.get_bytes("https://science.nasa.gov/feed", 100)
        self.assertEqual(client._read_bytes.call_count, 1)

    def test_dns_error_names_failed_host(self):
        client = NasaClient()
        client._read_bytes = Mock(side_effect=urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "host not found")))
        with self.assertLogs("apod_wallpaper.core", "WARNING"), self.assertRaisesRegex(AppError, "DNS could not find science.nasa.gov"):
            client.get_bytes("https://science.nasa.gov/feed", 100)

    def test_denied_or_rate_limited_requests_are_not_retried(self):
        for code in (401, 403, 429):
            with self.subTest(code=code):
                client = NasaClient()
                client._read_bytes = Mock(side_effect=urllib.error.HTTPError("https://science.nasa.gov/feed", code, "blocked", {}, None))
                with self.assertLogs("apod_wallpaper.core", "WARNING"), self.assertRaisesRegex(AppError, f"HTTP {code}"):
                    client.get_bytes("https://science.nasa.gov/feed", 100)
                self.assertEqual(client._read_bytes.call_count, 1)

    @patch("apod_wallpaper.core.time.sleep")
    def test_temporary_server_failure_retries_once(self, sleep):
        client = NasaClient()
        failure = urllib.error.HTTPError("https://science.nasa.gov/feed", 503, "unavailable", {}, None)
        client._read_bytes = Mock(side_effect=[failure, b"recovered"])
        with self.assertLogs("apod_wallpaper.core", "WARNING"):
            self.assertEqual(client.get_bytes("https://science.nasa.gov/feed", 100), b"recovered")
        self.assertEqual(client._read_bytes.call_count, 2)

    def test_atomic_write_preserves_previous_json_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json(path, {"old": True})
            with patch("apod_wallpaper.storage.os.replace", side_effect=OSError("disk problem")):
                with self.assertRaises(OSError):
                    write_json(path, {"new": True})
            self.assertEqual(read_json(path), {"old": True})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_second_update_cannot_acquire_same_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            with app_lock(directory), self.assertRaises(BusyError):
                with app_lock(directory):
                    pass

    def test_task_runs_interactively_without_admin_password_or_waking_pc(self):
        xml = task_xml("C:/User Name/venv/pythonw.exe", "C:/User Name/app.pyw", "C:/User Name/data", "S-1-5-21-123", datetime(2026, 10, 17, 9))
        tree = ET.fromstring(xml)
        ns = {"t": NS}
        self.assertEqual(tree.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns), "InteractiveToken")
        self.assertEqual(tree.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=ns), "LeastPrivilege")
        self.assertEqual(tree.findtext("t:Settings/t:WakeToRun", namespaces=ns), "false")
        self.assertEqual(tree.findtext("t:Triggers/t:CalendarTrigger/t:Repetition/t:Interval", namespaces=ns), "PT1H")
        arguments = tree.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
        self.assertIn('"C:/User Name/app.pyw"', arguments)
        self.assertIn('"C:/User Name/data"', arguments)
        self.assertNotIn("Password", xml)

    def test_windows_output_handles_unicode_xml(self):
        text = "<Task>José</Task>"
        for encoding in ("utf-16", "utf-16le", "utf-8"):
            self.assertEqual(decode_output(text.encode(encoding)), text)

    def test_image_conversion_preserves_transparency_as_black(self):
        raw = io.BytesIO()
        Image.new("RGBA", (100, 100), (255, 0, 0, 0)).save(raw, "PNG")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.jpg"
            prepare_image(raw.getvalue(), path)
            with Image.open(path) as image:
                self.assertEqual(image.getpixel((20, 20)), (0, 0, 0))

    def test_size_limited_http_response_is_rejected_before_reading(self):
        client = NasaClient()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.headers = {"Content-Length": "9999"}
        client.opener.open = Mock(return_value=response)
        with self.assertRaises(AppError):
            client.get_bytes("https://example.com/image.jpg", 100)
        response.read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
