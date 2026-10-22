# Architecture

APOD Wallpaper is a per-user Windows desktop application. It separates NASA
requests and update decisions from the GUI, persistent state, and Windows APIs.

## Components

| Module | Responsibility |
| --- | --- |
| `core.py` | NASA client, metadata parsing, image validation, update decisions |
| `storage.py` | Settings, atomic JSON writes, process lock |
| `windows.py` | Wallpaper API, fit settings, Task Scheduler registration |
| `gui.py` | Tkinter interface, worker thread, video notification window |
| `diagnostics.py` | Read-only connection checks and diagnostic report |
| `__main__.py` | GUI and command-line entry points |
| `install.py` | Per-user installation, dependencies, and shortcuts |

## Update flow

1. A manual check or scheduled process calls `Updater.update()`.
2. An operating-system file lock excludes concurrent updates.
3. `NasaClient.fetch()` retrieves APOD metadata over HTTPS. `parse_apod()`
   normalizes the entry and separates the article URL from image URLs.
4. The updater compares the entry with saved state:
   - An older entry leaves a newer applied wallpaper intact.
   - A video or interactive entry skips image downloads. Manual checks offer
     the article link; background checks respect the notification preference
     and suppress repeated alerts for the same entry.
   - An already-applied image with unchanged fit is skipped on scheduled checks.
     Manual checks force a refresh.
   - A new image or changed fit proceeds to download and validation.
5. Pillow decodes the complete image, applies EXIF orientation, and writes a
   Windows-compatible JPEG. Download and pixel limits reject oversized files.
6. Before the first wallpaper replacement, the updater saves the current
   wallpaper path, a backup of the image when available, and its fit settings.
7. `WindowsDesktop.apply()` updates the fit settings and invokes
   `SystemParametersInfoW` through `ctypes`.
8. After Windows reports success, the updater records the applied image and
   prunes the cache. The active wallpaper is retained.

## Networking

The client uses the system certificate store through `truststore`, with hostname
verification enabled. Temporary connection errors and selected server errors get
one retry. Certificate failures, HTTP access denials, and rate limits are reported
without repeated requests. Responses have size limits; image bytes are validated
before a Windows wallpaper call.

## Scheduling and concurrency

Windows Task Scheduler launches `app.pyw --background` hourly and after sign-in.
The task runs with the current user's interactive token and ordinary privileges.
It does not wake the computer. Background processes exit after checking the feed
and handling any notification; the main window does not need to remain open.

Manual GUI operations run on a worker thread. Results return through a queue,
and Tk widgets are updated on the main thread. An OS file lock coordinates manual
and scheduled processes.

## State and recovery

Settings and update state are stored under `%LOCALAPPDATA%\APODWallpaper`.
JSON files are written to temporary files and atomically replaced. The saved
wallpaper baseline is persisted before the first Windows change.

Windows wallpaper changes and JSON writes are separate operations. A crash
between them can cause the next check to repeat an update. State only records
an image as applied after Windows reports success.

Cache filenames include a digest of the downloaded bytes. A manual refresh can
therefore save changed image content at a new path without overwriting the file
currently used by the desktop.

Restore pauses automatic updates, restores the saved image and fit, and clears
the baseline. A subsequent wallpaper update captures a new baseline.

The preview displays the last successfully applied APOD image. Video entries
retain that preview; when no usable image is cached, a placeholder is displayed.

## Platform boundaries

The metadata client, image processing, and state logic can run without Windows.
Wallpaper changes and task registration require Windows 10 or 11. Automated
tests use fake adapters for desktop operations; see [testing](TESTING.md) for
coverage and integration checks.
